"""FastAPI app: Alertmanager webhook receiver that emits postmortem.md files.

Endpoints:
- ``POST /webhook`` — Accepts the Alertmanager webhook payload, correlates
  traces from Tempo, logs from Loki and metrics from Prometheus, generates a
  postmortem markdown file and persists incident metadata to SQLite.
- ``GET /incidents`` — Returns a list of all known incidents.
- ``GET /incidents/{id}`` — Returns a single incident + its rendered
  postmortem markdown.
- ``GET /healthz`` and ``GET /readyz`` — Kubernetes probes.
- ``GET /metrics`` — Prometheus scrape endpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from .chaos import ChaosSource, KubernetesChaosClient
from .clients import HTTPXUpstreamContext, StaticUpstreamContext, UpstreamContext, parse_iso
from .postmortem import build_summary, has_all_sections, render_postmortem, to_summary_payload
from .storage import (
    IncidentRecord,
    IncidentStore,
    build_postmortem_path,
    default_db_path,
    default_postmortem_dir,
)
from .timeline import TimelineEvent, build_timeline, correlate_chaos

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (env-driven so the same image works in tests + cluster)
# ---------------------------------------------------------------------------

TEMPO_URL = os.getenv("TEMPO_URL", "http://tempo.observability:3200")
LOKI_URL = os.getenv("LOKI_URL", "http://loki.observability:3100")
PROM_URL = os.getenv("PROM_URL", "http://prometheus.observability")
LOOKBACK_MINUTES = int(os.getenv("LOOKBACK_MINUTES", "5"))
RESOLVE_TIMEOUT_MINUTES = int(os.getenv("RESOLVE_TIMEOUT_MINUTES", "30"))
OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector.observability:4317")


def _incident_webhook_url() -> str:
    """Read the incident webhook URL at request time (so tests can override it)."""
    return os.getenv("INCIDENT_WEBHOOK_URL", "")

# ---------------------------------------------------------------------------
# Pydantic models for the Alertmanager webhook payload
# ---------------------------------------------------------------------------


class AlertmanagerAlert(BaseModel):
    """One element of the ``alerts`` array in the Alertmanager payload."""

    status: str
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: str
    endsAt: str | None = None
    generatorURL: str | None = None
    fingerprint: str | None = None


class AlertmanagerPayload(BaseModel):
    """Top-level Alertmanager webhook payload."""

    version: str = "4"
    groupKey: str | None = None
    status: str
    receiver: str | None = None
    groupLabels: dict[str, str] = Field(default_factory=dict)
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)
    externalURL: str | None = None
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)
    truncatedAlerts: int | None = None


class WebhookResponse(BaseModel):
    """JSON response returned from the webhook endpoint."""

    accepted: int
    incidents: list[str]
    postmortems: list[str]


# ---------------------------------------------------------------------------
# Application state container
# ---------------------------------------------------------------------------


class AppState:
    """Container for objects whose lifetime matches the FastAPI app."""

    def __init__(self) -> None:
        self.store = IncidentStore(default_db_path(), default_postmortem_dir())
        self.context: UpstreamContext = StaticUpstreamContext()  # placeholder
        self.client: httpx.AsyncClient | None = None
        self.chaos: ChaosSource | None = None
        self._background_task: asyncio.Task[None] | None = None


def _setup_otel_metrics() -> None:
    """Configure OTel meter provider with OTLP + Prometheus readers."""
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    resource = Resource.create(
        {
            "service.name": os.getenv("OTEL_SERVICE_NAME", "incident-engine"),
            "deployment.environment": "dev",
        }
    )
    readers: list = [PrometheusMetricReader()]
    try:
        exporter = OTLPMetricExporter(
            endpoint=OTLP_ENDPOINT,
            insecure=OTLP_ENDPOINT.startswith("http://"),
            timeout=5,
        )
        readers.append(PeriodicExportingMetricReader(exporter, export_interval_millis=30000))
        logger.info("OTLP metric exporter configured for %s", OTLP_ENDPOINT)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to create OTLP metric exporter: %s", exc)
    provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(provider)


# ---------------------------------------------------------------------------
# Resolution reconciliation background task
# ---------------------------------------------------------------------------


async def _resolve_reconciler(state: AppState) -> None:
    """Periodically mark stale incidents as ``resolved`` after timeout."""
    meter = metrics.get_meter(__name__)
    incidents_processed = meter.create_counter(
        "incident_engine_resolved_total",
        description="Number of incidents auto-resolved by the reconciler",
    )

    while True:
        try:
            now = datetime.now(UTC)
            cutoff = now - timedelta(minutes=RESOLVE_TIMEOUT_MINUTES)
            for record in state.store.list_all():
                if record.status != "firing":
                    continue
                starts_at = parse_iso(record.starts_at)
                if starts_at > cutoff:
                    continue
                record.status = "resolved"
                record.ends_at = now.isoformat()
                record.updated_at = now.isoformat()
                state.store.upsert(record)
                incidents_processed.add(1, {"alertname": record.alertname})
                logger.info(
                    "incident_resolved_timeout: id=%s alertname=%s service=%s",
                    record.id,
                    record.alertname,
                    record.service,
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("resolve_reconciler error: %s", exc)

        await asyncio.sleep(60)


def create_app(
    context: UpstreamContext | None = None,
    store: IncidentStore | None = None,
    chaos_source: ChaosSource | None = None,
) -> FastAPI:
    """Build a FastAPI instance — accepts overrides for tests."""
    app = FastAPI(
        title="incident-engine",
        description="Alertmanager webhook receiver that emits postmortem.md files.",
        version="0.1.0",
    )

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    LoggingInstrumentor().instrument(set_logging_format=True)

    @asynccontextmanager
    async def _custom_lifespan(app_ref: FastAPI) -> Any:
        """Lifecycle that honours explicit overrides for tests."""
        state = AppState()
        if store is not None:
            state.store = store
        if context is not None:
            state.context = context
        else:
            client = httpx.AsyncClient(timeout=10.0)
            state.client = client
            state.context = HTTPXUpstreamContext(
                tempo_url=TEMPO_URL,
                loki_url=LOKI_URL,
                prom_url=PROM_URL,
                client=client,
            )
        state.chaos = chaos_source or KubernetesChaosClient.from_cluster() or RegisteredChaosSource()
        app_ref.state.app_state = state
        try:
            yield
        finally:
            if state.context is not None and isinstance(state.context, HTTPXUpstreamContext):
                with contextlib.suppress(Exception):
                    await state.context.aclose()
            if state.client is not None:
                with contextlib.suppress(Exception):
                    await state.client.aclose()
            if isinstance(state.chaos, KubernetesChaosClient):
                with contextlib.suppress(Exception):
                    await state.chaos.aclose()

    app.router.lifespan_context = _custom_lifespan

    register_routes(app)
    return app


def get_state(request: Request) -> AppState:
    """FastAPI dependency that returns the current ``AppState``."""
    state: AppState | None = getattr(request.app.state, "app_state", None)
    if state is None:
        raise HTTPException(status_code=503, detail="app state not initialised")
    return state


# ---------------------------------------------------------------------------
# Webhook handler core
# ---------------------------------------------------------------------------


def _parse_alert(alert: AlertmanagerAlert) -> tuple[str, str, str, str, datetime, datetime | None, str]:
    """Extract (alertname, service, severity, slo, starts_at, ends_at, fingerprint) from an alert."""
    labels = alert.labels
    alertname = labels.get("alertname") or "UnknownAlert"
    service = labels.get("service") or labels.get("job") or labels.get("k8s_service") or "unknown"
    severity = labels.get("severity") or "warning"
    slo = labels.get("slo") or labels.get("sli") or ""
    starts_at = parse_iso(alert.startsAt)
    ends_at = parse_iso(alert.endsAt) if alert.endsAt else None
    fingerprint = alert.fingerprint or f"{alertname}:{service}:{starts_at.isoformat()}"
    return alertname, service, severity, slo, starts_at, ends_at, fingerprint


async def _process_alert(
    state: AppState,
    alert: AlertmanagerAlert,
    payload_status: str,
) -> tuple[IncidentRecord, list[TimelineEvent], str]:
    """Process a single alert and return (record, timeline, postmortem_md)."""
    alertname, service, severity, slo, starts_at, ends_at, fingerprint = _parse_alert(alert)

    lookback_end = ends_at or datetime.now(UTC)
    lookback_start = starts_at - timedelta(minutes=LOOKBACK_MINUTES)

    traces = await state.context.fetch_traces(service, lookback_start, lookback_end)
    logs = await state.context.fetch_logs(
        service,
        lookback_start,
        lookback_end,
        trace_ids=[trace.trace_id for trace in traces],
    )
    metrics_snapshots = await state.context.fetch_metrics(service, lookback_start, lookback_end)

    experiments = (
        await state.chaos.list_active(service, starts_at)
        if state.chaos is not None
        else _chaos_state_snapshot()
    )
    correlated = correlate_chaos(service, experiments, starts_at)
    incident_id = uuid.uuid5(
        uuid.NAMESPACE_OID,
        f"{fingerprint}:{starts_at.isoformat()}",
    ).hex
    existing = state.store.get(incident_id)
    authoritative_chaos = (
        existing.correlated_chaos
        if existing is not None and existing.correlated_chaos
        else correlated
    )
    timeline_events = build_timeline(
        alert_starts_at=starts_at,
        alert_ends_at=ends_at,
        service=service,
        alertname=alertname,
        traces=traces,
        logs=logs,
        metrics=metrics_snapshots,
        chaos_experiments=authoritative_chaos,
    )

    if payload_status == "resolved" or alert.status == "resolved" or ends_at is not None:
        record_status = "resolved"
    else:
        record_status = "firing"

    now = datetime.now(UTC).isoformat()
    if existing is not None:
        # Update mutable fields only; preserve created_at.
        existing.status = record_status
        existing.updated_at = now
        existing.ends_at = ends_at.isoformat() if ends_at else existing.ends_at
        previous_events = [TimelineEvent(**event) for event in existing.timeline]
        unique_events = {
            (event.timestamp, event.source, event.message): event
            for event in [*previous_events, *timeline_events]
        }
        timeline_events = sorted(unique_events.values(), key=lambda event: event.timestamp)
        existing.timeline = [event.to_dict() for event in timeline_events]
        existing.correlated_chaos = authoritative_chaos
        record = existing
    else:
        record = IncidentRecord(
            id=incident_id,
            alertname=alertname,
            service=service,
            severity=severity,
            slo=slo,
            status=record_status,
            starts_at=starts_at.isoformat(),
            ends_at=ends_at.isoformat() if ends_at else None,
            fingerprint=fingerprint,
            created_at=now,
            updated_at=now,
            summary="",  # filled below
            file_path=build_postmortem_path(alertname, starts_at.isoformat()),
            annotations=dict(alert.annotations),
            labels=dict(alert.labels),
            timeline=[event.to_dict() for event in timeline_events],
            correlated_chaos=authoritative_chaos,
        )

    record.summary = build_summary(record)
    state.store.upsert(record)
    markdown = render_postmortem(record, timeline_events)
    state.store.write_postmortem(record, markdown)
    return record, timeline_events, markdown


# ---------------------------------------------------------------------------
# Chaos experiment lookup (in-memory cache for now; can be extended to call
# chaos-mesh's HTTP API in a follow-up).
# ---------------------------------------------------------------------------

_chaos_experiments: list[dict[str, Any]] = []


def register_chaos_experiment(experiment: dict[str, Any]) -> None:
    """Add a chaos experiment to the in-memory correlation cache."""
    _chaos_experiments.append(experiment)


def _chaos_state_snapshot() -> list[dict[str, Any]]:
    """Return a copy of the current chaos experiment cache."""
    return list(_chaos_experiments)


class RegisteredChaosSource:
    """Local-only source retained for deterministic offline tests."""

    async def list_active(self, service: str, alert_time: datetime) -> list[dict[str, Any]]:
        del service, alert_time
        return _chaos_state_snapshot()


def reset_chaos_state() -> None:  # pragma: no cover - test helper
    """Clear the chaos experiment cache."""
    _chaos_experiments.clear()


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


def register_routes(app: FastAPI) -> None:
    """Register the FastAPI routes on the given app."""

    @app.get("/healthz")
    async def healthz(state: Annotated[AppState, Depends(get_state)]) -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(state: Annotated[AppState, Depends(get_state)]) -> dict[str, str]:
        """Readiness probe."""
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics_endpoint() -> JSONResponse:
        """Prometheus scrape endpoint."""
        return JSONResponse(content=generate_latest().decode("utf-8"), media_type=CONTENT_TYPE_LATEST)

    @app.post("/webhook", response_model=WebhookResponse)
    async def webhook(payload: AlertmanagerPayload, state: Annotated[AppState, Depends(get_state)]) -> WebhookResponse:
        """Accept an Alertmanager webhook payload and create incidents."""
        if not payload.alerts:
            return WebhookResponse(accepted=0, incidents=[], postmortems=[])

        accepted_ids: list[str] = []
        postmortem_paths: list[str] = []
        for alert in payload.alerts:
            try:
                record, _events, _markdown = await _process_alert(state, alert, payload.status)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("alert processing failed: %s", exc)
                continue
            accepted_ids.append(record.id)
            postmortem_paths.append(record.file_path)

            incident_webhook = _incident_webhook_url()
            if incident_webhook:
                await state.context.notify_webhook(
                    incident_webhook,
                    {
                        "alertname": record.alertname,
                        "service": record.service,
                        "severity": record.severity,
                        "status": record.status,
                        "postmortem": record.file_path,
                        "id": record.id,
                    },
                )

        return WebhookResponse(
            accepted=len(accepted_ids),
            incidents=accepted_ids,
            postmortems=postmortem_paths,
        )

    @app.get("/incidents")
    async def list_incidents(state: Annotated[AppState, Depends(get_state)]) -> list[dict[str, Any]]:
        """Return a summary list of every incident in storage."""
        records = state.store.list_all()
        summaries: list[dict[str, Any]] = []
        for record in records:
            timeline = [TimelineEvent(**event) for event in record.timeline]
            summaries.append(to_summary_payload(record, timeline))
        return summaries

    @app.get("/incidents/{incident_id}")
    async def get_incident(incident_id: str, state: Annotated[AppState, Depends(get_state)]) -> dict[str, Any]:
        """Return a single incident + its postmortem markdown body."""
        record = state.store.get(incident_id)
        if record is None:
            raise HTTPException(status_code=404, detail="incident not found")

        timeline = [TimelineEvent(**event) for event in record.timeline]
        body = render_postmortem(record, timeline)
        if not has_all_sections(body):
            logger.warning("postmortem %s is missing required sections", record.id)
        return {
            **to_summary_payload(record, timeline),
            "postmortem": body,
        }


# Build the application instance used by uvicorn.
app = create_app()


def main() -> None:  # pragma: no cover - manual launch helper
    """Launch uvicorn with the default config (used by the CLI / Docker CMD)."""
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8088, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = [
    "app",
    "create_app",
    "AlertmanagerPayload",
    "AlertmanagerAlert",
    "WebhookResponse",
    "register_chaos_experiment",
    "reset_chaos_state",
    "_process_alert",
    "_chaos_state_snapshot",
]
