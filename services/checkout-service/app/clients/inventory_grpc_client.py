"""gRPC client for inventory-service.

The plan defines proto/inventory.proto with three RPCs:
  * CheckStock    - used for /readyz only
  * ReserveStock  - used during checkout
  * ReleaseStock  - used during rollback

This module is async-first (grpc.aio). The OTel grpc client instrumentation
will pick up the outbound calls automatically once `init_tracing` has run.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol

import grpc

from ..config import settings
from ..models import Reservation
from ..proto_gen import inventory_pb2, inventory_pb2_grpc

logger = logging.getLogger(__name__)


class InventoryClient(Protocol):
    async def reserve(self, sku: str, quantity: int) -> Reservation | None: ...
    async def release(self, reservation: Reservation) -> None: ...
    async def health(self) -> bool: ...


class GrpcInventoryClient:
    def __init__(self, addr: str | None = None, channel: grpc.aio.Channel | None = None) -> None:
        self._addr = addr or settings.inventory_grpc_addr
        self._channel = channel  # tests inject an in-process channel
        self._owns_channel = channel is None
        self._stub: inventory_pb2_grpc.InventoryServiceStub | None = None

    async def aclose(self) -> None:
        if self._channel is not None and self._owns_channel:
            await self._channel.close()
            self._channel = None
            self._stub = None

    def _get_stub(self) -> inventory_pb2_grpc.InventoryServiceStub:
        if self._stub is None:
            if self._channel is None:
                self._channel = grpc.aio.insecure_channel(self._addr)
                self._owns_channel = True
            self._stub = inventory_pb2_grpc.InventoryServiceStub(self._channel)
        return self._stub

    async def reserve(self, sku: str, quantity: int) -> Reservation | None:
        """Returns a Reservation on success, or None if inventory was insufficient.

        Raises grpc.RpcError on transport-level failures (lets the orchestrator
        treat them as recoverable with rollback).
        """
        req = inventory_pb2.ReserveRequest(sku=sku, quantity=quantity)
        try:
            resp = await self._get_stub().ReserveStock(
                req, timeout=settings.grpc_timeout_seconds
            )
        except grpc.aio.AioRpcError as exc:
            if exc.code() == grpc.StatusCode.FAILED_PRECONDITION:
                return None
            raise
        if not resp.success:
            return None
        return Reservation(sku=sku, quantity=quantity)

    async def release(self, reservation: Reservation) -> None:
        """Idempotent. Swallows gRPC errors after logging - rollback must keep
        moving even if the inventory service is momentarily unhappy."""
        req = inventory_pb2.ReleaseRequest(
            sku=reservation.sku,
            quantity=reservation.quantity,
        )
        try:
            await self._get_stub().ReleaseStock(
                req, timeout=settings.grpc_timeout_seconds
            )
        except grpc.RpcError:
            logger.exception(
                "inventory.release failed for sku=%s", reservation.sku
            )

    async def health(self) -> bool:
        try:
            if self._channel is None:
                self._channel = grpc.aio.insecure_channel(self._addr)
                self._owns_channel = True
            await asyncio.wait_for(
                self._channel.channel_ready(), timeout=settings.grpc_timeout_seconds
            )
            return True
        except (TimeoutError, grpc.RpcError) as exc:
            logger.warning("inventory health: %s", exc)
            return False
