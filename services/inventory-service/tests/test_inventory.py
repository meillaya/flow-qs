"""Tests for the inventory service HTTP and gRPC endpoints."""

from __future__ import annotations

import asyncio
import sqlite3

import grpc
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import app.grpc_gen.inventory_pb2 as inventory_pb2
import app.grpc_gen.inventory_pb2_grpc as inventory_pb2_grpc
from app import db
from app.main import app


def _set_stock(sku: str, available: int, reserved: int) -> None:
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute(
        "UPDATE inventory SET available = ?, reserved = ? WHERE sku = ?",
        (available, reserved, sku),
    )
    conn.commit()
    conn.close()


def test_healthz() -> None:
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz() -> None:
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_inventory() -> None:
    with TestClient(app) as client:
        response = client.get("/inventory/SKU-001")
    assert response.status_code == 200
    data = response.json()
    assert data["sku"] == "SKU-001"
    assert data["available"] == 100
    assert data["reserved"] == 0


def test_get_inventory_404() -> None:
    with TestClient(app) as client:
        response = client.get("/inventory/UNKNOWN")
    assert response.status_code == 404


def test_list_inventory() -> None:
    with TestClient(app) as client:
        response = client.get("/inventory")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 20
    assert data[0]["sku"] == "SKU-001"


def test_reserve_and_release() -> None:
    with TestClient(app) as client:
        reserve = client.post("/inventory/SKU-002/reserve", json={"quantity": 3})
        assert reserve.status_code == 200
        assert reserve.json()["available"] == 97
        assert reserve.json()["reserved"] == 3

        release = client.post("/inventory/SKU-002/release", json={"quantity": 3})
        assert release.status_code == 200
        assert release.json()["available"] == 100
        assert release.json()["reserved"] == 0


def test_reserve_insufficient_stock() -> None:
    _set_stock("SKU-003", available=2, reserved=0)
    with TestClient(app) as client:
        response = client.post("/inventory/SKU-003/reserve", json={"quantity": 5})
    assert response.status_code == 409
    assert "Insufficient" in response.json()["detail"]


def test_release_insufficient_reserved() -> None:
    _set_stock("SKU-004", available=97, reserved=3)
    with TestClient(app) as client:
        response = client.post("/inventory/SKU-004/release", json={"quantity": 5})
    assert response.status_code == 400


def test_concurrent_reservation_race() -> None:
    """20 concurrent reservations of qty=1 from stock=10 must produce exactly 10 successes."""
    _set_stock("SKU-001", available=10, reserved=0)

    async def reserve_one(client: AsyncClient) -> int:
        response = await client.post(
            "/inventory/SKU-001/reserve", json={"quantity": 1}
        )
        return response.status_code

    async def _run() -> list[int]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await asyncio.gather(*[reserve_one(client) for _ in range(20)])

    results = asyncio.run(_run())
    success = results.count(200)
    failures = results.count(409)
    row = db.get_inventory("SKU-001")

    assert success == 10
    assert failures == 10
    assert row["available"] == 0
    assert row["reserved"] == 10


def test_grpc_check_stock() -> None:
    with TestClient(app):
        channel = grpc.insecure_channel("localhost:50051")
        stub = inventory_pb2_grpc.InventoryServiceStub(channel)
        response = stub.CheckStock(inventory_pb2.StockRequest(sku="SKU-001"))
    assert response.sku == "SKU-001"
    assert response.available == 100
    assert response.reserved == 0


def test_grpc_reserve_and_release() -> None:
    with TestClient(app):
        channel = grpc.insecure_channel("localhost:50051")
        stub = inventory_pb2_grpc.InventoryServiceStub(channel)
        reserve = stub.ReserveStock(
            inventory_pb2.ReserveRequest(sku="SKU-005", quantity=5)
        )
        assert reserve.success is True
        assert reserve.available == 95
        assert reserve.reserved == 5

        release = stub.ReleaseStock(
            inventory_pb2.ReleaseRequest(sku="SKU-005", quantity=5)
        )
        assert release.success is True
        assert release.available == 100
        assert release.reserved == 0
