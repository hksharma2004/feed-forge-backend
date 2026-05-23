from __future__ import annotations

import re
from typing import Any


def compact_text(text: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def normalize_score(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def sanitize_post_text(text: str) -> str:
    cleaned = text.replace("—", " - ").replace("*", "")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"^[A-Z][A-Za-z ]{2,30}:\s+", "", cleaned.strip())
    return cleaned.strip()
