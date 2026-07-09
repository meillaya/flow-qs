# synthetic-monitor

In-cluster synthetic health monitor for the Enten v2 e-commerce services.

## What it does

- Polls `/healthz` on all six services every 30 seconds using an async `httpx` client.
- Emits OpenTelemetry metrics:
  - `synthetic_check_success{service="X"}` — `1` on HTTP 200, otherwise `0`.
  - `synthetic_check_duration_seconds{service="X"}` — request latency.
- Exports metrics via OTLP to `http://otel-collector.observability:4317`.
- Exposes a Prometheus-compatible `/metrics` endpoint as a fallback when the OTLP collector is unavailable.
- Logs `synthetic_failure` after three consecutive failures for a service.

## Default targets

```text
catalog-service:8000
inventory-service:8001
cart-service:8002
checkout-service:8003
order-service:8004
payment-gateway:8005
```

Targets can be overridden with the `SYNTHETIC_TARGETS` environment variable as a comma-separated list.

## Run locally

```bash
uv sync
uv run pytest
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Build and push

```bash
docker build -t localhost:38053/synthetic-monitor:dev .
docker push localhost:38053/synthetic-monitor:dev
```

## Deploy

```bash
helm upgrade --install synthetic-monitor charts/synthetic-monitor \
  --namespace observability --create-namespace
```
