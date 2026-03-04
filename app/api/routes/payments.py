"""
Payment endpoints called by BotHelp.

POST /payments/create  — generate a Lava payment link for a user + plan
POST /payments/check   — check if user paid, return invite link if yes
"""
from __future__ import annotations

import logging

from aiogram import Bot
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bot, get_entitlement_service, require_admin_token
from app.core.config import Settings, get_settings
from app.db.repo import PendingInvoiceRepo
from app.db.session import get_db
from app.services.entitlements import CLUB_PRODUCT_KEY, EntitlementService
from app.services.lava_api import LavaAPIError, create_invoice
from app.services.telegram_access import TelegramAccessService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/payments",
    tags=["payments"],
    dependencies=[Depends(require_admin_token)],
)

# ── Plan → offer key mapping ─────────────────────────────────────────────────

_PLAN_TO_CONFIG_ATTR: dict[str, str] = {
    "1m": "LAVA_OFFER_CLUB_1M",
    "3m": "LAVA_OFFER_CLUB_3M",
    "6m": "LAVA_OFFER_CLUB_6M",
    "12m": "LAVA_OFFER_CLUB_12M",
}


# ── Create payment ───────────────────────────────────────────────────────────


class CreatePaymentRequest(BaseModel):
    telegram_user_id: int
    plan: str  # "1m", "3m", "6m", "12m"

    @field_validator("telegram_user_id", mode="before")
    @classmethod
    def coerce_telegram_id(cls, v):  # noqa: N805
        if isinstance(v, str) and not v.isdigit():
            raise ValueError(f"telegram_user_id must be a number, got '{v}'")
        return int(v)


class CreatePaymentResponse(BaseModel):
    payment_url: str
    payment_url_path: str
    invoice_id: str


@router.post("/create", response_model=CreatePaymentResponse)
async def create_payment(
    body: CreatePaymentRequest,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> CreatePaymentResponse:
    """
    Generate a Lava.top payment link.

    BotHelp calls this when the user taps a plan button.
    Returns a payment_url that BotHelp sends to the user.
    """
    logger.info("create_payment_request telegram_id=%s plan=%s", body.telegram_user_id, body.plan)
    # Resolve plan → offer_id
    config_attr = _PLAN_TO_CONFIG_ATTR.get(body.plan)
    if not config_attr:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown plan: {body.plan}. Valid: {list(_PLAN_TO_CONFIG_ATTR.keys())}",
        )

    offer_id = getattr(settings, config_attr)
    if not offer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Plan {body.plan} is not configured (empty offer ID).",
        )

    # Generate a deterministic email for this user (Lava requires an email).
    email = f"tg_{body.telegram_user_id}@{settings.LAVA_BUYER_EMAIL_DOMAIN}"

    try:
        result = await create_invoice(
            api_key=settings.LAVA_API_KEY,
            email=email,
            offer_id=offer_id,
        )
    except LavaAPIError as exc:
        logger.error("lava_create_invoice_failed error=%s", exc.detail)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create payment. Please try again.",
        ) from exc

    # Store mapping so the webhook can resolve telegram_user_id.
    repo = PendingInvoiceRepo(db)
    await repo.create(
        lava_invoice_id=result.invoice_id,
        telegram_user_id=body.telegram_user_id,
        offer_id=offer_id,
        plan=body.plan,
        payment_url=result.payment_url,
    )

    logger.info(
        "payment_created telegram_id=%d plan=%s invoice=%s",
        body.telegram_user_id,
        body.plan,
        result.invoice_id,
    )
    from urllib.parse import urlparse

    parsed = urlparse(result.payment_url)
    path_and_query = parsed.path.lstrip("/")
    if parsed.query:
        path_and_query += "?" + parsed.query
    return CreatePaymentResponse(
        payment_url=result.payment_url,
        payment_url_path=path_and_query,
        invoice_id=result.invoice_id,
    )


# ── Check payment & get invite ───────────────────────────────────────────────


class CheckPaymentRequest(BaseModel):
    telegram_user_id: int

    @field_validator("telegram_user_id", mode="before")
    @classmethod
    def coerce_telegram_id(cls, v):  # noqa: N805
        if isinstance(v, str) and not v.isdigit():
            raise ValueError(f"telegram_user_id must be a number, got '{v}'")
        return int(v)


class CheckPaymentResponse(BaseModel):
    paid: bool
    invite_link: str | None = None
    expires_at: str | None = None


@router.post("/check", response_model=CheckPaymentResponse)
async def check_payment(
    body: CheckPaymentRequest,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
    ent_service: EntitlementService = Depends(get_entitlement_service),
    bot: Bot = Depends(get_bot),
) -> CheckPaymentResponse:
    """
    Check if the user has an active entitlement (i.e. payment was processed).

    If yes, generate an invite link to the channel.
    BotHelp calls this when the user taps "Готово".
    """
    ent = await ent_service.get_for_telegram_user(
        body.telegram_user_id, CLUB_PRODUCT_KEY
    )

    if ent is None or ent.status.value != "active":
        return CheckPaymentResponse(paid=False)

    # Generate invite link
    tg_svc = TelegramAccessService(bot, settings.TG_CHANNEL_ID)
    invite_link, expire_ts = await tg_svc.create_invite_link(
        body.telegram_user_id, settings.INVITE_TTL_SECONDS
    )

    # Open join window so the access bot approves the request
    await ent_service.open_join_window(body.telegram_user_id, CLUB_PRODUCT_KEY)

    from datetime import datetime, timezone

    expires_at = datetime.fromtimestamp(expire_ts, tz=timezone.utc).isoformat()

    logger.info(
        "payment_check_ok telegram_id=%d invite_link=%s",
        body.telegram_user_id,
        invite_link[:40],
    )
    return CheckPaymentResponse(
        paid=True,
        invite_link=invite_link,
        expires_at=expires_at,
    )
