"""Trace propagation tests.

These tests verify that an active trace context is correctly propagated from
checkout-service into the downstream services it calls. We use:

  * An in-memory OTel span exporter (see conftest) to assert on the parent
    span emitted by the orchestrator.
  * A real httpx.AsyncClient with a MockTransport wrapped by OTel's
    `AsyncOpenTelemetryTransport` so the `traceparent` header is actually
    injected. (The plain instrumentor patches `AsyncHTTPTransport`, which
    does not run when a `MockTransport` is in use.)
  * An in-process grpc.aio server that records the gRPC metadata on each call
    so we can assert that the W3C `traceparent` is in there.

The point is to prove the saga is a single distributed trace, not a pile of
unrelated spans.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import grpc
import grpc.aio
import httpx
import pytest
import pytest_asyncio
from opentelemetry.instrumentation.httpx import AsyncOpenTelemetryTransport

from app.clients import GrpcInventoryClient, GrpcPaymentClient, HttpxCartClient, HttpxOrderClient
from app.models import CheckoutRequest
from app.orchestrator import CheckoutOrchestrator
from app.proto_gen import inventory_pb2, inventory_pb2_grpc, payment_pb2, payment_pb2_grpc

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Mock downstream receivers
# ---------------------------------------------------------------------------


def _traceparent_value(metadata: list[tuple[str, str]]) -> str | None:
    for k, v in metadata:
        if k == "traceparent":
            return v
    return None


class _CapturingInventoryServicer(inventory_pb2_grpc.InventoryServiceServicer):
    def __init__(self) -> None:
        self.calls: list[list[tuple[str, str]]] = []
        self._next_id = 1

    async def CheckStock(self, request, context):
        self.calls.append(list(context.invocation_metadata()))
        return inventory_pb2.StockResponse(sku=request.sku, available=10, reserved=0)

    async def ReserveStock(self, request, context):
        self.calls.append(list(context.invocation_metadata()))
        rid = f"res-{self._next_id}"
        self._next_id += 1
        return inventory_pb2.ReserveResponse(
            success=True, available=9, reserved=1, message=rid
        )

    async def ReleaseStock(self, request, context):
        self.calls.append(list(context.invocation_metadata()))
        return inventory_pb2.ReleaseResponse(success=True, available=10, reserved=0)


class _CapturingPaymentServicer(payment_pb2_grpc.PaymentServiceServicer):
    def __init__(self) -> None:
        self.calls: list[list[tuple[str, str]]] = []
        self._next_id = 1

    async def Charge(self, request, context):
        self.calls.append(list(context.invocation_metadata()))
        cid = f"ch-{self._next_id}"
        self._next_id += 1
        return payment_pb2.ChargeResponse(
            status=payment_pb2.APPROVED,
            charge_id=cid,
        )

    async def Refund(self, request, context):
        self.calls.append(list(context.invocation_metadata()))
        return payment_pb2.RefundResponse(status=payment_pb2.REFUNDED)

    async def GetStatus(self, request, context):
        self.calls.append(list(context.invocation_metadata()))
        return payment_pb2.GetStatusResponse(
            charge_id=request.charge_id, status=payment_pb2.APPROVED
        )


@pytest_asyncio.fixture
async def inventory_servicer() -> AsyncIterator[tuple[_CapturingInventoryServicer, str]]:
    servicer = _CapturingInventoryServicer()
    server = grpc.aio.server()
    inventory_pb2_grpc.add_InventoryServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        yield servicer, f"127.0.0.1:{port}"
    finally:
        await server.stop(grace=None)


@pytest_asyncio.fixture
async def payment_servicer() -> AsyncIterator[tuple[_CapturingPaymentServicer, str]]:
    servicer = _CapturingPaymentServicer()
    server = grpc.aio.server()
    payment_pb2_grpc.add_PaymentServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        yield servicer, f"127.0.0.1:{port}"
    finally:
        await server.stop(grace=None)


def _make_capture_transport(
    captured: dict[str, Any],
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.setdefault("headers", []).append(dict(request.headers))
        path = request.url.path
        if path.startswith("/cart/"):
            return httpx.Response(
                200,
                json={
                    "user_id": path.split("/")[-1],
                    "items": [{"sku": "SKU-001", "qty": 1, "unit_cents": 1000}],
                    "total_cents": 1000,
                },
            )
        if path == "/orders":
            return httpx.Response(
                201,
                json={
                    "id": "o-1",
                    "user_id": "alice",
                    "status": "pending",
                    "total_cents": 1000,
                },
            )
        if path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_traceparent_propagates_to_httpx_downstreams(
    span_exporter: Any,
) -> None:
    captured: dict[str, Any] = {}
    base_transport = _make_capture_transport(captured)
    # OTel's HTTPX instrumentor wraps AsyncHTTPTransport.handle_async_request;
    # since we're using a MockTransport, we must wrap the transport ourselves
    # so the instrumentation runs and injects the W3C `traceparent` header.
    transport = AsyncOpenTelemetryTransport(base_transport)
    async with httpx.AsyncClient(transport=transport, base_url="http://downstream") as client:
        cart = HttpxCartClient(client=client)
        order = HttpxOrderClient(client=client)
        # Cheap stand-ins for gRPC: never called, but the orchestrator accepts
        # the protocols. We use a no-op fakes here.
        orch = CheckoutOrchestrator(
            cart=cart,
            inventory=_NoopInventory(),
            payment=_NoopPayment(),
            order=order,
        )
        await orch.checkout(CheckoutRequest(user_id="alice", payment_method="card"))

    assert captured["headers"], "no downstream HTTP calls were captured"
    # At least one downstream call should carry a W3C traceparent header.
    any_traceparent = any(
        "traceparent" in h for h in captured["headers"]
    )
    assert any_traceparent, f"no traceparent in any captured headers: {captured['headers']}"

    # And the same trace_id should appear in our local spans
    spans = span_exporter.get_finished_spans()
    assert spans, "no spans captured"
    local_trace_id = next(s for s in spans if s.name == "checkout.run").context.trace_id
    for header_map in captured["headers"]:
        if "traceparent" not in header_map:
            continue
        # W3C traceparent format: "00-<trace_id>-<span_id>-<flags>"
        trace_id_hex = header_map["traceparent"].split("-")[1]
        assert int(trace_id_hex, 16) == local_trace_id, (
            f"downstream traceparent {trace_id_hex} does not match local "
            f"trace {local_trace_id:032x}"
        )


async def test_traceparent_propagates_to_grpc_downstreams(
    inventory_servicer: Any,
    payment_servicer: Any,
    span_exporter: Any,
) -> None:
    inv_servicer, inv_addr = inventory_servicer
    pay_servicer, pay_addr = payment_servicer

    inv_channel = grpc.aio.insecure_channel(inv_addr)
    pay_channel = grpc.aio.insecure_channel(pay_addr)

    try:
        # Real OTel gRPC client instrumentation runs against these clients.
        inv_client = GrpcInventoryClient(addr=inv_addr, channel=inv_channel)
        pay_client = GrpcPaymentClient(addr=pay_addr, channel=pay_channel)
        # Use httpx MockTransport for cart + order so this test focuses on gRPC.
        captured: dict[str, Any] = {}
        async with httpx.AsyncClient(transport=_make_capture_transport(captured)) as client:
            cart = HttpxCartClient(client=client)
            order = HttpxOrderClient(client=client)
            orch = CheckoutOrchestrator(
                cart=cart, inventory=inv_client, payment=pay_client, order=order
            )
            await orch.checkout(
                CheckoutRequest(user_id="alice", payment_method="card")
            )
    finally:
        await inv_channel.close()
        await pay_channel.close()

    # gRPC servicer must have seen traceparent metadata on each call.
    assert inv_servicer.calls, "inventory gRPC service was never called"
    assert pay_servicer.calls, "payment gRPC service was never called"

    inv_tp = _traceparent_value(inv_servicer.calls[0])
    pay_tp = _traceparent_value(pay_servicer.calls[0])
    assert inv_tp, f"no traceparent on inventory calls: {inv_servicer.calls[0]}"
    assert pay_tp, f"no traceparent on payment calls: {pay_servicer.calls[0]}"

    # And the trace_id must match the local checkout span
    spans = span_exporter.get_finished_spans()
    assert spans, "no spans captured"
    local_trace_id = spans[0].context.trace_id
    for tp in (inv_tp, pay_tp):
        assert int(tp.split("-")[1], 16) == local_trace_id


# ---------------------------------------------------------------------------
# Local no-op fakes for the HTTP-only propagation test (gRPC isn't needed)
# ---------------------------------------------------------------------------


class _NoopInventory:
    async def reserve(self, sku: str, quantity: int):  # pragma: no cover
        from app.models import Reservation

        return Reservation(sku=sku, quantity=quantity)

    async def release(self, reservation):  # pragma: no cover
        return None

    async def health(self) -> bool:  # pragma: no cover
        return True

    async def aclose(self) -> None:  # pragma: no cover
        return None


class _NoopPayment:
    async def charge(self, order_id: str, amount_cents: int, card_token: str):  # pragma: no cover
        from app.models import PaymentCharge

        return PaymentCharge(
            charge_id="noop", order_id=order_id, amount_cents=amount_cents
        )

    async def refund(self, charge_id: str) -> None:  # pragma: no cover
        return None

    async def health(self) -> bool:  # pragma: no cover
        return True

    async def aclose(self) -> None:  # pragma: no cover
        return None
