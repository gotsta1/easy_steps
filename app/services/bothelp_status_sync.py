"""Durable synchronization of current subscription state to BotHelp."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
import time

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.time import utcnow
from app.db.models import Entitlement, EntitlementStatus, User
from app.db.session import AsyncSessionFactory
from app.services.bothelp_api import BotHelpAPIError, BotHelpClient, get_bothelp_client
from app.services.bothelp_mapping import matches_identity, refresh_directory_mappings
from app.services.entitlements import CLUB_PRODUCT_KEY

logger = logging.getLogger(__name__)

NEVER_PAID = "never_paid"
ACTIVE = "active"
EXPIRED = "expired"


def subscription_status_for_entitlement(
    entitlement: Entitlement | None,
    now: datetime | None = None,
) -> str:
    """Return the three-state club status exposed to BotHelp."""
    if entitlement is None:
        return NEVER_PAID

    if now is None:
        now = utcnow()

    active_until = entitlement.active_until
    is_active = entitlement.status == EntitlementStatus.active and (
        active_until is None or active_until > now
    )
    return ACTIVE if is_active else EXPIRED


def is_bothelp_status_sync_configured(settings: Settings) -> bool:
    return bool(
        settings.BOTHELP_CLIENT_ID
        and settings.BOTHELP_CLIENT_SECRET
        and settings.BOTHELP_CHANNEL_ID
        and settings.BOTHELP_SUBSCRIPTION_STATUS_FIELD
    )


async def _load_user_and_club(
    db: AsyncSession,
    telegram_user_id: int,
) -> tuple[User | None, Entitlement | None]:
    result = await db.execute(
        select(User, Entitlement)
        .outerjoin(
            Entitlement,
            sa.and_(
                Entitlement.user_id == User.id,
                Entitlement.product_key == CLUB_PRODUCT_KEY,
            ),
        )
        .where(User.telegram_user_id == telegram_user_id)
        .with_for_update(of=User)
        .execution_options(populate_existing=True)
    )
    row = result.one_or_none()
    return (row[0], row[1]) if row else (None, None)


async def _write_status(
    db: AsyncSession,
    settings: Settings,
    client: BotHelpClient,
    user: User,
    desired_status: str,
) -> bool:
    if not user.bothelp_subscriber_id:
        return False

    now = utcnow()
    if user.bothelp_status_next_retry_at and user.bothelp_status_next_retry_at > now:
        return False
    user.bothelp_status_sync_attempts = (user.bothelp_status_sync_attempts or 0) + 1
    try:
        if (user.bothelp_mapping_verified_at is None
                or user.bothelp_mapping_verified_at < now - timedelta(seconds=settings.BOTHELP_MAPPING_REFRESH_SECONDS)):
            profile = None
            if client.directory_channel == settings.BOTHELP_CHANNEL_ID and client.directory is not None:
                if time.monotonic() - client.directory_refreshed_at < settings.BOTHELP_MAPPING_REFRESH_SECONDS:
                    profile = client.directory.get(user.bothelp_subscriber_id)
            if profile is None:
                profile = await client.get_subscriber(user.bothelp_subscriber_id)
            if not matches_identity(profile, user.telegram_user_id, settings.BOTHELP_CHANNEL_ID):
                raise BotHelpAPIError(404, "mapping_identity_not_verified")
            user.bothelp_mapping_verified_at = now
        await client.replace_custom_field(
            user.bothelp_subscriber_id,
            settings.BOTHELP_SUBSCRIPTION_STATUS_FIELD,
            desired_status,
        )
    except BotHelpAPIError as exc:
        user.bothelp_status_sync_last_error = str(exc)[:1000]
        delay = 3600 if exc.status_code == 404 else min(
            3600, 60 * 2 ** min(user.bothelp_status_sync_attempts - 1, 6),
        )
        user.bothelp_status_next_retry_at = utcnow() + timedelta(seconds=delay)
        if exc.status_code == 404:
            user.bothelp_mapping_verified_at = None
        logger.warning(
            "bothelp_status_sync_failed tg_id=%d bothelp_id=%d desired=%s attempt=%d",
            user.telegram_user_id,
            user.bothelp_subscriber_id,
            desired_status,
            user.bothelp_status_sync_attempts,
        )
        await db.flush()
        return False

    user.bothelp_subscription_status = desired_status
    user.bothelp_status_synced_at = utcnow()
    user.bothelp_status_sync_last_error = None
    user.bothelp_status_next_retry_at = None
    user.bothelp_status_sync_attempts = 0
    await db.flush()
    logger.info(
        "bothelp_status_synced tg_id=%d bothelp_id=%d status=%s",
        user.telegram_user_id,
        user.bothelp_subscriber_id,
        desired_status,
    )
    return True


async def sync_telegram_user_status(
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    client: BotHelpClient | None = None,
) -> bool:
    """Directly update the field after the business transaction has committed."""
    if not is_bothelp_status_sync_configured(settings):
        return False

    user, club = await _load_user_and_club(db, telegram_user_id)
    if user is None or not user.bothelp_subscriber_id:
        return False

    desired_status = subscription_status_for_entitlement(club)
    if user.bothelp_subscription_status == desired_status and user.bothelp_status_synced_at is not None:
        return True

    if client is None:
        client = get_bothelp_client()
    return await _write_status(db, settings, client, user, desired_status)


async def _load_sync_candidates(
    db: AsyncSession,
    limit: int,
    refresh_seconds: int = 86400,
) -> list[tuple[User, Entitlement | None, str]]:
    now = utcnow()
    active_condition = sa.and_(
        Entitlement.id.isnot(None),
        Entitlement.status == EntitlementStatus.active,
        sa.or_(Entitlement.active_until.is_(None), Entitlement.active_until > now),
    )
    desired_status = sa.case(
        (Entitlement.id.is_(None), NEVER_PAID),
        (active_condition, ACTIVE),
        else_=EXPIRED,
    )
    priority = sa.case(
        (active_condition, 0),
        (User.bothelp_subscription_status.is_distinct_from(desired_status), 1),
        (Entitlement.id.isnot(None), 2),
        else_=3,
    )
    result = await db.execute(
        select(User, Entitlement, desired_status.label("desired_status"))
        .outerjoin(
            Entitlement,
            sa.and_(
                Entitlement.user_id == User.id,
                Entitlement.product_key == CLUB_PRODUCT_KEY,
            ),
        )
        .where(
            User.bothelp_subscriber_id.isnot(None),
            sa.or_(User.bothelp_status_next_retry_at.is_(None), User.bothelp_status_next_retry_at <= now),
            sa.or_(
                User.bothelp_subscription_status.is_distinct_from(desired_status),
                sa.and_(Entitlement.id.isnot(None), sa.or_(
                    User.bothelp_status_synced_at.is_(None),
                    User.bothelp_status_synced_at < now - timedelta(seconds=refresh_seconds),
                )),
            ),
        )
        .order_by(priority, User.bothelp_status_synced_at.asc().nulls_first(), User.id)
        .limit(limit)
    )
    return [(row[0], row[1], row[2]) for row in result.all()]


async def run_status_sync_batch(settings: Settings) -> tuple[int, int]:
    """Process one retry/backfill batch; return (successes, candidates)."""
    if not is_bothelp_status_sync_configured(settings):
        return 0, 0

    client = get_bothelp_client()
    synced = 0
    async with AsyncSessionFactory() as db:
        candidates = await _load_sync_candidates(
            db,
            settings.BOTHELP_STATUS_SYNC_BATCH_SIZE,
            settings.BOTHELP_STATUS_REFRESH_SECONDS,
        )
        for candidate, _club, _desired in candidates:
            user, club = await _load_user_and_club(db, candidate.telegram_user_id)
            if user is None:
                continue
            desired_status = subscription_status_for_entitlement(club)
            if await _write_status(db, settings, client, user, desired_status):
                synced += 1
            # Preserve each result if the process stops midway through a batch.
            await db.commit()

    if synced:
        logger.info("bothelp_status_sync_batch_complete synced=%d", synced)
    return synced, len(candidates)


async def status_sync_loop(settings: Settings) -> None:
    """Continuously reconcile BotHelp fields with the database."""
    while True:
        try:
            _synced, candidate_count = await run_status_sync_batch(settings)
        except Exception:
            logger.exception("bothelp_status_sync_job_error")
            candidate_count = 0

        try:
            changed = await refresh_directory_mappings(settings, get_bothelp_client())
            if changed:
                candidate_count = settings.BOTHELP_STATUS_SYNC_BATCH_SIZE
        except Exception:
            logger.exception("bothelp_mapping_sync_job_error")

        interval = settings.BOTHELP_STATUS_SYNC_INTERVAL_SECONDS
        if candidate_count >= settings.BOTHELP_STATUS_SYNC_BATCH_SIZE:
            interval = settings.BOTHELP_STATUS_SYNC_BACKLOG_INTERVAL_SECONDS
        await asyncio.sleep(interval)
