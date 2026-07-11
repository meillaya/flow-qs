"""FastAPI entry point for checkout-service.

Endpoints:
  GET  /healthz    - liveness; always 200 if the process is up
  GET  /readyz     - readiness; 200 only if all downstreams are reachable
                      (unless STRICT_READINESS=false in which case it is
                       always 200, used during early demos)
  POST /checkout   - run the checkout saga; returns CheckoutResponse

Trace propagation:
  * The manual span around the full flow is set up inside CheckoutOrchestrator
  * The active context is propagated into the spawned asyncio tasks via
    FastAPI's middleware (the manual span is started on the same loop)
  * We expose the trace_id in the response body AND in the X-Trace-Id header
    so curl + Tempo verification is straightforward.
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.trace import format_trace_id

from .clients import (
    GrpcInventoryClient,
    GrpcPaymentClient,
    HttpxCartClient,
    HttpxOrderClient,
)
from .config import settings
from .models import CheckoutRequest, CheckoutResponse, CheckoutStatus
from .orchestrator import CheckoutError, CheckoutOrchestrator
from .otel import init_tracing

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Best-effort OTel init. Never fail boot on tracing problems.
    try:
        init_tracing()
    except Exception:  # pragma: no cover
        logger.exception("otel init failed; continuing without remote export")
    try:
        yield
    finally:
        await _cart.aclose()
        await _inventory.aclose()
        await _payment.aclose()
        await _order.aclose()


app = FastAPI(
    title="checkout-service",
    version="0.1.0",
    description="Saga-style checkout orchestrator with rollback and failure injection.",
    lifespan=lifespan,
)

# Singleton clients. They are closed on shutdown so gRPC channels don't leak.
_cart = HttpxCartClient()
_inventory = GrpcInventoryClient()
_payment = GrpcPaymentClient()
_order = HttpxOrderClient()
_orchestrator = CheckoutOrchestrator(
    cart=_cart, inventory=_inventory, payment=_payment, order=_order
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> JSONResponse:
    if not settings.strict_readiness:
        # Grace mode: ship anyway, downstream discovery is somebody else's problem.
        return JSONResponse({"status": "ok", "strict": False})
    checks: dict[str, bool] = {}
    checks["cart"] = await _cart.health()
    checks["inventory"] = await _inventory.health()
    checks["payment"] = await _payment.health()
    checks["order"] = await _order.health()
    healthy = all(checks.values())
    payload = {"status": "ok" if healthy else "degraded", "checks": checks}
    return JSONResponse(payload, status_code=200 if healthy else 503)


@app.post("/checkout", response_model=CheckoutResponse)
async def checkout(req: CheckoutRequest, request: Request) -> JSONResponse:
    try:
        outcome = await _orchestrator.checkout(req)
    except CheckoutError as exc:
        span = trace.get_current_span()
        trace_id = format_trace_id(span.get_span_context().trace_id) if span else None
        body = CheckoutResponse(
            status=CheckoutStatus.FAILED,
            total_cents=0,
            error=exc.message,
            trace_id=trace_id,
        )
        return JSONResponse(
            body.model_dump(),
            status_code=exc.http_status,
            headers={"X-Trace-Id": trace_id} if trace_id else {},
        )
    headers = {"X-Trace-Id": outcome.trace_id} if outcome.trace_id else {}
    return JSONResponse(outcome.response.model_dump(), headers=headers)


def main() -> None:  # pragma: no cover - exercised only in container
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.http_host,
        port=settings.http_port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
