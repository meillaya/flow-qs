"""Real Python-client to Go-gateway interoperability coverage."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import grpc
import pytest

from app.payment_client import get_payment_status
from app.proto import payment_pb2, payment_pb2_grpc


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


@pytest.fixture(scope="module")
def real_gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    gateway_dir = Path(__file__).resolve().parents[2] / "payment-gateway"
    grpc_port = _free_port()
    http_port = _free_port()
    address = f"127.0.0.1:{grpc_port}"
    env = os.environ | {
        "GRPC_PORT": str(grpc_port),
        "HTTP_PORT": str(http_port),
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4317",
    }
    binary = tmp_path_factory.mktemp("gateway") / "payment-gateway"
    subprocess.run(  # noqa: S603
        ["go", "build", "-o", str(binary), "."],  # noqa: S607
        cwd=gateway_dir,
        check=True,
        timeout=30,
    )
    process = subprocess.Popen(  # noqa: S603
        [str(binary)],
        cwd=gateway_dir,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    channel = grpc.insecure_channel(address)
    try:
        grpc.channel_ready_future(channel).result(timeout=20)
        yield address
    finally:
        channel.close()
        _stop_process_group(process)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", grpc_port)) != 0:
                    break
            time.sleep(0.05)
        else:
            pytest.fail(f"gateway process group still owns port {grpc_port}")


def test_get_payment_status_interoperates_with_real_go_gateway(real_gateway: str) -> None:
    """Given a Go charge, the Python order client reads its canonical status."""
    with grpc.insecure_channel(real_gateway) as channel:
        stub = payment_pb2_grpc.PaymentServiceStub(channel)
        charge = stub.Charge(
            payment_pb2.ChargeRequest(
                order_id="interop-order", amount_cents=5000, card_token="tok"
            ),
            timeout=2,
        )

    assert get_payment_status(charge.charge_id, address=real_gateway) == "approved"
