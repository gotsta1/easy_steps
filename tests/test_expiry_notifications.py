from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Entitlement, EntitlementStatus, User
from app.db.repo import EntitlementRepo


NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


@pytest.fixture
def notification_repo():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[User.__table__, Entitlement.__table__])
    with Session(engine, expire_on_commit=False) as db:
        db.add(User(id=1, telegram_user_id=123, created_at=NOW))
        db.commit()
        # Execute the real repository SELECT against an isolated database.
        adapter = SimpleNamespace(
            execute=AsyncMock(side_effect=db.execute),
            flush=AsyncMock(side_effect=db.flush),
            add=db.add,
        )
        yield EntitlementRepo(adapter), db
    engine.dispose()


def add_entitlement(db, hours, **overrides):
    values = dict(
        user_id=1,
        product_key="club",
        status=EntitlementStatus.active,
        active_until=NOW + timedelta(hours=hours),
        duration_days=30,
        created_at=NOW,
        updated_at=NOW,
    )
    values.update(overrides)
    ent = Entitlement(**values)
    db.add(ent)
    db.commit()
    return ent


@pytest.mark.parametrize(
    ("hours", "expected_threshold"),
    [
        (72.01, None),
        (72, 3),
        (48.01, 3),
        (48, 2),
        (24.01, 2),
        (24, 1),
        (12, 1),
        (0.01, 1),
        (0, None),
        (-1, None),
    ],
)
async def test_expiry_windows_do_not_overlap(notification_repo, hours, expected_threshold):
    repo, db = notification_repo
    add_entitlement(db, hours)

    matches = [days for days in (3, 2, 1) if await repo.get_expiring_soon(NOW, days)]

    assert matches == ([] if expected_threshold is None else [expected_threshold])


@pytest.mark.parametrize(
    "overrides",
    [
        {"product_key": "menu"},
        {"status": EntitlementStatus.inactive},
        {"status": EntitlementStatus.canceled},
        {"active_until": None},
        {"expiry_notified_days": 1},
    ],
)
async def test_one_day_excludes_ineligible_access(notification_repo, overrides):
    repo, db = notification_repo
    add_entitlement(db, 12, **overrides)

    assert await repo.get_expiring_soon(NOW, 1) == []


async def test_one_day_marker_prevents_repeats(notification_repo):
    repo, db = notification_repo
    ent = add_entitlement(db, 12, expiry_notified_days=2)

    assert await repo.get_expiring_soon(NOW, 1) == [ent]

    ent.expiry_notified_days = 1
    db.commit()

    assert await repo.get_expiring_soon(NOW + timedelta(minutes=15), 1) == []


async def test_renewal_resets_one_day_marker(notification_repo):
    repo, db = notification_repo
    ent = add_entitlement(db, 12, expiry_notified_days=1)
    new_expiry = NOW + timedelta(days=30)

    await repo.upsert(
        user_id=1,
        product_key="club",
        status=EntitlementStatus.active,
        active_until=new_expiry,
        duration_days=30,
    )
    db.commit()

    assert ent.expiry_notified_days is None
    assert await repo.get_expiring_soon(NOW, 1) == []
    assert await repo.get_expiring_soon(new_expiry - timedelta(hours=24), 1) == [ent]
