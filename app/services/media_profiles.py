from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TranscodeProfile:
    code: str
    height: int
    crf: int
    maxrate: str
    bufsize: str


PROFILES = {
    "480": TranscodeProfile("480", 480, 23, "1800k", "3600k"),
    "720": TranscodeProfile("720", 720, 22, "3500k", "7000k"),
    "1080": TranscodeProfile("1080", 1080, 21, "5500k", "11000k"),
}


def normalize_quality(value: str) -> str:
    raw = str(value or "").lower().replace("p", "").strip()
    if raw not in PROFILES:
        raise ValueError(f"Unsupported target quality: {value}")
    return raw


def get_profile(value: str) -> TranscodeProfile:
    return PROFILES[normalize_quality(value)]
