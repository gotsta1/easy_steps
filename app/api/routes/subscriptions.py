"""Subscription status endpoint called by BotHelp."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from app.api.deps import get_entitlement_service, require_admin_token
from app.db.models import Entitlement, EntitlementStatus
from app.services.entitlements import (
    CLUB_PRODUCT_KEY,
    MENU_PRODUCT_KEY,
    EntitlementService,
)

router = APIRouter(
    prefix="/subscriptions",
    tags=["subscriptions"],
    dependencies=[Depends(require_admin_token)],
)


class SubscriptionStatusRequest(BaseModel):
    telegram_user_id: int

    @field_validator("telegram_user_id", mode="before")
    @classmethod
    def coerce_telegram_id(cls, value):  # noqa: N805
        if isinstance(value, str) and not value.isdigit():
            raise ValueError(f"telegram_user_id must be a number, got '{value}'")
        return int(value)


class SubscriptionStatusResponse(BaseModel):
    club: str
    menu: str


def _normalized_expiry(entitlement: Entitlement | None) -> datetime | None:
    if entitlement is None or entitlement.active_until is None:
        return None
    if entitlement.active_until.tzinfo is None:
        return entitlement.active_until.replace(tzinfo=timezone.utc)
    return entitlement.active_until


def build_subscription_status(
    club_entitlement: Entitlement | None,
    menu_entitlement: Entitlement | None,
    now: datetime | None = None,
) -> SubscriptionStatusResponse:
    """Return remaining club days and whether lifetime menu access is active."""
    if now is None:
        now = datetime.now(tz=timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    club_value = "0"
    club_expiry = _normalized_expiry(club_entitlement)
    if (
        club_entitlement is not None
        and club_entitlement.status == EntitlementStatus.active
        and club_expiry is None
    ):
        club_value = "Бессрочно"
    elif (
        club_entitlement is not None
        and club_entitlement.status == EntitlementStatus.active
        and club_expiry is not None
        and club_expiry > now
    ):
        club_value = str(math.ceil((club_expiry - now).total_seconds() / 86400))

    menu_expiry = _normalized_expiry(menu_entitlement)
    has_menu = (
        menu_entitlement is not None
        and menu_entitlement.status == EntitlementStatus.active
        and (menu_expiry is None or menu_expiry > now)
    )

    return SubscriptionStatusResponse(club=club_value, menu=str(has_menu))


@router.post("/status", response_model=SubscriptionStatusResponse)
async def subscription_status(
    body: SubscriptionStatusRequest,
    ent_service: EntitlementService = Depends(get_entitlement_service),
) -> SubscriptionStatusResponse:
    club_entitlement = await ent_service.get_for_telegram_user(
        body.telegram_user_id,
        CLUB_PRODUCT_KEY,
    )
    menu_entitlement = await ent_service.get_for_telegram_user(
        body.telegram_user_id,
        MENU_PRODUCT_KEY,
    )
    return build_subscription_status(club_entitlement, menu_entitlement)
