# Enten v2: Distributed Observability Platform

[![Lint](https://github.com/Meillaya/enten-v2/actions/workflows/lint.yaml/badge.svg?branch=main)](https://github.com/Meillaya/enten-v2/actions/workflows/lint.yaml)
[![Test](https://github.com/Meillaya/enten-v2/actions/workflows/test.yaml/badge.svg?branch=main)](https://github.com/Meillaya/enten-v2/actions/workflows/test.yaml)
[![Build Images](https://github.com/Meillaya/enten-v2/actions/workflows/build-images.yaml/badge.svg?branch=main)](https://github.com/Meillaya/enten-v2/actions/workflows/build-images.yaml)

A Kubernetes-deployed distributed observability and incident response platform for a polyglot e-commerce microservices demo. OpenTelemetry SDKs instrument every service; the OTLP Collector routes traces to Tempo, logs to Loki, and metrics to Prometheus. Grafana provides correlated dashboards, and Prometheus multi-window burn-rate alerts flow through Alertmanager to an incident engine that assembles timelines and generates Markdown postmortems.

**Resume bullet**: Built a Kubernetes-deployed distributed observability platform using OpenTelemetry, Prometheus, Grafana, Loki, and Tempo to collect traces, logs, and metrics, detect SLO violations, run chaos experiments, and generate automated incident reports.

## Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| Cluster | k3d + Terraform | Local Kubernetes with reproducible infrastructure |
| Services | Python/FastAPI + Go/Chi | Polyglot microservices with OTel auto-instrumentation |
| Telemetry | OpenTelemetry Collector | One OTLP gateway plus node log agents; routes traces, logs, and metrics |
| Traces | Grafana Tempo | Distributed trace storage and query |
| Logs | Grafana Loki | Log aggregation |
| Metrics | Prometheus | Time-series metrics and SLO recording rules |
| Alerting | Alertmanager | Routes alerts to the incident engine |
| Dashboards | Grafana | Provisioned RED, SLO, trace, and log dashboards |
| Chaos | Chaos Mesh | Pod, network, DNS, and CPU fault injection |
| Load | k6 | Smoke, load, stress, and spike profiles |
| Dev env | devenv | Reproducible shell with Python 3.12, Go 1.26.5, k6, Terraform, kubectl, and Helm |

## Quick Start

```bash
# 1. Enter the reproducible development shell
devenv shell

# 2. Provision the k3d cluster, observability stack, and services
make up

# 3. Verify product telemetry, SLO rules, dashboards, and service health
make verify

# 4. Run the end-to-end demo: load -> chaos -> incident -> postmortem
make demo
```

The stack is intentionally private to the cluster. Open separate terminals for
the local demo endpoints; there is no Ingress or permanent host exposure:

```bash
kubectl -n observability port-forward svc/grafana 3000:80
kubectl -n observability port-forward svc/prometheus 9090:80
kubectl -n observability port-forward svc/prometheus-alertmanager 9093:9093
```

| Service | URL | Default credentials |
|---------|-----|---------------------|
| Grafana | http://localhost:3000 | `admin / admin` (local demo only) |
| Prometheus | http://localhost:9090 | none |
| Alertmanager | http://localhost:9093 | none |

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): system diagram, services, telemetry and alert flows, SLO strategy
- [docs/RUNBOOK.md](docs/RUNBOOK.md): common operations, recovery steps, and cookbooks
- [docs/DEMO.md](docs/DEMO.md): what `make demo` does and how to interpret results
- [docs/SETUP.md](docs/SETUP.md): full setup instructions and prerequisites
- [docs/examples/postmortems/](docs/examples/postmortems/): annotated template and historical illustrative postmortems

## Repository Layout

```text
.
├── Makefile                    # up, down, demo, lint, test
├── devenv.nix / devenv.yaml    # Reproducible dev shell
├── infra/                      # Terraform k3d cluster + OTel/Tempo/Loki/Prom/Grafana configs
├── services/                   # Six product services + synthetic monitor + incident engine
├── charts/                     # Per-service Helm charts
├── dashboards/                 # Provisioned Grafana dashboards
├── observability/              # Prometheus rules and Alertmanager templates
├── slo/                        # SLO definitions
├── chaos/                      # Chaos Mesh experiments
├── load/                       # k6 scripts and Kubernetes Jobs
├── scripts/                    # Validation and build helpers
└── docs/                       # README, ARCHITECTURE, RUNBOOK, DEMO, SETUP
```

## Development Commands

```bash
make lint        # Helm lint, Ruff, ty, gofmt, go vet, golangci-lint
make test        # pytest + go test
make lint-chart  # Helm lint every local chart
make verify      # validate live telemetry, rules, dashboards, and services
make down        # remove only this repository's runtime resources
make clean       # make down + remove Terraform/devenv local state
```

## Learnings

- Keep the gateway and node-agent Collector pipelines in Helm values as the single deployed source of truth.
- Collect Kubernetes container logs with one node agent per schedulable node; keep stateful servicegraph processing on the single local-demo gateway.
- Use `http_server_request_duration_seconds_{bucket,count,sum}` with `service_name`, `service_namespace`, `http_response_status_code`, and `http_route` as the stable RED contract. Payment uses the same schema through its bounded Prometheus scrape exception.
- Use provisioned Grafana JSON dashboards and Alertmanager YAML so the stack is reproducible without click-ops.
- Multi-window multi-burn-rate alerts reduce noise: fast-burn pages catch emergencies and slow-burn warnings catch budget drift.
- Running k6 as a Kubernetes Job keeps load generation inside the cluster network and avoids host-side port conflicts.
- A hand-off between Alertmanager and the incident engine via webhook is enough for demo-grade automated postmortems; no UI needed.
- Treat the k6 Job exit, incident correlation, and bounded cleanup as authoritative. The demo fails rather than fabricating a postmortem or weakening thresholds.
- Deploy immutable run-specific image tags so repeating `make up` changes pod templates and leaves a Kubernetes rollback target.
- Build every service from digest-pinned base/tool images and committed frozen dependency locks.
