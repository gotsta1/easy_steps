"""Reconcile post-subscription review mailing membership with club access."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.time import utcnow
from app.db.models import Entitlement, EntitlementStatus, User
from app.db.repo import EntitlementRepo
from app.db.session import AsyncSessionFactory
from app.services.bothelp_api import BotHelpAPIError, BotHelpClient

logger = logging.getLogger(__name__)

ENROLLED = "enrolled"
STOPPED = "stopped"


def is_review_mailing_configured(settings: Settings) -> bool:
    return bool(
        settings.BOTHELP_CLIENT_ID
        and settings.BOTHELP_CLIENT_SECRET
        and settings.BOTHELP_BOT_REFERRAL
        and settings.BOTHELP_STEP_REVIEW_MAILING
        and settings.BOTHELP_STEP_REVIEW_MAILING_STOP
    )


def desired_review_mailing_state(
    entitlement: Entitlement,
    now: datetime,
    delay_hours: int,
) -> str | None:
    """Return the BotHelp state required by the current club entitlement."""
    is_active = entitlement.status == EntitlementStatus.active and (
        entitlement.active_until is None or entitlement.active_until > now
    )
    if is_active:
        return STOPPED
    if (
        entitlement.active_until is not None
        and entitlement.active_until <= now - timedelta(hours=delay_hours)
    ):
        return ENROLLED
    return None


async def _apply_review_mailing_state(
    db: AsyncSession,
    settings: Settings,
    client: BotHelpClient,
    entitlement: Entitlement,
    user: User,
    desired_state: str,
) -> bool:
    """Trigger one BotHelp action and persist its confirmed state."""
    entitlement.review_mailing_attempts = (
        entitlement.review_mailing_attempts or 0
    ) + 1
    step_referral = (
        settings.BOTHELP_STEP_REVIEW_MAILING
        if desired_state == ENROLLED
        else settings.BOTHELP_STEP_REVIEW_MAILING_STOP
    )
    try:
        await client.trigger_bot_step(
            bothelp_subscriber_id=user.bothelp_subscriber_id,
            bot_referral=settings.BOTHELP_BOT_REFERRAL,
            step_referral=step_referral,
        )
    except (BotHelpAPIError, httpx.HTTPError) as exc:
        entitlement.review_mailing_last_error = str(exc)[:1000]
        logger.warning(
            "review_mailing_sync_failed tg_id=%d bothelp_id=%d desired=%s attempt=%d",
            user.telegram_user_id,
            user.bothelp_subscriber_id,
            desired_state,
            entitlement.review_mailing_attempts,
        )
        await db.flush()
        return False

    synced_at = utcnow()
    entitlement.review_mailing_state = desired_state
    entitlement.review_mailing_synced_at = synced_at
    entitlement.review_mailing_attempts = 0
    entitlement.review_mailing_last_error = None
    if desired_state == ENROLLED:
        entitlement.review_mailing_started_at = synced_at
    await db.flush()
    logger.info(
        "review_mailing_synced tg_id=%d bothelp_id=%d state=%s",
        user.telegram_user_id,
        user.bothelp_subscriber_id,
        desired_state,
    )
    return True


async def sync_review_mailing_for_telegram_user(
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    client: BotHelpClient | None = None,
) -> bool:
    """Immediately reconcile one user after access changes."""
    if not is_review_mailing_configured(settings):
        return False

    result = await db.execute(
        select(Entitlement, User)
        .join(User, User.id == Entitlement.user_id)
        .where(
            User.telegram_user_id == telegram_user_id,
            Entitlement.product_key == "club",
        )
    )
    row = result.one_or_none()
    if row is None or not row[1].bothelp_subscriber_id:
        return False

    entitlement, user = row
    desired_state = desired_review_mailing_state(
        entitlement,
        utcnow(),
        settings.BOTHELP_REVIEW_DELAY_HOURS,
    )
    if desired_state is None or entitlement.review_mailing_state == desired_state:
        return True

    if client is None:
        client = BotHelpClient(settings.BOTHELP_CLIENT_ID, settings.BOTHELP_CLIENT_SECRET)
    return await _apply_review_mailing_state(
        db, settings, client, entitlement, user, desired_state
    )


async def run_review_mailing_batch(settings: Settings) -> tuple[int, int]:
    """Reconcile one batch; active-user removals always have priority."""
    if not is_review_mailing_configured(settings):
        return 0, 0

    now = utcnow()
    cutoff = now - timedelta(hours=settings.BOTHELP_REVIEW_DELAY_HOURS)
    client = BotHelpClient(settings.BOTHELP_CLIENT_ID, settings.BOTHELP_CLIENT_SECRET)
    synced = 0

    async with AsyncSessionFactory() as db:
        repo = EntitlementRepo(db)
        stop_candidates = await repo.get_pending_review_mailing_stops(
            now,
            settings.BOTHELP_REVIEW_BATCH_SIZE,
        )
        remaining = settings.BOTHELP_REVIEW_BATCH_SIZE - len(stop_candidates)
        enroll_candidates = []
        if remaining > 0:
            enroll_candidates = await repo.get_pending_review_mailing_enrollments(
                cutoff,
                remaining,
            )
        candidates = [
            (entitlement, user, STOPPED) for entitlement, user in stop_candidates
        ] + [
            (entitlement, user, ENROLLED) for entitlement, user in enroll_candidates
        ]

        for entitlement, user, desired_state in candidates:
            if await _apply_review_mailing_state(
                db, settings, client, entitlement, user, desired_state
            ):
                synced += 1
            await db.commit()

    if synced:
        logger.info(
            "review_mailing_batch_complete synced=%d stopped=%d enrolled=%d",
            synced,
            len(stop_candidates),
            len(enroll_candidates),
        )
    return synced, len(candidates)


async def review_mailing_loop(settings: Settings) -> None:
    """Continuously reconcile review mailing membership."""
    while True:
        try:
            _synced, candidate_count = await run_review_mailing_batch(settings)
        except Exception:
            logger.exception("review_mailing_job_error")
            candidate_count = 0

        interval = settings.BOTHELP_REVIEW_INTERVAL_SECONDS
        if candidate_count >= settings.BOTHELP_REVIEW_BATCH_SIZE:
            interval = settings.BOTHELP_REVIEW_BACKLOG_INTERVAL_SECONDS
        await asyncio.sleep(interval)
