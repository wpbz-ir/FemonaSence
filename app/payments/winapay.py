from __future__ import annotations

import logging
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.payments.base import PaymentStartResult, PaymentVerifyResult
from app.services.runtime_payment_config import load_payment_config

logger = logging.getLogger(__name__)

# [INT-c] Runtime-config timeout bounds — mirror validate_payment_config()
# (app/services/runtime_payment_config.py: 5..120 seconds); 30s is the same
# default that module stores when the operator never touched the panel.
_GATEWAY_TIMEOUT_MIN = 5.0
_GATEWAY_TIMEOUT_MAX = 120.0
_GATEWAY_TIMEOUT_DEFAULT = 30.0


def _gateway_settings() -> tuple[str, float]:
    """[INT-c] Read base_url/timeout from the RUNTIME payment config.

    پیکربندی اجرایی (data/runtime/payment_gateway.json، ذخیره‌شده از پنل ادمین)
    ملاک است؛ اگر فایل غایب یا ناقص باشد، load_payment_config() خودش به مقادیر
    env (settings.winapay_base_url / پیش‌فرض‌ها) fallback می‌کند. timeout بر حسب
    ثانیه از پیکربندی خوانده و به بازه‌ی امن 5..120 محدود می‌شود.

    https-only نگاه داشته می‌شود: حتی اگر JSON به‌صورت دستی با http:// دست‌کاری
    شده باشد، این provider هرگز به نشانی غیر HTTPS درخواست نمی‌زند (پیکربندی
    ذخیره‌شده از پنل هم از validate_payment_config همان تضمین را می‌گیرد).
    خطا WinaPayError است تا مسیر فراخواننده‌ها (payments.py -> 502 + FAILED
    attempt) ساختاریافته باقی بماند.
    """
    config = load_payment_config()
    base_url = str(config.get("base_url") or settings.winapay_base_url).strip().rstrip("/")
    if not base_url.startswith("https://"):
        raise WinaPayError(
            "WINAPAY_CONFIG_INVALID",
            "نشانی پایه درگاه ویناپی باید با HTTPS باشد.",
        )
    try:
        timeout = float(config.get("timeout_seconds", _GATEWAY_TIMEOUT_DEFAULT))
    except (TypeError, ValueError):
        timeout = _GATEWAY_TIMEOUT_DEFAULT
    timeout = min(_GATEWAY_TIMEOUT_MAX, max(_GATEWAY_TIMEOUT_MIN, timeout))
    return base_url, timeout


class WinaPayError(Exception):
    """[P0-8] Structured WinaPay provider failure.

    Raised instead of letting a bare ValueError / decimal.InvalidOperation
    escape when prepare/verify INPUTS are invalid (non-numeric amount, empty
    Authority, malformed gateway Amount). Callers already catch broadly
    (app/api/payments.py per 4-G01-a: except Exception -> FAILED
    PaymentAttempt + HTTP 502), so raising this typed error is safe and keeps
    the failure reason structured via error_code/error_message.
    """

    def __init__(self, error_code: str, error_message: str) -> None:
        super().__init__(error_message or error_code)
        self.error_code = error_code
        self.error_message = error_message


def _quantize_amount(amount_toman: Decimal, *, context: str) -> Decimal:
    """[P0-8] Structured failure for a non-numeric amount instead of a raw
    decimal.InvalidOperation crash (which is an ArithmeticError, NOT a
    ValueError, so the old request-path except never caught it)."""
    try:
        return Decimal(amount_toman).quantize(Decimal("1"))
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise WinaPayError(
            "WINAPAY_AMOUNT_INVALID",
            f"مبلغ ویناپی در {context} عدد معتبر نیست: {amount_toman!r}",
        ) from exc


class WinaPayProvider:
    name = "WINAPAY"

    def __init__(self, *, timeout: float | None = None) -> None:
        # [INT-c] ``timeout`` is kept for backward compatibility but the EFFECTIVE
        # per-request timeout now comes from the runtime payment config
        # (data/runtime/payment_gateway.json -> load_payment_config, clamped to
        # 5..120s) with the env/settings fallback inside _gateway_settings().
        self.timeout = float(timeout) if timeout is not None else _GATEWAY_TIMEOUT_DEFAULT

    @property
    def merchant_id(self) -> str:
        return settings.winapay_merchant_id

    async def create_payment(
        self,
        *,
        order_number: str,
        amount_toman: Decimal,
        description: str,
        callback_url: str,
        email: str | None = None,
        mobile: str | None = None,
    ) -> PaymentStartResult:
        if not self.merchant_id:
            return PaymentStartResult(
                success=False,
                error_code="WINAPAY_CONFIG_MISSING",
                error_message="Merchant ID تنظیم نشده است.",
            )
        amount = _quantize_amount(amount_toman, context="PaymentRequest")
        # [FIX-B] is_finite guard: Decimal NaN compares False against < 100, so a
        # NaN amount used to slip past this check and be sent to the gateway as
        # the Amount field. (Infinity already fails quantize above; NaN does not.)
        if (not amount.is_finite()) or amount < 100:
            return PaymentStartResult(
                success=False,
                error_code="WINAPAY_AMOUNT_INVALID",
                error_message="حداقل مبلغ ویناپی 100 تومان است.",
            )

        # [INT-c] base_url/timeout از پیکربندی runtime؛ خطای پیکربندی (http غیر
        # مجاز) مثل خطای MerchantID با یک PaymentStartResult ساختاریافته برمی‌گردد.
        try:
            base_url, timeout = _gateway_settings()
        except WinaPayError as exc:
            return PaymentStartResult(
                success=False,
                error_code=exc.error_code,
                error_message=exc.error_message,
            )

        payload = urlencode(
            {
                "MerchantID": self.merchant_id,
                "Amount": str(amount),
                "InvoiceID": order_number,
                "Description": description,
                "Email": email or "",
                "Mobile": mobile or "",
                "CallbackURL": callback_url,
            }
        )
        # [P0-8] Gateway contract (winapay.io/webservice/rest, ZarinPal-family per
        # docs/FINAL_AUDIT_FA.md): request body is FORM-ENCODED (urlencode above —
        # no code comment or doc string anywhere claims a JSON request body), and
        # the response is JSON. Invariant: declared Content-Type must equal the
        # actual body, so it is application/x-www-form-urlencoded (was wrongly
        # application/json — the gateway could reject every Toman payment).
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.post(
                    f"{base_url}/PaymentRequest",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    content=payload,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.exception("WinaPay PaymentRequest failed")
            # [P0-8] Keyword args are mandatory: field order is (success,
            # payment_url, authority, provider_invoice_id, error_code,
            # error_message) — the old positional call landed the code in
            # payment_url and the exception text in authority.
            return PaymentStartResult(
                success=False,
                error_code="WINAPAY_REQUEST_FAILED",
                error_message=str(exc),
            )

        if data.get("Status") == 100:
            return PaymentStartResult(
                success=True,
                payment_url=data.get("PaymentUrl"),
                authority=data.get("Authority"),
                provider_invoice_id=order_number,
            )
        return PaymentStartResult(
            success=False,
            error_code=f"WINAPAY_STATUS_{data.get('Status')}",
            error_message=str(data),
        )

    async def verify_payment(
        self,
        *,
        authority: str,
        amount_toman: Decimal,
        callback_payload: dict,
    ) -> PaymentVerifyResult:
        if str(callback_payload.get("PaymentStatus", "")) != "OK":
            return PaymentVerifyResult(
                success=False,
                error_code="USER_CANCELLED_OR_FAILED",
                error_message="پرداخت در Callback موفق گزارش نشده است.",
            )

        # [P0-8] Structured failure for an invalid verify input: an empty
        # Authority used to be silently POSTed to the gateway as garbage.
        if not str(authority or "").strip():
            raise WinaPayError(
                "WINAPAY_AUTHORITY_INVALID",
                "Authority برای Verify ویناپی خالی است.",
            )

        amount = _quantize_amount(amount_toman, context="PaymentVerification")
        # [INT-c] همان پیکربندی runtime مسیر Verify — خطای https بودنِ base_url
        # اینجا مثل Authority نامعتبر به‌صورت WinaPayError ساختاریافته بالا می‌رود.
        base_url, timeout = _gateway_settings()
        payload = urlencode(
            {
                "MerchantID": self.merchant_id,
                "Amount": str(amount),
                "Authority": authority,
            }
        )
        # [P0-8] Same contract as PaymentRequest: form-encoded body, JSON response,
        # declared Content-Type must be application/x-www-form-urlencoded.
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.post(
                    f"{base_url}/PaymentVerification",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    content=payload,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.exception("WinaPay PaymentVerification failed")
            # [P0-8] Keyword args are mandatory: field order is (success,
            # provider_reference, amount, error_code, error_message) — the old
            # positional call landed the code in provider_reference and the
            # exception text into the Decimal amount field.
            return PaymentVerifyResult(
                success=False,
                error_code="WINAPAY_VERIFY_FAILED",
                error_message=str(exc),
            )

        if data.get("Status") != 100:
            return PaymentVerifyResult(
                success=False,
                error_code=f"WINAPAY_VERIFY_STATUS_{data.get('Status')}",
                error_message=str(data),
            )

        raw_amount = data.get("Amount")
        if raw_amount in (None, ""):
            provider_amount = None
        else:
            try:
                provider_amount = Decimal(str(raw_amount))
            except (ArithmeticError, TypeError, ValueError) as exc:
                raise WinaPayError(
                    "WINAPAY_RESPONSE_MALFORMED",
                    f"مبلغ بازگشتی از ویناپی عدد معتبر نیست: {raw_amount!r}",
                ) from exc
        if provider_amount is not None and provider_amount != amount:
            return PaymentVerifyResult(
                success=False,
                error_code="WINAPAY_AMOUNT_MISMATCH",
                error_message="مبلغ Verify شده با مبلغ سفارش یکسان نیست.",
            )
        return PaymentVerifyResult(
            success=True,
            provider_reference=str(data.get("RefID")) if data.get("RefID") is not None else None,
            amount=provider_amount,
        )
