from __future__ import annotations

from datetime import datetime

from zoneinfo import ZoneInfo


def cron_field_count(expr: str) -> int:
    return len(str(expr or "").split())


def iso_to_seven_field_cron(at_iso: str, *, timezone: str) -> str:
    """Convert ISO8601 datetime into 7-field cron (Quartz format):
    second minute hour day month dow year.

    If the input has no timezone, interpret it in `timezone`.
    """
    s = (at_iso or "").strip()
    if not s:
        raise ValueError("at_iso is empty")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    tz = ZoneInfo(timezone)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)
    return f"{dt.second} {dt.minute} {dt.hour} {dt.day} {dt.month} ? {dt.year}"


def validate_cron_expression(expr: str, *, timezone: str) -> None:
    """Validate cron expression format is 7 fields (Quartz format: second minute hour day month dow year).

    Note: for 7-field one-shot with a fixed past year, `croniter.get_next()`
    can fail; we only validate syntax here.
    """
    from croniter import croniter  # type: ignore

    raw = str(expr or "").strip()
    if not raw:
        raise ValueError("cron_expr is empty")

    n = cron_field_count(raw)
    if n != 7:
        raise ValueError(
            f"cron_expr must have 7 fields (second minute hour day month dow year), got {n} fields"
        )
    # Use second_at_beginning=True for Quartz 7-field format
    if not croniter.is_valid(raw, second_at_beginning=True):
        raise ValueError("invalid cron expression")
    _ = ZoneInfo(timezone)
    croniter(raw, datetime.now(tz=ZoneInfo(timezone)), second_at_beginning=True)
