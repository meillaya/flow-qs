"""Tests for the catalog service."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """Return a FastAPI TestClient."""
    return TestClient(app)


def test_healthz(client: TestClient) -> None:
    """Health endpoint returns ok."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz(client: TestClient) -> None:
    """Readiness endpoint returns ready."""
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_list_products(client: TestClient) -> None:
    """Seeded products are listed."""
    response = client.get("/products")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 20
    assert data[0]["id"].startswith("p")


def test_get_product(client: TestClient) -> None:
    """A known seeded product can be fetched."""
    response = client.get("/products/p001")
    assert response.status_code == 200
    product = response.json()
    assert product["id"] == "p001"
    assert "sku" in product


def test_get_product_not_found(client: TestClient) -> None:
    """Missing product returns 404."""
    response = client.get("/products/nonexistent")
    assert response.status_code == 404
    assert response.json()["detail"] == "Product not found"


def test_search_products(client: TestClient) -> None:
    """Search returns matching products."""
    response = client.get("/products/search?q=laptop")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert "laptop" in data[0]["name"].lower()


def test_search_empty_query(client: TestClient) -> None:
    """Empty search query returns 422."""
    response = client.get("/products/search?q=")
    assert response.status_code == 422


def test_create_product(client: TestClient) -> None:
    """A new product can be created."""
    payload = {
        "id": "p999",
        "name": "Test Gadget",
        "description": "A test product",
        "price_cents": 1234,
        "sku": "TST-001",
    }
    response = client.post("/products", json=payload)
    assert response.status_code == 201
    product = response.json()
    assert product["id"] == "p999"

    # Duplicate sku should fail with a 409 conflict.
    response = client.post("/products", json=payload)
    assert response.status_code == 409


def test_database_error_returns_internal_server_error(monkeypatch) -> None:
    """A SQLite read failure stays an HTTP 500 rather than false data."""
    import sqlite3

    from app import main

    def fail_list() -> list[dict[str, object]]:
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(main, "list_products", fail_list)
    with TestClient(app, raise_server_exceptions=False) as error_client:
        response = error_client.get("/products")

    assert response.status_code == 500


def test_search_latency_injection_preserves_results(client: TestClient, monkeypatch) -> None:
    """Configured query latency delays search without changing its payload."""
    import time

    monkeypatch.setenv("CATALOG_QUERY_DELAY_SECONDS", "0.02")
    started = time.perf_counter()
    response = client.get("/products/search?q=laptop")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert response.json()[0]["id"] == "p008"
    assert elapsed >= 0.02
