"""Saga-style checkout orchestrator.

The flow:
    1. cart   (HTTP)        -> CartView
    2. inv.   (gRPC, per item) -> Reservation list
    3. pay    (gRPC)        -> PaymentCharge
    4. order  (HTTP)        -> OrderView

On any failure between step 2 and step 4, the orchestrator runs compensations
in this order:
    1. release all inventory reservations
    2. refund the payment (only if a charge was successfully captured)

This is a synchronous saga (no background orchestrator, no event bus). It is
good enough for a single-shot demo: failures are visible, compensations are
observable, traces are coherent.

Failure injection is a process-wide random failure introduced BEFORE any
downstream call - this lets chaos demonstrations show "checkout fails before
it even talks to cart".
"""
from __future__ import annotations

import logging
import random
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace

from .clients import (
    CartClient,
    InventoryClient,
    OrderClient,
    PaymentClient,
    PaymentDeclined,
)
from .config import settings
from .models import (
    CartItem,
    CheckoutRequest,
    CheckoutResponse,
    CheckoutStatus,
    PaymentCharge,
    Reservation,
)

logger = logging.getLogger(__name__)


class CheckoutError(Exception):
    """Raised by the orchestrator on any non-recoverable failure.

    The HTTP layer converts this into a CheckoutResponse with status=failed.
    """

    def __init__(self, code: str, message: str, *, http_status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass
class CheckoutOutcome:
    """Return value of CheckoutOrchestrator.checkout().

    Holds the full saga bookkeeping so the HTTP layer can render a response
    and tests can assert compensations.
    """
    response: CheckoutResponse
    reservations: list[Reservation]
    payment: PaymentCharge | None
    trace_id: str | None
    error: str | None = None
    error_code: str | None = None


# A type alias for the injection probe so tests can swap in deterministic
# behaviour without monkeypatching random.
InjectProbe = Callable[[], bool]


class CheckoutOrchestrator:
    def __init__(
        self,
        *,
        cart: CartClient,
        inventory: InventoryClient,
        payment: PaymentClient,
        order: OrderClient,
        inject_probe: InjectProbe | None = None,
    ) -> None:
        self._cart = cart
        self._inventory = inventory
        self._payment = payment
        self._order = order
        if inject_probe is not None:
            self._inject = inject_probe
        else:
            rng = random.Random(settings.failure_injection_seed)
            rate = settings.failure_injection_rate
            self._inject = lambda: rate > 0 and rng.random() < rate

    # -- public entry point -------------------------------------------------

    async def checkout(self, req: CheckoutRequest) -> CheckoutOutcome:
        tracer = trace.get_tracer("checkout-service")
        with tracer.start_as_current_span("checkout.run") as span:
            span.set_attribute("checkout.user_id", req.user_id)

            # -- failure injection gate --
            if self._inject():
                span.set_attribute("checkout.injected_failure", True)
                self._fail(
                    span,
                    code="injected_failure",
                    message="injected failure (chaos)",
                    http_status=503,
                )
                raise CheckoutError(  # pragma: no cover - unreachable, kept for type-checker
                    code="injected_failure",
                    message="injected failure (chaos)",
                    http_status=503,
                )

            cart_view = await self._fetch_cart(span, req.user_id)
            span.set_attribute("checkout.item_count", len(cart_view.items))
            span.set_attribute("checkout.total_cents", cart_view.total_cents)

            if not cart_view.items:
                self._fail(span, code="cart_empty", message="cart is empty", http_status=400)
                raise CheckoutError(
                    code="cart_empty",
                    message="cart is empty",
                    http_status=400,
                )

            reservations = await self._reserve_all(span, cart_view.items)

            try:
                payment = await self._charge(span, req, cart_view.total_cents)
            except CheckoutError:
                await self._rollback_inventory(reservations)
                raise

            try:
                order = await self._create_order(span, req, cart_view, payment)
            except CheckoutError:
                await self._rollback_inventory(reservations)
                await self._rollback_payment(payment)
                raise

            span.set_attribute("checkout.order_id", order.id)
            span.set_attribute("checkout.status", CheckoutStatus.PAID.value)
            trace_id = format(span.get_span_context().trace_id, "032x")
            return CheckoutOutcome(
                response=CheckoutResponse(
                    order_id=order.id,
                    status=CheckoutStatus.PAID,
                    total_cents=cart_view.total_cents,
                    trace_id=trace_id,
                ),
                reservations=reservations,
                payment=payment,
                trace_id=trace_id,
            )

    # -- saga steps ---------------------------------------------------------

    async def _fetch_cart(self, span: trace.Span, user_id: str) -> Any:
        with trace.get_tracer("checkout-service").start_as_current_span(
            "checkout.step.cart"
        ) as step:
            step.set_attribute("cart.user_id", user_id)
            try:
                cart = await self._cart.get_cart(user_id)
            except Exception as exc:
                span.record_exception(exc)
                raise CheckoutError(
                    "cart_unavailable",
                    f"cart lookup failed: {exc}",
                    http_status=503,
                ) from exc
            return cart

    async def _reserve_all(
        self, span: trace.Span, items: list[CartItem]
    ) -> list[Reservation]:
        reservations: list[Reservation] = []
        for item in items:
            with trace.get_tracer("checkout-service").start_as_current_span(
                "checkout.step.reserve"
            ) as step:
                step.set_attribute("inventory.sku", item.sku)
                step.set_attribute("inventory.quantity", item.qty)
                try:
                    res = await self._inventory.reserve(item.sku, item.qty)
                except Exception as exc:
                    span.record_exception(exc)
                    # transport error - release whatever we have so far
                    await self._rollback_inventory(reservations)
                    raise CheckoutError(
                        "inventory_unavailable",
                        f"inventory reserve failed for {item.sku}: {exc}",
                        http_status=503,
                    ) from exc
                if res is None:
                    # insufficient - rollback everything reserved so far
                    await self._rollback_inventory(reservations)
                    raise CheckoutError(
                        "insufficient_inventory",
                        f"insufficient inventory for sku={item.sku}",
                        http_status=409,
                    )
                reservations.append(res)
        return reservations

    async def _charge(
        self, span: trace.Span, req: CheckoutRequest, total_cents: int
    ) -> PaymentCharge:
        with trace.get_tracer("checkout-service").start_as_current_span(
            "checkout.step.charge"
        ) as step:
            step.set_attribute("payment.amount_cents", total_cents)
            order_id = f"ord_{secrets.token_hex(8)}"
            step.set_attribute("payment.order_id", order_id)
            try:
                charge = await self._payment.charge(
                    order_id=order_id,
                    amount_cents=total_cents,
                    card_token=req.payment_method,
                )
            except PaymentDeclined as exc:
                step.set_attribute("payment.status", "declined")
                raise CheckoutError(
                    "payment_declined", str(exc), http_status=402
                ) from exc
            except Exception as exc:
                span.record_exception(exc)
                step.set_attribute("payment.status", "error")
                raise CheckoutError(
                    "payment_unavailable",
                    f"payment charge failed: {exc}",
                    http_status=503,
                ) from exc
            step.set_attribute("payment.charge_id", charge.charge_id)
            return charge

    async def _create_order(
        self,
        span: trace.Span,
        req: CheckoutRequest,
        cart_view: Any,
        payment: PaymentCharge,
    ) -> Any:
        with trace.get_tracer("checkout-service").start_as_current_span(
            "checkout.step.create_order"
        ):
            payload = {
                "user_id": req.user_id,
                "items": [
                    {"sku": it.sku, "qty": it.qty}
                    for it in cart_view.items
                ],
                "total_cents": cart_view.total_cents,
                "payment_charge_id": payment.charge_id,
                "payment_order_id": payment.order_id,
            }
            try:
                order = await self._order.create_order(payload)
            except Exception as exc:
                span.record_exception(exc)
                raise CheckoutError(
                    "order_unavailable",
                    f"order create failed: {exc}",
                    http_status=503,
                ) from exc
            if order.status != "paid":
                raise CheckoutError(
                    "order_not_paid",
                    "order service did not persist a paid order",
                    http_status=503,
                )
            return order

    # -- compensations ------------------------------------------------------

    async def _rollback_inventory(self, reservations: list[Reservation]) -> None:
        if not reservations:
            return
        with trace.get_tracer("checkout-service").start_as_current_span(
            "checkout.compensate.inventory"
        ) as span:
            span.set_attribute("inventory.release_count", len(reservations))
            # Release in reverse so a SKU reserved twice doesn't release the
            # wrong reservation.
            for r in reversed(reservations):
                await self._inventory.release(r)

    async def _rollback_payment(self, payment: PaymentCharge | None) -> None:
        if payment is None:
            return
        with trace.get_tracer("checkout-service").start_as_current_span(
            "checkout.compensate.payment"
        ) as span:
            span.set_attribute("payment.charge_id", payment.charge_id)
            await self._payment.refund(payment.charge_id)

    # -- helper -------------------------------------------------------------

    def _fail(
        self,
        span: trace.Span,
        *,
        code: str,
        message: str,
        http_status: int,
    ) -> None:
        """Annotate the current span with the failure and return.

        The caller is expected to raise CheckoutError immediately after.
        Splitting the bookkeeping from the raise keeps the orchestrator's
        flow legible and lets the trace capture both attributes and the
        exception event.
        """
        span.set_attribute("checkout.status", CheckoutStatus.FAILED.value)
        span.set_attribute("checkout.error_code", code)
