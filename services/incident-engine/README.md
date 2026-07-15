# incident-engine

Alertmanager webhook receiver that correlates traces from **Tempo**, logs from
**Loki** and metric snapshots from **Prometheus** to produce Markdown
postmortems for the Enten v2 observability platform.

## What it does

- Accepts the standard Alertmanager webhook payload at `POST /webhook`.
- Queries Tempo, Loki and Prometheus for the alert's `service.name` over a
  configurable lookback window (default 5 minutes).
- Builds a chronological timeline from the alert firing time, trace hits,
  log lines, Prometheus snapshots and any matching chaos-mesh experiments.
- Renders a postmortem.md with the Google SRE sections:
  **Summary**, **Impact**, **Timeline**, **Root Cause**, **Resolution**,
  **Lessons Learned**.
- Persists every incident in SQLite (`/data/incidents.db`) and writes the
  Markdown body to `/postmortems/`.
- Optional: POSTs a summary notification to `INCIDENT_WEBHOOK_URL`
  (Slack/Discord compatible JSON).
- Background reconciler auto-marks incidents as resolved after
  `RESOLVE_TIMEOUT_MINUTES` (default 30).

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/webhook` | Alertmanager webhook payload |
| `GET`  | `/incidents` | List all incidents (newest first) |
| `GET`  | `/incidents/{id}` | Return one incident + its postmortem |
| `GET`  | `/healthz` | Kubernetes liveness probe |
| `GET`  | `/readyz` | Kubernetes readiness probe |
| `GET`  | `/metrics` | Prometheus scrape endpoint |

## Configuration (env vars)

| Variable | Default | Purpose |
|----------|---------|---------|
| `TEMPO_URL` | `http://tempo.observability:3200` | Tempo HTTP API |
| `LOKI_URL` | `http://loki.observability:3100` | Loki HTTP API |
| `PROM_URL` | `http://prometheus.observability:80` | Prometheus in-cluster Service API |
| `POSTMORTEM_DIR` | `/postmortems` | Where postmortem.md files are written |
| `INCIDENT_DB_PATH` | `/data/incidents.db` | SQLite database file |
| `LOOKBACK_MINUTES` | `5` | Minutes before alert start to fetch context for |
| `RESOLVE_TIMEOUT_MINUTES` | `30` | Auto-resolve firing incidents older than this |
| `INCIDENT_WEBHOOK_URL` | _unset_ | Optional Slack/Discord webhook for notifications |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector.observability:4317` | OTLP exporter |
| `INCIDENT_ENGINE_DISABLE_OTEL` | _unset_ | Set to `1` to skip OTel metric setup (used in tests) |

## Run locally

```bash
uv sync
INCIDENT_ENGINE_DISABLE_OTEL=1 uv run uvicorn app.main:app --host 0.0.0.0 --port 8088
```

## Run tests

```bash
uv run pytest
```

The default `pyproject.toml` configures pytest to fail under 80% coverage; the
current suite contains 53 focused incident, context, lifecycle, and security tests.
The upstream API calls are mocked with `respx` and `StaticUpstreamContext`.

## Build and push

```bash
# From the repository root; discovers the current registry host port.
make build-push-images
```

## Deploy

```bash
IMAGE_TAG="git-$(git rev-parse --verify HEAD)"
helm upgrade --install incident-engine charts/incident-engine \
  --namespace incident --create-namespace \
  --set-string image.tag="${IMAGE_TAG}"
```

The Helm chart mounts two PVCs (`/data` and `/postmortems`) so incidents
survive pod restarts.
