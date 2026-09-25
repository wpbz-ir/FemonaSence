from __future__ import annotations

def _score(value: str) -> tuple[int, int, int]:
    persian = sum(0x0600 <= ord(ch) <= 0x06FF for ch in value)
    suspicious = sum(ch in "ÃÂØÙÛÐÑ�" for ch in value)
    replacement = value.count("�")
    return persian, suspicious, replacement

def repair_mojibake(value: str | None) -> str:
    text = "" if value is None else str(value)
    if not text:
        return text
    before = _score(text)
    if before[1] == 0 and before[2] == 0:
        return text
    candidates: list[str] = []
    for encoding in ("latin1", "cp1252"):
        try:
            repaired = text.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        candidates.append(repaired)
    if not candidates:
        return text
    best = max(candidates, key=_score)
    after = _score(best)
    if after[2] > before[2]:
        return text
    if after[0] > before[0] and after[1] <= before[1]:
        return best
    if before[2] and after[2] < before[2]:
        return best
    return text

def clean_text(value: str | None, limit: int | None = None) -> str:
    text = repair_mojibake(value).strip()
    return text if limit is None else text[:limit]
