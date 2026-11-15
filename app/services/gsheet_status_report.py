"""Daily, read-only club entitlement snapshot published to a dedicated worksheet."""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import Settings
from app.core.time import utcnow
from app.db.models import Entitlement, EntitlementStatus, User
from app.db.session import AsyncSessionFactory
from app.services.google_sheets_status import read_report_timestamp, replace_status_report

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))


@dataclass(frozen=True)
class StatusRow:
    telegram_user_id: int
    active: bool
    days: int | None
    expires_at: datetime | None


def is_status_report_configured(settings: Settings) -> bool:
    return bool(
        settings.GSHEET_CREDENTIALS_PATH
        and settings.GSHEET_SPREADSHEET_ID
        and settings.GSHEET_STATUS_SHEET_ID is not None
        and settings.GSHEET_STATUS_SHEET_NAME
        and settings.GSHEET_STATUS_SHEET_NAME != settings.GSHEET_SHEET_NAME
    )


def report_is_due(last_success: datetime | None, now: datetime, hour_msk: int) -> bool:
    if last_success is None:
        return True
    cutoff = now.astimezone(MSK).replace(hour=hour_msk, minute=0, second=0, microsecond=0)
    if cutoff > now:
        cutoff -= timedelta(days=1)
    return last_success < cutoff


def make_status_row(telegram_id: int, entitlement: Entitlement, now: datetime) -> StatusRow:
    expires = entitlement.active_until
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    active = entitlement.status == EntitlementStatus.active and (
        expires is None or expires > now
    )
    days = None if active and expires is None else (
        math.ceil((expires - now).total_seconds() / 86400) if active else 0
    )
    return StatusRow(telegram_id, active, days, expires)


async def load_status_rows(now: datetime) -> list[StatusRow]:
    async with AsyncSessionFactory() as db:
        result = await db.execute(
            select(User.telegram_user_id, Entitlement)
            .join(Entitlement, Entitlement.user_id == User.id)
            .where(Entitlement.product_key == "club")
        )
        records = result.all()
    # Do not silently double-count a broken one-entitlement-per-product invariant.
    ids = [telegram_id for telegram_id, _ in records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_club_entitlements_in_status_report")
    rows = [make_status_row(telegram_id, entitlement, now) for telegram_id, entitlement in records]
    return sorted(rows, key=lambda row: (not row.active, row.days is None,
                                        row.days or 0, row.telegram_user_id))


class StatusReportJob:
    """Share the delivery loop; a sheet timestamp survives application restarts."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.loaded = False
        self.last_success: datetime | None = None

    async def run_if_due(self) -> bool:
        if not is_status_report_configured(self.settings):
            return False
        try:
            if not self.loaded:
                self.last_success = await asyncio.to_thread(read_report_timestamp, self.settings)
                self.loaded = True
            now = utcnow()
            if not report_is_due(self.last_success, now, self.settings.GSHEET_STATUS_UPDATE_HOUR_MSK):
                return False
            rows = await load_status_rows(now)
            await asyncio.to_thread(replace_status_report, self.settings, rows, now)
            self.last_success = now
            logger.info("gsheet_status_report_updated users=%d active=%d", len(rows),
                        sum(row.active for row in rows))
            return True
        except Exception as exc:
            # Provider response bodies can contain private sheet data or credentials.
            logger.error("gsheet_status_report_failed error_type=%s", type(exc).__name__)
            return False
