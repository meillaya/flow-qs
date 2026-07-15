from __future__ import annotations

import os
import time
from typing import Final

from fastapi import Request, Response
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from starlette.middleware.base import RequestResponseEndpoint

EXPLICIT_BOUNDARIES: Final[tuple[float, ...]] = (
    0.05,
    0.1,
    0.15,
    0.2,
    0.3,
    0.5,
    1.0,
    2.0,
    2.5,
    5.0,
    10.0,
)
EXCLUDED_ROUTES: Final[frozenset[str]] = frozenset({"/healthz", "/readyz", "/metrics"})


class HTTPMetrics:
    def __init__(self, service_name: str, metric_reader: MetricReader | None = None) -> None:
        readers: list[MetricReader] = []
        if metric_reader is not None:
            readers.append(metric_reader)
        elif os.getenv("OTEL_SDK_DISABLED", "").lower() not in {"true", "1"}:
            endpoint = os.getenv(
                "OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector.observability:4317"
            )
            exporter = OTLPMetricExporter(
                endpoint=endpoint,
                insecure=endpoint.startswith("http://"),
                timeout=2,
            )
            readers.append(
                PeriodicExportingMetricReader(
                    exporter,
                    export_interval_millis=30_000,
                    export_timeout_millis=2_000,
                )
            )
        self._provider = MeterProvider(
            resource=Resource.create({"service.name": service_name}),
            metric_readers=readers,
            views=[
                View(
                    instrument_name="http.server.request.duration",
                    aggregation=ExplicitBucketHistogramAggregation(EXPLICIT_BOUNDARIES),
                    attribute_keys={
                        "service_name",
                        "http_response_status_code",
                        "http_route",
                    },
                )
            ],
            shutdown_on_exit=False,
        )
        self._histogram = self._provider.get_meter(__name__).create_histogram(
            "http.server.request.duration",
            unit="s",
            description="Inbound HTTP request duration",
        )
        self._service_name = service_name
        self._shutdown = False

    async def middleware(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in EXCLUDED_ROUTES:
            return await call_next(request)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
            self._record(request, 500, time.perf_counter() - started)
            raise
        self._record(request, response.status_code, time.perf_counter() - started)
        return response

    def _record(self, request: Request, status_code: int, duration_seconds: float) -> None:
        route = request.scope.get("route")
        route_template = getattr(route, "path", "unmatched")
        self._histogram.record(
            duration_seconds,
            {
                "service_name": self._service_name,
                "http_response_status_code": str(status_code),
                "http_route": route_template,
            },
        )

    def shutdown(self) -> None:
        if not self._shutdown:
            self._provider.shutdown(timeout_millis=2_000)
            self._shutdown = True
