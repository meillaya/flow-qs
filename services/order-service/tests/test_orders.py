import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture(autouse=True)
def _patch_db_and_payment(monkeypatch, tmp_path):
    db_path = str(tmp_path / "orders_test.db")
    monkeypatch.setattr(main, "DB_PATH", db_path)
    monkeypatch.setattr(main, "_setup_tracing", lambda: None)
    main._init_db()
    monkeypatch.setattr(main, "get_payment_status", lambda _order_id: "succeeded")


@pytest.fixture
def client():
    return TestClient(main.app)


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz(client: TestClient) -> None:
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_create_order(client: TestClient) -> None:
    payload = {
        "user_id": "u1",
        "items": [{"sku": "SKU-001", "qty": 2}],
        "total_cents": 5000,
    }
    resp = client.post("/orders", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["user_id"] == "u1"
    assert data["items"] == payload["items"]
    assert data["total_cents"] == 5000
    assert data["status"] == "pending"
    assert "id" in data


def test_get_order(client: TestClient) -> None:
    payload = {
        "user_id": "u1",
        "items": [{"sku": "SKU-001", "qty": 1}],
        "total_cents": 2500,
    }
    created = client.post("/orders", json=payload).json()
    order_id = created["id"]

    resp = client.get(f"/orders/{order_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == order_id
    assert data["status"] == "pending"


def test_get_order_not_found(client: TestClient) -> None:
    resp = client.get("/orders/does-not-exist")
    assert resp.status_code == 404


def test_list_orders_filtered_by_user_id(client: TestClient) -> None:
    client.post("/orders", json={"user_id": "alice", "items": [], "total_cents": 100}).json()
    client.post("/orders", json={"user_id": "bob", "items": [], "total_cents": 200}).json()

    resp = client.get("/orders?user_id=alice")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["user_id"] == "alice"


def test_get_order_with_user_id_filter(client: TestClient) -> None:
    created = client.post(
        "/orders", json={"user_id": "alice", "items": [], "total_cents": 100}
    ).json()
    resp = client.get(f"/orders/{created['id']}?user_id=alice")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]

    resp = client.get(f"/orders/{created['id']}?user_id=bob")
    assert resp.status_code == 404
