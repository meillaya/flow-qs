"""Regression tests for live Tempo/Loki/Prometheus incident context."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.clients import HTTPXUpstreamContext, StaticUpstreamContext
from app.main import AlertmanagerAlert, AlertmanagerPayload, create_app
from app.storage import IncidentStore
from app.timeline import build_timeline


@pytest.mark.asyncio
async def test_fetches_tempo_contract_and_exact_trace_correlated_loki_log() -> None:
    """Given Tempo/Loki OTLP payloads, context preserves the shared trace ID."""
    start = datetime(2026, 7, 11, 6, 0, tzinfo=UTC)
    end = datetime(2026, 7, 11, 6, 5, tzinfo=UTC)
    trace_id = "036316594906ac984ad63936aae81985"
    tempo_payload = {
        "traces": [
            {
                "traceID": trace_id.lstrip("0"),
                "rootServiceName": "synthetic-monitor",
                "startTimeUnixNano": "1783751446172507001",
                "durationMs": 8,
            },
            {"rootServiceName": "checkout-service", "startTimeUnixNano": "1783751446"},
        ]
    }
    loki_payload = {
        "data": {
            "result": [
                {
                    "stream": {
                        "service_name": "synthetic-monitor",
                        "trace_id": trace_id,
                    },
                    "values": [["1783751446180740096", "checkout health failed"]],
                }
            ]
        }
    }

    async with httpx.AsyncClient() as client:
        context = HTTPXUpstreamContext(
            "http://tempo.test", "http://loki.test", "http://prom.test", client=client
        )
        with respx.mock(assert_all_called=False) as router:
            router.get("http://tempo.test/api/search").respond(json=tempo_payload)
            router.get("http://loki.test/loki/api/v1/query_range").respond(json=loki_payload)
            traces = await context.fetch_traces("checkout-service", start, end)
            logs = await context.fetch_logs(
                "checkout-service", start, end, trace_ids=[hit.trace_id for hit in traces]
            )

    assert traces[0].trace_id == trace_id
    assert len(traces) == 1
    assert traces[0].start_time == "2026-07-11T06:30:46.172507+00:00"
    assert traces[0].duration_ms == 8
    assert logs[0].labels["trace_id"] == trace_id


@pytest.mark.asyncio
async def test_fetches_checkout_synthetic_prometheus_value_without_invalid_escape() -> None:
    start = datetime(2026, 7, 11, 6, 0, tzinfo=UTC)
    end = datetime(2026, 7, 11, 6, 5, tzinfo=UTC)
    observed_queries: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        observed_queries.append(query)
        result = []
        if query.startswith("synthetic_check_success"):
            result = [
                {
                    "metric": {"service": "checkout-service-checkout-service.ecom:8003"},
                    "values": [[1783751446, "0"]],
                }
            ]
        return httpx.Response(200, json={"data": {"result": result}})

    async with httpx.AsyncClient() as client:
        context = HTTPXUpstreamContext(
            "http://tempo.test", "http://loki.test", "http://prom.test", client=client
        )
        with respx.mock as router:
            router.get("http://prom.test/api/v1/query_range").mock(side_effect=respond)
            snapshots = await context.fetch_metrics("checkout-service", start, end)

    assert all(r"\-" not in query for query in observed_queries)
    assert snapshots[0].name == "synthetic_check_success"
    assert snapshots[0].value == 0


def test_timeline_exposes_trace_id_and_honest_empty_backends() -> None:
    """Given empty upstreams, Timeline says so instead of claiming correlation."""
    started = datetime(2026, 7, 11, 6, 0, tzinfo=UTC)
    empty = build_timeline(started, None, "checkout-service", "ServiceDown", [], [], [])
    messages = [event.message for event in empty]
    assert "No Tempo traces returned for the incident window" in messages
    assert "No Loki logs returned for the incident window" in messages
    assert "No Prometheus samples returned for the incident window" in messages


def test_webhook_preserves_original_start_outside_correlation_horizon(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bounding upstream queries must not rewrite the incident's actual start."""
    monkeypatch.setenv("INCIDENT_DB_PATH", str(tmp_path / "incidents.db"))
    monkeypatch.setenv("POSTMORTEM_DIR", str(tmp_path / "postmortems"))
    starts_at = datetime.now(UTC) - timedelta(hours=2)
    alert = AlertmanagerAlert(
        status="firing",
        labels={"alertname": "OldAlert", "service": "checkout-service"},
        startsAt=starts_at.isoformat(),
        fingerprint="old-alert-timeline",
    )
    app = create_app(context=StaticUpstreamContext())

    with TestClient(app) as client:
        response = client.post(
            "/webhook",
            json=AlertmanagerPayload(status="firing", alerts=[alert]).model_dump(),
        )
        incident_id = response.json()["incidents"][0]
        incident = client.get(f"/incidents/{incident_id}").json()

    alert_started = next(
        event
        for event in incident["timeline"]
        if event["source"] == "alertmanager" and "started firing" in event["message"]
    )
    assert alert_started["timestamp"] == starts_at.isoformat()


def test_resolved_webhook_preserves_initiating_chaos_and_context(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given cause deletion before resolution, the firing-time cause remains authoritative."""
    experiment = {
        "kind": "NetworkChaos",
        "name": "demo-alert-checkout",
        "namespace": "chaos-testing",
        "start_time": "2026-07-11T05:59:00+00:00",
        "target": "checkout-service",
        "spec": {"direction": "from"},
    }

    class ChangingChaos:
        def __init__(self) -> None:
            self.calls = 0

        async def list_active(
            self, service: str, alert_time: datetime
        ) -> list[dict[str, Any]]:
            self.calls += 1
            return [experiment] if self.calls == 1 else []

    monkeypatch.setenv("INCIDENT_DB_PATH", str(tmp_path / "default.db"))
    monkeypatch.setenv("POSTMORTEM_DIR", str(tmp_path / "default-postmortems"))
    store = IncidentStore(tmp_path / "incidents.db", tmp_path / "postmortems")
    app = create_app(
        context=StaticUpstreamContext(), store=store, chaos_source=ChangingChaos()
    )
    firing = AlertmanagerAlert(
        status="firing",
        labels={"alertname": "ServiceDown", "service": "checkout-service"},
        startsAt="2026-07-11T06:00:00+00:00",
        fingerprint="preserve-context",
    )
    resolved = firing.model_copy(
        update={"status": "resolved", "endsAt": "2026-07-11T06:02:00+00:00"}
    )

    with TestClient(app) as client:
        first = client.post(
            "/webhook", json=AlertmanagerPayload(status="firing", alerts=[firing]).model_dump()
        ).json()
        client.post(
            "/webhook", json=AlertmanagerPayload(status="resolved", alerts=[resolved]).model_dump()
        )
        incident = client.get(f"/incidents/{first['incidents'][0]}").json()

    assert [item["name"] for item in incident["correlated_chaos"]] == [
        "demo-alert-checkout"
    ]
    assert "demo-alert-checkout" in incident["postmortem"]
    assert "resolved" in incident["postmortem"]
