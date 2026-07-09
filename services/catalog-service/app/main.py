"""Catalog service FastAPI application."""

import os
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from opentelemetry import trace

from app.db import check_connection, create_product, get_product, list_products, search_products
from app.instrumentation import configure_tracing, instrument_app
from app.models import Product, ProductCreate
from app.seed import seed_if_empty

SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "catalog-service")
configure_tracing(SERVICE_NAME)
tracer = trace.get_tracer(SERVICE_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Seed the database on application startup."""
    seed_if_empty()
    yield


app = FastAPI(title="Catalog Service", version="0.1.0", lifespan=lifespan)
instrument_app(app)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness endpoint."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    """Readiness endpoint; verifies SQLite connectivity."""
    if check_connection():
        return {"status": "ready"}
    raise HTTPException(status_code=503, detail="Database not reachable")


@app.get("/products")
def get_products() -> list[Product]:
    """List all products."""
    with tracer.start_as_current_span("catalog.list_products"):
        return [Product(**p) for p in list_products()]


@app.get("/products/search")
def search(q: str = Query(..., min_length=1)) -> list[Product]:
    """Search products by name."""
    with tracer.start_as_current_span("catalog.search_products") as span:
        span.set_attribute("search.query", q)
        return [Product(**p) for p in search_products(q)]


@app.get("/products/{product_id}")
def get_single_product(product_id: str) -> Product:
    """Return a single product by id."""
    with tracer.start_as_current_span("catalog.get_product") as span:
        span.set_attribute("product.id", product_id)
        product = get_product(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return Product(**product)


@app.post("/products", status_code=201)
def add_product(product: ProductCreate) -> Product:
    """Create a new product."""
    with tracer.start_as_current_span("catalog.create_product") as span:
        span.set_attribute("product.id", product.id)
        span.set_attribute("product.sku", product.sku)
        try:
            created = create_product(product.model_dump())
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail=f"Product with id or sku already exists: {exc}"
            ) from exc
    return Product(**created)
