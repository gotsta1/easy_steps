from __future__ import annotations

import base64
import logging
import secrets

from fastapi import Request

logger = logging.getLogger(__name__)


async def verify_lava_basic_auth(
    request: Request, expected_login: str, expected_password: str
) -> bool:
    """Return True if the request carries valid Basic Auth credentials matching Lava config."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        logger.warning("lava_auth_header_missing")
        return False

    if not auth_header.startswith("Basic "):
        logger.warning("lava_auth_not_basic")
        return False

    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
    except Exception:
        logger.warning("lava_auth_decode_failed")
        return False

    if ":" not in decoded:
        logger.warning("lava_auth_bad_format")
        return False

    login, password = decoded.split(":", 1)

    login_ok = secrets.compare_digest(login, expected_login)
    password_ok = secrets.compare_digest(password, expected_password)

    if not (login_ok and password_ok):
        logger.warning("lava_auth_invalid_credentials")
        return False

    return True
