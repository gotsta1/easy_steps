from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utcnow
from app.db.models import Entitlement, EntitlementStatus, LavaEvent, PendingInvoice, User

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# User repository
# ─────────────────────────────────────────────────────────────────────────────


class UserRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        result = await self._db.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, telegram_user_id: int) -> tuple[User, bool]:
        """Return (user, created). Flushes but does not commit."""
        user = await self.get_by_telegram_id(telegram_user_id)
        if user:
            return user, False
        user = User(telegram_user_id=telegram_user_id)
        self._db.add(user)
        await self._db.flush()
        logger.info("user_created telegram_user_id=%d", telegram_user_id)
        return user, True


# ─────────────────────────────────────────────────────────────────────────────
# Entitlement repository
# ─────────────────────────────────────────────────────────────────────────────


class EntitlementRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def get_by_user_and_product(
        self, user_id: int, product_key: str
    ) -> Entitlement | None:
        result = await self._db.execute(
            select(Entitlement).where(
                Entitlement.user_id == user_id,
                Entitlement.product_key == product_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_telegram_and_product(
        self, telegram_user_id: int, product_key: str
    ) -> Entitlement | None:
        result = await self._db.execute(
            select(Entitlement)
            .join(User, Entitlement.user_id == User.id)
            .where(
                User.telegram_user_id == telegram_user_id,
                Entitlement.product_key == product_key,
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        user_id: int,
        product_key: str,
        status: EntitlementStatus,
        active_until: datetime | None = None,
        allowed_to_join_until: datetime | None = None,
    ) -> Entitlement:
        """Insert or update entitlement; flushes but does not commit."""
        ent = await self.get_by_user_and_product(user_id, product_key)
        now = utcnow()
        if ent is None:
            ent = Entitlement(
                user_id=user_id,
                product_key=product_key,
                status=status,
                active_until=active_until,
                allowed_to_join_until=allowed_to_join_until,
                updated_at=now,
            )
            self._db.add(ent)
        else:
            ent.status = status
            ent.updated_at = now
            if active_until is not None:
                ent.active_until = active_until
            if allowed_to_join_until is not None:
                ent.allowed_to_join_until = allowed_to_join_until
        await self._db.flush()
        return ent

    async def get_expired_active(self, cutoff: datetime) -> list[Entitlement]:
        """
        Return entitlements that are still marked active but whose active_until
        is before `cutoff` (i.e. expired beyond any grace period).
        """
        result = await self._db.execute(
            select(Entitlement).where(
                Entitlement.status == EntitlementStatus.active,
                Entitlement.active_until.isnot(None),
                Entitlement.active_until < cutoff,
            )
        )
        return list(result.scalars().all())


# ─────────────────────────────────────────────────────────────────────────────
# Lava event repository (idempotency)
# ─────────────────────────────────────────────────────────────────────────────


class LavaEventRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def exists(self, event_id: str) -> bool:
        result = await self._db.execute(
            select(LavaEvent.id).where(LavaEvent.event_id == event_id)
        )
        return result.scalar_one_or_none() is not None

    async def create(
        self, event_id: str, event_type: str, payload: dict
    ) -> LavaEvent:
        evt = LavaEvent(
            event_id=event_id,
            event_type=event_type,
            payload_json=payload,
        )
        self._db.add(evt)
        await self._db.flush()
        return evt


# ─────────────────────────────────────────────────────────────────────────────
# Pending invoice repository
# ─────────────────────────────────────────────────────────────────────────────


class PendingInvoiceRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def create(
        self,
        lava_invoice_id: str,
        telegram_user_id: int,
        offer_id: str,
        plan: str,
        payment_url: str,
    ) -> PendingInvoice:
        inv = PendingInvoice(
            lava_invoice_id=lava_invoice_id,
            telegram_user_id=telegram_user_id,
            offer_id=offer_id,
            plan=plan,
            payment_url=payment_url,
        )
        self._db.add(inv)
        await self._db.flush()
        return inv

    async def get_by_lava_id(self, lava_invoice_id: str) -> PendingInvoice | None:
        result = await self._db.execute(
            select(PendingInvoice).where(
                PendingInvoice.lava_invoice_id == lava_invoice_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_contract_id(self, contract_id: str) -> PendingInvoice | None:
        """Alias — Lava uses 'contractId' in webhooks, same as invoice ID."""
        return await self.get_by_lava_id(contract_id)

    async def mark_paid(self, lava_invoice_id: str) -> PendingInvoice | None:
        inv = await self.get_by_lava_id(lava_invoice_id)
        if inv:
            inv.paid = True
            await self._db.flush()
        return inv
