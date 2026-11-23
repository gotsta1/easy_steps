from __future__ import annotations

import httpx
import pytest

from app.services.lava_api import LavaAPIError, get_offer_price_rub, money_amount, paid_amount_rub


def product(offer="offer-month", amount=1390):
    return {"type": "PRODUCT", "data": {"offers": [{"id": offer, "prices": [
        {"currency": "USD", "amount": 17.1}, {"currency": "RUB", "amount": amount},
    ]}]}}


@pytest.mark.parametrize("value", [None, True, False, "", "wrong", -1, "NaN", "Infinity", "1e9999"])
def test_invalid_money_is_never_replaced_with_zero(value):
    with pytest.raises(ValueError):
        money_amount(value)


@pytest.mark.parametrize("value,expected", [(0, 0), (1390, 1390), ("695.50", 695.5)])
def test_valid_money_including_free_discount(value, expected):
    assert money_amount(value) == expected


def test_foreign_payment_does_not_claim_to_be_rubles():
    assert paid_amount_rub({"amount": "16.96", "currency": "USD"}) is None
    assert paid_amount_rub({"amount": "1390", "currency": "RUB"}) == 1390
    assert paid_amount_rub({"amount": "bad", "currency": "RUB"}) is None


async def test_catalog_reads_hidden_offer_and_never_caches_price_changes():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v2/products"
        assert request.url.params["feedVisibility"] == "ALL"
        assert request.url.params["showAllSubscriptionPeriods"] == "true"
        return httpx.Response(200, json={"items": [product(amount=1390 if len(calls)==1 else 1490)]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await get_offer_price_rub("key", "offer-month", http_client=client) == 1390
        assert await get_offer_price_rub("key", "offer-month", http_client=client) == 1490


async def test_catalog_resolves_exact_hidden_discount_offer():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, json={"items": [product(), product("discount", 695)]},
    ))) as client:
        assert await get_offer_price_rub("key", "discount", http_client=client) == 695


async def test_catalog_accepts_actual_direct_product_objects_from_live_api():
    live_items = [product()["data"], product("discount", 695)["data"]]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, json={"items": live_items},
    ))) as client:
        assert await get_offer_price_rub("key", "offer-month", http_client=client) == 1390
        assert await get_offer_price_rub("key", "discount", http_client=client) == 695


async def test_catalog_paginates_preserving_hidden_product_parameters():
    def handler(request):
        if "beforeCreatedAt" not in request.url.params:
            return httpx.Response(200, json={"items": [product("other")], "nextPage":
                "https://gate.lava.top/api/v2/products?beforeCreatedAt=2026-01-01"})
        assert request.url.params["feedVisibility"] == "ALL"
        return httpx.Response(200, json={"items": [product()]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await get_offer_price_rub("key", "offer-month", http_client=client) == 1390


@pytest.mark.parametrize("body", [
    {"items": []}, {"items": [product(amount=None)]}, {"items": [product(amount=-1)]},
    {"items": [product(), product()]}, {"items": "bad"}, [],
    {"items": [], "nextPage": "https://evil.example/api/v2/products?beforeCreatedAt=x"},
])
async def test_bad_catalog_cannot_supply_a_guess_or_send_key_elsewhere(body):
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.url.host == "gate.lava.top"
        return httpx.Response(200, json=body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LavaAPIError):
            await get_offer_price_rub("key", "offer-month", http_client=client)
    assert len(calls)==1


async def test_non_advancing_catalog_is_bounded():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, json={"items": [], "nextPage": "/api/v2/products?beforeCreatedAt=same"},
    ))) as client:
        with pytest.raises(LavaAPIError, match="non_advancing"):
            await get_offer_price_rub("key", "offer-month", http_client=client)
