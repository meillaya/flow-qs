"""gRPC servicer for inventory operations."""

from __future__ import annotations

import asyncio
from typing import Any

import grpc

import app.grpc_gen.inventory_pb2 as inventory_pb2
import app.grpc_gen.inventory_pb2_grpc as inventory_pb2_grpc
from app.db import get_inventory, release_stock, reserve_stock


def _to_stock_response(row: dict[str, Any]) -> inventory_pb2.StockResponse:
    return inventory_pb2.StockResponse(
        sku=row["sku"],
        available=row["available"],
        reserved=row["reserved"],
    )


class InventoryServicer(inventory_pb2_grpc.InventoryServiceServicer):
    async def CheckStock(
        self,
        request: inventory_pb2.StockRequest,
        context: grpc.aio.ServicerContext,
    ) -> inventory_pb2.StockResponse:
        try:
            row = await asyncio.to_thread(get_inventory, request.sku)
            return _to_stock_response(row)
        except KeyError:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("SKU not found")
            return inventory_pb2.StockResponse()

    async def ReserveStock(
        self,
        request: inventory_pb2.ReserveRequest,
        context: grpc.aio.ServicerContext,
    ) -> inventory_pb2.ReserveResponse:
        try:
            result = await asyncio.to_thread(reserve_stock, request.sku, request.quantity)
            if not result["success"]:
                context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
                context.set_details(result["message"])
            return inventory_pb2.ReserveResponse(
                success=result["success"],
                available=result["available"],
                reserved=result["reserved"],
                message=result["message"],
            )
        except KeyError:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("SKU not found")
            return inventory_pb2.ReserveResponse()
        except ValueError:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("quantity must be positive")
            return inventory_pb2.ReserveResponse()

    async def ReleaseStock(
        self,
        request: inventory_pb2.ReleaseRequest,
        context: grpc.aio.ServicerContext,
    ) -> inventory_pb2.ReleaseResponse:
        try:
            result = await asyncio.to_thread(release_stock, request.sku, request.quantity)
            if not result["success"]:
                context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
                context.set_details(result["message"])
            return inventory_pb2.ReleaseResponse(
                success=result["success"],
                available=result["available"],
                reserved=result["reserved"],
                message=result["message"],
            )
        except KeyError:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("SKU not found")
            return inventory_pb2.ReleaseResponse()
        except ValueError:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("quantity must be positive")
            return inventory_pb2.ReleaseResponse()
