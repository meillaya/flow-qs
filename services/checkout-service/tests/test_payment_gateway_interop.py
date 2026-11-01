"""Real checkout-client to Go payment-gateway interoperability tests."""

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

from app.clients.payment_grpc_client import GrpcPaymentClient, PaymentDeclined


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    """Stop the gateway process group, tolerating an already-exited child."""
    if process.poll() is not None:
        process.wait()
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=5)


@pytest.fixture(scope="module")
def real_gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    gateway_dir = Path(__file__).resolve().parents[2] / "payment-gateway"
    grpc_port = _free_port()
    address = f"127.0.0.1:{grpc_port}"
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
        env=os.environ | {"GRPC_PORT": str(grpc_port), "HTTP_PORT": str(_free_port())},
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


@pytest.mark.asyncio
async def test_decline_from_real_gateway_maps_to_payment_declined(real_gateway: str) -> None:
    """A canonical Go decline is mapped without reading invented response fields."""
    client = GrpcPaymentClient(addr=real_gateway)
    try:
        with pytest.raises(PaymentDeclined, match="payment declined"):
            await client.charge("declined-order", 200_000, "tok")
    finally:
        await client.aclose()
