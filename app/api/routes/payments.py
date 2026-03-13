"""
Payment endpoints called by BotHelp.

POST /payments/create  — generate a Lava payment link for a user + product
POST /payments/check   — check if user paid for product, return invite link
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_entitlement_service, require_admin_token
from app.core.config import Settings, get_settings
from app.db.repo import PendingInvoiceRepo
from app.db.session import get_db
from app.services.entitlements import (
    CLUB_PRODUCT_KEY,
    MENU_PRODUCT_KEY,
    EntitlementService,
)
from app.services.lava_api import LavaAPIError, create_invoice

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/payments",
    tags=["payments"],
    dependencies=[Depends(require_admin_token)],
)

# ── Plan → offer key mapping ─────────────────────────────────────────────────

_PLAN_TO_CONFIG_ATTR: dict[str, str] = {
    "1w": "LAVA_OFFER_CLUB_1W",
    "1m": "LAVA_OFFER_CLUB_1M",
    "3m": "LAVA_OFFER_CLUB_3M",
    "6m": "LAVA_OFFER_CLUB_6M",
    "12m": "LAVA_OFFER_CLUB_12M",
}
TRIAL_PLAN = "1w"

_PLAN_ALIASES: dict[str, str] = {
    # canonical
    "1w": "1w",
    "1m": "1m",
    "3m": "3m",
    "6m": "6m",
    "12m": "12m",
    # numeric + suffix
    "1н": "1w",
    "1нед": "1w",
    "1": "1m",
    "3": "3m",
    "6": "6m",
    "12": "12m",
    # cyrillic variants often used in BotHelp button payloads
    "1м": "1m",
    "3м": "3m",
    "6м": "6m",
    "12м": "12m",
    "1мес": "1m",
    "3мес": "3m",
    "6мес": "6m",
    "12мес": "12m",
}


def normalize_plan(raw_plan: str) -> str:
    token = str(raw_plan).strip().lower().replace(" ", "")
    normalized = _PLAN_ALIASES.get(token)
    if normalized is None:
        raise ValueError(
            f"Unknown plan: {raw_plan}. Valid canonical values: {list(_PLAN_TO_CONFIG_ATTR.keys())}"
        )
    return normalized


def normalize_product(raw_product: str | None) -> str:
    token = str(raw_product or CLUB_PRODUCT_KEY).strip().lower()
    if token not in (CLUB_PRODUCT_KEY, MENU_PRODUCT_KEY):
        raise ValueError(
            f"Unknown product: {raw_product}. Valid: {[CLUB_PRODUCT_KEY, MENU_PRODUCT_KEY]}"
        )
    return token


# ── Create payment ───────────────────────────────────────────────────────────


VALID_CURRENCIES = {"RUB", "USD", "EUR"}
VALID_PAYMENT_METHODS = {"SBP", "CARD"}


class CreatePaymentRequest(BaseModel):
    telegram_user_id: int
    plan: str | None = None
    product: str = CLUB_PRODUCT_KEY
    payment_method: str | None = None  # "SBP" / "CARD"
    currency: str = "RUB"  # "RUB" / "USD" / "EUR"

    @field_validator("telegram_user_id", mode="before")
    @classmethod
    def coerce_telegram_id(cls, v):  # noqa: N805
        if isinstance(v, str) and not v.isdigit():
            raise ValueError(f"telegram_user_id must be a number, got '{v}'")
        return int(v)


class CreatePaymentResponse(BaseModel):
    ok: bool = True
    error_code: str | None = None
    detail: str | None = None
    payment_url: str | None = None
    payment_url_path: str | None = None
    invoice_id: str | None = None


@router.post("/create", response_model=CreatePaymentResponse)
async def create_payment(
    body: CreatePaymentRequest,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> CreatePaymentResponse:
    """
    Generate a Lava.top payment link.

    BotHelp calls this when the user taps a product/plan button.
    Returns a payment_url that BotHelp sends to the user.
    """
    try:
        product = normalize_product(body.product)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    plan: str
    offer_id: str

    if product == CLUB_PRODUCT_KEY:
        if body.plan is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parameter 'plan' is required for product 'club'.",
            )
        try:
            plan = normalize_plan(body.plan)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        # Resolve plan → offer_id
        config_attr = _PLAN_TO_CONFIG_ATTR[plan]
        offer_id = getattr(settings, config_attr)
        if not offer_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Plan {plan} is not configured (empty offer ID).",
            )
    else:
        plan = MENU_PRODUCT_KEY
        offer_id = settings.LAVA_OFFER_MENU
        if not offer_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Product 'menu' is not configured (empty offer ID).",
            )

    logger.info(
        "create_payment_request telegram_id=%s product=%s plan=%s",
        body.telegram_user_id,
        product,
        plan,
    )

    repo = PendingInvoiceRepo(db)
    if product == CLUB_PRODUCT_KEY and plan == TRIAL_PLAN:
        if await repo.has_paid_plan(body.telegram_user_id, TRIAL_PLAN):
            logger.info(
                "trial_already_used telegram_id=%d",
                body.telegram_user_id,
            )
            return CreatePaymentResponse(
                ok=False,
                error_code="trial_already_used",
                detail="Trial plan '1w' can be purchased only once.",
            )

    # Generate a deterministic email for this user (Lava requires an email).
    email = f"tg_{body.telegram_user_id}@{settings.LAVA_BUYER_EMAIL_DOMAIN}"

    try:
        currency = body.currency.upper()
        if currency not in VALID_CURRENCIES:
            currency = "RUB"
        method = body.payment_method
        if method:
            method = method.upper()
            if method not in VALID_PAYMENT_METHODS:
                method = None
        # SBP only works with RUB
        if method == "SBP" and currency != "RUB":
            method = None

        result = await create_invoice(
            api_key=settings.LAVA_API_KEY,
            email=email,
            offer_id=offer_id,
            currency=currency,
            payment_method=method,
        )
    except LavaAPIError as exc:
        logger.error("lava_create_invoice_failed error=%s", exc.detail)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create payment. Please try again.",
        ) from exc

    # Store mapping so the webhook can resolve telegram_user_id.
    await repo.create(
        lava_invoice_id=result.invoice_id,
        telegram_user_id=body.telegram_user_id,
        offer_id=offer_id,
        plan=plan,
        payment_url=result.payment_url,
    )

    logger.info(
        "payment_created telegram_id=%d product=%s plan=%s invoice=%s",
        body.telegram_user_id,
        product,
        plan,
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
    product: str = CLUB_PRODUCT_KEY  # "club" | "menu"

    @field_validator("telegram_user_id", mode="before")
    @classmethod
    def coerce_telegram_id(cls, v):  # noqa: N805
        if isinstance(v, str) and not v.isdigit():
            raise ValueError(f"telegram_user_id must be a number, got '{v}'")
        return int(v)


class CheckPaymentResponse(BaseModel):
    paid: str  # "true" / "false" as string for BotHelp compatibility


@router.post("/check", response_model=CheckPaymentResponse)
async def check_payment(
    body: CheckPaymentRequest,
    ent_service: EntitlementService = Depends(get_entitlement_service),
) -> CheckPaymentResponse:
    """
    Check if the user has an active entitlement (i.e. payment was processed).

    BotHelp calls this when the user taps "Готово". The invite link is
    embedded directly in the BotHelp message button, not returned here.
    The access bot approves/declines join requests based on entitlements.
    """
    try:
        product = normalize_product(body.product)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    ent = await ent_service.get_for_telegram_user(body.telegram_user_id, product)

    if ent is None or ent.status.value != "active":
        return CheckPaymentResponse(paid="false")

    logger.info(
        "payment_check_ok telegram_id=%d product=%s",
        body.telegram_user_id,
        product,
    )
    return CheckPaymentResponse(paid="true")
