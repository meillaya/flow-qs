# Loki (log aggregation)

Single-binary Loki deployment using `grafana/loki` chart `18.5.0` with local filesystem storage.

## Deployment

```bash
make apply
```

Or manually:

```bash
helm repo add grafana-community https://grafana-community.github.io/helm-charts
helm repo update grafana-community
helm upgrade --install loki grafana-community/loki \
  --version 18.5.0 \
  --namespace observability \
  --create-namespace \
  --values infra/loki/helm-values.yaml
```

## Configuration notes

- **Mode**: `SingleBinary` — one pod runs all Loki components. No microservices overhead.
- **Storage**: `filesystem` on a 10Gi PVC mounted at `/loki`.
- **Schema**: `v13` with the TSDB index store and `filesystem` object store.
- **Retention**: `168h` (7 days), enforced by the compactor with the filesystem delete-request store.
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

This uninstalls the release and deletes its PVCs. It is a destructive local
demo reset: all retained logs are lost. Reinstalling or changing an immutable
PVC field requires deliberate PVC recreation; the project does not claim an
in-place storage migration.
