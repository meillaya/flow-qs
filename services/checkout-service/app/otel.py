"""OpenTelemetry initialization.

Wires:
  * TracerProvider with OTLP gRPC exporter (best-effort, never crash startup)
  * Resource attributes (service.name, service.version, deployment env)
  * Auto-instrumentation for FastAPI, httpx, grpcio (client + server)

The exporter is wrapped in a try/except so a missing Collector does not take
down the checkout process. When the Collector is unreachable, the SDK buffers
a tiny number of spans and silently drops the rest.
"""
from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.grpc import GrpcAioInstrumentorClient, GrpcAioInstrumentorServer
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from .config import settings

logger = logging.getLogger(__name__)

_initialized = False


def init_tracing() -> None:
    """Idempotently set up the global tracer + auto-instrumentation.

    Safe to call multiple times - only the first call wires exporters.
    """
    global _initialized
    if _initialized:
        return

    resource = Resource.create(
        {
            "service.name": settings.service_name,
            "service.version": "0.1.0",
            "deployment.environment": "dev",
        }
    )
    provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)
    if settings.enable_otlp_exporter:
        try:
            exporter = OTLPSpanExporter(
                endpoint=settings.otlp_endpoint, insecure=True
            )
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info(
                "otel: OTLP exporter wired to %s", settings.otlp_endpoint
            )
        except Exception:  # pragma: no cover - never fail boot
            logger.exception("otel: failed to wire OTLP exporter; continuing without remote export")

    trace.set_tracer_provider(provider)

    # Auto-instrument the libraries this service actually uses. Each
    # instrumentor patches its respective client/server on first instrument().
    FastAPIInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    try:
        GrpcAioInstrumentorClient().instrument()
        GrpcAioInstrumentorServer().instrument()
    except Exception:  # pragma: no cover - instrumentor import quirk
        logger.exception("otel: failed to instrument grpcio; client spans may be missing")

    _initialized = True


def get_tracer(name: str = "checkout-service") -> trace.Tracer:
    return trace.get_tracer(name)
