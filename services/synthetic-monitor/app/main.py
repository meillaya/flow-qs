"""In-cluster synthetic monitor that polls /healthz on all ecom services."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Response
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

logger = logging.getLogger(__name__)

DEFAULT_TARGETS = (
    "catalog-service:8000",
    "inventory-service:8001",
    "cart-service:8002",
    "checkout-service:8003",
    "order-service:8004",
    "payment-gateway:8005",
)

POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "30"))
HEALTHZ_PATH = os.getenv("HEALTHZ_PATH", "/healthz")
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "5"))
FAILURE_THRESHOLD = int(os.getenv("FAILURE_THRESHOLD", "3"))
OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector.observability:4317")
OTLP_PROTOCOL = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")


def _parse_targets(raw: str | None) -> tuple[str, ...]:
    """Parse comma-separated service targets from env."""
    if not raw:
        return DEFAULT_TARGETS
    return tuple(item.strip() for item in raw.split(",") if item.strip())


TARGETS = _parse_targets(os.getenv("SYNTHETIC_TARGETS"))

# Shared mutable state for the observable gauge callback.
_last_results: dict[str, bool] = dict.fromkeys(TARGETS, False)


def _setup_otel_metrics() -> None:
    """Configure OTel meter provider with OTLP + Prometheus readers."""
    resource = Resource.create(
        {
            "service.name": os.getenv("OTEL_SERVICE_NAME", "synthetic-monitor"),
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


def _success_callback(options):
    """Observable gauge callback returning the last known success state."""
    for service, ok in _last_results.items():
        yield metrics.Observation(1 if ok else 0, {"service": service})


async def _poll_one(client: httpx.AsyncClient, url: str) -> tuple[bool, float]:
    """Return (success, duration_seconds) for a single healthz URL."""
    start = time.perf_counter()
    try:
        response = await client.get(url, timeout=REQUEST_TIMEOUT)
        ok = response.status_code == 200
    except Exception:
        ok = False
    elapsed = time.perf_counter() - start
    return ok, elapsed


async def _poll_loop() -> None:
    """Background loop that polls all targets every POLL_INTERVAL seconds."""
    meter = metrics.get_meter(__name__)
    meter.create_observable_gauge(
        "synthetic_check_success",
        description="1 if the last synthetic health check succeeded, 0 otherwise",
        unit="1",
        callbacks=[_success_callback],
    )
    duration_histogram = meter.create_histogram(
        "synthetic_check_duration_seconds",
        description="Duration of the synthetic health check",
        unit="s",
    )

    failure_counts: dict[str, int] = dict.fromkeys(TARGETS, 0)

    async with httpx.AsyncClient(
        follow_redirects=True,
        limits=httpx.Limits(max_connections=20),
    ) as client:
        while True:
            start_cycle = time.perf_counter()
            for target in TARGETS:
                url = f"http://{target}{HEALTHZ_PATH}"
                ok, elapsed = await _poll_one(client, url)
                _last_results[target] = ok
                duration_histogram.record(elapsed, {"service": target})

                if ok:
                    if failure_counts[target] >= FAILURE_THRESHOLD:
                        logger.info(
                            "synthetic_recovery: service=%s recovered after failure",
                            target,
                        )
                    failure_counts[target] = 0
                else:
                    failure_counts[target] += 1
                    if failure_counts[target] == FAILURE_THRESHOLD:
                        logger.error(
                            "synthetic_failure: service=%s failed %d consecutive health checks",
                            target,
                            FAILURE_THRESHOLD,
                            extra={"event": "synthetic_failure", "service": target},
                        )

            elapsed_cycle = time.perf_counter() - start_cycle
            sleep_for = max(0.0, POLL_INTERVAL - elapsed_cycle)
            await asyncio.sleep(sleep_for)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start the polling background task on startup."""
    _setup_otel_metrics()
    task = asyncio.create_task(_poll_loop())
    logger.info("Synthetic monitor started for targets: %s", TARGETS)
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Synthetic Monitor", lifespan=_lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness endpoint for Kubernetes probes."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    """Readiness endpoint for Kubernetes probes."""
    return {"status": "ok"}


@app.get("/metrics")
async def metrics_endpoint() -> Response:
    """Prometheus scrape endpoint (fallback when OTLP collector is unavailable)."""
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
