"""Shared pytest fixtures for checkout-service.

These fixtures wire a real FastAPI app instance to fake downstream clients so
each test can decide what the downstreams return. We also set up OTel with an
in-memory span exporter so tests can assert on trace propagation without
running a Collector.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any

# Force the SDK to use an in-memory exporter BEFORE app modules are imported.
os.environ.setdefault("ENABLE_OTLP_EXPORTER", "false")
os.environ.setdefault("FAILURE_INJECTION_RATE", "0.0")
os.environ.setdefault("OTEL_SERVICE_NAME", "checkout-service-test")

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from app.main import app
from app.orchestrator import CheckoutOrchestrator

# ---------------------------------------------------------------------------
# OTel wiring (in-memory exporter, never reaches network)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def span_exporter() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "checkout-service-test"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Replace any global provider; OK for a single test process.
    trace.set_tracer_provider(provider)
    # httpx instrumentation is process-global. Hooking it here means every
    # AsyncClient (including ones built inside the test bodies) emits
    # client spans and injects `traceparent` headers automatically.
    HTTPXClientInstrumentor().instrument()
    yield exporter
    provider.shutdown()


@pytest.fixture(autouse=True)
def _reset_spans(span_exporter: InMemorySpanExporter) -> None:
    span_exporter.clear()


# ---------------------------------------------------------------------------
# FastAPI test client
# ---------------------------------------------------------------------------


@pytest.fixture
def fastapi_client() -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# Fake downstream services
# ---------------------------------------------------------------------------


class FakeCartClient:
    def __init__(self) -> None:
        self.carts: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []

    def set_cart(self, user_id: str, items: list[dict[str, Any]]) -> None:
        total = sum(it["qty"] * it["unit_cents"] for it in items)
        self.carts[user_id] = {"user_id": user_id, "items": items, "total_cents": total}

    async def get_cart(self, user_id: str) -> Any:
        self.calls.append(user_id)
        from app.models import CartView

        if user_id not in self.carts:
            raise httpx.HTTPStatusError(
                "not found",
                request=httpx.Request("GET", f"/cart/{user_id}"),
                response=httpx.Response(404),
            )
        return CartView.model_validate(self.carts[user_id])

    async def health(self) -> bool:
        return True

    async def aclose(self) -> None:  # parity with real client
        return None


class FakeInventoryClient:
    def __init__(self) -> None:
        self.reservations: list[Any] = []
        self.released: list[Any] = []
        self.fail_sku: str | None = None
        self.fail_transport_sku: str | None = None

    async def reserve(self, sku: str, quantity: int) -> Any:
        from app.models import Reservation

        if self.fail_transport_sku and self.fail_transport_sku == sku:
            raise RuntimeError("simulated transport failure")
        if self.fail_sku and self.fail_sku == sku:
            return None  # insufficient
        r = Reservation(sku=sku, quantity=quantity)
        self.reservations.append(r)
        return r

    async def release(self, reservation: Any) -> None:
        self.released.append(reservation)

    async def health(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


class FakePaymentClient:
    def __init__(self) -> None:
        self.charges: list[Any] = []
        self.refunds: list[str] = []
        self.decline_amount_above: int | None = None
        self._next_id = 1

    async def charge(self, order_id: str, amount_cents: int, card_token: str) -> Any:
        from app.clients import PaymentDeclined
        from app.models import PaymentCharge

        if self.decline_amount_above is not None and amount_cents > self.decline_amount_above:
            raise PaymentDeclined("amount over limit")
        charge = PaymentCharge(
            charge_id=f"ch-{self._next_id}",
            order_id=order_id,
            amount_cents=amount_cents,
        )
        self._next_id += 1
        self.charges.append(charge)
        return charge

    async def refund(self, charge_id: str) -> None:
        self.refunds.append(charge_id)

    async def health(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


class FakeOrderClient:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.fail: bool = False
        self._next_id = 1

    async def create_order(self, payload: dict[str, Any]) -> Any:
        if self.fail:
            raise RuntimeError("simulated order-service failure")
        from app.models import OrderView

        order = OrderView(
            id=f"o-{self._next_id}",
            user_id=payload["user_id"],
            status="pending",
            total_cents=payload["total_cents"],
        )
        self._next_id += 1
        self.created.append({"id": order.id, "payload": payload, "view": order})
        return order

    async def health(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


@pytest.fixture
def fake_cart() -> FakeCartClient:
    return FakeCartClient()


@pytest.fixture
def fake_inventory() -> FakeInventoryClient:
    return FakeInventoryClient()


@pytest.fixture
def fake_payment() -> FakePaymentClient:
    return FakePaymentClient()


@pytest.fixture
def fake_order() -> FakeOrderClient:
    return FakeOrderClient()


# A factory that builds an orchestrator with the supplied fakes and an
# optional inject probe.
@pytest.fixture
def make_orchestrator(
    fake_cart: FakeCartClient,
    fake_inventory: FakeInventoryClient,
    fake_payment: FakePaymentClient,
    fake_order: FakeOrderClient,
) -> Callable[..., CheckoutOrchestrator]:
    def _make(
        *,
        inject: Callable[[], bool] | None = None,
    ) -> CheckoutOrchestrator:
        return CheckoutOrchestrator(
            cart=fake_cart,
            inventory=fake_inventory,
            payment=fake_payment,
            order=fake_order,
            inject_probe=inject,
        )

    return _make
