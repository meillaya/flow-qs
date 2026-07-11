"""Real-surface contract tests for checkout dependencies."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import grpc
import httpx
import pytest

from app import main
from app.clients import GrpcInventoryClient, HttpxCartClient
from app.orchestrator import CheckoutOrchestrator


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def _wait_http(url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"service exited with {process.returncode}")
        try:
            if httpx.get(url, timeout=0.2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    pytest.fail(f"service did not become ready: {url}")


@pytest.fixture(scope="module")
def real_cart() -> Iterator[str]:
    service_dir = Path(__file__).resolve().parents[2] / "cart-service"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(  # noqa: S603
        ["uv", "run", "--frozen", "uvicorn", "app.main:app", "--port", str(port)],  # noqa: S607
        cwd=service_dir,
        env=os.environ | {"OTEL_SDK_DISABLED": "true"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_http(f"{base_url}/healthz", process)
        yield base_url
    finally:
        _stop_group(process)


@pytest.fixture(scope="module")
def real_inventory(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    service_dir = Path(__file__).resolve().parents[2] / "inventory-service"
    http_port = _free_port()
    grpc_port = 50051
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", grpc_port)) == 0:
            pytest.fail("inventory contract test requires free local port 50051")
    process = subprocess.Popen(  # noqa: S603
        ["uv", "run", "--frozen", "uvicorn", "app.main:app", "--port", str(http_port)],  # noqa: S607
        cwd=service_dir,
        env=os.environ
        | {
            "INVENTORY_DB_PATH": str(tmp_path_factory.mktemp("inventory") / "inventory.db"),
            "OTEL_SDK_DISABLED": "true",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    address = f"127.0.0.1:{grpc_port}"
    channel = grpc.insecure_channel(address)
    try:
        grpc.channel_ready_future(channel).result(timeout=20)
        yield address
    finally:
        channel.close()
        _stop_group(process)


@pytest.mark.asyncio
async def test_inventory_readiness_uses_real_supported_surface(real_inventory: str) -> None:
    client = GrpcInventoryClient(addr=real_inventory)
    try:
        assert await client.health() is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_real_cart_payload_completes_checkout_surface(
    real_cart: str,
    fake_inventory: Any,
    fake_payment: Any,
    fake_order: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = httpx.post(
        f"{real_cart}/cart/contract-user/items",
        json={"sku": "SKU-001", "qty": 2},
        timeout=2,
    )
    assert response.status_code == 200

    cart = HttpxCartClient(base_url=real_cart)
    monkeypatch.setattr(
        main,
        "_orchestrator",
        CheckoutOrchestrator(
            cart=cart,
            inventory=fake_inventory,
            payment=fake_payment,
            order=fake_order,
        ),
    )
    try:
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://checkout") as client:
            checkout = await client.post(
                "/checkout",
                json={"user_id": "contract-user", "payment_method": "card"},
            )
    finally:
        await cart.aclose()

    assert checkout.status_code == 200
    assert checkout.json()["status"] == "paid"
    assert checkout.json()["total_cents"] == 200
