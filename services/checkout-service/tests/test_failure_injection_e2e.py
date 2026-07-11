"""Failure-injection chaos test.

Runs the orchestrator 100 times with a 0.5 failure-injection probe and asserts
that the failure rate lands in the +/-15% margin the plan permits. Uses the
real `CheckoutOrchestrator` with stub downstreams so we never hit the network.
"""
from __future__ import annotations

import random
from typing import Any

import pytest

from app.models import (
    CartItem,
    CartView,
    CheckoutRequest,
    OrderView,
    PaymentCharge,
    Reservation,
)
from app.orchestrator import CheckoutOrchestrator


class _StubCart:
    async def get_cart(self, user_id: str) -> CartView:  # pragma: no cover
        return CartView(
            user_id=user_id,
            items=[CartItem(sku="S", qty=1, unit_cents=100)],
            total_cents=100,
        )

    async def health(self) -> bool:  # pragma: no cover
        return True

    async def aclose(self) -> None:  # pragma: no cover
        return None


class _StubInventory:
    async def reserve(self, sku: str, quantity: int) -> Reservation:  # pragma: no cover
        return Reservation(sku=sku, quantity=quantity)

    async def release(self, reservation: Reservation) -> None:  # pragma: no cover
        return None

    async def health(self) -> bool:  # pragma: no cover
        return True

    async def aclose(self) -> None:  # pragma: no cover
        return None


class _StubPayment:
    async def charge(  # pragma: no cover
        self, order_id: str, amount_cents: int, card_token: str
    ) -> PaymentCharge:
        return PaymentCharge(
            charge_id="c", order_id=order_id, amount_cents=amount_cents
        )

    async def refund(self, charge_id: str) -> None:  # pragma: no cover
        return None

    async def health(self) -> bool:  # pragma: no cover
        return True

    async def aclose(self) -> None:  # pragma: no cover
        return None


class _StubOrder:
    async def create_order(self, payload: dict[str, Any]) -> OrderView:  # pragma: no cover
        return OrderView(
            id="o-1",
            user_id=payload["user_id"],
            status="pending",
            total_cents=payload["total_cents"],
        )

    async def health(self) -> bool:  # pragma: no cover
        return True

    async def aclose(self) -> None:  # pragma: no cover
        return None


@pytest.mark.asyncio
async def test_failure_injection_rate_around_50_percent() -> None:
    """With a 0.5 injection probe, 100 runs should yield 35-65 failures."""
    rng = random.Random(42)
    orch = CheckoutOrchestrator(
        cart=_StubCart(),
        inventory=_StubInventory(),
        payment=_StubPayment(),
        order=_StubOrder(),
        inject_probe=lambda: rng.random() < 0.5,
    )
    n_total = 100
    n_failed = 0
    for _ in range(n_total):
        try:
            await orch.checkout(CheckoutRequest(user_id="u", payment_method="card"))
        except Exception as exc:
            if getattr(exc, "code", None) == "injected_failure":
                n_failed += 1

    assert 35 <= n_failed <= 65, (
        f"failure count {n_failed}/{n_total} outside +/-15% margin of 50"
    )
