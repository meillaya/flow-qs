"""order-service HTTP client.

Posts a paid order after the payment is captured. The order-service owns its
own row in the order DB; checkout just hands over the canonical record.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

from ..config import settings
from ..models import OrderView

logger = logging.getLogger(__name__)


class OrderClient(Protocol):
    async def create_order(self, payload: dict[str, Any]) -> OrderView: ...
    async def health(self) -> bool: ...


class HttpxOrderClient:
    def __init__(
        self,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = (base_url or settings.order_service_url).rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=settings.http_timeout_seconds,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_order(self, payload: dict[str, Any]) -> OrderView:
        resp = await self._client.post(f"{self._base_url}/orders", json=payload)
        resp.raise_for_status()
        return OrderView.model_validate(resp.json())

    async def health(self) -> bool:
        try:
            resp = await self._client.get(f"{self._base_url}/healthz")
            return resp.status_code == 200
        except httpx.HTTPError as exc:
            logger.warning("order health: %s", exc)
            return False
