"""Authoritative Telegram/BotHelp identity checks and directory reconciliation."""
from __future__ import annotations

import logging
import time
from collections import defaultdict

import sqlalchemy as sa
from sqlalchemy import select

from app.core.time import utcnow
from app.db.models import Entitlement, User
from app.services.bothelp_api import BotHelpAPIError, BotHelpSubscriber

logger = logging.getLogger(__name__)


class MappingConflict(Exception):
    pass


def matches_identity(profile, telegram_id: int, channel_id: str) -> bool:
    return (
        profile is not None and profile.telegram_user_id == telegram_id
        and profile.channel_id == channel_id
    )


def unique_profiles(profiles: dict[int, BotHelpSubscriber]) -> dict[int, BotHelpSubscriber]:
    by_user = defaultdict(list)
    for profile in profiles.values():
        by_user[profile.telegram_user_id].append(profile)
    # Multiple profiles in the same channel require investigation, not guessing.
    return {uid: rows[0] for uid, rows in by_user.items() if len(rows) == 1}


async def bind_verified_subscriber(db, user: User, profile: BotHelpSubscriber) -> bool:
    await db.refresh(user, with_for_update=True)
    if profile.telegram_user_id != user.telegram_user_id:
        raise MappingConflict("telegram_identity_mismatch")
    other = await db.scalar(select(User.id).where(
        User.bothelp_subscriber_id == profile.subscriber_id, User.id != user.id,
    ))
    if other is not None:
        raise MappingConflict("subscriber_already_bound")
    changed = user.bothelp_subscriber_id != profile.subscriber_id
    user.bothelp_subscriber_id = profile.subscriber_id
    user.bothelp_mapping_verified_at = utcnow()
    if changed:
        user.bothelp_subscription_status = None
        user.bothelp_status_synced_at = None
        user.bothelp_status_sync_attempts = 0
        user.bothelp_status_sync_last_error = None
        user.bothelp_status_next_retry_at = None
        await db.execute(sa.update(Entitlement).where(
            Entitlement.user_id == user.id, Entitlement.product_key == "club",
        ).values(review_mailing_state=None, review_mailing_synced_at=None,
                 review_mailing_attempts=0, review_mailing_last_error=None))
    await db.flush()
    return changed


async def refresh_directory_mappings(settings, client, *, force=False) -> int:
    """Apply only a complete, channel-scoped directory snapshot. No access changes."""
    from app.db.session import AsyncSessionFactory

    clock = time.monotonic()
    if not force:
        if client.directory_channel == settings.BOTHELP_CHANNEL_ID and client.directory is not None:
            if clock - client.directory_refreshed_at < settings.BOTHELP_MAPPING_REFRESH_SECONDS:
                return 0
        if clock - client.directory_attempted_at < 300:
            return 0
    client.directory_attempted_at = clock
    profiles = await client.fetch_directory(settings.BOTHELP_CHANNEL_ID)
    # Fail closed on an unexpectedly empty catalog; never erase mappings on an outage.
    if not profiles:
        raise BotHelpAPIError(502, "empty_channel_directory")
    by_user = unique_profiles(profiles)
    newest_id = max(profiles)
    changed = 0
    async with AsyncSessionFactory() as db:
        rows = (await db.execute(select(User).where(sa.or_(
            User.bothelp_subscriber_id.isnot(None),
            sa.exists(select(Entitlement.id).where(
                Entitlement.user_id == User.id, Entitlement.product_key == "club",
            )),
        )).order_by(User.id).with_for_update())).scalars().all()
        invalid = [user for user in rows if user.bothelp_subscriber_id
                   and user.bothelp_subscriber_id <= newest_id
                   and not matches_identity(profiles.get(user.bothelp_subscriber_id),
                                            user.telegram_user_id, settings.BOTHELP_CHANNEL_ID)]
        # A truncated/misconfigured directory must not erase most stored identities.
        mapped_count = sum(user.bothelp_subscriber_id is not None for user in rows)
        if len(invalid) > max(50, mapped_count // 10):
            raise BotHelpAPIError(502, "directory_would_invalidate_too_many_mappings")
        # Clear invalid claims first so a correct owner can take its unique binding.
        for user in rows:
            current = profiles.get(user.bothelp_subscriber_id)
            if current is None and user.bothelp_subscriber_id and user.bothelp_subscriber_id > newest_id:
                continue  # Created after this directory snapshot, not proven stale.
            if user.bothelp_subscriber_id and not matches_identity(
                current, user.telegram_user_id, settings.BOTHELP_CHANNEL_ID,
            ):
                user.bothelp_subscriber_id = None
                user.bothelp_mapping_verified_at = None
                user.bothelp_subscription_status = None
                user.bothelp_status_synced_at = None
                user.bothelp_status_sync_last_error = "mapping_not_found_or_mismatched"
                user.bothelp_status_next_retry_at = None
                changed += 1
        await db.flush()
        for user in rows:
            profile = by_user.get(user.telegram_user_id)
            if profile and user.bothelp_subscriber_id != profile.subscriber_id:
                await bind_verified_subscriber(db, user, profile)
                changed += 1
        await db.commit()
    client.directory = profiles
    client.directory_channel = settings.BOTHELP_CHANNEL_ID
    client.directory_refreshed_at = time.monotonic()
    logger.info("bothelp_mapping_reconciled profiles=%d changed=%d", len(profiles), changed)
    return changed
