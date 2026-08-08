"""Durable synchronization of current subscription state to BotHelp."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.time import utcnow
from app.db.models import Entitlement, EntitlementStatus, User
from app.db.session import AsyncSessionFactory
from app.services.bothelp_api import BotHelpAPIError, BotHelpClient
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
        and settings.BOTHELP_BOT_REFERRAL
        and settings.BOTHELP_STEP_SUBSCRIPTION_SYNC
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
    )
    row = result.one_or_none()
    return (row[0], row[1]) if row else (None, None)


async def _trigger_sync_step(
    db: AsyncSession,
    settings: Settings,
    client: BotHelpClient,
    user: User,
    desired_status: str,
) -> bool:
    if not user.bothelp_subscriber_id:
        return False

    user.bothelp_status_sync_attempts = (user.bothelp_status_sync_attempts or 0) + 1
    try:
        await client.trigger_bot_step(
            bothelp_subscriber_id=user.bothelp_subscriber_id,
            bot_referral=settings.BOTHELP_BOT_REFERRAL,
            step_referral=settings.BOTHELP_STEP_SUBSCRIPTION_SYNC,
        )
    except BotHelpAPIError as exc:
        user.bothelp_status_sync_last_error = str(exc)[:1000]
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
    """Synchronize one user after the business transaction has committed."""
    if not is_bothelp_status_sync_configured(settings):
        return False

    user, club = await _load_user_and_club(db, telegram_user_id)
    if user is None or not user.bothelp_subscriber_id:
        return False

    desired_status = subscription_status_for_entitlement(club)
    if user.bothelp_subscription_status == desired_status:
        return True

    if client is None:
        client = BotHelpClient(settings.BOTHELP_CLIENT_ID, settings.BOTHELP_CLIENT_SECRET)
    return await _trigger_sync_step(db, settings, client, user, desired_status)


async def _load_sync_candidates(
    db: AsyncSession,
    limit: int,
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
            User.bothelp_subscription_status.is_distinct_from(desired_status),
        )
        .order_by(User.id)
        .limit(limit)
    )
    return [(row[0], row[1], row[2]) for row in result.all()]


async def run_status_sync_batch(settings: Settings) -> int:
    """Process one retry/backfill batch and return successful sync count."""
    if not is_bothelp_status_sync_configured(settings):
        return 0

    client = BotHelpClient(settings.BOTHELP_CLIENT_ID, settings.BOTHELP_CLIENT_SECRET)
    synced = 0
    async with AsyncSessionFactory() as db:
        candidates = await _load_sync_candidates(
            db,
            settings.BOTHELP_STATUS_SYNC_BATCH_SIZE,
        )
        for user, _club, desired_status in candidates:
            if await _trigger_sync_step(db, settings, client, user, desired_status):
                synced += 1
            # Preserve each result if the process stops midway through a batch.
            await db.commit()

    if synced:
        logger.info("bothelp_status_sync_batch_complete synced=%d", synced)
    return synced


async def status_sync_loop(settings: Settings) -> None:
    """Continuously reconcile BotHelp fields with the database."""
    while True:
        try:
            await run_status_sync_batch(settings)
        except Exception:
            logger.exception("bothelp_status_sync_job_error")
        await asyncio.sleep(settings.BOTHELP_STATUS_SYNC_INTERVAL_SECONDS)
