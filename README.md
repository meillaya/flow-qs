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
| Telemetry | OpenTelemetry Collector | Receives OTLP and fans out traces/logs/metrics |
| Traces | Grafana Tempo | Distributed trace storage and query |
| Logs | Grafana Loki | Log aggregation |
| Metrics | Prometheus | Time-series metrics and SLO recording rules |
| Alerting | Alertmanager | Routes alerts to the incident engine |
| Dashboards | Grafana | Provisioned RED, SLO, trace, and log dashboards |
| Chaos | Chaos Mesh | Pod, network, DNS, and CPU fault injection |
| Load | k6 | Smoke, load, stress, and spike profiles |
| Dev env | devenv | Reproducible shell with Python 3.12, Go from locked rolling nixpkgs, k6, terraform, kubectl, helm |

## Quick Start

```bash
# 1. Enter the reproducible development shell
devenv shell

# 2. Provision the k3d cluster, observability stack, and services
make up

# 3. Run the end-to-end demo: load -> chaos -> incident -> postmortem
make demo
```

After `make up`, the following ports are forwarded to the k3d load balancer:

| Service | URL | Default credentials |
|---------|-----|---------------------|
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | none |
| Alertmanager | http://localhost:9093 | none |

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): system diagram, services, telemetry and alert flows, SLO strategy
- [docs/RUNBOOK.md](docs/RUNBOOK.md): common operations, recovery steps, and cookbooks
- [docs/DEMO.md](docs/DEMO.md): what `make demo` does and how to interpret results
- [docs/SETUP.md](docs/SETUP.md): full setup instructions and prerequisites
- [docs/examples/postmortems/](docs/examples/postmortems/): annotated template and real example postmortems

## Repository Layout

```text
.
├── Makefile                    # up, down, demo, lint, test
├── devenv.nix / devenv.yaml    # Reproducible dev shell
├── infra/                      # Terraform k3d cluster + OTel/Tempo/Loki/Prom/Grafana configs
├── services/                   # catalog, inventory, cart, checkout, order, payment-gateway
├── charts/                     # Per-service Helm charts
├── observability/              # Dashboards and Prometheus rules
├── slo/                        # SLO definitions
├── chaos/                      # Chaos Mesh experiments
├── load/                       # k6 scripts and Kubernetes Jobs
├── scripts/                    # Validation and build helpers
└── docs/                       # README, ARCHITECTURE, RUNBOOK, DEMO, SETUP
```

## Development Commands

```bash
make lint        # ruff, pyright, golangci-lint
make test        # pytest + go test
make lint-chart  # helm lint on svc-template
make down        # tear down the cluster
make clean       # remove Terraform state and local artifacts
```

## Learnings

- Keep the OTel Collector pipeline as the single source of truth; mirror it into Helm values to avoid drift.
- Use provisioned Grafana JSON dashboards and Alertmanager YAML so the stack is reproducible without click-ops.
- Multi-window multi-burn-rate alerts reduce noise: fast-burn pages catch emergencies, slow-burn tickets catch budget drift.
- Running k6 as a Kubernetes Job keeps load generation inside the cluster network and avoids host-side port conflicts.
- A hand-off between Alertmanager and the incident engine via webhook is enough for demo-grade automated postmortems; no UI needed.
- Demo orchestration needs a hard watchdog timeout (15 min subshell SIGTERM), trap-based cleanup on Ctrl+C, and a fallback postmortem path so the artifact pipeline still produces content when the incident-engine is not yet deployed.
- Parsing k6 stdout with a Python heredoc is a reliable fallback for extracting p95/error-rate when the in-pod `/tmp/results.json` is unreachable after Job termination.
