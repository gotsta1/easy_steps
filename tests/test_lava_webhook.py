from __future__ import annotations

from datetime import datetime, timezone

from app.api.routes.lava_webhook import _extract_payment_time


def test_extract_payment_time_uses_lava_timestamp() -> None:
    result = _extract_payment_time({"timestamp": "2026-07-31T12:21:32.585825Z"})

    assert result == datetime(2026, 7, 31, 12, 21, 32, 585825, tzinfo=timezone.utc)
