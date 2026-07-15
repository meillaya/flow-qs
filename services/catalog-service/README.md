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
# From the repository root; discovers the current registry host port.
make build-push-images
```

## HTTP SLI metrics

Requests other than `/healthz`, `/readyz`, and `/metrics` emit the
`http.server.request.duration` histogram over OTLP. Its Prometheus-normalized
family is `http_server_request_duration_seconds_*`, partitioned by
`service_name`, `http_response_status_code`, and the route template in
`http_route`.

## Helm

```bash
IMAGE_TAG="git-$(git rev-parse --verify HEAD)"
helm upgrade --install catalog charts/catalog-service -n ecom --create-namespace \
  --set-string svc-template.image.tag="${IMAGE_TAG}"
```
