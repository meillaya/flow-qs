"""End-to-end-ish tests for the orchestrator using the fake clients.

Each test wires a fresh orchestrator to a controllable set of fakes so we can
assert on the saga bookkeeping (reservations, payments, refunds) and the
returned outcome.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.models import CheckoutRequest, CheckoutStatus
from app.orchestrator import CheckoutError, CheckoutOrchestrator

pytestmark = pytest.mark.asyncio


def _req(user_id: str = "alice") -> CheckoutRequest:
    return CheckoutRequest(user_id=user_id, payment_method="card")


async def test_happy_path_creates_order_and_pays(
    make_orchestrator: Any,
    fake_cart: Any,
    fake_inventory: Any,
    fake_payment: Any,
    fake_order: Any,
) -> None:
    fake_cart.set_cart(
        "alice",
        [
            {"sku": "SKU-001", "qty": 2, "unit_cents": 2500},
            {"sku": "SKU-002", "qty": 1, "unit_cents": 1500},
        ],
    )
    orch: CheckoutOrchestrator = make_orchestrator()
    outcome = await orch.checkout(_req())

    assert outcome.response.status == CheckoutStatus.PAID
    assert outcome.response.total_cents == 2500 * 2 + 1500
    assert outcome.response.order_id is not None
    assert len(outcome.reservations) == 2
    assert outcome.payment is not None
    assert fake_payment.charges and fake_payment.charges[0].order_id
    assert fake_order.created and fake_order.created[0]["payload"]["total_cents"] == 6500
    # No rollbacks on the happy path
    assert fake_inventory.released == []
    assert fake_payment.refunds == []


async def test_insufficient_inventory_releases_reservations_and_skips_payment(
    make_orchestrator: Any,
    fake_cart: Any,
    fake_inventory: Any,
    fake_payment: Any,
) -> None:
    fake_cart.set_cart("bob", [{"sku": "SKU-001", "qty": 1, "unit_cents": 1000}])
    fake_inventory.fail_sku = "SKU-001"
    orch: CheckoutOrchestrator = make_orchestrator()

    with pytest.raises(CheckoutError) as ei:
        await orch.checkout(_req("bob"))
    assert ei.value.code == "insufficient_inventory"
    # No reservations persisted, no charges, no releases
    assert fake_inventory.reservations == []
    assert fake_payment.charges == []


async def test_insufficient_inventory_after_partial_releases_first_n(
    make_orchestrator: Any,
    fake_cart: Any,
    fake_inventory: Any,
    fake_payment: Any,
) -> None:
    """If reservations succeed for items [0..k) and item k fails, then the
    successful reservations MUST be released and no further items attempted."""
    fake_cart.set_cart(
        "carol",
        [
            {"sku": "SKU-A", "qty": 1, "unit_cents": 100},
            {"sku": "SKU-B", "qty": 1, "unit_cents": 200},
            {"sku": "SKU-C", "qty": 1, "unit_cents": 300},
        ],
    )
    fake_inventory.fail_sku = "SKU-B"  # second one blows up
    orch: CheckoutOrchestrator = make_orchestrator()

    with pytest.raises(CheckoutError) as ei:
        await orch.checkout(_req("carol"))
    assert ei.value.code == "insufficient_inventory"
    # Only SKU-A was successfully reserved; only that should be released.
    assert len(fake_inventory.released) == 1
    assert fake_inventory.released[0].sku == "SKU-A"
    # SKU-C was never even attempted.
    assert all(r.sku != "SKU-C" for r in fake_inventory.reservations)
    # No charge, no refund
    assert fake_payment.charges == []
    assert fake_payment.refunds == []


async def test_payment_decline_releases_inventory_no_refund(
    make_orchestrator: Any,
    fake_cart: Any,
    fake_inventory: Any,
    fake_payment: Any,
) -> None:
    fake_cart.set_cart("dave", [{"sku": "SKU-001", "qty": 1, "unit_cents": 5000}])
    fake_payment.decline_amount_above = 1000  # 5000 > 1000 -> decline
    orch: CheckoutOrchestrator = make_orchestrator()

    with pytest.raises(CheckoutError) as ei:
        await orch.checkout(_req("dave"))
    assert ei.value.code == "payment_declined"
    assert ei.value.http_status == 402
    # The reservation that succeeded MUST be released
    assert len(fake_inventory.released) == 1
    # No refund because we never charged
    assert fake_payment.refunds == []


async def test_order_creation_failure_releases_inventory_and_refunds_payment(
    make_orchestrator: Any,
    fake_cart: Any,
    fake_inventory: Any,
    fake_payment: Any,
    fake_order: Any,
) -> None:
    fake_cart.set_cart("eve", [{"sku": "SKU-001", "qty": 1, "unit_cents": 700}])
    fake_order.fail = True
    orch: CheckoutOrchestrator = make_orchestrator()

    with pytest.raises(CheckoutError) as ei:
        await orch.checkout(_req("eve"))
    assert ei.value.code == "order_unavailable"
    # Both compensations must have fired
    assert len(fake_inventory.released) == 1
    assert len(fake_payment.refunds) == 1
    assert fake_payment.refunds[0] == fake_payment.charges[0].charge_id


async def test_pending_order_releases_inventory_and_refunds_payment(
    make_orchestrator: Any,
    fake_cart: Any,
    fake_inventory: Any,
    fake_payment: Any,
    fake_order: Any,
) -> None:
    fake_cart.set_cart("eve", [{"sku": "SKU-001", "qty": 1, "unit_cents": 700}])
    fake_order.status = "pending"
    orchestrator: CheckoutOrchestrator = make_orchestrator()

    with pytest.raises(CheckoutError) as captured:
        await orchestrator.checkout(_req("eve"))

    assert captured.value.code == "order_not_paid"
    assert captured.value.http_status == 503
    assert len(fake_inventory.released) == 1
    assert fake_payment.refunds == [fake_payment.charges[0].charge_id]


async def test_empty_cart_short_circuits(
    make_orchestrator: Any,
    fake_cart: Any,
    fake_inventory: Any,
    fake_payment: Any,
) -> None:
    fake_cart.set_cart("frank", [])  # empty
    orch: CheckoutOrchestrator = make_orchestrator()

    with pytest.raises(CheckoutError) as ei:
        await orch.checkout(_req("frank"))
    assert ei.value.code == "cart_empty"
    assert ei.value.http_status == 400
    assert fake_inventory.reservations == []
    assert fake_payment.charges == []


async def test_failure_injection_raises_injected_failure(
    make_orchestrator: Any,
) -> None:
    orch: CheckoutOrchestrator = make_orchestrator(inject=lambda: True)
    with pytest.raises(CheckoutError) as ei:
        await orch.checkout(_req("grace"))
    assert ei.value.code == "injected_failure"
    assert ei.value.http_status == 503


async def test_inventory_transport_error_releases_partial_reservations(
    make_orchestrator: Any,
    fake_cart: Any,
    fake_inventory: Any,
) -> None:
    fake_cart.set_cart(
        "heidi",
        [
            {"sku": "SKU-A", "qty": 1, "unit_cents": 100},
            {"sku": "SKU-B", "qty": 1, "unit_cents": 200},
        ],
    )
    fake_inventory.fail_transport_sku = "SKU-B"
    orch: CheckoutOrchestrator = make_orchestrator()

    with pytest.raises(CheckoutError) as ei:
        await orch.checkout(_req("heidi"))
    assert ei.value.code == "inventory_unavailable"
    # SKU-A was reserved before the failure on SKU-B -> must be released
    assert len(fake_inventory.released) == 1
    assert fake_inventory.released[0].sku == "SKU-A"


async def test_orchestrator_creates_manual_checkout_span(
    make_orchestrator: Any,
    fake_cart: Any,
    span_exporter: Any,
) -> None:
    fake_cart.set_cart("ivan", [{"sku": "SKU-001", "qty": 1, "unit_cents": 1000}])
    orch: CheckoutOrchestrator = make_orchestrator()
    await orch.checkout(_req("ivan"))

    spans = span_exporter.get_finished_spans()
    names = [s.name for s in spans]
    assert "checkout.run" in names
    checkout_run = next(s for s in spans if s.name == "checkout.run")
    attrs = dict(checkout_run.attributes or {})
    assert attrs.get("checkout.user_id") == "ivan"
    assert attrs.get("checkout.item_count") == 1
    assert attrs.get("checkout.total_cents") == 1000
