"""Deployment and HTTP contract tests for the in-cluster Prometheus Service."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
import yaml

from app import main
from app.clients import HTTPXUpstreamContext

PROMETHEUS_SERVICE_URL = "http://prometheus.observability"


def test_application_default_uses_prometheus_service_port() -> None:
    assert main.PROM_URL == PROMETHEUS_SERVICE_URL


def test_chart_renders_prometheus_service_port() -> None:
    chart = Path(__file__).resolve().parents[3] / "charts" / "incident-engine"
    rendered = subprocess.run(  # noqa: S603
        ["helm", "template", "incident-engine", str(chart)],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    ).stdout
    documents = [document for document in yaml.safe_load_all(rendered) if document]
    deployment = next(document for document in documents if document["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    environment = {item["name"]: item["value"] for item in container["env"]}

    assert environment["PROM_URL"] == PROMETHEUS_SERVICE_URL


@pytest.mark.asyncio
@respx.mock
async def test_prometheus_query_uses_service_port_80() -> None:
    route = respx.get(f"{PROMETHEUS_SERVICE_URL}/api/v1/query_range").respond(
        200,
        json={"status": "success", "data": {"result": []}},
    )
    start = datetime.now(tz=UTC) - timedelta(minutes=5)
    end = datetime.now(tz=UTC)
    async with httpx.AsyncClient() as client:
        context = HTTPXUpstreamContext(
            tempo_url="http://tempo",
            loki_url="http://loki",
            prom_url=PROMETHEUS_SERVICE_URL,
            client=client,
        )
        result = await context.fetch_metrics("checkout-service", start, end)

    assert result == []
    assert route.called
    assert route.calls[0].request.url.port is None
    assert route.calls[0].request.url.netloc == b"prometheus.observability"
