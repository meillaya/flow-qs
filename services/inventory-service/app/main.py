"""Inventory service: FastAPI HTTP + asyncio gRPC server with OTel."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.grpc import GrpcInstrumentorClient, GrpcInstrumentorServer
from opentelemetry.instrumentation.grpc._aio_server import OpenTelemetryAioServerInterceptor
from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

if os.getenv("OTEL_SDK_DISABLED") not in ("true", "1"):
    _resource = Resource.create(
        {
            "service.name": os.getenv("OTEL_SERVICE_NAME", "inventory-service"),
            "deployment.environment": "dev",
        }
    )
    _provider = TracerProvider(resource=_resource)
    _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(_provider)

# Apply OTel instrumentation before importing the instrumented libraries.
SQLite3Instrumentor().instrument()
GrpcInstrumentorServer().instrument()
GrpcInstrumentorClient().instrument()

import grpc
from fastapi import FastAPI, HTTPException
from grpc_reflection.v1alpha import reflection
from pydantic import BaseModel, Field

import app.grpc_gen.inventory_pb2 as inventory_pb2
import app.grpc_gen.inventory_pb2_grpc as inventory_pb2_grpc
from app.db import get_inventory, init_db, list_inventory, release_stock, reserve_stock
from app.grpc_server import InventoryServicer
from app.http_metrics import HTTPMetrics

logger = logging.getLogger(__name__)


class Quantity(BaseModel):
    quantity: int = Field(default=1, ge=1)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await asyncio.to_thread(init_db)
    server = grpc.aio.server(
        interceptors=[OpenTelemetryAioServerInterceptor(trace.get_tracer(__name__))]
    )
    inventory_pb2_grpc.add_InventoryServiceServicer_to_server(InventoryServicer(), server)
    service_names = (
        inventory_pb2.DESCRIPTOR.services_by_name["InventoryService"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)
    server.add_insecure_port("[::]:50051")
    await server.start()
    logger.info("gRPC server listening on port 50051")
    try:
        yield
    finally:
        logger.info("Shutting down gRPC server")
        await server.stop(5)
        http_metrics.shutdown()


app = FastAPI(title="Inventory Service", lifespan=lifespan)
http_metrics = HTTPMetrics(os.getenv("OTEL_SERVICE_NAME", "inventory-service"))
app.middleware("http")(http_metrics.middleware)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    try:
        await asyncio.to_thread(list_inventory)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Not ready") from exc
    return {"status": "ok"}


@app.get("/inventory")
async def get_all_inventory() -> list[dict[str, object]]:
    return await asyncio.to_thread(list_inventory)


@app.get("/inventory/{sku}")
async def get_item(sku: str) -> dict[str, object]:
    try:
        return await asyncio.to_thread(get_inventory, sku)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="SKU not found") from exc


@app.post("/inventory/{sku}/reserve")
async def reserve(sku: str, body: Quantity) -> dict[str, object]:
    try:
        result = await asyncio.to_thread(reserve_stock, sku, body.quantity)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="SKU not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result["success"]:
        raise HTTPException(status_code=409, detail=result["message"])
    return result


@app.post("/inventory/{sku}/release")
async def release(sku: str, body: Quantity) -> dict[str, object]:
    try:
        result = await asyncio.to_thread(release_stock, sku, body.quantity)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="SKU not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


FastAPIInstrumentor.instrument_app(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8001)
