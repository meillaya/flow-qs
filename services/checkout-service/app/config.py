"""Runtime configuration for checkout-service.

All values are sourced from environment variables with sane defaults so the
service can boot in any environment (dev shell, kind/k3d, CI).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"env var {name} must be int, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        rate = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"env var {name} must be float, got {raw!r}") from exc
    if not 0.0 <= rate <= 1.0:
        raise RuntimeError(f"env var {name} must be in [0.0, 1.0], got {rate!r}")
    return rate


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    return raw.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # -- Service identity --
    service_name: str = "checkout-service"
    http_host: str = "0.0.0.0"
    http_port: int = 8003

    # -- Downstream service addresses --
    # HTTP services
    cart_service_url: str = "http://cart-service:8000"
    order_service_url: str = "http://order-service:8004"

    # gRPC services
    inventory_grpc_addr: str = "inventory-service:50051"
    payment_grpc_addr: str = "payment-gateway:50052"

    # gRPC dial deadline in seconds (no retry; chaos shows failures)
    grpc_timeout_seconds: float = 5.0

    # HTTP call timeout
    http_timeout_seconds: float = 5.0

    # -- Failure injection (chaos) --
    # Probability in [0.0, 1.0] of randomly failing a checkout request before
    # any downstream call is made. Default 0.0 disables the feature flag.
    failure_injection_rate: float = 0.0
    # When set, the random failure is deterministic per process (used by tests).
    failure_injection_seed: int = 0

    # -- Tracing --
    # OTLP endpoint - injected by otel-distro but exposed here for tests that
    # want to point at an in-process collector.
    otlp_endpoint: str = "http://otel-collector:4317"
    enable_otlp_exporter: bool = True

    # -- Readiness --
    # If True, /readyz returns 200 only when ALL downstreams are reachable.
    # If False, /readyz always returns 200 as long as the process is up
    # (grace mode for k8s demos where downstreams are still being deployed).
    strict_readiness: bool = True

    extra_env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            service_name=_env("OTEL_SERVICE_NAME", "checkout-service"),
            http_host=_env("CHECKOUT_HTTP_HOST", "0.0.0.0"),
            http_port=_env_int("CHECKOUT_HTTP_PORT", 8003),
            cart_service_url=_env("CART_SERVICE_URL", "http://cart-service:8000"),
            order_service_url=_env("ORDER_SERVICE_URL", "http://order-service:8004"),
            inventory_grpc_addr=_env("INVENTORY_GRPC_ADDR", "inventory-service:50051"),
            payment_grpc_addr=_env("PAYMENT_GRPC_ADDR", "payment-gateway:50052"),
            grpc_timeout_seconds=float(_env("GRPC_TIMEOUT_SECONDS", "5.0")),
            http_timeout_seconds=float(_env("HTTP_TIMEOUT_SECONDS", "5.0")),
            failure_injection_rate=_env_float("FAILURE_INJECTION_RATE", 0.0),
            failure_injection_seed=_env_int("FAILURE_INJECTION_SEED", 0),
            otlp_endpoint=_env("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"),
            enable_otlp_exporter=_env_bool("ENABLE_OTLP_EXPORTER", True),
            strict_readiness=_env_bool("STRICT_READINESS", True),
        )


# A process-wide singleton - clients/modules import `settings` and tests can
# monkeypatch it.
settings = Settings.from_env()
