from __future__ import annotations

import re

_MOJIBAKE = set("ØÙÛÃÂâ€š™œž�")
_PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")


def _score(value: str) -> tuple[int, int]:
    moj = sum(value.count(ch) for ch in _MOJIBAKE)
    fa = len(_PERSIAN_RE.findall(value))
    return moj, fa


def repair_mojibake(value: str | None) -> str | None:
    if value is None or not isinstance(value, str) or not value:
        return value
    before = _score(value)
    if before[0] < 2:
        return value

    candidates: list[str] = []
    for encoding in ("latin1", "cp1252"):
        try:
            candidates.append(value.encode(encoding).decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    best = value
    best_score = before
    for candidate in candidates:
        score = _score(candidate)
        if score[1] > best_score[1] and score[0] < best_score[0]:
            best = candidate
            best_score = score
    return best
