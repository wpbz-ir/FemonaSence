from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PaymentStartResult:
    success: bool
    payment_url: str | None = None
    authority: str | None = None
    provider_invoice_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentVerifyResult:
    success: bool
    provider_reference: str | None = None
    amount: Decimal | None = None
    error_code: str | None = None
    error_message: str | None = None


class PaymentProvider(Protocol):
    name: str

    async def create_payment(
        self,
        *,
        order_number: str,
        amount_toman: Decimal,
        description: str,
        callback_url: str,
        email: str | None = None,
        mobile: str | None = None,
    ) -> PaymentStartResult: ...

    async def verify_payment(
        self,
        *,
        authority: str,
        amount_toman: Decimal,
        callback_payload: dict,
    ) -> PaymentVerifyResult: ...
