"""
Admin API — stub endpoints for internal operations.

All routes require the X-Admin-Token header.

TODO: Implement the following endpoints:
  - GET  /admin/users                             — paginated list with entitlement status
  - GET  /admin/users/{telegram_user_id}          — single user detail
  - POST /admin/entitlements/{telegram_user_id}   — manual override (activate / cancel)
  - POST /admin/kick/{telegram_user_id}           — manual kick from channel
  - GET  /admin/events                            — recent lava_events log
  - GET  /admin/stats                             — active subscriber count, etc.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import require_admin_token

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_token)],
)


@router.get("/ping")
async def admin_ping() -> dict:
    """Health-check for admin auth — returns 200 when the token is valid."""
    return {"status": "ok"}
