from __future__ import annotations

from aiogram import Bot
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.services.entitlements import EntitlementService


# ── Bot dependency ────────────────────────────────────────────────────────────

def get_bot(request: Request) -> Bot:
    """Return the singleton Bot stored in app state."""
    return request.app.state.bot


# ── Database / service dependencies ──────────────────────────────────────────

async def get_entitlement_service(
    db: AsyncSession = Depends(get_db),
) -> EntitlementService:
    return EntitlementService(db)


# ── Auth dependencies ─────────────────────────────────────────────────────────

async def require_admin_token(
    x_admin_token: str = Header(..., alias="X-Admin-Token"),
    settings: Settings = Depends(get_settings),
) -> None:
    """FastAPI dependency that validates the X-Admin-Token header."""
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing admin token.",
        )
