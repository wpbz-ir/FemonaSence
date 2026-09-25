from __future__ import annotations

from dataclasses import dataclass
from typing import Final

CURRENCY: Final[str] = "XTR"


@dataclass(frozen=True, slots=True)
class StarsInvoice:
    payload: str
    title: str
    description: str
    amount_stars: int


class TelegramStarsProvider:
    """Adapter boundary for Telegram Stars payments.

    Actual invoice creation/handling belongs in the Bot layer so Telegram's
    pre-checkout and successful-payment updates remain the source of the
    payment event. The commerce core consumes the normalized result.
    """

    name = "TELEGRAM_STARS"
    currency = CURRENCY
