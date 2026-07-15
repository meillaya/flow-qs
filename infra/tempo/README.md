# Tempo (local filesystem trace storage)

Single-replica Grafana Tempo deployment using `grafana/tempo` chart `2.2.3`
Helm chart with local filesystem persistence and 7-day trace retention.

## Files

- `helm-values.yaml` — Helm values override
- `Makefile` — `lint`, `apply`, `reset` helpers

## Configuration highlights

| Setting | Value | Why |
|---|---|---|
| `storage.trace.backend` | `local` | No object storage dependency |
| `storage.trace.local.path` | `/var/tempo/traces` | Trace block storage |
| `storage.trace.wal.path` | `/var/tempo/wal` | Write-ahead log |
| `tempo.retention` | `168h` | 7-day retention via compactor |
| `tempo.querier.max_query_duration` | `0` | No query duration limit for demo |
| `persistence.enabled` | `true` | 10Gi PVC on `local-path` StorageClass |
| `tempo.metricsGenerator.enabled` | `false` | Prometheus handles metrics |
| `tempo.multitenancyEnabled` | `false` | Open access inside cluster |

## Service ports

The chart's Service exposes multiple receivers. The ones used here:

- `3200` / `tempo-prom-metrics` — Tempo HTTP API (`/ready`, `/api/search`, etc.)
- `4317` / `grpc-tempo-otlp` — OTLP gRPC receiver (used by the OTel Collector)
- `4318` / `tempo-otlp-http` — OTLP HTTP receiver

## Deploy

```bash
make apply
```

Or manually:

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm upgrade --install tempo grafana/tempo --version 2.2.3 \
  -n observability -f infra/tempo/helm-values.yaml --create-namespace
```

## Verify

```bash
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=tempo -n observability --timeout=120s
curl -sf http://tempo.observability:3200/ready
kubectl get pvc -n observability -l app.kubernetes.io/name=tempo
```

## Reset

```bash
make reset
```

This uninstalls the release and deletes the Tempo PVCs. All retained traces
are lost. Recreate a PVC deliberately when an immutable storage field changes;
there is no in-place migration guarantee in this local-demo stack.
