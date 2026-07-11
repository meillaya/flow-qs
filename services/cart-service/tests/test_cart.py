"""Tests for the in-memory cart service."""
# pyright: reportAny=false, reportExplicitAny=false, reportUnusedFunction=false

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient  # type: ignore[import-untyped]

from app.main import app, carts


@pytest.fixture(autouse=True)
def _clear_carts() -> Iterator[None]:
    """Reset the in-memory cart store before every test."""
    carts.clear()
    yield


@pytest.fixture
def client() -> Any:
    return TestClient(app)


def test_healthz(client: Any) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_readyz(client: Any) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_add_and_get_cart(client: Any) -> None:
    post = client.post(
        "/cart/user123/items",
        json={"sku": "SKU-001", "qty": 2},
    )
    assert post.status_code == 200
    body = post.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["sku"] == "SKU-001"
    assert body["items"][0]["qty"] == 2

    get = client.get("/cart/user123")
    assert get.status_code == 200
    assert get.json()["items"][0]["sku"] == "SKU-001"


def test_add_merges_quantity(client: Any) -> None:
    client.post("/cart/user123/items", json={"sku": "SKU-001", "qty": 2})
    client.post("/cart/user123/items", json={"sku": "SKU-001", "qty": 3})

    response = client.get("/cart/user123")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["qty"] == 5


def test_remove_item(client: Any) -> None:
    client.post("/cart/user123/items", json={"sku": "SKU-001", "qty": 1})
    delete = client.delete("/cart/user123/items/SKU-001")
    assert delete.status_code == 200
    assert len(delete.json()["items"]) == 0


def test_clear_cart(client: Any) -> None:
    client.post("/cart/user123/items", json={"sku": "SKU-001", "qty": 1})
    client.post("/cart/user123/items", json={"sku": "SKU-002", "qty": 2})

    response = client.delete("/cart/user123")
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_multi_user_isolation(client: Any) -> None:
    client.post("/cart/alice/items", json={"sku": "SKU-001", "qty": 1})
    client.post("/cart/bob/items", json={"sku": "SKU-002", "qty": 1})

    alice = client.get("/cart/alice").json()
    bob = client.get("/cart/bob").json()

    assert len(alice["items"]) == 1
    assert alice["items"][0]["sku"] == "SKU-001"
    assert len(bob["items"]) == 1
    assert bob["items"][0]["sku"] == "SKU-002"


def test_total_cents_computed(client: Any) -> None:
    response = client.post("/cart/user123/items", json={"sku": "SKU-001", "qty": 3})
    assert response.json()["total_cents"] == 300


def test_cart_item_contract_contains_only_sku_and_quantity(client: Any) -> None:
    """Cart items follow the plan contract consumed by checkout."""
    response = client.post(
        "/cart/contract-user/items",
        json={"sku": "SKU-001", "qty": 2},
    )

    assert response.status_code == 200
    assert response.json()["items"] == [{"sku": "SKU-001", "qty": 2}]
    assert response.json()["total_cents"] == 200
