# cart-service

Shopping cart service built with Python/FastAPI and OTel instrumentation.

## State

Cart state is stored in an in-memory Python dictionary keyed by `user_id`.  Data is
lost when the pod or process restarts.  This is a deliberate demo limitation;
persistence (Redis, database, etc.) is intentionally out of scope for this task.

## Endpoints

- `GET /healthz` — liveness probe
- `GET /readyz` — readiness probe
- `GET /cart/{user_id}` — return the current cart
- `POST /cart/{user_id}/items` — add an item (`{"sku": str, "qty": int}`); merges
  quantities when the same SKU is added again
- `DELETE /cart/{user_id}/items/{sku}` — remove a SKU from the cart
- `DELETE /cart/{user_id}` — clear the entire cart

## Pricing

The service reports `total_cents` using a fixed demo unit price of 100 cents per
quantity.  There is no integration with catalog pricing.

## OTel

The container is started with `opentelemetry-instrument`, which auto-instruments
FastAPI and `requests` using the installed instrumentation packages.

User-facing requests also emit the explicit `http.server.request.duration`
histogram over OTLP. Its Prometheus-normalized family is
`http_server_request_duration_seconds_*`, labelled by `service_name`,
`http_response_status_code`, and the low-cardinality `http_route` template.
Health, readiness, and metrics probes are excluded.
