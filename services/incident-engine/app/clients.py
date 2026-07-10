"""HTTP clients for Tempo, Loki, Prometheus and the optional incident webhook."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)


@dataclass
class TraceHit:
    """A single trace reference returned from Tempo."""

    trace_id: str
    root_service: str = ""
    duration_ms: float = 0.0
    start_time: str = ""


@dataclass
class LogHit:
    """A single log line returned from Loki."""

    timestamp: str
    line: str
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class MetricSnapshot:
    """Prometheus metric snapshot used in postmortems."""

    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    timestamp: str = ""


class UpstreamContext(Protocol):
    """Protocol for the upstream context fetcher used by main.py."""

    async def fetch_traces(self, service: str, start: datetime, end: datetime, limit: int = 10) -> list[TraceHit]: ...
    async def fetch_logs(self, service: str, start: datetime, end: datetime, limit: int = 20) -> list[LogHit]: ...
    async def fetch_metrics(self, service: str, start: datetime, end: datetime) -> list[MetricSnapshot]: ...
    async def notify_webhook(self, url: str, payload: dict[str, Any]) -> bool: ...


class HTTPXUpstreamContext:
    """Default upstream context implementation backed by httpx.AsyncClient."""

    def __init__(
        self,
        tempo_url: str,
        loki_url: str,
        prom_url: str,
        timeout: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.tempo_url = tempo_url.rstrip("/")
        self.loki_url = loki_url.rstrip("/")
        self.prom_url = prom_url.rstrip("/")
        self.timeout = timeout
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        """Close the underlying httpx client if we created it."""
        if self._owns_client:
            await self._client.aclose()

    async def fetch_traces(self, service: str, start: datetime, end: datetime, limit: int = 10) -> list[TraceHit]:
        """Query Tempo's search API for traces matching `service.name=<service>`."""
        url = f"{self.tempo_url}/api/search"
        params = {
            "tags": f"service.name={service}",
            "limit": str(limit),
            "start": str(int(start.timestamp())),
            "end": str(int(end.timestamp())),
        }
        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("tempo fetch failed: %s", exc)
            return []

        hits: list[TraceHit] = []
        for trace in data.get("traces", []) or [][:limit]:
            trace_id = str(trace.get("traceID") or trace.get("traceId") or "")
            if not trace_id:
                continue
            root = trace.get("rootServiceName") or ""
            duration_ms = float(trace.get("duration") or 0) / 1_000_000.0
            start_time_ms = int(trace.get("startTime") or 0)
            start_iso = (
                datetime.fromtimestamp(start_time_ms / 1000.0, tz=UTC).isoformat()
                if start_time_ms
                else ""
            )
            hits.append(
                TraceHit(
                    trace_id=trace_id,
                    root_service=str(root),
                    duration_ms=duration_ms,
                    start_time=start_iso,
                )
            )
        return hits

    async def fetch_logs(self, service: str, start: datetime, end: datetime, limit: int = 20) -> list[LogHit]:
        """Query Loki's range query API for `{service="<service>"}` in the window."""
        url = f"{self.loki_url}/loki/api/v1/query_range"
        params = {
            "query": f'{{service="{service}"}}',
            "start": str(int(start.timestamp() * 1_000_000_000)),
            "end": str(int(end.timestamp() * 1_000_000_000)),
            "limit": str(limit),
            "direction": "backward",
        }
        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("loki fetch failed: %s", exc)
            return []

        hits: list[LogHit] = []
        streams = (data.get("data") or {}).get("result") or []
        for stream in streams:
            labels = stream.get("stream") or {}
            for entry in stream.get("values") or []:
                if len(entry) < 2:
                    continue
                ts_ns, line = entry[0], entry[1]
                try:
                    ts_iso = datetime.fromtimestamp(int(ts_ns) / 1_000_000_000, tz=UTC).isoformat()
                except (TypeError, ValueError):
                    ts_iso = ""
                hits.append(LogHit(timestamp=ts_iso, line=str(line), labels=dict(labels)))
                if len(hits) >= limit:
                    return hits
        return hits

    async def fetch_metrics(self, service: str, start: datetime, end: datetime) -> list[MetricSnapshot]:
        """Query Prometheus for error rate and latency p99 at the alert time."""
        step = "30s"
        snapshots: list[MetricSnapshot] = []

        queries = {
            "error_rate": (
                f'sum(rate({{__name__=~"http_requests_total"}},service="{service}")[1m])'
            ),
            "latency_p99": (
                f'histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket{{service="{service}"}}[1m])))'
            ),
        }

        for name, query in queries.items():
            params = {
                "query": query,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "step": step,
            }
            url = f"{self.prom_url}/api/v1/query_range"
            try:
                response = await self._client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("prometheus fetch failed (%s): %s", name, exc)
                continue
            results = (data.get("data") or {}).get("result") or []
            for series in results:
                metric_labels = series.get("metric") or {}
                values = series.get("values") or []
                if not values:
                    continue
                # Pick the value closest to the alert start.
                _, raw_value = values[len(values) // 2]
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    continue
                snapshots.append(
                    MetricSnapshot(
                        name=name,
                        value=value,
                        labels=dict(metric_labels),
                        timestamp=start.isoformat(),
                    )
                )
        return snapshots

    async def notify_webhook(self, url: str, payload: dict[str, Any]) -> bool:
        """POST a JSON payload to the configured Slack/Discord webhook URL."""
        if not url:
            return False
        try:
            response = await self._client.post(url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.warning("incident webhook POST failed: %s", exc)
            return False


class StaticUpstreamContext:
    """In-memory context for tests and offline operation."""

    def __init__(
        self,
        traces: list[TraceHit] | None = None,
        logs: list[LogHit] | None = None,
        metrics: list[MetricSnapshot] | None = None,
    ) -> None:
        self.traces = traces or []
        self.logs = logs or []
        self.metrics = metrics or []
        self.webhook_calls: list[tuple[str, dict[str, Any]]] = []

    async def fetch_traces(self, service: str, start: datetime, end: datetime, limit: int = 10) -> list[TraceHit]:
        return list(self.traces)[:limit]

    async def fetch_logs(self, service: str, start: datetime, end: datetime, limit: int = 20) -> list[LogHit]:
        return list(self.logs)[:limit]

    async def fetch_metrics(self, service: str, start: datetime, end: datetime) -> list[MetricSnapshot]:
        return list(self.metrics)

    async def notify_webhook(self, url: str, payload: dict[str, Any]) -> bool:
        self.webhook_calls.append((url, payload))
        return True


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp into a tz-aware datetime."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
