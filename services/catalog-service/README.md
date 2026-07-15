# catalog-service

Catalog service for the Enten v2 observability platform.

Built with **Python 3.12**, **FastAPI**, **SQLite** (stdlib `sqlite3`), and **OpenTelemetry** auto-instrumentation.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | Liveness probe |
| GET | `/readyz` | Readiness probe (checks SQLite) |
| GET | `/products` | List all products |
| GET | `/products/{id}` | Get a single product |
| POST | `/products` | Create a product |
| GET | `/products/search?q=` | Search products by name |

## Local development

```bash
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

## Tests & linting

```bash
uv run pytest -v
uv run ruff check .
uv run ty check .
```

## Docker

```bash
IMAGE_TAG=<immutable-tag>
docker build -t "k3d-observability-registry:5000/catalog-service:${IMAGE_TAG}" .
docker push "k3d-observability-registry:5000/catalog-service:${IMAGE_TAG}"
```

## HTTP SLI metrics

Requests other than `/healthz`, `/readyz`, and `/metrics` emit the
`http.server.request.duration` histogram over OTLP. Its Prometheus-normalized
family is `http_server_request_duration_seconds_*`, partitioned by
`service_name`, `http_response_status_code`, and the route template in
`http_route`.

## Helm

```bash
helm install catalog charts/catalog-service -n ecom --create-namespace
```
