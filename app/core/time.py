from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    """Return current UTC time (always timezone-aware)."""
    return datetime.now(tz=timezone.utc)


def utcnow_plus(seconds: int) -> datetime:
    """Return UTC now + `seconds`."""
    return utcnow() + timedelta(seconds=seconds)


def is_in_future(dt: datetime | None) -> bool:
    """Return True when `dt` is None (no expiry) or still in the future."""
    if dt is None:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt > utcnow()


def ensure_tz(dt: datetime | None) -> datetime | None:
    """Attach UTC timezone to a naive datetime, or return None as-is."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
