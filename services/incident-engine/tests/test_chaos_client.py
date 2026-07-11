"""Chaos Mesh Kubernetes API discovery and webhook correlation tests."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.chaos import KubernetesChaosClient

BASE = "https://kubernetes.default.svc"


def _resource(kind: str, name: str, service: str, *, timestamp: str = "2026-07-11T02:55:00Z") -> dict:
    return {
        "apiVersion": "chaos-mesh.org/v1alpha1",
        "kind": kind,
        "metadata": {"name": name, "namespace": "chaos-testing", "creationTimestamp": timestamp},
        "spec": {
            "selector": {
                "namespaces": ["ecom"],
                "labelSelectors": {"app": service},
            },
            "duration": "5m",
            "mode": "one",
        },
        "status": {"conditions": [{"type": "Selected", "status": "True"}]},
    }


@pytest.mark.asyncio
@respx.mock
async def test_lists_active_relevant_catalog_and_checkout_experiments() -> None:
    resources = {
        "podchaos": [_resource("PodChaos", "kill-catalog", "catalog-service")],
        "networkchaos": [_resource("NetworkChaos", "delay-checkout", "checkout-service")],
    }
    for plural in KubernetesChaosClient.RESOURCE_PLURALS:
        respx.get(
            f"{BASE}/apis/chaos-mesh.org/v1alpha1/namespaces/chaos-testing/{plural}"
        ).respond(200, json={"items": resources.get(plural, [])})
    async with httpx.AsyncClient() as http:
        client = KubernetesChaosClient(BASE, "token", http)
        catalog = await client.list_active("catalog-service", datetime.now(UTC))
        checkout = await client.list_active("checkout-service", datetime.now(UTC))

    assert [(item["kind"], item["name"]) for item in catalog] == [("PodChaos", "kill-catalog")]
    assert catalog[0]["target"] == "catalog-service"
    assert catalog[0]["start_time"] == "2026-07-11T02:55:00Z"
    assert catalog[0]["spec"]["duration"] == "5m"
    assert [(item["kind"], item["name"]) for item in checkout] == [
        ("NetworkChaos", "delay-checkout")
    ]


@pytest.mark.asyncio
@respx.mock
async def test_returns_no_match_and_skips_finished_or_malformed_objects() -> None:
    finished = _resource("StressChaos", "old-stress", "catalog-service")
    finished["status"] = {"conditions": [{"type": "AllRecovered", "status": "True"}]}
    for plural in KubernetesChaosClient.RESOURCE_PLURALS:
        items = [finished, {"kind": "PodChaos", "metadata": {}}] if plural == "stresschaos" else []
        respx.get(
            f"{BASE}/apis/chaos-mesh.org/v1alpha1/namespaces/chaos-testing/{plural}"
        ).respond(200, json={"items": items})
    async with httpx.AsyncClient() as http:
        client = KubernetesChaosClient(BASE, "token", http)
        result = await client.list_active("order-service", datetime.now(UTC))
    assert result == []


@pytest.mark.asyncio
@respx.mock
async def test_api_unavailable_degrades_to_empty_correlation(caplog) -> None:
    for plural in KubernetesChaosClient.RESOURCE_PLURALS:
        respx.get(
            f"{BASE}/apis/chaos-mesh.org/v1alpha1/namespaces/chaos-testing/{plural}"
        ).mock(side_effect=httpx.ConnectError("kubernetes unavailable"))
    async with httpx.AsyncClient() as http:
        client = KubernetesChaosClient(BASE, "token", http)
        result = await client.list_active("checkout-service", datetime.now(UTC))
    assert result == []
    assert "Chaos Mesh API unavailable" in caplog.text


@pytest.mark.asyncio
async def test_webhook_fetches_live_chaos_and_renders_kind_name_timing_spec(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from app.clients import StaticUpstreamContext
    from app.main import AlertmanagerAlert, AlertmanagerPayload, create_app
    from app.storage import IncidentStore

    experiment = {
        "kind": "PodChaos",
        "name": "kill-checkout-live",
        "namespace": "chaos-testing",
        "start_time": "2026-07-09T20:58:00+00:00",
        "target": "checkout-service",
        "spec": {"duration": "5m", "mode": "one"},
    }

    class FakeChaosSource:
        async def list_active(self, service: str, alert_time: datetime) -> list[dict]:
            assert service == "checkout-service"
            assert alert_time.tzinfo is not None
            return [experiment]

    monkeypatch.setenv("INCIDENT_DB_PATH", str(tmp_path / "default.db"))
    monkeypatch.setenv("POSTMORTEM_DIR", str(tmp_path / "default-postmortems"))
    store = IncidentStore(tmp_path / "incidents.db", tmp_path / "postmortems")
    app = create_app(
        context=StaticUpstreamContext(),
        store=store,
        chaos_source=FakeChaosSource(),
    )
    payload = AlertmanagerPayload(
        status="firing",
        alerts=[
            AlertmanagerAlert(
                status="firing",
                labels={"alertname": "CheckoutChaos", "service": "checkout-service"},
                annotations={},
                startsAt="2026-07-09T21:00:00+00:00",
                endsAt="2026-07-09T21:07:00+00:00",
                fingerprint="checkout-chaos",
            )
        ],
    )
    with TestClient(app) as client:
        response = client.post("/webhook", json=payload.model_dump())
        incident = client.get(f"/incidents/{response.json()['incidents'][0]}").json()

    markdown = incident["postmortem"]
    assert "kill-checkout-live" in markdown
    assert "PodChaos" in markdown
    assert "namespace `chaos-testing`" in markdown
    assert "2026-07-09T20:58:00+00:00" in markdown
    assert '"duration": "5m"' in markdown


def test_chart_grants_read_only_chaos_mesh_access() -> None:
    import subprocess
    from pathlib import Path

    import yaml

    chart = Path(__file__).resolve().parents[3] / "charts" / "incident-engine"
    rendered = subprocess.run(  # noqa: S603
        ["helm", "template", "incident-engine", str(chart), "-n", "incident"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    ).stdout
    documents = [document for document in yaml.safe_load_all(rendered) if document]
    role = next(document for document in documents if document["kind"] == "Role")
    binding = next(document for document in documents if document["kind"] == "RoleBinding")

    assert role["metadata"]["namespace"] == "chaos-testing"
    assert role["rules"][0]["apiGroups"] == ["chaos-mesh.org"]
    assert set(role["rules"][0]["verbs"]) == {"get", "list", "watch"}
    assert set(role["rules"][0]["resources"]) == {
        "podchaos",
        "networkchaos",
        "stresschaos",
        "dnschaos",
        "schedules",
        "workflows",
    }
    assert binding["subjects"][0]["namespace"] == "incident"


def test_demo_network_chaos_prefers_primary_selector_over_peer_target() -> None:
    from pathlib import Path

    import yaml

    manifest = Path(__file__).resolve().parents[3] / "chaos" / "experiments" / "demo-alert-checkout.yaml"
    raw = yaml.safe_load(manifest.read_text())
    raw["metadata"]["creationTimestamp"] = "2026-07-11T05:30:00Z"
    experiment = KubernetesChaosClient._parse_experiment(raw)

    assert experiment is not None
    assert experiment["name"] == "demo-alert-checkout"
    assert experiment["kind"] == "NetworkChaos"
    assert experiment["target"] == "checkout-service"

    from app.postmortem import render_postmortem
    from app.storage import IncidentRecord

    record = IncidentRecord(
        id="demo",
        alertname="ServiceDown",
        service="checkout-service",
        severity="critical",
        slo="availability",
        status="firing",
        starts_at="2026-07-11T05:31:00Z",
        ends_at=None,
        fingerprint="demo",
        created_at="2026-07-11T05:31:00Z",
        updated_at="2026-07-11T05:31:00Z",
        summary="demo",
        file_path="/tmp/demo.md",
        correlated_chaos=[experiment],
    )
    markdown = render_postmortem(record, [])
    assert "**demo-alert-checkout** (NetworkChaos)" in markdown
    assert "targeting `checkout-service`" in markdown
    assert "targeting `synthetic-monitor`" not in markdown


def test_selector_fallback_recurses_for_peer_and_schedule_workflow_shapes() -> None:
    assert KubernetesChaosClient._target_from_spec(
        {"target": {"selector": {"labelSelectors": {"app": "peer-service"}}}}
    ) == "peer-service"
    assert KubernetesChaosClient._target_from_spec(
        {
            "schedule": "@every 1m",
            "type": "PodChaos",
            "podChaos": {
                "selector": {
                    "labelSelectors": {"app.kubernetes.io/name": "scheduled-service"}
                }
            },
        }
    ) == "scheduled-service"
    assert KubernetesChaosClient._target_from_spec({"mode": "all"}) == ""
