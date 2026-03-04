from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Bot
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_bot, get_entitlement_service, require_admin_token
from app.core.config import Settings, get_settings
from app.services.entitlements import (
    CLUB_PRODUCT_KEY,
    MENU_PRODUCT_KEY,
    EntitlementService,
)
from app.services.telegram_access import TelegramAccessService

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["invite"],
    dependencies=[Depends(require_admin_token)],
)


class InviteRequest(BaseModel):
    telegram_user_id: int


class InviteResponse(BaseModel):
    invite_link: str
    expires_at: str | None  # ISO-8601 UTC or null for permanent links


@router.post("/invites/club", response_model=InviteResponse)
async def create_club_invite(
    body: InviteRequest,
    settings: Settings = Depends(get_settings),
    ent_service: EntitlementService = Depends(get_entitlement_service),
    bot: Bot = Depends(get_bot),
) -> InviteResponse:
    """
    Generate a join-request invite link for the club channel.

    Requires an active entitlement.  Refreshes the join window so the user
    can actually use the link once they receive it.

    Authentication: ``X-Admin-Token`` header (or forwarded from BotHelp).
    """
    ent = await ent_service.get_for_telegram_user(body.telegram_user_id, CLUB_PRODUCT_KEY)

    if ent is None or ent.status.value != "active":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"No active subscription. Current status: {ent.status.value if ent else 'none'}.",
        )

    tg_svc = TelegramAccessService(bot, settings.TG_CHANNEL_ID)

    invite_link, expire_ts = await tg_svc.create_invite_link(
        body.telegram_user_id, settings.INVITE_TTL_SECONDS
    )

    # Open/refresh the join window so the access bot approves the upcoming request.
    await ent_service.open_join_window(body.telegram_user_id, CLUB_PRODUCT_KEY)

    expires_at = datetime.fromtimestamp(expire_ts, tz=timezone.utc).isoformat()
    return InviteResponse(invite_link=invite_link, expires_at=expires_at)


@router.post("/invites/menu", response_model=InviteResponse)
async def create_menu_invite(
    body: InviteRequest,
    settings: Settings = Depends(get_settings),
    ent_service: EntitlementService = Depends(get_entitlement_service),
    bot: Bot = Depends(get_bot),
) -> InviteResponse:
    """
    Generate a permanent join-request invite link for the menu channel.

    Requires an active 'menu' entitlement.
    """
    if not settings.TG_MENU_CHANNEL_ID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Menu channel is not configured (TG_MENU_CHANNEL_ID is empty).",
        )

    ent = await ent_service.get_for_telegram_user(body.telegram_user_id, MENU_PRODUCT_KEY)
    if ent is None or ent.status.value != "active":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"No active menu access. Current status: {ent.status.value if ent else 'none'}.",
        )

    tg_svc = TelegramAccessService(bot, settings.TG_MENU_CHANNEL_ID)
    invite_link, _ = await tg_svc.create_invite_link(
        body.telegram_user_id,
        invite_ttl_seconds=None,
        link_name_prefix=MENU_PRODUCT_KEY,
    )
    return InviteResponse(invite_link=invite_link, expires_at=None)
