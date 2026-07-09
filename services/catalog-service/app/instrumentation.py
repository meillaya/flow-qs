"""OpenTelemetry instrumentation setup for the catalog service."""

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure_tracing(service_name: str) -> trace.Tracer:
    """Configure the global tracer provider and instrument libraries."""
    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": os.environ.get("DEPLOYMENT_ENVIRONMENT", "dev"),
        }
    )

    provider = TracerProvider(resource=resource)

    exporter_name = os.environ.get("OTEL_TRACES_EXPORTER", "otlp").lower()
    if exporter_name != "none":
        endpoint = os.environ.get(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector.observability:4317"
        )
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            timeout=5,
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)

    SQLite3Instrumentor().instrument()
    RequestsInstrumentor().instrument()

    return trace.get_tracer(service_name)


def instrument_app(app) -> None:
    """Instrument a FastAPI application."""
    FastAPIInstrumentor.instrument_app(app)
