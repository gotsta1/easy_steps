"""Synchronize active-user cleanup and recurring club retention messages."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

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

STOPPED = "stopped"
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def _has_bothelp_credentials(settings: Settings) -> bool:
    return bool(
        settings.BOTHELP_CLIENT_ID
        and settings.BOTHELP_CLIENT_SECRET
        and settings.BOTHELP_BOT_REFERRAL
    )


def is_club_lifecycle_configured(settings: Settings) -> bool:
    """Return whether at least one lifecycle action can run."""
    return _has_bothelp_credentials(settings) and bool(
        settings.BOTHELP_STEP_REVIEW_MAILING_STOP
        or (
            settings.BOTHELP_STEP_RETENTION_OFFER
            and settings.BOTHELP_STEP_RETENTION_USED
        )
    )


def is_active_club(entitlement: Entitlement, now: datetime) -> bool:
    return entitlement.status == EntitlementStatus.active and (
        entitlement.active_until is None or entitlement.active_until > now
    )


def _retention_anchor(
    entitlement: Entitlement,
    kick_grace_seconds: int = 0,
) -> datetime | None:
    retention_anchor = entitlement.kicked_at
    if retention_anchor is None and entitlement.active_until is not None:
        # Old records predate kick tracking. Keep kicked_at unknown, but use the
        # expiry plus grace period only for placing them into retention.
        retention_anchor = entitlement.active_until + timedelta(
            seconds=kick_grace_seconds
        )
    return retention_anchor


def random_retention_send_at(
    base: datetime,
    min_days: int,
    max_days: int,
    start_hour_msk: int,
    end_hour_msk: int,
    rng: random.Random | None = None,
) -> datetime:
    """Choose and return a persisted random Moscow delivery time in UTC."""
    if min_days < 1 or max_days < min_days:
        raise ValueError("Invalid retention repeat day range")
    if not 0 <= start_hour_msk < end_hour_msk <= 24:
        raise ValueError("Invalid retention delivery hour range")

    source = rng or random.SystemRandom()
    local_base = base.astimezone(MOSCOW_TZ)
    target_date = local_base.date() + timedelta(
        days=source.randint(min_days, max_days)
    )
    window_start = datetime.combine(
        target_date,
        time(hour=start_hour_msk),
        tzinfo=MOSCOW_TZ,
    )
    if end_hour_msk == 24:
        window_end = datetime.combine(
            target_date + timedelta(days=1),
            time.min,
            tzinfo=MOSCOW_TZ,
        )
    else:
        window_end = datetime.combine(
            target_date,
            time(hour=end_hour_msk),
            tzinfo=MOSCOW_TZ,
        )
    offset_seconds = source.randrange(int((window_end - window_start).total_seconds()))
    return (window_start + timedelta(seconds=offset_seconds)).astimezone(timezone.utc)


def retention_message_is_due(
    entitlement: Entitlement,
    now: datetime,
    delay_hours: int,
    kick_grace_seconds: int = 0,
) -> bool:
    """Return whether an initial or already scheduled retention message is due."""
    retention_anchor = _retention_anchor(entitlement, kick_grace_seconds)
    if (
        is_active_club(entitlement, now)
        or retention_anchor is None
        or retention_anchor > now - timedelta(hours=delay_hours)
    ):
        return False

    last_sent_at = entitlement.retention_message_sent_at
    if last_sent_at is None or last_sent_at < retention_anchor:
        return True
    return bool(
        entitlement.retention_next_message_at is not None
        and entitlement.retention_next_message_at <= now
    )


def _retention_repeat_needs_scheduling(
    entitlement: Entitlement,
    now: datetime,
    delay_hours: int,
    kick_grace_seconds: int,
) -> bool:
    """Return whether a sent retention cycle needs its first persisted schedule."""
    retention_anchor = _retention_anchor(entitlement, kick_grace_seconds)
    last_sent_at = entitlement.retention_message_sent_at
    return bool(
        not is_active_club(entitlement, now)
        and retention_anchor is not None
        and retention_anchor <= now - timedelta(hours=delay_hours)
        and last_sent_at is not None
        and last_sent_at >= retention_anchor
        and entitlement.retention_next_message_at is None
    )


def retention_offer_is_unused(entitlement: Entitlement | None) -> bool:
    """Return whether the lifetime one-time retention discount is unused."""
    return bool(
        entitlement is not None
        and (entitlement.retention_offers or 0) == 0
    )


async def _trigger_active_cleanup(
    db: AsyncSession,
    settings: Settings,
    client: BotHelpClient,
    entitlement: Entitlement,
    user: User,
) -> bool:
    """Remove an active club member from all BotHelp inactive-user mailings."""
    entitlement.review_mailing_attempts = (
        entitlement.review_mailing_attempts or 0
    ) + 1
    try:
        await client.trigger_bot_step(
            bothelp_subscriber_id=user.bothelp_subscriber_id,
            bot_referral=settings.BOTHELP_BOT_REFERRAL,
            step_referral=settings.BOTHELP_STEP_REVIEW_MAILING_STOP,
        )
    except (BotHelpAPIError, httpx.HTTPError) as exc:
        entitlement.review_mailing_last_error = str(exc)[:1000]
        logger.warning(
            "club_mailing_cleanup_failed tg_id=%d bothelp_id=%d attempt=%d",
            user.telegram_user_id,
            user.bothelp_subscriber_id,
            entitlement.review_mailing_attempts,
        )
        await db.flush()
        return False

    synced_at = utcnow()
    entitlement.review_mailing_state = STOPPED
    entitlement.review_mailing_synced_at = synced_at
    entitlement.review_mailing_attempts = 0
    entitlement.review_mailing_last_error = None
    await db.flush()
    logger.info(
        "club_mailing_cleanup_complete tg_id=%d bothelp_id=%d",
        user.telegram_user_id,
        user.bothelp_subscriber_id,
    )
    return True


async def _trigger_retention_message(
    db: AsyncSession,
    settings: Settings,
    client: BotHelpClient,
    entitlement: Entitlement,
    user: User,
) -> bool:
    """Trigger the appropriate retention message and mark confirmed delivery."""
    now = utcnow()
    if _retention_repeat_needs_scheduling(
        entitlement,
        now,
        settings.BOTHELP_RETENTION_DELAY_HOURS,
        settings.KICK_GRACE_SECONDS,
    ):
        last_sent_at = entitlement.retention_message_sent_at
        assert last_sent_at is not None
        schedule_base = (
            last_sent_at
            if last_sent_at > now - timedelta(
                days=settings.BOTHELP_RETENTION_REPEAT_MIN_DAYS
            )
            else now
        )
        entitlement.retention_next_message_at = random_retention_send_at(
            schedule_base,
            settings.BOTHELP_RETENTION_REPEAT_MIN_DAYS,
            settings.BOTHELP_RETENTION_REPEAT_MAX_DAYS,
            settings.BOTHELP_RETENTION_SEND_START_HOUR_MSK,
            settings.BOTHELP_RETENTION_SEND_END_HOUR_MSK,
        )
        await db.flush()
        logger.info(
            "retention_message_scheduled tg_id=%d next_at=%s",
            user.telegram_user_id,
            entitlement.retention_next_message_at.isoformat(),
        )
        return False

    if not retention_message_is_due(
        entitlement,
        now,
        settings.BOTHELP_RETENTION_DELAY_HOURS,
        settings.KICK_GRACE_SECONDS,
    ):
        return False

    offer_available = (entitlement.retention_offers or 0) == 0
    step_referral = (
        settings.BOTHELP_STEP_RETENTION_OFFER
        if offer_available
        else settings.BOTHELP_STEP_RETENTION_USED
    )

    try:
        await client.trigger_bot_step(
            bothelp_subscriber_id=user.bothelp_subscriber_id,
            bot_referral=settings.BOTHELP_BOT_REFERRAL,
            step_referral=step_referral,
        )
    except (BotHelpAPIError, httpx.HTTPError):
        logger.warning(
            "retention_message_send_failed tg_id=%d bothelp_id=%d offer_available=%s",
            user.telegram_user_id,
            user.bothelp_subscriber_id,
            offer_available,
            exc_info=True,
        )
        return False

    sent_at = utcnow()
    entitlement.retention_message_sent_at = sent_at
    entitlement.retention_next_message_at = random_retention_send_at(
        sent_at,
        settings.BOTHELP_RETENTION_REPEAT_MIN_DAYS,
        settings.BOTHELP_RETENTION_REPEAT_MAX_DAYS,
        settings.BOTHELP_RETENTION_SEND_START_HOUR_MSK,
        settings.BOTHELP_RETENTION_SEND_END_HOUR_MSK,
    )
    await db.flush()
    logger.info(
        "retention_message_sent tg_id=%d bothelp_id=%d offer_available=%s next_at=%s",
        user.telegram_user_id,
        user.bothelp_subscriber_id,
        offer_available,
        entitlement.retention_next_message_at.isoformat(),
    )
    return True


async def sync_active_user_mailing_cleanup(
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    client: BotHelpClient | None = None,
) -> bool:
    """Immediately remove one newly active user from inactive-user mailings."""
    if not (
        _has_bothelp_credentials(settings)
        and settings.BOTHELP_STEP_REVIEW_MAILING_STOP
    ):
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
    if not is_active_club(entitlement, utcnow()):
        return False
    if client is None:
        client = BotHelpClient(settings.BOTHELP_CLIENT_ID, settings.BOTHELP_CLIENT_SECRET)
    # Always trigger after activation because the BotHelp step may gain new
    # mailings after a user's previous synchronization.
    return await _trigger_active_cleanup(db, settings, client, entitlement, user)


async def run_club_lifecycle_batch(settings: Settings) -> tuple[int, int]:
    """Process one bounded batch; active-user cleanup has priority."""
    if not is_club_lifecycle_configured(settings):
        return 0, 0

    now = utcnow()
    first_send_cutoff = now - timedelta(hours=settings.BOTHELP_RETENTION_DELAY_HOURS)
    historical_first_send_cutoff = first_send_cutoff - timedelta(
        seconds=settings.KICK_GRACE_SECONDS
    )
    client = BotHelpClient(settings.BOTHELP_CLIENT_ID, settings.BOTHELP_CLIENT_SECRET)
    synced = 0

    async with AsyncSessionFactory() as db:
        repo = EntitlementRepo(db)
        stop_candidates: list[tuple[Entitlement, User]] = []
        if settings.BOTHELP_STEP_REVIEW_MAILING_STOP:
            stop_candidates = await repo.get_pending_review_mailing_stops(
                now,
                settings.BOTHELP_REVIEW_BATCH_SIZE,
            )

        remaining = settings.BOTHELP_REVIEW_BATCH_SIZE - len(stop_candidates)
        retention_candidates: list[tuple[Entitlement, User]] = []
        if (
            remaining > 0
            and settings.BOTHELP_STEP_RETENTION_OFFER
            and settings.BOTHELP_STEP_RETENTION_USED
        ):
            retention_candidates = await repo.get_pending_retention_messages(
                first_send_cutoff,
                historical_first_send_cutoff,
                now,
                remaining,
            )

        for entitlement, user in stop_candidates:
            if await _trigger_active_cleanup(db, settings, client, entitlement, user):
                synced += 1
            await db.commit()

        for entitlement, user in retention_candidates:
            if await _trigger_retention_message(db, settings, client, entitlement, user):
                synced += 1
            await db.commit()

    candidate_count = len(stop_candidates) + len(retention_candidates)
    if synced:
        logger.info(
            "club_lifecycle_batch_complete synced=%d stopped=%d retention_sent=%d",
            synced,
            len(stop_candidates),
            len(retention_candidates),
        )
    return synced, candidate_count


async def club_lifecycle_loop(settings: Settings) -> None:
    """Continuously synchronize club lifecycle actions with BotHelp."""
    while True:
        try:
            _synced, candidate_count = await run_club_lifecycle_batch(settings)
        except Exception:
            logger.exception("club_lifecycle_job_error")
            candidate_count = 0

        interval = settings.BOTHELP_REVIEW_INTERVAL_SECONDS
        if candidate_count >= settings.BOTHELP_REVIEW_BATCH_SIZE:
            interval = settings.BOTHELP_REVIEW_BACKLOG_INTERVAL_SECONDS
        await asyncio.sleep(interval)
