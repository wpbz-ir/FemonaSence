from __future__ import annotations

import logging
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.payments.base import PaymentStartResult, PaymentVerifyResult

logger = logging.getLogger(__name__)


class WinaPayProvider:
    name = "WINAPAY"

    def __init__(self, *, timeout: float = 30.0) -> None:
        self.timeout = timeout

    @property
    def merchant_id(self) -> str:
        return "sandbox" if settings.winapay_sandbox else settings.winapay_merchant_id

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
        amount = Decimal(amount_toman).quantize(Decimal("1"))
        if amount < 100:
            return PaymentStartResult(
                success=False,
                error_code="WINAPAY_AMOUNT_INVALID",
                error_message="حداقل مبلغ ویناپی 100 تومان است.",
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
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                response = await client.post(
                    f"{settings.winapay_base_url.rstrip('/')}/PaymentRequest",
                    headers={"Content-Type": "application/json"},
                    content=payload,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.exception("WinaPay PaymentRequest failed")
            return PaymentStartResult(False, "WINAPAY_REQUEST_FAILED", str(exc))

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

        amount = Decimal(amount_toman).quantize(Decimal("1"))
        payload = urlencode(
            {
                "MerchantID": self.merchant_id,
                "Amount": str(amount),
                "Authority": authority,
            }
        )
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                response = await client.post(
                    f"{settings.winapay_base_url.rstrip('/')}/PaymentVerification",
                    headers={"Content-Type": "application/json"},
                    content=payload,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.exception("WinaPay PaymentVerification failed")
            return PaymentVerifyResult(False, "WINAPAY_VERIFY_FAILED", str(exc))

        if data.get("Status") != 100:
            return PaymentVerifyResult(
                success=False,
                error_code=f"WINAPAY_VERIFY_STATUS_{data.get('Status')}",
                error_message=str(data),
            )

        raw_amount = data.get("Amount")
        provider_amount = Decimal(str(raw_amount)) if raw_amount not in (None, "") else None
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
