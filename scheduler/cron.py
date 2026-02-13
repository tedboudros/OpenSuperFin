"""Minimal cron expression parser. No external dependencies.

Supports standard 5-field cron: minute hour day_of_month month day_of_week

Examples:
    "0 16 * * 1-5"   -> weekdays at 4pm
    "0 9 * * 0"      -> Sundays at 9am
    "*/5 * * * *"     -> every 5 minutes
    "0 9,17 * * *"    -> 9am and 5pm daily
"""

from __future__ import annotations

from datetime import datetime


def cron_matches(expression: str, dt: datetime) -> bool:
    """Check if a datetime matches a cron expression.

    Args:
        expression: 5-field cron string (minute hour dom month dow)
        dt: datetime to check against

    Returns:
        True if the datetime matches all cron fields.
    """
    parts = expression.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (need 5 fields): {expression!r}")

    minute, hour, dom, month, dow = parts

    return (
        _field_matches(minute, dt.minute, 0, 59)
        and _field_matches(hour, dt.hour, 0, 23)
        and _field_matches(dom, dt.day, 1, 31)
        and _field_matches(month, dt.month, 1, 12)
        and _field_matches(dow, dt.isoweekday() % 7, 0, 6)  # 0=Sun, 6=Sat
    )


def _field_matches(field: str, value: int, min_val: int, max_val: int) -> bool:
    """Check if a single cron field matches a value.

    Supports: *, */N, N, N-M, N,M,O
    """
    # Wildcard
    if field == "*":
        return True

    # Step: */N
    if field.startswith("*/"):
        try:
            step = int(field[2:])
            return value % step == 0
        except ValueError:
            raise ValueError(f"Invalid cron step: {field!r}")

    # List: N,M,O (may contain ranges)
    if "," in field:
        return any(_field_matches(part.strip(), value, min_val, max_val) for part in field.split(","))

    # Range: N-M
    if "-" in field:
        try:
            start, end = field.split("-", 1)
            return int(start) <= value <= int(end)
        except ValueError:
            raise ValueError(f"Invalid cron range: {field!r}")

    # Exact value
    try:
        return value == int(field)
    except ValueError:
        raise ValueError(f"Invalid cron field: {field!r}")
