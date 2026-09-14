from datetime import datetime, timezone
from types import SimpleNamespace

from app.db.models import EntitlementStatus
from app.main import _mark_entitlement_kicked


def test_mark_entitlement_kicked_records_exact_time() -> None:
    kicked_at = datetime(2026, 9, 14, 12, 30, tzinfo=timezone.utc)
    entitlement = SimpleNamespace(
        status=EntitlementStatus.active,
        kicked_at=None,
        updated_at=None,
    )

    _mark_entitlement_kicked(entitlement, kicked_at)

    assert entitlement.status == EntitlementStatus.inactive
    assert entitlement.kicked_at == kicked_at
    assert entitlement.updated_at == kicked_at
