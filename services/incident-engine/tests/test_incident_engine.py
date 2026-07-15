"""End-to-end tests for the incident-engine webhook receiver."""

from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

os.environ["INCIDENT_ENGINE_DISABLE_OTEL"] = "1"

from app.clients import LogHit, MetricSnapshot, StaticUpstreamContext, TraceHit  # noqa: E402
from app.main import (  # noqa: E402
    AlertmanagerAlert,
    AlertmanagerPayload,
    AppState,
    _process_alert,
    create_app,
    register_chaos_experiment,
    reset_chaos_state,
)
from app.postmortem import REQUIRED_SECTIONS, has_all_sections, render_postmortem  # noqa: E402
from app.storage import IncidentStore, build_postmortem_path  # noqa: E402
from app.timeline import build_timeline, correlate_chaos  # noqa: E402


@pytest.fixture
def temp_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Point the incident store at temp paths for isolation."""
    db_path = tmp_path / "incidents.db"
    postmortem_dir = tmp_path / "postmortems"
    postmortem_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("INCIDENT_DB_PATH", str(db_path))
    monkeypatch.setenv("POSTMORTEM_DIR", str(postmortem_dir))
    return {"db": str(db_path), "postmortems": str(postmortem_dir)}


@pytest.fixture
def static_context() -> object:
    """Return a fully populated StaticUpstreamContext for assertions."""
    from app.clients import StaticUpstreamContext

    return StaticUpstreamContext(
        traces=[
            TraceHit(
                trace_id="abc123",
                root_service="cart-service",
                duration_ms=123.4,
                start_time="2026-07-09T21:00:00+00:00",
            )
        ],
        logs=[
            LogHit(
                timestamp="2026-07-09T21:00:01+00:00",
                line="ERROR checkout-service upstream timeout",
                labels={"service": "cart-service"},
            )
        ],
        metrics=[
            MetricSnapshot(name="error_rate", value=0.42, timestamp="2026-07-09T21:00:00+00:00"),
            MetricSnapshot(name="latency_p99", value=1.7, timestamp="2026-07-09T21:00:00+00:00"),
        ],
    )


def _make_alert(
    alertname: str = "HighErrorRate", service: str = "cart-service"
) -> AlertmanagerAlert:
    """Build a minimal Alertmanager alert object."""
    return AlertmanagerAlert(
        status="firing",
        labels={
            "alertname": alertname,
            "service": service,
            "severity": "critical",
            "slo": "availability",
        },
        annotations={"summary": f"{service} errors > 5% for 5m"},
        startsAt="2026-07-09T21:00:00+00:00",
        endsAt="2026-07-09T21:07:00+00:00",
        generatorURL="http://prometheus.example/graph?test=1",
        fingerprint=f"{alertname}-{service}-2026-07-09T21:00:00Z",
    )


class FailingUpstreamContext(StaticUpstreamContext):
    def __init__(self, fail_service: str | None) -> None:
        super().__init__()
        self.fail_service = fail_service

    async def fetch_traces(
        self, service: str, start: datetime, end: datetime, limit: int = 10
    ) -> list[TraceHit]:
        del start, end, limit
        if service == self.fail_service:
            raise RuntimeError("trace backend unavailable")
        return []


def test_alert_parsing_extracts_labels() -> None:
    """_parse_alert reads labels and falls back when missing."""
    from app.main import _parse_alert

    alert = _make_alert()
    alertname, service, severity, slo, starts_at, ends_at, fingerprint = _parse_alert(alert)
    assert alertname == "HighErrorRate"
    assert service == "cart-service"
    assert severity == "critical"
    assert slo == "availability"
    assert starts_at == datetime(2026, 7, 9, 21, 0, 0, tzinfo=UTC)
    assert ends_at == datetime(2026, 7, 9, 21, 7, 0, tzinfo=UTC)
    assert fingerprint == alert.fingerprint


def test_parse_alert_handles_missing_optional_fields() -> None:
    """_parse_alert tolerates alerts with missing service/slo/endsAt."""
    from app.main import _parse_alert

    alert = AlertmanagerAlert(
        status="firing",
        labels={"alertname": "PingFail"},
        annotations={},
        startsAt="2026-07-09T20:00:00+00:00",
        endsAt=None,
        fingerprint="ping",
    )
    alertname, service, severity, slo, starts_at, ends_at, fp = _parse_alert(alert)
    assert alertname == "PingFail"
    assert service == "unknown"
    assert severity == "warning"
    assert slo == ""
    assert ends_at is None
    assert fp == "ping"


def test_build_postmortem_path_is_filesystem_safe() -> None:
    """Postmortem filenames strip colons and other special characters."""
    path = build_postmortem_path("HighErrorRate/cart", "2026-07-09T21:00:00+00:00")
    assert "higherrorrate-cart" in path
    assert ":" not in path
    assert path.endswith(".md")


def test_timeline_includes_alert_traces_logs_and_metrics() -> None:
    """build_timeline returns events in chronological order across sources."""
    events = build_timeline(
        alert_starts_at=datetime(2026, 7, 9, 21, 0, 0, tzinfo=UTC),
        alert_ends_at=datetime(2026, 7, 9, 21, 7, 0, tzinfo=UTC),
        service="cart-service",
        alertname="HighErrorRate",
        traces=[
            TraceHit(
                trace_id="t1", root_service="cart-service", start_time="2026-07-09T21:00:00+00:00"
            )
        ],
        logs=[LogHit(timestamp="2026-07-09T21:00:01+00:00", line="hi")],
        metrics=[
            MetricSnapshot(name="error_rate", value=0.5, timestamp="2026-07-09T21:00:30+00:00")
        ],
    )
    timestamps = [event.timestamp for event in events]
    assert timestamps == sorted(timestamps)
    sources = {event.source for event in events}
    assert sources == {"alertmanager", "tempo", "loki", "prometheus"}


def test_correlate_chaos_matches_target_or_window() -> None:
    """correlate_chaos returns experiments that match the service or window."""
    experiments = [
        {
            "name": "pod-kill",
            "kind": "PodChaos",
            "target": "cart-service",
            "start_time": "2026-07-09T20:58:00+00:00",
        },
        {
            "name": "net-delay",
            "kind": "NetworkChaos",
            "target": "order-service",
            "start_time": "2026-07-09T19:00:00+00:00",
        },
        {
            "name": "stale-random",
            "kind": "StressChaos",
            "target": "catalog-service",
            "start_time": "2026-07-09T17:00:00+00:00",
        },
    ]
    alert_starts_at = datetime(2026, 7, 9, 21, 0, 0, tzinfo=UTC)
    correlated = correlate_chaos("cart-service", experiments, alert_starts_at)
    assert {exp["name"] for exp in correlated} == {"pod-kill"}


def test_postmortem_renders_all_sections() -> None:
    """render_postmortem produces every required section header."""
    record = _build_sample_record()
    events = build_timeline(
        alert_starts_at=datetime(2026, 7, 9, 21, 0, 0, tzinfo=UTC),
        alert_ends_at=datetime(2026, 7, 9, 21, 7, 0, tzinfo=UTC),
        service="cart-service",
        alertname="HighErrorRate",
        traces=[],
        logs=[],
        metrics=[],
    )
    body = render_postmortem(record, events)
    for section in REQUIRED_SECTIONS:
        assert f"## {section}" in body
    assert has_all_sections(body)


def test_postmortem_marks_section_missing_when_absent() -> None:
    """has_all_sections returns False when a section header is missing."""
    assert has_all_sections("## Summary\nbody\n") is False


@pytest.mark.asyncio
async def test_http_upstream_context_handles_404_and_500(temp_paths) -> None:
    """HTTPXUpstreamContext swallows HTTP errors and returns empty lists."""
    from app.clients import HTTPXUpstreamContext

    async with httpx.AsyncClient() as client:
        ctx = HTTPXUpstreamContext(
            tempo_url="http://tempo.test:3200",
            loki_url="http://loki.test:3100",
            prom_url="http://prom.test:9090",
            client=client,
        )
        with respx.mock(assert_all_called=False) as mock:
            mock.get("http://tempo.test:3200/api/search").respond(500)
            mock.get("http://loki.test:3100/loki/api/v1/query_range").respond(404)
            mock.get("http://prom.test:9090/api/v1/query_range").respond(500)
            traces = await ctx.fetch_traces("cart-service", datetime.now(UTC), datetime.now(UTC))
            logs = await ctx.fetch_logs("cart-service", datetime.now(UTC), datetime.now(UTC))
            metrics = await ctx.fetch_metrics("cart-service", datetime.now(UTC), datetime.now(UTC))
        assert traces == []
        assert logs == []
        assert metrics == []


@pytest.mark.asyncio
async def test_failed_incident_notification_does_not_log_webhook_secret(caplog) -> None:
    """A failed secret-bearing webhook request logs no credential material."""
    from app.clients import HTTPXUpstreamContext

    secret_url = "https://hooks.example.test/services/SECRET/TOKEN?key=PRIVATE"
    with respx.mock() as mock:
        mock.post(secret_url).respond(500)
        async with httpx.AsyncClient() as client:
            context = HTTPXUpstreamContext(
                tempo_url="http://tempo.test",
                loki_url="http://loki.test",
                prom_url="http://prom.test",
                client=client,
            )
            sent = await context.notify_webhook(secret_url, {"status": "firing"})

    assert sent is False
    assert "SECRET" not in caplog.text
    assert "PRIVATE" not in caplog.text


@pytest.mark.asyncio
async def test_incident_notification_emits_no_secret_bearing_http_span() -> None:
    """Secret-bearing webhook URLs never enter exported HTTP span attributes."""
    from app.clients import HTTPXUpstreamContext

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    instrumentor = HTTPXClientInstrumentor()
    instrumentor.uninstrument()
    instrumentor.instrument(tracer_provider=provider)
    secret_url = "https://hooks.example.test/services/SECRET/TOKEN?key=PRIVATE"
    try:
        with respx.mock() as mock:
            mock.post(secret_url).respond(500)
            async with httpx.AsyncClient() as client:
                context = HTTPXUpstreamContext(
                    tempo_url="http://tempo.test",
                    loki_url="http://loki.test",
                    prom_url="http://prom.test",
                    client=client,
                )
                await context.notify_webhook(secret_url, {"status": "firing"})
    finally:
        instrumentor.uninstrument()

    finished_spans = exporter.get_finished_spans()
    assert finished_spans == ()


class WindowRecordingContext(StaticUpstreamContext):
    """Capture every correlation query interval for boundary assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.windows: list[tuple[datetime, datetime]] = []

    async def fetch_traces(
        self, service: str, start: datetime, end: datetime, limit: int = 10
    ) -> list[TraceHit]:
        del service, limit
        self.windows.append((start, end))
        return []

    async def fetch_logs(
        self,
        service: str,
        start: datetime,
        end: datetime,
        limit: int = 20,
        trace_ids: list[str] | None = None,
    ) -> list[LogHit]:
        del service, limit, trace_ids
        self.windows.append((start, end))
        return []

    async def fetch_metrics(
        self, service: str, start: datetime, end: datetime
    ) -> list[MetricSnapshot]:
        del service
        self.windows.append((start, end))
        return []


@pytest.mark.parametrize(
    "starts_at",
    ["0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z"],
)
def test_webhook_bounds_hostile_correlation_windows(
    temp_paths: dict[str, str], starts_at: str
) -> None:
    """Extreme alert timestamps cannot expand upstream correlation queries."""
    reset_chaos_state()
    register_chaos_experiment(
        {
            "name": "current-chaos",
            "kind": "PodChaos",
            "target": "cart-service",
            "start_time": datetime.now(UTC).isoformat(),
        }
    )
    context = WindowRecordingContext()
    alert = _make_alert().model_copy(update={"startsAt": starts_at, "endsAt": None})
    payload = AlertmanagerPayload(status="firing", alerts=[alert])
    app = create_app(context=context)

    with TestClient(app) as client:
        response = client.post("/webhook", json=payload.model_dump())

    assert response.status_code == 200
    assert len(context.windows) == 3
    for query_start, query_end in context.windows:
        assert query_start <= query_end
        assert query_end - query_start <= timedelta(hours=1)


@pytest.mark.asyncio
async def test_webhook_creates_incident_and_postmortem(temp_paths, static_context) -> None:
    """POST /webhook persists an incident, writes a postmortem, and returns ids."""
    reset_chaos_state()
    app = create_app(context=static_context)  # type: ignore[arg-type]

    payload = AlertmanagerPayload(
        version="4",
        status="firing",
        receiver="incident-engine",
        alerts=[_make_alert()],
    )

    with TestClient(app) as client:
        response = client.post("/webhook", json=payload.model_dump())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["accepted"] == 1
        assert len(body["incidents"]) == 1
        assert len(body["postmortems"]) == 1

        list_response = client.get("/incidents")
        assert list_response.status_code == 200
        assert len(list_response.json()) == 1

        incident_id = body["incidents"][0]
        detail_response = client.get(f"/incidents/{incident_id}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["alertname"] == "HighErrorRate"
        assert detail["service"] == "cart-service"
        assert "## Summary" in detail["postmortem"]
        assert "## Impact" in detail["postmortem"]
        assert "## Timeline" in detail["postmortem"]
        assert "## Root Cause" in detail["postmortem"]
        assert "## Resolution" in detail["postmortem"]
        assert "## Lessons Learned" in detail["postmortem"]

    postmortem_path = Path(body["postmortems"][0])
    assert postmortem_path.exists()
    assert "## Root Cause" in postmortem_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_webhook_with_chaos_correlation(temp_paths, static_context) -> None:
    """A chaos experiment matching the affected service appears in root cause."""
    from app.main import register_chaos_experiment

    reset_chaos_state()
    register_chaos_experiment(
        {
            "name": "pod-kill-checkout",
            "kind": "PodChaos",
            "target": "cart-service",
            "start_time": "2026-07-09T20:58:00+00:00",
        }
    )
    app = create_app(context=static_context)  # type: ignore[arg-type]
    payload = AlertmanagerPayload(
        version="4", status="firing", alerts=[_make_alert("CartCrash", "cart-service")]
    )
    with TestClient(app) as client:
        response = client.post("/webhook", json=payload.model_dump())
        assert response.status_code == 200
        incident_id = response.json()["incidents"][0]
        detail = client.get(f"/incidents/{incident_id}").json()
    assert "pod-kill-checkout" in detail["postmortem"]
    assert "## Root Cause" in detail["postmortem"]


@pytest.mark.asyncio
async def test_webhook_handles_resolve_status(temp_paths, static_context) -> None:
    """A resolved payload marks the incident as resolved with ends_at set."""
    reset_chaos_state()
    app = create_app(context=static_context)  # type: ignore[arg-type]

    firing_payload = AlertmanagerPayload(
        version="4", status="firing", alerts=[_make_alert("RecoveredAlert", "cart-service")]
    )
    resolved_alert = _make_alert("RecoveredAlert", "cart-service")
    resolved_alert.status = "resolved"
    resolved_payload = AlertmanagerPayload(
        version="4",
        status="resolved",
        alerts=[resolved_alert],
    )

    with TestClient(app) as client:
        first = client.post("/webhook", json=firing_payload.model_dump()).json()
        incident_id = first["incidents"][0]
        second = client.post("/webhook", json=resolved_payload.model_dump()).json()
        assert second["accepted"] == 1
        detail = client.get(f"/incidents/{incident_id}").json()

    assert detail["status"] == "resolved"
    assert detail["ends_at"] is not None


@pytest.mark.asyncio
async def test_firing_webhook_ignores_alertmanager_zero_ends_at(
    temp_paths, static_context
) -> None:
    """A firing alert's zero ``endsAt`` sentinel must not claim resolution."""
    reset_chaos_state()
    alert = _make_alert("StillFiring", "cart-service")
    alert.endsAt = "0001-01-01T00:00:00Z"
    payload = AlertmanagerPayload(version="4", status="firing", alerts=[alert])
    app = create_app(context=static_context)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.post("/webhook", json=payload.model_dump())
        incident_id = response.json()["incidents"][0]
        detail = client.get(f"/incidents/{incident_id}").json()

    assert detail["status"] == "firing"
    assert detail["ends_at"] is None
    assert "0001-01-01" not in detail["postmortem"]


def test_resolved_incident_ignores_stale_firing_retry(temp_paths, static_context) -> None:
    """An out-of-order firing retry cannot reopen or contaminate a resolved incident."""
    reset_chaos_state()
    app = create_app(context=static_context)
    alert = _make_alert("RecoveredAlert", "cart-service")
    resolved = alert.model_copy(update={"status": "resolved"})

    with TestClient(app) as client:
        created = client.post(
            "/webhook",
            json=AlertmanagerPayload(status="firing", alerts=[alert]).model_dump(),
        ).json()
        incident_id = created["incidents"][0]
        client.post(
            "/webhook",
            json=AlertmanagerPayload(status="resolved", alerts=[resolved]).model_dump(),
        )
        resolved_detail = client.get(f"/incidents/{incident_id}").json()
        static_context.traces = [
            TraceHit(
                trace_id="post-resolution-trace",
                root_service="cart-service",
                duration_ms=99.0,
                start_time="2026-07-09T22:00:00+00:00",
            )
        ]
        register_chaos_experiment(
            {
                "name": "post-resolution-chaos",
                "namespace": "default",
                "kind": "PodChaos",
                "target_service": "cart-service",
                "started_at": "2026-07-09T22:00:00+00:00",
            }
        )
        client.post(
            "/webhook",
            json=AlertmanagerPayload(status="firing", alerts=[alert]).model_dump(),
        )
        detail = client.get(f"/incidents/{incident_id}").json()

    assert detail["status"] == "resolved"
    assert detail["ends_at"] == alert.endsAt
    assert detail["timeline"] == resolved_detail["timeline"]
    assert detail["correlated_chaos"] == resolved_detail["correlated_chaos"]


@pytest.mark.asyncio
async def test_concurrent_resolution_cannot_be_reopened_by_inflight_firing_retry(
    temp_paths,
) -> None:
    """Concurrent webhook completion cannot reverse a resolved incident."""

    class BlockingContext(StaticUpstreamContext):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def fetch_traces(
            self, service: str, start: datetime, end: datetime, limit: int = 10
        ) -> list[TraceHit]:
            del service, start, end, limit
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                await self.release.wait()
            return []

    store = IncidentStore(temp_paths["db"], temp_paths["postmortems"])
    initial_context = StaticUpstreamContext()
    state = AppState(store=store, context=initial_context)
    alert = _make_alert("ConcurrentRecovery", "cart-service")
    resolved = alert.model_copy(update={"status": "resolved"})
    created, _, _ = await _process_alert(state, alert, "firing")

    blocking_context = BlockingContext()
    state.context = blocking_context
    stale_task = asyncio.create_task(_process_alert(state, alert, "firing"))
    await blocking_context.started.wait()
    resolved_task = asyncio.create_task(_process_alert(state, resolved, "resolved"))
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(asyncio.shield(resolved_task), timeout=0.1)
    blocking_context.release.set()
    await asyncio.gather(stale_task, resolved_task)

    persisted = store.get(created.id)
    assert persisted is not None
    assert persisted.status == "resolved"
    assert persisted.ends_at == alert.endsAt


def test_webhook_rejects_unbounded_alert_batches(temp_paths, static_context) -> None:
    """Webhook fan-out is bounded before upstream queries or persistence."""
    app = create_app(context=static_context)  # type: ignore[arg-type]
    alerts = [
        _make_alert(f"BatchAlert{index}", "cart-service")
        for index in range(21)
    ]
    payload = {
        "version": "4",
        "status": "firing",
        "alerts": [alert.model_dump() for alert in alerts],
    }

    with TestClient(app) as client:
        response = client.post("/webhook", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_webhook_sends_optional_notification(temp_paths, static_context, monkeypatch) -> None:
    """When INCIDENT_WEBHOOK_URL is set, the static context records the call."""
    monkeypatch.setenv("INCIDENT_WEBHOOK_URL", "http://example.test/notify")
    reset_chaos_state()
    app = create_app(context=static_context)  # type: ignore[arg-type]
    payload = AlertmanagerPayload(
        version="4", status="firing", alerts=[_make_alert("NotifyAlert", "cart-service")]
    )

    with TestClient(app) as client:
        client.post("/webhook", json=payload.model_dump())

    assert static_context.webhook_calls  # type: ignore[attr-defined]
    url, body = static_context.webhook_calls[0]  # type: ignore[attr-defined]
    assert url == "http://example.test/notify"
    assert body["alertname"] == "NotifyAlert"


def test_webhook_returns_503_when_alert_cannot_be_persisted(
    temp_paths: dict[str, str],
) -> None:
    context = FailingUpstreamContext(fail_service="cart-service")
    store = IncidentStore(temp_paths["db"], temp_paths["postmortems"])
    app = create_app(context=context, store=store)
    payload = AlertmanagerPayload(
        version="4", status="firing", alerts=[_make_alert("BrokenAlert", "cart-service")]
    )

    with TestClient(app) as client:
        response = client.post("/webhook", json=payload.model_dump())

    assert response.status_code == 503
    assert response.json() == {"detail": "alert processing failed"}
    assert store.list_all() == []


def test_webhook_retry_is_idempotent_and_notifies_only_after_batch_is_durable(
    temp_paths: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INCIDENT_WEBHOOK_URL", "http://example.test/notify")
    context = FailingUpstreamContext(fail_service="order-service")
    store = IncidentStore(temp_paths["db"], temp_paths["postmortems"])
    app = create_app(context=context, store=store)
    payload = AlertmanagerPayload(
        version="4",
        status="firing",
        alerts=[
            _make_alert("CartAlert", "cart-service"),
            _make_alert("OrderAlert", "order-service"),
        ],
    )

    with TestClient(app) as client:
        first = client.post("/webhook", json=payload.model_dump())
        first_records = store.list_all()
        first_webhook_calls = list(context.webhook_calls)
        context.fail_service = None
        second = client.post("/webhook", json=payload.model_dump())

    assert first.status_code == 503
    assert len(first_records) == 1
    assert first_webhook_calls == []
    first_identity = (first_records[0].id, first_records[0].created_at)
    assert len(context.webhook_calls) == 2
    assert {call[1]["alertname"] for call in context.webhook_calls} == {
        "CartAlert",
        "OrderAlert",
    }
    assert second.status_code == 200
    assert second.json()["accepted"] == 2
    final_records = store.list_all()
    assert len(final_records) == 2
    replayed = next(record for record in final_records if record.id == first_identity[0])
    assert replayed.created_at == first_identity[1]


@pytest.mark.asyncio
async def test_resolve_reconciler_marks_stale_incidents(temp_paths, static_context) -> None:
    """The reconciler flips firing incidents older than the timeout to resolved."""
    from datetime import timedelta

    reset_chaos_state()
    app = create_app(context=static_context)  # type: ignore[arg-type]

    with TestClient(app) as client:
        payload = AlertmanagerPayload(
            version="4",
            status="firing",
            alerts=[_make_alert("OldAlert", "cart-service")],
        )
        response = client.post("/webhook", json=payload.model_dump()).json()
        incident_id = response["incidents"][0]

        # Manually rewind starts_at past the timeout window.

        record = client.get(f"/incidents/{incident_id}").json()
        store = IncidentStore(temp_paths["db"], temp_paths["postmortems"])
        stored = store.get(incident_id)
        assert stored is not None
        old_start = (datetime.now(UTC) - timedelta(minutes=120)).isoformat()
        stored.starts_at = old_start
        store.upsert(stored)

        # Run a single reconciler pass.
        from app.main import AppState, _resolve_reconciler

        state = AppState()
        state.store = store
        task = asyncio.create_task(_resolve_reconciler(state))
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        detail = client.get(f"/incidents/{incident_id}").json()
    assert detail["status"] == "resolved"


def _build_sample_record():
    """Build a populated IncidentRecord for rendering tests."""
    from app.storage import IncidentRecord

    return IncidentRecord(
        id="sample",
        alertname="HighErrorRate",
        service="cart-service",
        severity="critical",
        slo="availability",
        status="firing",
        starts_at="2026-07-09T21:00:00+00:00",
        ends_at=None,
        fingerprint="sample",
        created_at="2026-07-09T21:00:00+00:00",
        updated_at="2026-07-09T21:00:00+00:00",
        summary="",
        file_path="/tmp/sample.md",
        labels={"alertname": "HighErrorRate"},
        annotations={},
        timeline=[],
        correlated_chaos=[],
    )
