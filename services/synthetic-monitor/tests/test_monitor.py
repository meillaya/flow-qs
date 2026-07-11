"""Tests for the synthetic monitor health polling logic."""

import asyncio
import contextlib
import sys

import httpx
import pytest
import respx

from app.main import _poll_one


@pytest.mark.asyncio
@respx.mock
async def test_poll_one_success():
    """A 200 response is reported as success."""
    route = respx.get("http://fake-service:9999/healthz").respond(200)
    client = httpx.AsyncClient()
    ok, elapsed = await _poll_one(client, "http://fake-service:9999/healthz")
    assert ok is True
    assert elapsed >= 0.0
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_poll_one_failure():
    """A non-200 response is reported as failure."""
    route = respx.get("http://fake-service:9999/healthz").respond(503)
    client = httpx.AsyncClient()
    ok, elapsed = await _poll_one(client, "http://fake-service:9999/healthz")
    assert ok is False
    assert elapsed >= 0.0
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_poll_one_timeout():
    """A timeout is reported as failure."""
    route = respx.get("http://fake-service:9999/healthz").mock(
        side_effect=httpx.TimeoutException("too slow")
    )
    client = httpx.AsyncClient()
    ok, elapsed = await _poll_one(client, "http://fake-service:9999/healthz")
    assert ok is False
    assert elapsed >= 0.0
    assert route.called


@pytest.fixture
def one_target(monkeypatch):
    """Run the monitor against a single fake service."""
    monkeypatch.setenv("SYNTHETIC_TARGETS", "fake-service:9999")
    monkeypatch.setenv("POLL_INTERVAL", "0.05")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    # Ensure a fresh module import so TARGETS is re-evaluated.
    sys.modules.pop("app.main", None)


@pytest.mark.asyncio
@respx.mock
async def test_poll_loop_emits_failure_event_after_three_failures(caplog, monkeypatch, one_target):
    """Three consecutive failures trigger a synthetic_failure log/event."""
    monkeypatch.setenv("FAILURE_THRESHOLD", "3")
    respx.get("http://fake-service:9999/healthz").respond(503)

    from app import main as main_module

    main_module.TARGETS = ("fake-service:9999",)
    main_module._last_results = {"fake-service:9999": False}
    main_module.POLL_INTERVAL = 0.05

    task = asyncio.create_task(main_module._poll_loop())
    await asyncio.sleep(0.25)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    failure_records = [
        r for r in caplog.records if r.levelname == "ERROR" and "synthetic_failure" in r.message
    ]
    assert len(failure_records) >= 1


@pytest.mark.asyncio
@respx.mock
async def test_poll_loop_resets_failure_count_on_success(monkeypatch, one_target):
    """A successful check resets the consecutive failure counter."""
    monkeypatch.setenv("FAILURE_THRESHOLD", "2")
    route = respx.get("http://fake-service:9999/healthz")
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(200),
        httpx.Response(200),
    ]

    from app import main as main_module

    main_module.TARGETS = ("fake-service:9999",)
    main_module._last_results = {"fake-service:9999": False}
    main_module.POLL_INTERVAL = 0.05

    task = asyncio.create_task(main_module._poll_loop())
    await asyncio.sleep(0.25)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert route.call_count >= 2


def test_failure_threshold_emits_otel_span_event(monkeypatch) -> None:
    """A threshold breach records a structured event on the current OTel span."""
    from app import main as main_module

    events: list[tuple[str, dict[str, int | str]]] = []

    class RecordingSpan:
        def add_event(self, name: str, attributes: dict[str, int | str]) -> None:
            events.append((name, attributes))

    monkeypatch.setattr(main_module.trace, "get_current_span", RecordingSpan)
    main_module._emit_failure_event("catalog-service:8000")

    assert events == [
        (
            "synthetic_failure",
            {"service.name": "catalog-service:8000", "failure.count": 3},
        )
    ]
