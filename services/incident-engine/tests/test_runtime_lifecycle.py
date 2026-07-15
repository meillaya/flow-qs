"""Runtime API and lifespan ownership tests."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import main
from app.clients import StaticUpstreamContext
from app.storage import IncidentStore


def test_exporter_shutdown_timeouts_fit_the_ten_second_drain_budget() -> None:
    repository = Path(__file__).resolve().parents[3]
    dockerfile = (repository / "services" / "incident-engine" / "Dockerfile").read_text()

    assert 'OTEL_EXPORTER_OTLP_TRACES_TIMEOUT="2"' in dockerfile
    assert 'OTEL_EXPORTER_OTLP_LOGS_TIMEOUT="2"' in dockerfile

    rendered = subprocess.run(  # noqa: S603
        ["helm", "template", "incident-engine", str(repository / "charts" / "incident-engine")],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    ).stdout
    documents = [document for document in yaml.safe_load_all(rendered) if document]
    deployment = next(document for document in documents if document["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    environment = {item["name"]: item["value"] for item in container["env"]}

    assert environment["OTEL_EXPORTER_OTLP_TRACES_TIMEOUT"] == "2"
    assert environment["OTEL_EXPORTER_OTLP_LOGS_TIMEOUT"] == "2"


def test_metrics_is_prometheus_text_not_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INCIDENT_DB_PATH", str(tmp_path / "default.db"))
    monkeypatch.setenv("POSTMORTEM_DIR", str(tmp_path / "default-postmortems"))
    app = main.create_app(
        context=StaticUpstreamContext(),
        store=IncidentStore(tmp_path / "incidents.db", tmp_path / "postmortems"),
    )
    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert not response.text.startswith('"')
    assert "# HELP" in response.text


def test_create_app_with_store_override_never_constructs_default_store(
    tmp_path, monkeypatch
) -> None:
    def default_path_must_not_be_read() -> Path:
        raise AssertionError("default store path was read")

    monkeypatch.setattr(main, "default_db_path", default_path_must_not_be_read)
    store = IncidentStore(tmp_path / "override.db", tmp_path / "postmortems")

    app = main.create_app(context=StaticUpstreamContext(), store=store)

    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert app.state.app_state.store is store


def test_reconciler_uses_the_lifespan_owned_meter_provider(tmp_path, monkeypatch) -> None:
    calls = {"get_meter": 0, "create_counter": 0}
    monkeypatch.setenv("INCIDENT_DB_PATH", str(tmp_path / "default.db"))
    monkeypatch.setenv("POSTMORTEM_DIR", str(tmp_path / "default-postmortems"))

    class FakeCounter:
        def add(self, _value: int, _attributes: dict[str, str]) -> None:
            raise AssertionError("an empty store must not record a resolution")

    class FakeMeter:
        def create_counter(self, _name: str, *, description: str) -> FakeCounter:
            assert description
            calls["create_counter"] += 1
            return FakeCounter()

    class FakeMeterProvider:
        def get_meter(self, _name: str) -> FakeMeter:
            calls["get_meter"] += 1
            return FakeMeter()

    async def run_one_iteration() -> None:
        state = main.AppState()
        state.store = IncidentStore(tmp_path / "incidents.db", tmp_path / "postmortems")
        monkeypatch.setattr(state, "_meter_provider", FakeMeterProvider())
        task = asyncio.create_task(main._resolve_reconciler(state))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_one_iteration())

    assert calls == {"get_meter": 1, "create_counter": 1}


def test_lifespan_starts_metrics_and_one_reconciler_then_cancels(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("INCIDENT_DB_PATH", str(tmp_path / "default.db"))
    monkeypatch.setenv("POSTMORTEM_DIR", str(tmp_path / "default-postmortems"))
    calls = {"metrics": 0, "started": 0, "cancelled": 0}
    provider_shutdown_timeout: list[int | None] = [None]
    started = asyncio.Event()

    class FakeMeterProvider:
        def shutdown(self, timeout_millis: int) -> None:
            provider_shutdown_timeout[0] = timeout_millis

    def setup_metrics() -> FakeMeterProvider:
        calls["metrics"] += 1
        return FakeMeterProvider()

    async def reconciler(_state: main.AppState) -> None:
        calls["started"] += 1
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            calls["cancelled"] += 1

    monkeypatch.setattr(main, "_setup_otel_metrics", setup_metrics)
    monkeypatch.setattr(main, "_resolve_reconciler", reconciler)
    app = main.create_app(
        context=StaticUpstreamContext(),
        store=IncidentStore(tmp_path / "incidents.db", tmp_path / "postmortems"),
    )
    with TestClient(app):
        state = app.state.app_state
        assert state._background_task is not None
        assert started.is_set()
        assert calls == {"metrics": 1, "started": 1, "cancelled": 0}
        assert provider_shutdown_timeout == [None]

    assert state._background_task.done()
    assert calls == {"metrics": 1, "started": 1, "cancelled": 1}
    assert provider_shutdown_timeout == [2_000]
