from __future__ import annotations

import re

# کاراکترهای نشانه mojibake (UTF-8 خوانده‌شده به‌عنوان cp1252/latin1)
_MOJIBAKE = set("ØÙÛÃÂâ€š™œž�")
_PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")
# کنترل‌کاراکترهای C1 که هنگام decode بایت‌های تعریف‌نشده cp1252 (0x81،0x8D،0x8F،0x90،0x9D)
# با latin1 باقی می‌مانند و در نمایش نامرئی‌اند.
_C1_RE = re.compile(r"[\u0080-\u009f]")
_REPL = "\ufffd"

# بهترین‌حدس برای بایت‌های ازدست‌رفته (U+FFFD) در توالی‌های پرتکرار فارسی:
#   «Ù�» → D9 81 = «ف»   |   «Ø�» → D8 81 = «ء»   |   «Û�» → DB 81 = «ہ»
_BEST_FIT = {
    "Ù\ufffd": "Ù\x81",
    "Ø\ufffd": "Ø\x81",
    "Û\ufffd": "Û\x81",
    "Ã\ufffd": "Ã\x81",
}

_MAX_PASSES = 4


def _score(value: str) -> tuple[int, int]:
    """(امتیاز کیفیت، امتیاز جریمه) — هرچه فارسیِ سالم بیشتر و خرابی کمتر بهتر."""
    fa = len(_PERSIAN_RE.findall(value))
    penalty = sum(value.count(ch) for ch in _MOJIBAKE)
    penalty += len(_C1_RE.findall(value))
    penalty += value.count(_REPL) * 2
    return fa, -penalty


def _has_artifacts(value: str) -> bool:
    return any(ch in value for ch in _MOJIBAKE) or bool(_C1_RE.search(value)) or _REPL in value


def _hybrid_bytes(value: str) -> bytes | None:
    """کدگذار ترکیبی: cp1252 برای کاراکترهای پرینتی (€،‡،Œ،…) و بایت خام برای C1.

    متن‌های mojibake واقعی معمولاً ترکیبی هستند: بخشی از بایت‌ها به cp1252 نگاشت
    شده‌اند (مثل 0x80→€ و 0x87→‡) و بخشی (بایت‌های تعریف‌نشده cp1252 مثل 0x81)
    به‌صورت کنترل‌کاراکتر C1 باقی مانده‌اند. هیچ کدگذار استانداردی هر دو را با هم
    پوشش نمی‌دهد؛ این تابع دقیقاً همان نگاشت معکوس را انجام می‌دهد.
    """
    out = bytearray()
    for ch in value:
        code = ord(ch)
        if 0x80 <= code <= 0x9F:
            out.append(code)
            continue
        try:
            out.extend(ch.encode("cp1252"))
        except UnicodeEncodeError:
            return None
    return bytes(out)


def _round_trip_candidates(value: str) -> list[str]:
    """تلاش برای بازگردانی UTF-8 که در گذشته به‌اشتباه به‌صورت cp1252/latin1 خوانده شده است.

    - cp1252 برای € و Œ و ... جواب می‌دهد ولی روی کنترل‌کاراکترهای C1 خطا می‌دهد.
    - latin1 برعکس: C1 را پاس می‌دهد ولی روی € خطا می‌دهد.
    - hybrid هر دو دنیا را پوشش می‌دهد (حالت غالب متن‌های واقعی).
    هر سه امتحان می‌شوند؛ امتیازدهی بهترین را برمی‌گزیند.
    """
    candidates: list[str] = []
    for encoding in ("cp1252", "latin1"):
        try:
            candidates.append(value.encode(encoding).decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    hybrid = _hybrid_bytes(value)
    if hybrid is not None:
        try:
            candidates.append(hybrid.decode("utf-8"))
        except UnicodeDecodeError:
            pass
    return candidates


def _best_fit_candidate(value: str) -> str | None:
    """برای متن‌هایی که بخشی از بایت‌هایشان برای همیشه با � جایگزین شده است،
    یک نامزد با جایگزینی بهترین‌حدسی می‌سازد."""
    replaced = value
    for broken, fixed in _BEST_FIT.items():
        replaced = replaced.replace(broken, fixed)
    return replaced if replaced != value else None


def repair_mojibake(value: str | None) -> str | None:
    if value is None or not isinstance(value, str) or not value:
        return value
    if not _has_artifacts(value):
        return value

    best = value
    best_score = _score(best)

    # چند پاس: mojibake دولایه (دوبار encode/decode اشتباه) نیاز به دو round-trip دارد.
    for _ in range(_MAX_PASSES):
        candidates = _round_trip_candidates(best)
        fit = _best_fit_candidate(best)
        if fit is not None:
            candidates.extend(_round_trip_candidates(fit))

        improved = False
        for candidate in candidates:
            score = _score(candidate)
            if score > best_score:
                best, best_score, improved = candidate, score, True
        if not improved:
            break

    return best


def clean_text(value: str | None, limit: int | None = None) -> str:
    """پاکسازی عمومی متن: حذف فاصله‌های اضافی + اصلاح mojibake. همیشه رشته برمی‌گرداند."""
    text = repair_mojibake(value)
    if text is None:
        return ""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rstrip()
    return text
