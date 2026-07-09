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
docker build -t k3d-observability-registry:5000/catalog-service:dev .
docker push k3d-observability-registry:5000/catalog-service:dev
```

## Helm

```bash
helm install catalog charts/catalog-service -n ecom --create-namespace
```
