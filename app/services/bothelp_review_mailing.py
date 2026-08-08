"""Durable BotHelp enrollment into the post-subscription review mailing."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from app.core.config import Settings
from app.core.time import utcnow
from app.db.repo import EntitlementRepo
from app.db.session import AsyncSessionFactory
from app.services.bothelp_api import BotHelpAPIError, BotHelpClient

logger = logging.getLogger(__name__)


def is_review_mailing_configured(settings: Settings) -> bool:
    return bool(
        settings.BOTHELP_CLIENT_ID
        and settings.BOTHELP_CLIENT_SECRET
        and settings.BOTHELP_BOT_REFERRAL
        and settings.BOTHELP_STEP_REVIEW_MAILING
    )


async def run_review_mailing_batch(settings: Settings) -> tuple[int, int]:
    """Enroll one batch; return (successful enrollments, candidates)."""
    if not is_review_mailing_configured(settings):
        return 0, 0

    cutoff = utcnow() - timedelta(hours=settings.BOTHELP_REVIEW_DELAY_HOURS)
    client = BotHelpClient(settings.BOTHELP_CLIENT_ID, settings.BOTHELP_CLIENT_SECRET)
    enrolled = 0

    async with AsyncSessionFactory() as db:
        repo = EntitlementRepo(db)
        candidates = await repo.get_pending_review_mailing(
            cutoff,
            settings.BOTHELP_REVIEW_BATCH_SIZE,
        )

        for entitlement, user in candidates:
            entitlement.review_mailing_attempts = (
                entitlement.review_mailing_attempts or 0
            ) + 1
            try:
                await client.trigger_bot_step(
                    bothelp_subscriber_id=user.bothelp_subscriber_id,
                    bot_referral=settings.BOTHELP_BOT_REFERRAL,
                    step_referral=settings.BOTHELP_STEP_REVIEW_MAILING,
                )
            except BotHelpAPIError as exc:
                entitlement.review_mailing_last_error = str(exc)[:1000]
                logger.warning(
                    "review_mailing_enroll_failed tg_id=%d bothelp_id=%d attempt=%d",
                    user.telegram_user_id,
                    user.bothelp_subscriber_id,
                    entitlement.review_mailing_attempts,
                )
            else:
                entitlement.review_mailing_started_at = utcnow()
                entitlement.review_mailing_last_error = None
                enrolled += 1
                logger.info(
                    "review_mailing_enrolled tg_id=%d bothelp_id=%d",
                    user.telegram_user_id,
                    user.bothelp_subscriber_id,
                )

            # Persist each result and release the row lock between API calls.
            await db.commit()

    if enrolled:
        logger.info("review_mailing_batch_complete enrolled=%d", enrolled)
    return enrolled, len(candidates)


async def review_mailing_loop(settings: Settings) -> None:
    """Continuously enroll eligible expired club users."""
    while True:
        try:
            _enrolled, candidate_count = await run_review_mailing_batch(settings)
        except Exception:
            logger.exception("review_mailing_job_error")
            candidate_count = 0

        interval = settings.BOTHELP_REVIEW_INTERVAL_SECONDS
        if candidate_count >= settings.BOTHELP_REVIEW_BATCH_SIZE:
            interval = settings.BOTHELP_REVIEW_BACKLOG_INTERVAL_SECONDS
        await asyncio.sleep(interval)
