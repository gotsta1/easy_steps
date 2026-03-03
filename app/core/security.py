from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import Request

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Lava.top signature verification
#
# Lava's exact signing scheme is not fully documented at time of writing.
# The implementation below uses HMAC-SHA256 over the raw request body, keyed
# with LAVA_SECRET, comparing against the value in the "X-Signature" header.
#
# HOW TO ADJUST:
#   • Change SIGNATURE_HEADER to the actual header name Lava sends.
#   • Replace compute_hmac_sha256 body with the correct algorithm if Lava uses
#     a different scheme (e.g. SHA-1, RSA-SHA256, or a query-string digest).
#   • In production, switch the early-return status to 401 once confirmed.
# ─────────────────────────────────────────────────────────────────────────────

SIGNATURE_HEADER = "X-Signature"  # TODO: confirm with Lava docs


def compute_hmac_sha256(secret: str, raw_body: bytes) -> str:
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


async def verify_lava_signature(request: Request, secret: str) -> bool:
    """Return True if the request carries a valid Lava signature."""
    provided = request.headers.get(SIGNATURE_HEADER)
    if not provided:
        logger.warning("lava_signature_header_missing header=%s", SIGNATURE_HEADER)
        return False

    # body() caches the result; safe to call multiple times in the same request.
    raw_body = await request.body()
    expected = compute_hmac_sha256(secret, raw_body)

    valid = hmac.compare_digest(provided.lower(), expected.lower())
    if not valid:
        logger.warning(
            "lava_signature_mismatch provided_prefix=%s",
            provided[:12] + "…",
        )
    return valid
