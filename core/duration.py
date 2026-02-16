"""Duration parsing helpers for configuration values."""

from __future__ import annotations

import re
from datetime import timedelta

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}


def parse_duration(value: str) -> timedelta:
    """Parse compact duration strings like '60s', '4h', '7d'."""
    match = _DURATION_RE.match(str(value or ""))
    if not match:
        raise ValueError(f"Invalid duration: {value!r}. Expected '<int><s|m|h|d>'.")

    amount = int(match.group(1))
    unit = match.group(2).lower()
    seconds = amount * _UNIT_SECONDS[unit]
    return timedelta(seconds=seconds)
