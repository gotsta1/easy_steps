from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.api.routes import payments as payments_route
from app.db.models import EntitlementStatus


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        LAVA_OFFER_CLUB_1W="offer_1w",
        LAVA_OFFER_CLUB_1M="offer_1m",
        LAVA_OFFER_CLUB_3M="offer_3m",
        LAVA_OFFER_CLUB_6M="offer_6m",
        LAVA_OFFER_CLUB_12M="offer_12m",
        LAVA_OFFER_MENU="offer_menu",
        LAVA_RETENTION_PROMO_CODE="-50",
        LAVA_BUYER_EMAIL_DOMAIN="easysteps.app",
        LAVA_API_KEY="lava_api_key",
        KICK_GRACE_SECONDS=86400,
    )


def test_create_payment_trial_already_used_returns_200_payload(monkeypatch) -> None:
    class FakeRepo:
        def __init__(self, _db) -> None:
            pass

        async def has_paid_plan(self, telegram_user_id: int, plan: str) -> bool:
            assert telegram_user_id == 7838784017
            assert plan == "1w"
            return True

        async def create(self, **kwargs) -> None:
            raise AssertionError("create() must not be called when trial is already used")

    async def fake_create_invoice(**kwargs):
        raise AssertionError("create_invoice() must not be called when trial is already used")

    monkeypatch.setattr(payments_route, "PendingInvoiceRepo", FakeRepo)
    monkeypatch.setattr(payments_route, "create_invoice", fake_create_invoice)

    body = payments_route.CreatePaymentRequest(
        telegram_user_id=7838784017,
        product="club",
        plan="1w",
    )
    response = asyncio.run(
        payments_route.create_payment(
            body=body,
            settings=_settings(),
            db=object(),
        )
    )

    assert response.ok is False
    assert response.error_code == "trial_already_used"
    assert response.payment_url is None
    assert response.payment_url_path is None
    assert response.invoice_id is None


def test_create_payment_trial_success_returns_payment_link(monkeypatch) -> None:
    class FakeRepo:
        def __init__(self, _db) -> None:
            self.created_kwargs = None

        async def has_paid_plan(self, telegram_user_id: int, plan: str) -> bool:
            assert telegram_user_id == 123456789
            assert plan == "1w"
            return False

        async def create(self, **kwargs) -> None:
            self.created_kwargs = kwargs

    class FakeInvoiceResult:
        invoice_id = "inv_123"
        payment_url = "https://app.lava.top/products/abc/offer_1w?foo=bar"
        status = "new"
        amount = 329.0
        currency = "RUB"

    async def fake_create_invoice(**kwargs):
        assert kwargs["offer_id"] == "offer_1w"
        return FakeInvoiceResult()

    monkeypatch.setattr(payments_route, "PendingInvoiceRepo", FakeRepo)
    monkeypatch.setattr(payments_route, "create_invoice", fake_create_invoice)

    body = payments_route.CreatePaymentRequest(
        telegram_user_id=123456789,
        product="club",
        plan="1w",
    )
    response = asyncio.run(
        payments_route.create_payment(
            body=body,
            settings=_settings(),
            db=object(),
        )
    )

    assert response.ok is True
    assert response.error_code is None
    assert response.invoice_id == "inv_123"
    assert response.payment_url is not None
    assert response.payment_url_path == "products/abc/offer_1w?foo=bar"


def test_retention_payment_uses_month_offer_and_server_promo(monkeypatch) -> None:
    created: dict = {}

    class FakePendingRepo:
        def __init__(self, _db) -> None:
            pass

        async def create(self, **kwargs) -> None:
            created.update(kwargs)

    class FakeEntitlementRepo:
        def __init__(self, _db) -> None:
            pass

        async def get_by_telegram_and_product(self, telegram_user_id, product_key):
            assert telegram_user_id == 123456789
            assert product_key == "club"
            now = datetime.now(timezone.utc)
            return SimpleNamespace(
                status=EntitlementStatus.inactive,
                active_until=None,
                kicked_at=now - timedelta(days=8),
                retention_message_sent_at=now - timedelta(days=1),
                retention_offers=0,
            )

    class FakeInvoiceResult:
        invoice_id = "retention_inv"
        payment_url = "https://app.lava.top/products/abc/offer_1m"
        status = "new"
        amount = 695.0
        currency = "RUB"

    async def fake_create_invoice(**kwargs):
        assert kwargs["offer_id"] == "offer_1m"
        assert kwargs["promo_code"] == "-50"
        return FakeInvoiceResult()

    monkeypatch.setattr(payments_route, "PendingInvoiceRepo", FakePendingRepo)
    monkeypatch.setattr(payments_route, "EntitlementRepo", FakeEntitlementRepo)
    monkeypatch.setattr(payments_route, "create_invoice", fake_create_invoice)

    response = asyncio.run(
        payments_route.create_payment(
            body=payments_route.CreatePaymentRequest(
                telegram_user_id=123456789,
                product="club",
                plan="retention_1m",
            ),
            settings=_settings(),
            db=object(),
        )
    )

    assert response.ok is True
    assert response.amount_display == "695₽"
    assert created["plan"] == "retention_1m"


def test_retention_payment_is_rejected_after_redemption(monkeypatch) -> None:
    class FakePendingRepo:
        def __init__(self, _db) -> None:
            pass

    class FakeEntitlementRepo:
        def __init__(self, _db) -> None:
            pass

        async def get_by_telegram_and_product(self, _telegram_user_id, _product_key):
            now = datetime.now(timezone.utc)
            return SimpleNamespace(
                status=EntitlementStatus.inactive,
                active_until=None,
                kicked_at=now - timedelta(days=8),
                retention_message_sent_at=now - timedelta(days=1),
                retention_offers=1,
            )

    async def fake_create_invoice(**_kwargs):
        raise AssertionError("redeemed retention offer must not create an invoice")

    monkeypatch.setattr(payments_route, "PendingInvoiceRepo", FakePendingRepo)
    monkeypatch.setattr(payments_route, "EntitlementRepo", FakeEntitlementRepo)
    monkeypatch.setattr(payments_route, "create_invoice", fake_create_invoice)

    response = asyncio.run(
        payments_route.create_payment(
            body=payments_route.CreatePaymentRequest(
                telegram_user_id=123456789,
                product="club",
                plan="retention_1m",
            ),
            settings=_settings(),
            db=object(),
        )
    )

    assert response.ok is False
    assert response.error_code == "retention_offer_unavailable"
