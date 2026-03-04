from __future__ import annotations

import pytest

from app.api.routes.payments import normalize_plan, normalize_product


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1m", "1m"),
        ("3m", "3m"),
        ("6m", "6m"),
        ("12m", "12m"),
        ("1", "1m"),
        ("3", "3m"),
        ("6", "6m"),
        ("12", "12m"),
        ("3м", "3m"),
        ("6мес", "6m"),
        (" 12M ", "12m"),
        (" 3 m ", "3m"),
    ],
)
def test_normalize_plan_supported_variants(raw: str, expected: str) -> None:
    assert normalize_plan(raw) == expected


@pytest.mark.parametrize("raw", ["", "0", "2m", "month", "forever"])
def test_normalize_plan_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_plan(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("club", "club"),
        ("menu", "menu"),
        (" CLUB ", "club"),
    ],
)
def test_normalize_product_supported_variants(raw: str, expected: str) -> None:
    assert normalize_product(raw) == expected


@pytest.mark.parametrize("raw", ["", "vip", "menus"])
def test_normalize_product_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_product(raw)
