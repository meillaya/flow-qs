# checkout-service

Saga-style checkout orchestrator for the Enten v2 observability demo.

## What it does

```
POST /checkout  --[cart HTTP]-> cart-service
              --[gRPC]-------> inventory-service.ReserveStock (per item)
              --[gRPC]-------> payment-gateway.Charge
              --[HTTP]-------> order-service.orders  (POST)

   any failure
              --[gRPC]-------> inventory-service.ReleaseStock  (compensate)
              --[gRPC]-------> payment-gateway.Refund          (compensate, only if charged)
```

## Endpoints

| Method | Path        | Purpose                                                           |
|--------|-------------|-------------------------------------------------------------------|
| GET    | `/healthz`  | Liveness. Always 200 if the process is up.                        |
| GET    | `/readyz`   | Readiness. 200 only if all downstreams are reachable (toggle with `STRICT_READINESS=false`). |
| POST   | `/checkout` | Run the checkout saga. Body: `{"user_id": "...", "payment_method": "card"}`. |

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `CHECKOUT_HTTP_PORT` | 8003 | HTTP listen port |
| `CART_SERVICE_URL` | `http://cart-service:8000` | cart HTTP base URL |
| `ORDER_SERVICE_URL` | `http://order-service:8004` | order HTTP base URL |
| `INVENTORY_GRPC_ADDR` | `inventory-service:50051` | inventory gRPC address |
| `PAYMENT_GRPC_ADDR` | `payment-gateway:50052` | payment gRPC address |
| `FAILURE_INJECTION_RATE` | 0.0 | Probability in [0.0, 1.0] of injected failure |
| `FAILURE_INJECTION_SEED` | 0 | Seed for the RNG (deterministic in tests) |
| `STRICT_READINESS` | true | If false, `/readyz` always returns 200 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4317` | OTLP gRPC endpoint |
| `ENABLE_OTLP_EXPORTER` | true | Set to `false` for tests / no-Collector runs |

User-facing requests emit the explicit `http.server.request.duration`
histogram over OTLP. The normalized Prometheus family is
`http_server_request_duration_seconds_*` with `service_name`,
`http_response_status_code`, and route-template `http_route` labels. Probe
routes are excluded.

## Local run

```bash
uv venv
uv pip install -e .[dev]
./scripts/gen_proto.sh
uv run uvicorn app.main:app --reload --port 8003
```

## Tests

```bash
uv run pytest -v
```

The test suite uses `httpx.MockTransport` and an in-process `grpc.aio` server
to simulate the downstream services. The OTel SDK is configured to use an
in-memory exporter so tests can assert on trace propagation without
contacting a Collector.

## Notes on end-to-end testing

Real downstream services (cart-service, inventory-service, payment-gateway,
order-service) must be deployed for an actual end-to-end test. Until then,
the unit + integration tests above provide coverage of the orchestrator
saga, compensation, and trace propagation behaviour.
