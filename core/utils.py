"""通用小工具。"""

from __future__ import annotations

import re


def _safe_name(text: str, fallback: str = "video") -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", (text or "").strip())
    return text[:80] or fallback
