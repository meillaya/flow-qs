"""Cart-service HTTP client.

Talks to the cart-service REST API:
  GET  /cart/{user_id}   -> CartView
  GET  /healthz         -> 200
"""
from __future__ import annotations

import logging
from typing import Protocol

import httpx

from ..config import settings
from ..models import CartView

logger = logging.getLogger(__name__)


class CartClient(Protocol):
    async def get_cart(self, user_id: str) -> CartView: ...
    async def health(self) -> bool: ...


class HttpxCartClient:
    """Async HTTP client for cart-service.

    Uses a single shared AsyncClient to benefit from httpx connection pooling
    and the OTel httpx auto-instrumentation.
    """

    def __init__(
        self,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = (base_url or settings.cart_service_url).rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=settings.http_timeout_seconds,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_cart(self, user_id: str) -> CartView:
        resp = await self._client.get(f"{self._base_url}/cart/{user_id}")
        resp.raise_for_status()
        body = resp.json()
        return CartView.model_validate(body)

    async def health(self) -> bool:
        try:
            resp = await self._client.get(f"{self._base_url}/healthz")
            return resp.status_code == 200
        except httpx.HTTPError as exc:
            logger.warning("cart health: %s", exc)
            return False
