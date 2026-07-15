"""Cart service with in-memory state and OTel auto-instrumentation.

State is stored in a plain Python dict keyed by ``user_id``.  Cart data is lost
when the process restarts — this is an intentional demo limitation, not a bug.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, TypedDict

from fastapi import FastAPI, Path
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.metrics import NoOpMeterProvider
from pydantic import BaseModel, Field

from app.http_metrics import HTTPMetrics


class CartItemDict(TypedDict):
    """Internal representation of a cart item."""

    sku: str
    qty: int


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------
# ``carts`` maps a user_id to a list of cart items.  This data structure is
# intentionally ephemeral; restarting the pod clears all carts.
carts: dict[str, list[CartItemDict]] = {}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class CartItem(BaseModel):
    sku: str = Field(..., min_length=1, description="Stock keeping unit identifier")
    qty: int = Field(..., gt=0, description="Quantity of the item")


class CartItemUpdate(BaseModel):
    sku: str = Field(..., min_length=1)
    qty: int = Field(..., gt=0)


class CartResponse(BaseModel):
    user_id: str
    items: list[CartItem]
    total_cents: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
UNIT_PRICE_CENTS = 100  # Demo pricing: every SKU costs 100 cents.


def _cart_for(user_id: str) -> list[CartItemDict]:
    """Return the mutable cart list for a user, creating it if absent."""
    if user_id not in carts:
        carts[user_id] = []
    return carts[user_id]


def _total_cents(items: list[CartItemDict]) -> int:
    return sum(item["qty"] * UNIT_PRICE_CENTS for item in items)


def _to_response(user_id: str, items: list[CartItemDict]) -> CartResponse:
    return CartResponse(
        user_id=user_id,
        items=[CartItem(sku=item["sku"], qty=item["qty"]) for item in items],
        total_cents=_total_cents(items),
    )


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: state is already in memory."""
    try:
        yield
    finally:
        http_metrics.shutdown()


RequestsInstrumentor().instrument()

app = FastAPI(
    title="Cart Service",
    description="In-memory shopping cart API with OTel instrumentation",
    version="0.1.0",
    lifespan=lifespan,
)
http_metrics = HTTPMetrics(os.getenv("OTEL_SERVICE_NAME", "cart-service"))
app.middleware("http")(http_metrics.middleware)
FastAPIInstrumentor.instrument_app(app, meter_provider=NoOpMeterProvider())


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ready"}


@app.get("/cart/{user_id}", response_model=CartResponse)
async def get_cart(
    user_id: Annotated[str, Path(min_length=1)],
) -> CartResponse:
    items = _cart_for(user_id)
    return _to_response(user_id, items)


@app.post("/cart/{user_id}/items", response_model=CartResponse)
async def add_item(
    item_update: CartItemUpdate,
    user_id: Annotated[str, Path(min_length=1)],
) -> CartResponse:
    items = _cart_for(user_id)
    for item in items:
        if item["sku"] == item_update.sku:
            item["qty"] += item_update.qty
            break
    else:
        items.append({"sku": item_update.sku, "qty": item_update.qty})
    return _to_response(user_id, items)


@app.delete("/cart/{user_id}/items/{sku}", response_model=CartResponse)
async def remove_item(
    user_id: Annotated[str, Path(min_length=1)],
    sku: Annotated[str, Path(min_length=1)],
) -> CartResponse:
    items = _cart_for(user_id)
    carts[user_id] = [item for item in items if item["sku"] != sku]
    return _to_response(user_id, carts[user_id])


@app.delete("/cart/{user_id}", response_model=CartResponse)
async def clear_cart(
    user_id: Annotated[str, Path(min_length=1)],
) -> CartResponse:
    carts[user_id] = []
    return _to_response(user_id, carts[user_id])
