import json
import logging
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from typing import Annotated

import grpc
from fastapi import FastAPI, HTTPException, Query
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.grpc import GrpcInstrumentorClient
from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from app.models import OrderCreateRequest
from app.payment_client import get_payment_status

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

DB_PATH = os.getenv("DB_PATH", "orders.db")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "order-service")


def _setup_tracing() -> None:
    resource = Resource.create(
        {"service.name": OTEL_SERVICE_NAME, "deployment.environment": "dev"}
    )
    provider = TracerProvider(resource=resource)
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    exporter: SpanExporter
    if endpoint:
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    else:
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        exporter = InMemorySpanExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


_setup_tracing()
SQLite3Instrumentor().instrument()
GrpcInstrumentorClient().instrument()


def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                items JSON NOT NULL,
                total_cents INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)")
        conn.commit()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "items": json.loads(row["items"]),
        "total_cents": row["total_cents"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


@contextmanager
def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db()
    yield


app = FastAPI(title="order-service", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict:
    with _get_db() as conn:
        conn.execute("SELECT 1 FROM orders LIMIT 1")
    return {"status": "ready"}


@app.get("/orders")
def list_orders(user_id: Annotated[str | None, Query()] = None) -> list[dict]:
    with _get_db() as conn:
        if user_id:
            rows = conn.execute(
                "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    return [_row_to_dict(row) for row in rows]


@app.get("/orders/{order_id}")
def get_order(order_id: str, user_id: Annotated[str | None, Query()] = None) -> dict:
    with _get_db() as conn:
        if user_id:
            row = conn.execute(
                "SELECT * FROM orders WHERE id = ? AND user_id = ?", (order_id, user_id)
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="order not found")
    return _row_to_dict(row)


@app.post("/orders", status_code=201)
def create_order(payload: OrderCreateRequest) -> dict:
    order_id = str(uuid.uuid4())
    status = "pending"
    if payload.payment_charge_id is not None:
        try:
            payment_status = get_payment_status(payload.payment_charge_id)
        except grpc.RpcError as exc:
            raise HTTPException(
                status_code=503, detail="payment status unavailable"
            ) from exc
        if payment_status != "approved":
            raise HTTPException(status_code=409, detail="payment is not approved")
        status = "paid"

    items = [item.model_dump() for item in payload.items]
    created_at = datetime.now(UTC).isoformat()

    with _get_db() as conn:
        conn.execute(
            """
            INSERT INTO orders (id, user_id, items, total_cents, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                payload.user_id,
                json.dumps(items),
                payload.total_cents,
                status,
                created_at,
            ),
        )
        conn.commit()

    return {
        "id": order_id,
        "user_id": payload.user_id,
        "items": items,
        "total_cents": payload.total_cents,
        "status": status,
        "created_at": created_at,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8004)
