"""gRPC client for payment-gateway.

The plan defines proto/payment.proto with three RPCs:
  * Charge         - used during checkout
  * Refund         - used during rollback
  * GetPaymentStatus - unused here, but kept for proto symmetry
"""
from __future__ import annotations

import logging
from typing import Protocol

import grpc

from ..config import settings
from ..models import PaymentCharge
from ..proto_gen import payment_pb2, payment_pb2_grpc

logger = logging.getLogger(__name__)


class PaymentDeclined(Exception):
    """Raised when the payment service responds with STATUS_DECLINED."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PaymentClient(Protocol):
    async def charge(self, order_id: str, amount_cents: int, card_token: str) -> PaymentCharge: ...
    async def refund(self, charge_id: str) -> None: ...
    async def health(self) -> bool: ...


class GrpcPaymentClient:
    def __init__(self, addr: str | None = None, channel: grpc.aio.Channel | None = None) -> None:
        self._addr = addr or settings.payment_grpc_addr
        self._channel = channel
        self._owns_channel = channel is None
        self._stub: payment_pb2_grpc.PaymentServiceStub | None = None

    async def aclose(self) -> None:
        if self._channel is not None and self._owns_channel:
            await self._channel.close()
            self._channel = None
            self._stub = None

    def _get_stub(self) -> payment_pb2_grpc.PaymentServiceStub:
        if self._stub is None:
            if self._channel is None:
                self._channel = grpc.aio.insecure_channel(self._addr)
                self._owns_channel = True
            self._stub = payment_pb2_grpc.PaymentServiceStub(self._channel)
        return self._stub

    async def charge(self, order_id: str, amount_cents: int, card_token: str) -> PaymentCharge:
        """Charge the supplied card. Raises PaymentDeclined on a soft decline.

        gRPC transport errors propagate as grpc.RpcError so the orchestrator
        can trigger rollback for those too.
        """
        req = payment_pb2.ChargeRequest(
            order_id=order_id, amount_cents=amount_cents, card_token=card_token
        )
        resp = await self._get_stub().Charge(
            req, timeout=settings.grpc_timeout_seconds
        )
        if resp.status == payment_pb2.ChargeResponse.STATUS_DECLINED:
            raise PaymentDeclined(resp.reason or "payment declined")
        if resp.status == payment_pb2.ChargeResponse.STATUS_APPROVED:
            return PaymentCharge(
                charge_id=resp.charge_id, order_id=order_id, amount_cents=amount_cents
            )
        raise PaymentDeclined("payment returned unspecified status")

    async def refund(self, charge_id: str) -> None:
        """Best-effort refund. Logs and swallows errors - we have to keep going
        even if payment is having a bad time."""
        req = payment_pb2.RefundRequest(charge_id=charge_id)
        try:
            await self._get_stub().Refund(
                req, timeout=settings.grpc_timeout_seconds
            )
        except grpc.RpcError:
            logger.exception("payment.refund failed for charge_id=%s", charge_id)

    async def health(self) -> bool:
        try:
            # We don't have a dedicated Health RPC, so probe via a cheap GetPaymentStatus.
            req = payment_pb2.GetPaymentStatusRequest(order_id="__health__")
            await self._get_stub().GetPaymentStatus(
                req, timeout=settings.grpc_timeout_seconds
            )
            return True
        except grpc.RpcError as exc:
            logger.warning("payment health: %s", exc)
            return False
