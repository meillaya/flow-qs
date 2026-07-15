from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from app.http_metrics import EXPLICIT_BOUNDARIES, HTTPMetrics


def _histogram_data(reader: InMemoryMetricReader):
    metrics_data = reader.get_metrics_data()
    return next(
        metric.data
        for resource in metrics_data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "http.server.request.duration"
    )


def test_http_metrics_records_canonical_histogram_with_route_template(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    reader = InMemoryMetricReader()
    runtime = HTTPMetrics(service_name="test-service", metric_reader=reader)
    app = FastAPI()
    app.middleware("http")(runtime.middleware)

    @app.get("/widgets/{widget_id}")
    async def widget(widget_id: str) -> Response:
        del widget_id
        return Response(status_code=503)

    with TestClient(app) as client:
        response = client.get("/widgets/secret-id")

    assert response.status_code == 503
    points = _histogram_data(reader).data_points
    assert len(points) == 1
    point = points[0]
    assert point.count == 1
    assert point.sum >= 0
    assert point.explicit_bounds == EXPLICIT_BOUNDARIES
    assert point.attributes == {
        "service_name": "test-service",
        "http_response_status_code": "503",
        "http_route": "/widgets/{widget_id}",
    }
    runtime.shutdown()


def test_http_metrics_excludes_probe_and_metrics_routes(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    reader = InMemoryMetricReader()
    runtime = HTTPMetrics(service_name="test-service", metric_reader=reader)
    app = FastAPI()
    app.middleware("http")(runtime.middleware)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> str:
        return ""

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
        assert client.get("/metrics").status_code == 200

    assert reader.get_metrics_data() is None
    runtime.shutdown()
