# synthetic-monitor

In-cluster synthetic health monitor for the Enten v2 e-commerce services.

## What it does

- Polls `/healthz` on all six services every 30 seconds using an async `httpx` client.
- Emits OpenTelemetry metrics:
  - `synthetic_check_success{service="X"}` — `1` on HTTP 200, otherwise `0`.
  - `synthetic_check_duration_seconds{service="X"}` — request latency.
- Exports metrics via OTLP to `http://otel-collector.observability:4317` and
  simultaneously exposes the same process metrics at the Prometheus-compatible
  `/metrics` endpoint. The two readers are independent; `/metrics` is not an
  OTLP failover mechanism.
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
# From the repository root; discovers the current registry host port.
make build-push-images
```

## Deploy

```bash
IMAGE_TAG="git-$(git rev-parse --verify HEAD)"
helm upgrade --install synthetic-monitor charts/synthetic-monitor \
  --namespace ecom --create-namespace \
  --set-string svc-template.image.tag="${IMAGE_TAG}"
```
