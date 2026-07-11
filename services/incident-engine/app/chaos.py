"""Read-only Chaos Mesh discovery through the in-cluster Kubernetes API."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)


class ChaosSource(Protocol):
    async def list_active(self, service: str, alert_time: datetime) -> list[dict[str, Any]]: ...


class KubernetesChaosClient:
    """Minimal authenticated client for namespaced Chaos Mesh resources."""

    RESOURCE_PLURALS = (
        "podchaos",
        "networkchaos",
        "stresschaos",
        "dnschaos",
        "schedules",
        "workflows",
    )

    def __init__(
        self,
        base_url: str,
        token: str,
        client: httpx.AsyncClient,
        *,
        namespace: str = "chaos-testing",
        owns_client: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.client = client
        self.namespace = namespace
        self.owns_client = owns_client

    @classmethod
    def from_cluster(cls) -> KubernetesChaosClient | None:
        host = os.getenv("KUBERNETES_SERVICE_HOST")
        if not host:
            return None
        port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        token_path = Path(
            os.getenv(
                "KUBERNETES_SERVICEACCOUNT_TOKEN_FILE",
                "/var/run/secrets/kubernetes.io/serviceaccount/token",
            )
        )
        ca_path = Path(
            os.getenv(
                "KUBERNETES_SERVICEACCOUNT_CA_FILE",
                "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
            )
        )
        try:
            token = token_path.read_text().strip()
        except OSError as exc:
            logger.warning("Chaos Mesh API disabled: service account token unavailable: %s", exc)
            return None
        verify: str | bool = str(ca_path) if ca_path.is_file() else True
        client = httpx.AsyncClient(verify=verify, timeout=5)
        namespace = os.getenv("CHAOS_NAMESPACE", "chaos-testing")
        return cls(f"https://{host}:{port}", token, client, namespace=namespace, owns_client=True)

    async def aclose(self) -> None:
        if self.owns_client:
            await self.client.aclose()

    async def list_active(self, service: str, alert_time: datetime) -> list[dict[str, Any]]:
        del alert_time
        found: list[dict[str, Any]] = []
        for plural in self.RESOURCE_PLURALS:
            url = (
                f"{self.base_url}/apis/chaos-mesh.org/v1alpha1/"
                f"namespaces/{self.namespace}/{plural}"
            )
            try:
                response = await self.client.get(
                    url,
                    headers={"Authorization": f"Bearer {self.token}"},
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("Chaos Mesh API unavailable for %s: %s", plural, exc)
                continue
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                logger.warning("Chaos Mesh API returned malformed %s list", plural)
                continue
            for item in items:
                experiment = self._parse_experiment(item)
                if experiment is not None and self._matches_service(experiment, service):
                    found.append(experiment)
        return found

    @staticmethod
    def _parse_experiment(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        metadata = raw.get("metadata")
        spec = raw.get("spec")
        kind = raw.get("kind")
        if (
            not isinstance(metadata, dict)
            or not isinstance(spec, dict)
            or not isinstance(kind, str)
        ):
            return None
        name = metadata.get("name")
        start_time = metadata.get("creationTimestamp")
        if not isinstance(name, str) or not isinstance(start_time, str):
            return None
        if metadata.get("deletionTimestamp"):
            return None
        status = raw.get("status")
        if isinstance(status, dict):
            conditions = status.get("conditions")
            if isinstance(conditions, list):
                for condition in conditions:
                    if (
                        isinstance(condition, dict)
                        and condition.get("type") in {"AllRecovered", "Paused"}
                        and condition.get("status") == "True"
                    ):
                        return None
        target = KubernetesChaosClient._target_from_spec(spec)
        return {
            "kind": kind,
            "name": name,
            "namespace": metadata.get("namespace", "chaos-testing"),
            "start_time": start_time,
            "target": target,
            "spec": spec,
        }

    @staticmethod
    def _target_from_spec(spec: dict[str, Any]) -> str:
        primary = KubernetesChaosClient._service_label(spec.get("selector"))
        if primary:
            return primary

        pending: list[Any] = []
        target = spec.get("target")
        if target is not None:
            pending.append(target)
        pending.extend(value for key, value in spec.items() if key not in {"selector", "target"})
        while pending:
            candidate = pending.pop(0)
            service = KubernetesChaosClient._service_label(candidate)
            if service:
                return service
            if isinstance(candidate, dict):
                pending.extend(candidate.values())
            elif isinstance(candidate, list):
                pending.extend(candidate)
        return ""

    @staticmethod
    def _service_label(candidate: Any) -> str:
        if not isinstance(candidate, dict):
            return ""
        labels = candidate.get("labelSelectors")
        if not isinstance(labels, dict):
            return ""
        for key in ("app", "app.kubernetes.io/name"):
            value = labels.get(key)
            if isinstance(value, str):
                return value
        return ""

    @staticmethod
    def _matches_service(experiment: dict[str, Any], service: str) -> bool:
        target = experiment.get("target")
        return isinstance(target, str) and service in target
