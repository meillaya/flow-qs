"""Pydantic models for HTTP and inter-module data exchange.

Domain types are intentionally small - this service orchestrates other
services, it does not own the source of truth for cart / inventory / payment /
order state.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class CartItem(BaseModel):
    sku: str
    qty: int = Field(ge=1)
    unit_cents: int = Field(ge=0)


class CartView(BaseModel):
    user_id: str
    items: list[CartItem] = Field(default_factory=list)
    total_cents: int = 0


class Reservation(BaseModel):
    """A single inventory reservation, used to roll back on failure."""
    sku: str
    quantity: int
    reservation_id: str


class PaymentCharge(BaseModel):
    """A successful payment charge, used to roll back (refund) on failure."""
    charge_id: str
    order_id: str
    amount_cents: int
    status: Literal["approved"] = "approved"


class OrderView(BaseModel):
    id: str
    user_id: str
    status: str
    total_cents: int


class CheckoutRequest(BaseModel):
    user_id: str
    payment_method: str = "card"  # accepted but only used as `card_token`


class CheckoutStatus(str, Enum):
    PAID = "paid"
    FAILED = "failed"


class CheckoutResponse(BaseModel):
    order_id: str | None = None
    status: CheckoutStatus
    total_cents: int
    error: str | None = None
    trace_id: str | None = None
