"""Tests for the FastAPI HTTP surface.

These tests patch the singleton clients in app.main with the fakes from
conftest so the live FastAPI app can be exercised end-to-end through the
TestClient.
"""
from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def wired_main(
    monkeypatch: pytest.MonkeyPatch,
    fake_cart: Any,
    fake_inventory: Any,
    fake_payment: Any,
    fake_order: Any,
    make_orchestrator: Any,
):
    """Replace the orchestrator + clients in app.main with fakes."""
    import app.main as main

    async def fake_healthz_caller() -> bool:
        return True

    # Replace the orchestrator with one wired to fakes
    orch = make_orchestrator()
    monkeypatch.setattr(main, "_orchestrator", orch, raising=True)
    # Make readyz look healthy in all four downstream health probes
    monkeypatch.setattr(main._cart, "health", fake_healthz_caller, raising=True)
    monkeypatch.setattr(main._inventory, "health", fake_healthz_caller, raising=True)
    monkeypatch.setattr(main._payment, "health", fake_healthz_caller, raising=True)
    monkeypatch.setattr(main._order, "health", fake_healthz_caller, raising=True)
    return orch


def test_healthz_returns_ok(fastapi_client: Any) -> None:
    resp = fastapi_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_reports_all_healthy(fastapi_client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.main as main

    async def healthy() -> bool:
        return True

    for c in (main._cart, main._inventory, main._payment, main._order):
        monkeypatch.setattr(c, "health", healthy, raising=True)
    resp = fastapi_client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"cart": True, "inventory": True, "payment": True, "order": True}


def test_readyz_reports_degraded_when_a_downstream_is_down(
    fastapi_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.main as main

    async def healthy() -> bool:
        return True

    async def down() -> bool:
        return False

    for c in (main._cart, main._inventory, main._payment, main._order):
        monkeypatch.setattr(c, "health", healthy, raising=True)
    monkeypatch.setattr(main._payment, "health", down, raising=True)
    resp = fastapi_client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["payment"] is False


def test_checkout_happy_path(
    fastapi_client: Any, wired_main: Any, fake_cart: Any
) -> None:
    fake_cart.set_cart(
        "alice",
        [{"sku": "SKU-001", "qty": 1, "unit_cents": 2500}],
    )
    resp = fastapi_client.post(
        "/checkout",
        json={"user_id": "alice", "payment_method": "card"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "paid"
    assert body["total_cents"] == 2500
    assert body["order_id"] is not None
    assert body["trace_id"] is not None
    assert resp.headers.get("X-Trace-Id") == body["trace_id"]


def test_checkout_insufficient_inventory_returns_409(
    fastapi_client: Any, wired_main: Any, fake_cart: Any, fake_inventory: Any
) -> None:
    fake_cart.set_cart("bob", [{"sku": "SKU-001", "qty": 1, "unit_cents": 1000}])
    fake_inventory.fail_sku = "SKU-001"
    resp = fastapi_client.post(
        "/checkout",
        json={"user_id": "bob", "payment_method": "card"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["status"] == "failed"
    assert "insufficient" in body["error"].lower()


def test_checkout_payment_decline_returns_402(
    fastapi_client: Any, wired_main: Any, fake_cart: Any, fake_payment: Any
) -> None:
    fake_cart.set_cart("dave", [{"sku": "SKU-001", "qty": 1, "unit_cents": 999_999}])
    fake_payment.decline_amount_above = 1000
    resp = fastapi_client.post(
        "/checkout",
        json={"user_id": "dave", "payment_method": "card"},
    )
    assert resp.status_code == 402
    body = resp.json()
    assert body["status"] == "failed"


def test_checkout_empty_cart_returns_400(
    fastapi_client: Any, wired_main: Any, fake_cart: Any
) -> None:
    fake_cart.set_cart("frank", [])
    resp = fastapi_client.post(
        "/checkout",
        json={"user_id": "frank", "payment_method": "card"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "failed"
    assert "empty" in body["error"].lower()


def test_checkout_order_failure_triggers_rollback(
    fastapi_client: Any,
    wired_main: Any,
    fake_cart: Any,
    fake_inventory: Any,
    fake_payment: Any,
    fake_order: Any,
) -> None:
    fake_cart.set_cart("eve", [{"sku": "SKU-001", "qty": 1, "unit_cents": 700}])
    fake_order.fail = True
    resp = fastapi_client.post(
        "/checkout",
        json={"user_id": "eve", "payment_method": "card"},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "failed"
    # Rollback comps must have fired
    assert len(fake_inventory.released) == 1
    assert len(fake_payment.refunds) == 1
