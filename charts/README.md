# Enten v2 Helm Charts

This directory contains reusable Helm charts for the observability platform.

## `svc-template`

A minimal umbrella chart used as a base for all Enten v2 services. It provides:

- A `Deployment` with standard Kubernetes labels, resource limits, liveness/readiness probes, and OpenTelemetry environment variables.
- A `ClusterIP` `Service`.
- A minimal `ServiceAccount`.
- An optional `ConfigMap` for non-secret environment variables defined in `values.yaml`.

### Consuming the chart

Render the chart locally:

```bash
helm template my-svc charts/svc-template -f my-values.yaml
```

Example `my-values.yaml`:

```yaml
serviceName: catalog-service
image:
  repository: ghcr.io/enten/catalog-service
  tag: latest
service:
  port: 8000
  targetPort: 8000
env:
  LOG_LEVEL: info
```

### Linting

```bash
helm lint charts/svc-template
```

### Probes and OTel

The chart injects the following environment variables automatically:

- `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.observability:4317`
- `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`
- `OTEL_SERVICE_NAME` (from `.Values.serviceName`)
- `OTEL_RESOURCE_ATTRIBUTES=service.name=<serviceName>,deployment.environment=dev`
- `OTEL_TRACES_EXPORTER=otlp`
- `OTEL_METRICS_EXPORTER=otlp`
- `OTEL_LOGS_EXPORTER=otlp`

Liveness and readiness probe paths default to `/healthz` and `/readyz` and are configurable via `values.yaml`.
