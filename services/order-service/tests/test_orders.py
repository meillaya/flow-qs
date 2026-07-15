from concurrent.futures import ThreadPoolExecutor

import grpc
import pytest
from fastapi.testclient import TestClient

from app import main
from app.payment_client import PaymentStatus


@pytest.fixture(autouse=True)
def _patch_db_and_payment(monkeypatch, tmp_path):
    db_path = str(tmp_path / "orders_test.db")
    monkeypatch.setattr(main, "DB_PATH", db_path)
    monkeypatch.setattr(main, "_setup_tracing", lambda: None)
    main._init_db()
    monkeypatch.setattr(
        main,
        "get_payment_status",
        lambda charge_id: PaymentStatus(charge_id, "payment-order-1", 5000, "approved"),
    )


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
    client.post(
        "/orders",
        json={
            "user_id": "alice",
            "items": [{"sku": "SKU-001", "qty": 1}],
            "total_cents": 100,
        },
    ).json()
    client.post(
        "/orders",
        json={
            "user_id": "bob",
            "items": [{"sku": "SKU-002", "qty": 1}],
            "total_cents": 200,
        },
    ).json()

    resp = client.get("/orders?user_id=alice")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["user_id"] == "alice"


def test_get_order_with_user_id_filter(client: TestClient) -> None:
    created = client.post(
        "/orders",
        json={
            "user_id": "alice",
            "items": [{"sku": "SKU-001", "qty": 1}],
            "total_cents": 100,
        },
    ).json()
    resp = client.get(f"/orders/{created['id']}?user_id=alice")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]

    resp = client.get(f"/orders/{created['id']}?user_id=bob")
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"user_id": " ", "items": [{"sku": "SKU-001", "qty": 1}], "total_cents": 100},
        {"user_id": "u1", "items": [], "total_cents": 100},
        {"user_id": "u1", "items": "bad", "total_cents": 100},
        {"user_id": "u1", "items": [{"sku": " ", "qty": 1}], "total_cents": 100},
        {"user_id": "u1", "items": [{"sku": "SKU-001", "qty": 0}], "total_cents": 100},
        {"user_id": "u1", "items": [{"sku": "SKU-001", "qty": -1}], "total_cents": 100},
        {"user_id": "u1", "items": [{"sku": "SKU-001", "qty": 1}], "total_cents": "100"},
        {"user_id": "u1", "items": [{"sku": "SKU-001", "qty": 1}], "total_cents": 0},
        {"user_id": "u1", "items": [{"sku": "SKU-001", "qty": 1}], "total_cents": -1},
        {
            "user_id": "u1",
            "items": [{"sku": "SKU-001", "qty": 1}],
            "total_cents": 100,
            "unknown": True,
        },
    ],
)
def test_create_order_rejects_invalid_domain_payload(
    client: TestClient, payload: dict[str, object]
) -> None:
    response = client.post("/orders", json=payload)

    assert response.status_code == 422
    assert client.get("/orders").json() == []


def test_create_order_persists_paid_when_charge_is_approved(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        main,
        "get_payment_status",
        lambda charge_id: PaymentStatus(charge_id, "payment-order-1", 5000, "approved"),
    )
    payload = {
        "user_id": "u1",
        "items": [{"sku": "SKU-001", "qty": 1}],
        "total_cents": 5000,
        "payment_charge_id": "ch-1",
        "payment_order_id": "payment-order-1",
    }

    response = client.post("/orders", json=payload)

    assert response.status_code == 201
    assert response.json()["status"] == "paid"
    assert client.get(f"/orders/{response.json()['id']}").json()["status"] == "paid"


@pytest.mark.parametrize("payment_status", ["declined", "refunded", "unspecified"])
def test_create_order_rejects_nonapproved_charge_without_insert(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    payment_status: str,
) -> None:
    monkeypatch.setattr(
        main,
        "get_payment_status",
        lambda charge_id: PaymentStatus(
            charge_id, "payment-order-1", 5000, payment_status
        ),
    )

    response = client.post(
        "/orders",
        json={
            "user_id": "u1",
            "items": [{"sku": "SKU-001", "qty": 1}],
            "total_cents": 5000,
            "payment_charge_id": "ch-1",
        "payment_order_id": "payment-order-1",
        },
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "payment is not approved"}
    assert client.get("/orders").json() == []


@pytest.mark.parametrize(
    ("gateway_order_id", "gateway_amount", "detail"),
    [
        ("other-order", 5000, "payment order does not match"),
        ("payment-order-1", 4999, "payment amount does not match"),
    ],
)
def test_create_order_rejects_mismatched_payment_identity(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    gateway_order_id: str,
    gateway_amount: int,
    detail: str,
) -> None:
    monkeypatch.setattr(
        main,
        "get_payment_status",
        lambda charge_id: PaymentStatus(
            charge_id, gateway_order_id, gateway_amount, "approved"
        ),
    )
    response = client.post(
        "/orders",
        json={
            "user_id": "u1",
            "items": [{"sku": "SKU-001", "qty": 1}],
            "total_cents": 5000,
            "payment_charge_id": "ch-1",
            "payment_order_id": "payment-order-1",
        },
    )
    assert response.status_code == 409
    assert response.json() == {"detail": detail}
    assert client.get("/orders").json() == []


def test_create_order_rejects_replayed_charge(
    client: TestClient,
) -> None:
    payload = {
        "user_id": "u1",
        "items": [{"sku": "SKU-001", "qty": 1}],
        "total_cents": 5000,
        "payment_charge_id": "ch-1",
        "payment_order_id": "payment-order-1",
    }
    assert client.post("/orders", json=payload).status_code == 201
    replay = client.post("/orders", json={**payload, "user_id": "attacker"})
    assert replay.status_code == 409
    assert replay.json() == {"detail": "payment charge already used"}
    assert len(client.get("/orders").json()) == 1



def test_create_order_rejects_concurrent_charge_replays(client: TestClient) -> None:
    payload = {
        "user_id": "u1",
        "items": [{"sku": "SKU-001", "qty": 1}],
        "total_cents": 5000,
        "payment_charge_id": "ch-concurrent",
        "payment_order_id": "payment-order-1",
    }

    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(lambda _: client.post("/orders", json=payload), range(20)))

    assert [response.status_code for response in responses].count(201) == 1
    assert [response.status_code for response in responses].count(409) == 19
    assert len(client.get("/orders").json()) == 1


def test_create_order_returns_503_when_payment_lookup_fails_without_insert(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(_charge_id: str) -> PaymentStatus:
        raise grpc.RpcError("unavailable")

    monkeypatch.setattr(main, "get_payment_status", unavailable)

    response = client.post(
        "/orders",
        json={
            "user_id": "u1",
            "items": [{"sku": "SKU-001", "qty": 1}],
            "total_cents": 5000,
            "payment_charge_id": "ch-1",
        "payment_order_id": "payment-order-1",
        },
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "payment status unavailable"}
    assert client.get("/orders").json() == []
