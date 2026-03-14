from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utcnow
from app.db.models import Entitlement, EntitlementStatus
from app.db.repo import EntitlementRepo, UserRepo

logger = logging.getLogger(__name__)

# Product keys used across payments, webhook processing, and join approvals.
# Club offers (1w/1m/3m/6m/12m) share one entitlement key with varying duration.
CLUB_PRODUCT_KEY = "club"
MENU_PRODUCT_KEY = "menu"


# ─────────────────────────────────────────────────────────────────────────────
# Pure decision function — no I/O, fully unit-testable
# ─────────────────────────────────────────────────────────────────────────────


def can_approve_join(
    entitlement: Entitlement | None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """
    Decide whether to approve a Telegram channel join request.

    Parameters
    ----------
    entitlement:
        The user's entitlement record, or None if they have none.
    now:
        The reference timestamp (defaults to current UTC time).
        Providing an explicit value makes the function deterministic for tests.

    Returns
    -------
    (approved, reason)
        ``approved`` is True only when all conditions are met.
        ``reason`` is a short lowercase string describing the outcome.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    if entitlement is None:
        return False, "no_entitlement"

    if entitlement.status != EntitlementStatus.active:
        return False, f"status_{entitlement.status.value}"

    active_until = entitlement.active_until
    if active_until is not None:
        if active_until.tzinfo is None:
            active_until = active_until.replace(tzinfo=timezone.utc)
        if now > active_until:
            return False, "subscription_expired"

    return True, "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Entitlement service — orchestrates DB repos
# ─────────────────────────────────────────────────────────────────────────────


class EntitlementService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._users = UserRepo(db)
        self._entitlements = EntitlementRepo(db)

    # ── Queries ──────────────────────────────────────────────────────────────

    async def get_for_telegram_user(
        self, telegram_user_id: int, product_key: str = CLUB_PRODUCT_KEY
    ) -> Entitlement | None:
        return await self._entitlements.get_by_telegram_and_product(
            telegram_user_id, product_key
        )

    # ── Mutations ─────────────────────────────────────────────────────────────

    async def apply_payment_success(
        self,
        telegram_user_id: int,
        duration_days: int,
        product_key: str = CLUB_PRODUCT_KEY,
    ) -> Entitlement:
        """
        Activate or extend the entitlement by ``duration_days``.

        Stacking logic:
          - If the user already has an active entitlement with active_until
            in the future, the new duration is added on top of the remaining
            time: ``new_active_until = max(existing_active_until, now) + duration``.
          - If no entitlement exists or it has expired:
            ``new_active_until = now + duration``.

        Also opens the join window so the user can join the channel.
        """
        user, _ = await self._users.get_or_create(telegram_user_id)

        now = utcnow()
        existing = await self._entitlements.get_by_user_and_product(user.id, product_key)

        # Determine the base for stacking.
        if existing and existing.active_until and existing.active_until > now:
            base = existing.active_until
        else:
            base = now

        active_until = base + timedelta(days=duration_days)

        ent = await self._entitlements.upsert(
            user_id=user.id,
            product_key=product_key,
            status=EntitlementStatus.active,
            active_until=active_until,
            duration_days=duration_days,
        )
        logger.info(
            "entitlement_activated telegram_id=%d product=%s duration_days=%d "
            "active_until=%s stacked_on=%s",
            telegram_user_id,
            product_key,
            duration_days,
            active_until.isoformat(),
            "existing" if base != now else "now",
        )
        return ent

    async def apply_canceled(
        self, telegram_user_id: int, product_key: str = CLUB_PRODUCT_KEY
    ) -> Entitlement | None:
        ent = await self._entitlements.get_by_telegram_and_product(
            telegram_user_id, product_key
        )
        if ent:
            ent.status = EntitlementStatus.canceled
            ent.updated_at = utcnow()
            await self._db.flush()
            logger.info(
                "entitlement_canceled telegram_id=%d product=%s",
                telegram_user_id,
                product_key,
            )
        return ent

    async def apply_lifetime_success(
        self,
        telegram_user_id: int,
        product_key: str = MENU_PRODUCT_KEY,
    ) -> Entitlement:
        """
        Activate a lifetime entitlement.

        Lifetime means:
          - status=active
          - active_until=NULL (never expires)
          - duration_days=NULL
        """
        user, _ = await self._users.get_or_create(telegram_user_id)
        existing = await self._entitlements.get_by_user_and_product(user.id, product_key)

        if existing is None:
            ent = await self._entitlements.upsert(
                user_id=user.id,
                product_key=product_key,
                status=EntitlementStatus.active,
                active_until=None,
            )
        else:
            existing.status = EntitlementStatus.active
            existing.active_until = None
            existing.duration_days = None
            existing.expiry_notified_days = None
            existing.expiry_notified_3h_at = None
            existing.expiry_notified_10h_after_at = None
            existing.updated_at = utcnow()
            await self._db.flush()
            ent = existing

        logger.info(
            "entitlement_lifetime_activated telegram_id=%d product=%s",
            telegram_user_id,
            product_key,
        )
        return ent

    async def apply_payment_failed(
        self, telegram_user_id: int, product_key: str = CLUB_PRODUCT_KEY
    ) -> None:
        """Log a failed payment.  No entitlement changes for digital products."""
        logger.warning(
            "payment_failed telegram_id=%d product=%s",
            telegram_user_id,
            product_key,
        )
