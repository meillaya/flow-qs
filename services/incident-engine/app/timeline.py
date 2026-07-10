"""Timeline builder that fuses alerts, traces, logs and chaos experiments."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .clients import LogHit, MetricSnapshot, TraceHit

logger = logging.getLogger(__name__)


@dataclass
class TimelineEvent:
    """A single ordered event on an incident timeline."""

    timestamp: str
    source: str
    message: str
    severity: str = "info"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "source": self.source,
            "message": self.message,
            "severity": self.severity,
            "details": self.details,
        }


def _iso(value: datetime) -> str:
    """Return an ISO-8601 UTC string for a datetime."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def build_timeline(
    alert_starts_at: datetime,
    alert_ends_at: datetime | None,
    service: str,
    alertname: str,
    traces: Iterable[TraceHit],
    logs: Iterable[LogHit],
    metrics: Iterable[MetricSnapshot],
    chaos_experiments: Iterable[dict[str, Any]] | None = None,
) -> list[TimelineEvent]:
    """Build a deterministic, time-ordered timeline for the incident."""
    events: list[TimelineEvent] = []

    events.append(
        TimelineEvent(
            timestamp=_iso(alert_starts_at),
            source="alertmanager",
            message=f"Alert '{alertname}' started firing for service '{service}'",
            severity="warning",
            details={"alertname": alertname, "service": service},
        )
    )

    if alert_ends_at is not None:
        events.append(
            TimelineEvent(
                timestamp=_iso(alert_ends_at),
                source="alertmanager",
                message=f"Alert '{alertname}' resolved for service '{service}'",
                severity="info",
                details={"alertname": alertname, "service": service},
            )
        )

    for trace in traces:
        if not trace.start_time:
            continue
        events.append(
            TimelineEvent(
                timestamp=trace.start_time,
                source="tempo",
                message=f"Trace observed for service '{trace.root_service or service}' (duration {trace.duration_ms:.1f}ms)",
                severity="info",
                details={"trace_id": trace.trace_id, "duration_ms": trace.duration_ms},
            )
        )

    for log in logs:
        if not log.timestamp:
            continue
        snippet = log.line if len(log.line) < 200 else f"{log.line[:197]}..."
        events.append(
            TimelineEvent(
                timestamp=log.timestamp,
                source="loki",
                message=f"Log entry: {snippet}",
                severity="info",
                details={"labels": log.labels, "line": log.line},
            )
        )

    for snap in metrics:
        events.append(
            TimelineEvent(
                timestamp=snap.timestamp or _iso(alert_starts_at),
                source="prometheus",
                message=f"Metric snapshot: {snap.name}={snap.value}",
                severity="warning" if snap.name == "error_rate" and snap.value > 0 else "info",
                details={"labels": snap.labels, "value": snap.value},
            )
        )

    for chaos in chaos_experiments or []:
        start_raw = chaos.get("start_time")
        if not start_raw:
            continue
        try:
            chaos_start = _parse_loose_iso(start_raw)
        except ValueError:
            continue
        # Only include chaos experiments whose start time falls inside the incident window.
        window_start = alert_starts_at - timedelta(minutes=15)
        window_end = alert_ends_at or (alert_starts_at + timedelta(minutes=30))
        if not (window_start <= chaos_start <= window_end):
            continue
        events.append(
            TimelineEvent(
                timestamp=_iso(chaos_start),
                source="chaos-mesh",
                message=(
                    f"Chaos experiment '{chaos.get('name', 'unknown')}' "
                    f"of kind '{chaos.get('kind', 'unknown')}' applied to "
                    f"'{chaos.get('target', service)}'"
                ),
                severity="warning",
                details=chaos,
            )
        )

    events.sort(key=lambda e: e.timestamp)
    return events


def correlate_chaos(
    service: str,
    chaos_experiments: Iterable[dict[str, Any]],
    alert_starts_at: datetime,
) -> list[dict[str, Any]]:
    """Return chaos experiments whose selector matches the affected service.

    Heuristic: a chaos experiment is considered correlated when:
    - its ``target`` field contains the service name, OR
    - the ``selector.service`` field equals the service, OR
    - the experiment was applied inside the lookback window before the alert.
    """
    correlated: list[dict[str, Any]] = []
    window_start = alert_starts_at - timedelta(minutes=30)

    for experiment in chaos_experiments:
        target = str(experiment.get("target") or "")
        selector_service = str(((experiment.get("selector") or {}).get("service")) or "")
        kinds = experiment.get("kind", "")
        if service in target or selector_service == service or service in kinds:
            correlated.append(experiment)
            continue
        start_raw = experiment.get("start_time")
        if not start_raw:
            continue
        try:
            experiment_start = _parse_loose_iso(start_raw)
        except ValueError:
            continue
        if window_start <= experiment_start <= alert_starts_at:
            correlated.append(experiment)

    return correlated


def _parse_loose_iso(value: str) -> datetime:
    """Parse a possibly-truncated ISO-8601 timestamp."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
