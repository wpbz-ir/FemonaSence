from __future__ import annotations

# موتور واحد و قوی برای اصلاح mojibake در app/services/text_normalization.py پیاده شده است.
# این ماژول فقط یک لایه سازگاری برای فراخواننده‌های قدیمی (catalog.clean_caption و ...) است.
from app.services.text_normalization import clean_text, repair_mojibake  # noqa: F401

__all__ = ["repair_mojibake", "clean_text"]
