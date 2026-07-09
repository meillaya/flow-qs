# Loki (log aggregation)

Single-binary Loki deployment using the upstream `grafana/loki` Helm chart with local filesystem storage.

## Deployment

```bash
make apply
```

Or manually:

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update grafana
helm upgrade --install loki grafana/loki \
  --namespace observability \
  --create-namespace \
  --values infra/loki/helm-values.yaml
```

## Configuration notes

- **Mode**: `SingleBinary` — one pod runs all Loki components. No microservices overhead.
- **Storage**: `filesystem` on a 10Gi PVC mounted at `/loki`.
- **Schema**: `v13` with `boltdb-shipper` index store and `filesystem` object store.
- **Retention**: `168h` (7 days).
- **Ingestion limits**: `50 MB/s` rate, `100 MB/s` burst.
- **Auth**: disabled. Loki is reached cluster-internally at `http://loki.observability:3100`.
- **OTLP**: The OpenTelemetry Collector (Task 3) forwards logs via `otlphttp/loki` to `http://loki.observability:3100/otlp`.

## Verification

```bash
make ready   # wait for pod and query labels
make push    # push a test log line and query it back
```

## Reset

```bash
make reset
```

This uninstalls the Helm release and deletes the namespace's Loki PVCs.
