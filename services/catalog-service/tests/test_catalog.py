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
