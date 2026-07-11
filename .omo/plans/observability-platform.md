# Distributed Observability + Incident Response Platform

## TL;DR

> **Quick Summary**: Build a Kubernetes-deployed distributed observability + incident response platform for a polyglot (Python/Go) e-commerce microservices demo. OpenTelemetry SDKs instrument all services; OTLP Collector routes traces to Tempo, logs to Loki, metrics to Prometheus. Grafana provides correlated dashboards. Prometheus multi-window burn-rate alerts flow through Alertmanager to a Python incident-engine that assembles timelines and generates `postmortem.md`. Chaos Mesh injects faults; k6 drives load; everything is reproducible via `devenv shell` against a Terraform-managed k3d cluster.
>
> **Deliverables**:
> - 5 Python/FastAPI services + 1 Go/Chi service (payment-gateway) + 1 Python incident-engine
> - k3d cluster via Terraform + per-service Helm charts
> - OTel Collector + Tempo + Loki + Prometheus + Alertmanager + Grafana stack
> - 5 provisioned Grafana dashboards (Overview, RED per-service, Trace Explorer, Log Explorer, SLO Status)
> - SLO definitions + burn-rate alerts
> - Chaos Mesh experiments (4 types) + k6 load scripts (4 profiles)
> - Synthetic monitor (Python)
> - GitHub Actions CI (lint, type-check, test, build images)
> - Documentation: README, ARCHITECTURE.md, RUNBOOK.md, postmortem examples
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES - 4 implementation waves + final review, max 8 concurrent tasks
> **Critical Path**: Wave 1 (devenv+k3d+OTel collector) → Wave 2 (services) → Wave 3 (alerting+dashboards+chaos+k6+CI) → Wave 4 (incident-engine+E2E+demo+docs) → Final reviews

---

## Context

### Original Request
Build a distributed observability + incident response platform featuring microservices communicating over HTTP/gRPC with full OpenTelemetry instrumentation, distributed tracing, logs/metrics/traces correlation, Prometheus/Grafana dashboards, SLO/SLA tracking, synthetic monitoring, chaos testing, incident timeline generator, auto-generated postmortem, and load testing. Resume bullet target: "Built a Kubernetes-deployed distributed observability platform using OpenTelemetry, Prometheus, Grafana, Loki, and Tempo to collect traces, logs, and metrics, detect SLO violations, run chaos experiments, and generate automated incident reports."

### Interview Summary

**Key Discussions**:
- **Domain**: E-commerce (catalog, inventory, cart, checkout, order, payment) — chosen for familiar, well-understood failure modes and realistic trace shapes.
- **Deployment**: Real k3d + Terraform (not docker-compose shortcuts) — required for portfolio-grade "Kubernetes-deployed" bullet.
- **Languages**: Python (FastAPI) for 5 services + Go (Chi) for payment-gateway — chosen for polyglot OTel context propagation demo with minimal language sprawl.
- **Incident engine**: Standalone Python service + Markdown files (no web UI) — matches "minimal abstraction" while keeping postmortems runnable + inspectable.
- **Chaos**: Chaos Mesh (kernel/network fault injection) — more realistic than simple service-toggle, lighter than full Litmus.
- **Load**: k6 — modern, JS-based, runs as K8s Job for clean cluster-internal load.
- **Testing**: pytest for service units + agent-executed QA for E2E (curl, kubectl, k6, Grafana/Tempo/Loki/Prom HTTP APIs, grpcurl).

**Research Findings**:
- OTel Collector as central hub is the standard pattern (single deployment vs sidecar per pod is simpler for 7-service demo).
- k3d requires ≥6 GB RAM allocated to Docker; plan must start with `k3d cluster create --servers 1 --agents 2 --memory 6G`.
- Chaos Mesh needs cluster-admin or specific RBAC; must install via Helm with proper service account.
- Multi-window multi-burn-rate (MWMB) alerting is the Google SRE Workbook standard for SLOs.
- Tempo + Loki + Prometheus + Grafana all have official Helm charts; using those reduces maintenance.
- gRPC + OTel: both Python (grpcio + opentelemetry-instrumentation-grpc) and Go (go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc) support context propagation via W3C Trace Context headers — must verify cross-language trace continuity.

### Metis Review
Pre-planning analyst (Metis) timed out before producing findings. Planner applied the following gap-mitigation defaults during plan generation:

- **Database backends**: SQLite (file-backed, in-cluster PVC) for catalog/inventory/orders; in-memory dict for cart (stateful demo); in-memory for payment-gateway mock. Rationale: zero external infra dependency, fully self-contained k3d demo, easy to reset.
- **Service-to-service auth**: NetworkPolicy only (allow pod-to-pod on cluster-internal CIDR). No application-layer auth. Documented as out-of-scope.
- **gRPC vs HTTP split**: HTTP/JSON for browser-facing or trivial calls (catalog reads, cart, order history); gRPC for service-internal high-fanout (catalog↔inventory stock checks, checkout↔payment). Documented.
- **OTel Collector deployment**: Single shared `Deployment` with 2 replicas, NOT DaemonSet — simpler, fewer pods, sufficient throughput for demo.
- **Resource constraints**: k3d created with 6 GB server + 4 GB agent memory; each service capped at 256 MB requests / 512 MB limits.
- **Secret management**: N/A — no real payment processor, no external API keys. Documented.
- **Graceful shutdown**: All services must implement SIGTERM handlers with 10s drain window. Standard FastAPI lifespan + Go signal.Notify context.
- **Postmortem examples**: 2 example postmortems committed: one real (from chaos test run) + one annotated template.
- **Grafana provisioning**: 100% YAML-file-based, no UI click-ops. Datasources, dashboards, alert contact points all provisioned.
- **Tempo/Loki/Prometheus storage**: local FS in PVCs. Retention: Tempo 7d (168h block retention), Loki 7d (168h), Prometheus 15d (longer is fine for metrics — they're cheap and useful for SLO burn-rate window math). Documented as demo-grade.
- **k6 location**: Runs as Kubernetes `Job` (k6-owned image) inside the cluster, hits services via ClusterIP DNS. Cleaner than host-side runs.
- **Chaos Mesh RBAC**: Installed via Helm with `--set controllerManager.serviceAccount=rbac enabled`; experiments created in dedicated `chaos-testing` namespace.
- **OTel version compat**: Lock both `opentelemetry-python` and `go.opentelemetry.io/otel` to v1.27+ (latest stable at plan time). OTLP v1 over gRPC.
- **CI**: GitHub Actions with `ubuntu-latest` runners; matrix on Python 3.12 / Go 1.22; push to `ghcr.io` (GitHub Container Registry).
- **Test tooling**: `pytest`, `pytest-asyncio`, `httpx` (test client), `testcontainers` (for integration with real Tempo/Loki/Prom in CI), `grpcurl` (gRPC QA), `k6` (load QA), `kubectl` + `kustomize` (K8s QA).

### Execution-time compatibility amendments

Live failing-first verification established five necessary corrections to the original draft:

- devenv 2.x declares executable scripts in `devenv.nix`; `devenv.yaml` is reserved for inputs/configuration.
- The Collector's synchronized repository-owned Helm chart is the deployment source of truth; maintaining a second Kustomize deployment path caused configuration drift.
- Tempo 2.9 rejects `querier.max_query_duration`, so the removed field must not be reintroduced.
- Loki 3.x schema v13 uses TSDB/tsdb-shipper; boltdb-shipper is a legacy store incompatible with the selected chart contract.
- Order status uses the payment gateway's canonical `GetStatus(charge_id)` RPC, avoiding a second incompatible payment protocol.

These amendments preserve the requested behavior while replacing invalid or duplicate implementation mechanisms discovered during real deployment.

---

## Work Objectives

### Core Objective
Ship a runnable, portfolio-grade Kubernetes-deployed observability platform that demonstrates polyglot OTel instrumentation, SLO-based alerting, chaos engineering, automated incident timeline + postmortem generation, and load testing — all reproducible via `devenv shell` on a Terraform-provisioned k3d cluster.

### Concrete Deliverables
- Working repository at `/home/mei/projects/enten-v2/`
- `devenv shell` brings up the entire stack end-to-end
- `make demo` runs a scripted 5-minute demo (load → chaos → incident → postmortem)
- 5 provisioned Grafana dashboards visible at `localhost:3000` (admin/admin)
- ≥1 example generated postmortem committed under `docs/examples/postmortems/`
- GitHub Actions green on `main` branch

### Definition of Done
- [ ] `devenv shell -- make up` brings the full stack up in <5 min from clean state
- [ ] `k6 run` of `smoke` profile passes against running services
- [ ] `kubectl apply` of any chaos experiment triggers a real Prometheus alert within 60s
- [ ] `incident-engine` produces a real `postmortem.md` for that alert
- [ ] `ruff check` + `ty check` + `pytest` + `go vet` + `go test` all pass with zero errors
- [ ] `devenv shell -- make down` tears down cluster and removes all containers
- [ ] GitHub Actions workflow completes successfully on push to `main`

### Must Have
- All 7 services (6 user + incident-engine) deployed and reachable
- OTel SDK in every service auto-instrumenting each protocol that service actually uses (HTTP server, HTTP client, and/or gRPC)
- OTel Collector receiving OTLP from all services on `4317` (gRPC) and `4318` (HTTP)
- Tempo + Loki + Prometheus all receiving data and queryable via HTTP API
- Grafana datasources provisioned (Tempo, Loki, Prometheus, Alertmanager)
- 5 Grafana dashboards provisioned as JSON files (not click-ops)
- SLO definitions in `slo/` directory with Prometheus recording rules + alert rules
- Multi-window multi-burn-rate alert rules per service
- Alertmanager configured to route to incident-engine webhook
- incident-engine listening on `:8088/webhook` and creating `postmortem.md` files
- Chaos Mesh installed with RBAC; ≥4 chaos experiments as YAML manifests
- k6 scripts for 4 profiles (smoke, load, stress, spike) as `.js` files
- Synthetic monitor as Python script running in-cluster
- Terraform under `infra/` provisioning the k3d cluster
- Helm chart per service under `charts/<service>/`
- `devenv.nix` + `devenv.yaml` providing Python 3.12, Go 1.22, k6, terraform, kubectl, helm, kustomize, gRPC tools
- GitHub Actions workflows for lint, test, build (under `.github/workflows/`)
- README.md, ARCHITECTURE.md, RUNBOOK.md under `docs/`
- ≥1 example postmortem.md under `docs/examples/postmortems/`

### Must NOT Have (Guardrails)
- No docker-compose for the demo stack (k3d only — Terraform-managed)
- No real cloud-provider integrations (S3, GCS, Azure) — local FS only
- No real payment processor integration (Stripe, Adyen, etc.)
- No AuthN/AuthZ implementation (NetworkPolicy only)
- No frontend UI for incident-engine (CLI + Markdown files only)
- No multi-region deployment
- No Kubernetes operators other than Chaos Mesh
- No auto-remediation (postmortems are generated, never auto-applied)
- No Prometheus pushgateway (use pull model only)
- No abstraction layers (no "MetricsProvider" wrappers — use OTel SDK directly)
- No generic helper libraries (no `common/` module — copy minimal code between services, no DRY-for-its-own-sake)
- No comments explaining WHAT the code does (only WHY when non-obvious)
- No emoji in code, commits, or docs (unless user explicitly requests)
- No premature Kubernetes features (no HelmOperator, no ArgoCD, no cert-manager, no ingress-nginx — port-forward only for local demo)
- No database migrations framework (SQLite with schema-on-init)
- No message broker (Kafka, RabbitMQ) — direct HTTP/gRPC calls only
- No API gateway (Kong, Istio) — services call each other directly

### Locust Explicitly Rejected
The original spec mentioned "k6/Locust". k6 was chosen for these reasons:
- Native Kubernetes support via official `grafana/k6` image (runs as a Job)
- JS-based scripts align with modern load-testing tooling
- Lower memory footprint than Locust
- Single binary, no Python dependency at runtime

Locust would have required either a Python runtime in the cluster or a sidecar pattern. Rejected to keep minimal abstraction.

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** - ALL verification is agent-executed. No exceptions.
> Acceptance criteria requiring "user manually tests/confirms" are FORBIDDEN.

### Test Decision
- **Infrastructure exists**: NO (greenfield)
- **Automated tests**: Tests-after — each service task writes pytest after implementation; integration tests via testcontainers; agent-Executed QA is the primary E2E verification.
- **Framework**: pytest + pytest-asyncio + httpx (Python), go test (Go), testcontainers-python for integration.
- **Test tooling**: grpcurl (gRPC manual QA), k6 (load QA), kubectl (K8s QA), Grafana HTTP API (dashboard QA), Tempo/Loki/Prometheus HTTP APIs (data verification).

### QA Policy
Every task MUST include agent-executed QA scenarios (see TODO template below).
Evidence saved to `.omo/evidence/task-{N}-{scenario-slug}.{ext}`.

- **HTTP/REST APIs**: `curl` with specific endpoints, status codes, JSON assertions
- **gRPC APIs**: `grpcurl` with proto reflection, specific method/route assertions
- **Kubernetes resources**: `kubectl wait` for rollouts, `kubectl get` for resource existence, `kubectl logs` for content
- **Grafana dashboards**: HTTP API GET /api/dashboards/uid/{uid} verify exists; HTTP API GET /api/datasources verify provisioned
- **Tempo**: HTTP API GET /api/search?tags=service.name=X verify traces exist for service
- **Loki**: HTTP API GET /loki/api/v1/query verify logs queryable by trace_id
- **Prometheus**: HTTP API GET /api/v1/query?query=up verify metrics scraped
- **Alertmanager**: HTTP API GET /api/v1/alerts verify alert firing after chaos
- **Load**: `k6 run --out json=evidence.json` parse JSON for p95/p99 + error rate assertions
- **Chaos**: `kubectl apply -f chaos.yaml` then wait + verify alert + verify postmortem

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — all independent, max parallelism):
├── Task 1:  devenv.sh shell + uv/ty/ruff/golangci-lint/k6/kubectl/helm/terraform tooling
├── Task 2:  Terraform k3d cluster + namespaces + local registry
├── Task 3:  OpenTelemetry Collector config (receivers/processors/exporters) + Collector Helm chart
├── Task 4:  Tempo Helm deployment + S3-style local backend + retention config
├── Task 5:  Loki Helm deployment + filesystem storage + retention config
├── Task 6:  Prometheus + Alertmanager Helm deployment + scrape configs + alert routing
├── Task 7:  Grafana Helm deployment + datasources provisioning (Tempo/Loki/Prom/Alertmanager)
└── Task 8:  Umbrella Helm chart skeleton (chartMuseum-style base templates)

Wave 2 (Services — all independent, max parallelism):
├── Task 9:  catalog-service (Python/FastAPI, SQLite, OTel auto-instrumentation, /healthz, /products endpoints)
├── Task 10: inventory-service (Python/FastAPI, SQLite, OTel, /inventory/{sku} endpoints)
├── Task 11: cart-service (Python/FastAPI, in-memory dict, OTel, /cart/{user_id} endpoints)
├── Task 12: order-service (Python/FastAPI, SQLite, OTel, /orders endpoints, gRPC client to payment)
├── Task 13: checkout-service (Python/FastAPI, OTel, orchestrates catalog→inventory→cart→payment→order)
└── Task 14: payment-gateway (Go/Chi, in-memory mock, OTel Go SDK, gRPC server + HTTP /charge endpoint)

Wave 3 (Telemetry + alerting + dashboards + chaos + load + CI — all independent, max parallelism):
├── Task 15: SLO definitions (slo/*.yaml per service with availability + latency objectives + error budgets)
├── Task 16: Prometheus recording rules + multi-window multi-burn-rate alert rules
├── Task 17: 5 provisioned Grafana dashboards (Service Overview, RED per-service, Trace Explorer, Log Explorer, SLO Status)
├── Task 18: Alertmanager config (route to incident-engine webhook + inhibit rules)
├── Task 19: Synthetic monitor (Python script running in-cluster, hits /healthz on all services)
├── Task 20: Chaos Mesh install + RBAC + 4 experiment manifests (PodChaos, NetworkChaos, StressChaos, DNSChaos)
├── Task 21: k6 scripts (smoke, load, stress, spike) + k6 Job template
└── Task 26: GitHub Actions workflows (lint, test, build) + docker-bake.hcl

Wave 4 (Integration + E2E + docs — depends on alerts/AM from Wave 3):
├── Task 22: incident-engine (Python service: Alertmanager webhook → pull Tempo traces + Loki logs → write postmortem.md)
├── Task 23: End-to-end stack deployment verification (devenv up → all services healthy → telemetry flowing)
├── Task 24: Full demo orchestration script (Makefile target: make demo = load → chaos → wait → verify postmortem)
└── Task 25: Documentation (README.md, ARCHITECTURE.md, RUNBOOK.md, docs/examples/postmortems/incident-2026-XX-XX.md)

Wave FINAL (4 parallel reviews, then user explicit OK):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality + lint + type-check review
├── Task F3: Real E2E QA from clean state (devenv up → demo → verify)
└── Task F4: Scope fidelity check (no AI slop, no scope creep)
→ Present results → Get explicit user OK before completion

Critical Path: T1 (devenv) → T2 (k3d) → T3 (Collector) → T13 (checkout) → T16 (alert rules) → T22 (incident-engine) → T23 (E2E) → T24 (demo) → F1-F4 → user OK
Parallel Speedup: ~75% faster than sequential
Max Concurrent: 8 (Waves 1, 3)
```

Note: Wave numbering renumbered from the originally drafted 6-wave structure to 4 implementation waves + FINAL after Oracle phase-2 review flagged Wave 4 (2 tasks) and Wave 6 (1 task) as sub-threshold. T20, T21, T26 (GitHub Actions) merged into Wave 3 (8 tasks); T22–T25 (incident-engine, E2E verify, demo, docs) collapsed into Wave 4 (4 tasks). All task numbers (1–26) preserved.

### Dependency Matrix

- **T1 (devenv)**: None (start immediately)
- **T2 (Terraform k3d)**: None
- **T3 (OTel Collector)**: None
- **T4 (Tempo)**: T2
- **T5 (Loki)**: T2
- **T6 (Prom+Alertmanager)**: T2
- **T7 (Grafana)**: T4, T5, T6
- **T8 (Umbrella chart)**: None
- **T9-T14 (services)**: T1, T8
- **T15 (SLO defs)**: T6 (Prom must exist)
- **T16 (alert rules)**: T6, T9-T14 (services must be deployed to scrape)
- **T17 (dashboards)**: T7, T15, T9-T14
- **T18 (Alertmanager config)**: T6
- **T19 (synthetic monitor)**: T9-T14 (services exist)
- **T20 (Chaos Mesh)**: T2 (cluster must exist)
- **T21 (k6 scripts)**: T9-T14 (services exist), T1 (k6 in devenv)
- **T26 (GitHub Actions)**: T9-T14 (must have working code to test)
- **T22 (incident-engine)**: T6, T16, T18 (Prom, alert rules, AM config from Wave 3)
- **T23 (E2E verify)**: T1-T22 (everything must be deployed)
- **T24 (demo script)**: T22, T23 (incident-engine + verified stack)
- **T25 (docs)**: T23, T24 (verified stack + demo results)
- **F1-F4**: T1-T26

### Agent Dispatch Summary

- **Wave 1 (8 tasks)**: T1=`quick`, T2=`quick`, T3=`unspecified-high`, T4=`quick`, T5=`quick`, T6=`unspecified-high`, T7=`unspecified-high`, T8=`quick` → quick × 5, unspecified-high × 3
- **Wave 2 (6 tasks)**: T9=`quick`, T10=`quick`, T11=`quick`, T12=`quick`, T13=`unspecified-high`, T14=`quick` → quick × 5, unspecified-high × 1
- **Wave 3 (8 tasks)**: T15=`quick`, T16=`unspecified-high`, T17=`visual-engineering`, T18=`quick`, T19=`quick`, T20=`quick`, T21=`quick`, T26=`quick` → quick × 6, unspecified-high × 1, visual-engineering × 1
- **Wave 4 (4 tasks)**: T22=`unspecified-high`, T23=`unspecified-high`, T24=`unspecified-high`, T25=`writing` → unspecified-high × 3, writing × 1
- **Wave FINAL (4 tasks)**: F1=`oracle`, F2=`unspecified-high`, F3=`unspecified-high`, F4=`deep`

---

## TODOs

> Implementation + Test = ONE Task. Never separate.
> EVERY task MUST have: Recommended Agent Profile + Parallelization info + QA Scenarios.
> **A task WITHOUT QA Scenarios is INCOMPLETE. No exceptions.**
> **FORMAT**: Task labels MUST use bare numbers: `1.`, `2.`, `3.` — NOT `T1.`, `Task 1.`, `Phase 1:`.
> The /start-work progress counter requires exact format. Deviation = progress shows 0/0.
> Final Verification Wave labels MUST use `F1.`, `F2.`, etc. — NOT `T-F1.`, `F-1.`, `Final 1:`.

- [x] 1. devenv.sh + Nix flakes for Python 3.12, Go 1.22, k6, kubectl, helm, terraform, gRPC tools

  **What to do**:
  - Create `devenv.nix` providing all required tooling with pinned versions
  - Declare scripts `up`, `down`, `clean`, `demo`, `lint`, `test` in `devenv.nix`; `devenv.yaml` remains the supported inputs/config file
  - Configure `languages.python = { enable = true; version = "3.12"; }` with uv-managed deps via `[uv]`
  - Configure `languages.go = { enable = true; version = "1.22"; }`
  - Pin: terraform 1.7+, kubectl 1.29+, helm 3.14+, k6 0.50+, grpcurl 1.8+, kustomize 5+
  - Add scripts that source env vars for `KUBECONFIG=~/.kube/config-observability` and `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.observability:4317`
  - Verify `devenv shell -- terraform version` outputs pinned version

  **Must NOT do**:
  - Do not create `requirements.txt` (use uv)
  - Do not add Vagrant, VirtualBox, or any VM-based tooling
  - Do not add Docker Desktop-specific commands

  **Recommended Agent Profile**:
  - **Category**: `quick` — single config file, well-known toolchain
  - **Skills**: none required
  - **Skills Evaluated but Omitted**:
    - `git-master`: not needed, this is initial scaffolding

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3, 4, 5, 6, 7, 8)
  - **Blocks**: Tasks 9-14 (services need dev env), Task 26 (CI needs same toolchain)
  - **Blocked By**: None (start immediately)

  **References**:
  - devenv.sh docs: https://devenv.sh/getting-started/
  - devenv.sh Python integration: https://devenv.sh/languages/python/
  - devenv.sh Go integration: https://devenv.sh/languages/go/

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: devenv shell starts and tools are available
    Tool: Bash (devenv shell -- <cmd>)
    Preconditions: Clean repo, devenv installed on host
    Steps:
      1. devenv shell -- python --version
      2. devenv shell -- go version
      3. devenv shell -- terraform version
      4. devenv shell -- kubectl version --client
      5. devenv shell -- helm version
      6. devenv shell -- k6 version
      7. devenv shell -- grpcurl --version
    Expected Result: All commands exit 0 with correct pinned versions
    Evidence: .omo/evidence/task-1-devenv-tools.txt

  Scenario: devenv scripts are wired
    Tool: Bash (devenv shell -- make <target>)
    Preconditions: Makefile present
    Steps:
      1. devenv shell -- make -n up
      2. devenv shell -- make -n down
      3. devenv shell -- make -n demo
    Expected Result: Each target shows commands without error (dry-run)
    Evidence: .omo/evidence/task-1-devenv-scripts.txt
  ```

  **Commit**: YES
  - Message: `feat(infra): devenv.nix with Python 3.12, Go 1.22, k6, terraform, kubectl, helm`
  - Files: `devenv.nix`, `devenv.yaml`, `Makefile`
  - Pre-commit: `devenv shell -- make lint` (if Makefile target exists)

- [x] 2. Terraform k3d cluster provisioning + namespaces + local registry

  **What to do**:
  - Create `infra/cluster/main.tf` using `rahulwagh/k3d` provider (or null_resource + k3d CLI)
  - Cluster name: `observability-platform`, k3s version pinned to v1.29.x
  - Servers: 1 node, agents: 2 nodes, server memory 6GB, agent memory 4GB each
  - Port mappings: 8080 (Grafana), 9090 (Prometheus), 9093 (Alertmanager), 3100 (Loki), 3200 (Tempo), 4317 (OTLP gRPC), 4318 (OTLP HTTP)
  - Create namespaces: `observability`, `ecom`, `chaos-testing`, `load`, `incident`
  - Create local Docker registry `k3d-observability-registry:5000` and attach to cluster
  - Output: `kubeconfig_path`, `cluster_name`, `registry_url`
  - Add `Makefile` targets: `cluster-up`, `cluster-down`, `cluster-list` wrapping terraform

  **Must NOT do**:
  - Do not use EKS, GKE, AKS, or any cloud provider
  - Do not add Helm releases in this task (separate task)
  - Do not configure TLS / cert-manager (port-forward only)

  **Recommended Agent Profile**:
  - **Category**: `quick` — Terraform HCL + k3d provider, well-documented
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3, 4, 5, 6, 7, 8)
  - **Blocks**: All Helm-based deployments (Tasks 3-7)
  - **Blocked By**: Task 1 (devenv shell must exist to run terraform)

  **References**:
  - k3d getting started: https://k3d.io/stable/#getting-started
  - k3d Terraform provider: https://registry.terraform.io/providers/rahulwagh/k3d/latest/docs
  - Alternative: null_resource pattern using `k3d cluster create` directly

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Cluster provisions from clean state
    Tool: Bash (terraform + kubectl)
    Preconditions: Docker running, devenv shell active
    Steps:
      1. devenv shell -- terraform -chdir=infra/cluster init
      2. devenv shell -- terraform -chdir=infra/cluster apply -auto-approve
      3. sleep 5
      4. devenv shell -- kubectl get nodes
    Expected Result: 3 nodes (1 server + 2 agents) in Ready state
    Evidence: .omo/evidence/task-2-cluster-up.txt

  Scenario: All required namespaces exist
    Tool: Bash (kubectl get ns)
    Preconditions: Cluster exists
    Steps:
      1. kubectl get ns observability ecom chaos-testing load incident
    Expected Result: All 5 namespaces listed with Active status
    Evidence: .omo/evidence/task-2-namespaces.txt

  Scenario: Local registry is reachable from cluster
    Tool: Bash (docker + kubectl)
    Preconditions: Cluster exists
    Steps:
      1. docker ps | grep k3d-observability-registry
      2. kubectl run registry-test --image=busybox --rm -it --restart=Never -- nslookup k3d-observability-registry
    Expected Result: Registry resolves via cluster DNS, container exits 0
    Evidence: .omo/evidence/task-2-registry.txt

  Scenario: Cluster can be torn down
    Tool: Bash (terraform destroy)
    Preconditions: Cluster exists
    Steps:
      1. devenv shell -- terraform -chdir=infra/cluster destroy -auto-approve
      2. docker ps | grep k3d-observability
    Expected Result: Cluster destroyed, no k3d containers remain
    Evidence: .omo/evidence/task-2-cluster-down.txt
  ```

  **Commit**: YES
  - Message: `feat(infra): terraform k3d cluster with 6GB/4GB memory + 5 namespaces + local registry`
  - Files: `infra/cluster/main.tf`, `infra/cluster/variables.tf`, `infra/cluster/outputs.tf`, `infra/cluster/terraform.tfvars`
  - Pre-commit: `devenv shell -- terraform -chdir=infra/cluster validate`

- [x] 3. OpenTelemetry Collector config + Helm chart

  **What to do**:
  - Create `infra/otel-collector/configmap.yaml` with full Collector pipeline:
    - Receivers: `otlp` (gRPC :4317, HTTP :4318)
    - Processors: `batch` (timeout 5s, send_batch_size 1024), `memory_limiter` (check_interval 1s, limit_mib 1024), `resource` (add `k8s.*` attributes), `attributes` (add `deployment.environment=dev`)
    - Exporters: `otlp/tempo` (traces → Tempo :4317), `otlphttp/loki` (logs → Loki :3100), `prometheusremotewrite` (metrics → Prom :9090)
    - Service: pipelines for traces, logs, metrics; telemetry logs at info level
  - Create `infra/otel-collector/helm/` chart: Deployment (2 replicas, 256MB/512MB), Service (ClusterIP, ports 4317+4318+8889 for health), ConfigMap mounting the config, ServiceAccount with minimal RBAC
  - Deploy through the repository-owned `infra/otel-collector/helm/` chart; render and validate the synchronized Collector configuration before Helm upgrade
  - Add `infra/otel-collector/values.yaml` with resource limits

  **Must NOT do**:
  - Do not configure sampling (full retention for demo)
  - Do not add file-based exporters (OTLP only)
  - Do not deploy as DaemonSet (single Deployment, 2 replicas)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — OTel Collector config has many gotchas (pipeline wiring, resource detection, exporter endpoints)
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 4, 5, 6, 7, 8)
  - **Blocks**: Services (Tasks 9-14) need OTLP endpoint configured
  - **Blocked By**: Task 2 (cluster must exist for kubectl apply)

  **References**:
  - OTel Collector config: https://opentelemetry.io/docs/collector/configuration/
  - OTel Collector receivers: https://github.com/open-telemetry/opentelemetry-collector/blob/main/receiver/otlpreceiver/README.md
  - OTel Collector k8s attributes: https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/k8sattributesprocessor

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Collector pod starts and reports healthy
    Tool: Bash (kubectl + curl)
    Preconditions: Cluster running
    Steps:
      1. helm upgrade --install otel-collector infra/otel-collector/helm -n observability --create-namespace
      2. kubectl wait --for=condition=ready pod -l app=otel-collector -n observability --timeout=60s
      3. kubectl logs -l app=otel-collector -n observability --tail=20
      4. curl http://localhost:8889/status
    Expected Result: 2/2 pods Ready, logs show pipeline started, /status returns receivers healthy
    Evidence: .omo/evidence/task-3-collector-up.txt

  Scenario: OTLP endpoint accepts test span
    Tool: Bash (otel-cli or curl with OTLP HTTP)
    Preconditions: Collector running
    Steps:
      1. Use any OTLP test client to POST a test trace to localhost:4318/v1/traces
      2. kubectl logs -l app=otel-collector -n observability --tail=5
    Expected Result: Collector logs "traces exported" or similar success message
    Evidence: .omo/evidence/task-3-collector-otlp.txt

  Scenario: Collector survives memory pressure
    Tool: Bash (kubectl stress)
    Preconditions: Collector running
    Steps:
      1. Send 1000 spans in burst
      2. Verify collector pod stays Ready (not OOMKilled)
    Expected Result: Pod remains Ready, batch processor handles backpressure
    Evidence: .omo/evidence/task-3-collector-burst.txt
  ```

  **Commit**: YES
  - Message: `feat(observability): OTel Collector with traces/logs/metrics pipelines (2 replicas, memory_limiter, batch)`
  - Files: `infra/otel-collector/configmap.yaml`, `infra/otel-collector/helm/*`, `infra/otel-collector/kustomize/*`
  - Pre-commit: `devenv shell -- helm lint infra/otel-collector/helm`

- [x] 4. Tempo deployment (traces) with local filesystem storage

  **What to do**:
  - Create `infra/tempo/helm-values.yaml` overriding defaults:
    - `storage.trace.backend: local` + `local.path: /var/tempo/traces`
    - `storage.trace.wal: local` (write-ahead-log)
    - `compactor.compaction.block_retention: 168h` (7 days)
    - Do not set the removed `querier.max_query_duration` field; Tempo 2.9 rejects it, so use the chart's supported default query limit
    - Service: ports 3100 (HTTP), 4317 (OTLP gRPC) — but in this case we go through Collector
    - Persistence: PVC 10Gi for traces + WAL
  - Deploy via `helm install tempo grafana/tempo -n observability -f infra/tempo/helm-values.yaml`
  - Verify Tempo HTTP API at `http://tempo.observability:3200`

  **Must NOT do**:
  - Do not configure S3/GCS/Azure backends
  - Do not enable authentication (open access inside cluster)
  - Do not configure metrics generator (we use Prom for metrics)

  **Recommended Agent Profile**:
  - **Category**: `quick` — Helm values + known config keys
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3, 5, 6, 7, 8)
  - **Blocks**: Grafana (Task 7) needs Tempo as datasource
  - **Blocked By**: Task 2 (cluster must exist)

  **References**:
  - Tempo Helm chart: https://github.com/grafana/helm-charts/tree/main/charts/tempo
  - Tempo configuration: https://grafana.com/docs/tempo/latest/configuration/

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Tempo pod is ready
    Tool: Bash (helm + kubectl + curl)
    Preconditions: Cluster running
    Steps:
      1. helm repo add grafana https://grafana.github.io/helm-charts
      2. helm install tempo grafana/tempo -n observability -f infra/tempo/helm-values.yaml --create-namespace
      3. kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=tempo -n observability --timeout=120s
      4. curl -sf http://tempo.observability:3200/ready | grep -q ready
    Expected Result: 200 OK with "ready" in response
    Evidence: .omo/evidence/task-4-tempo-ready.txt

  Scenario: Tempo accepts traces via OTLP
    Tool: Bash (curl OTLP HTTP)
    Preconditions: Tempo running, Collector running
    Steps:
      1. Send a test trace through the Collector (or directly to Tempo :4317)
      2. curl -G http://tempo.observability:3200/api/search --data-urlencode "tags=service.name=test" | jq '.traces | length'
    Expected Result: ≥1 trace returned
    Evidence: .omo/evidence/task-4-tempo-search.json

  Scenario: Tempo PVC is mounted
    Tool: Bash (kubectl)
    Preconditions: Tempo running
    Steps:
      1. kubectl get pvc -n observability | grep tempo
      2. kubectl exec -n observability deploy/tempo -- df -h /var/tempo/traces
    Expected Result: PVC bound, mount shows available space
    Evidence: .omo/evidence/task-4-tempo-pvc.txt
  ```

  **Commit**: YES
  - Message: `feat(observability): Tempo trace storage with local filesystem + 7d retention`
  - Files: `infra/tempo/helm-values.yaml`, `infra/tempo/Makefile` (or kustomize)
  - Pre-commit: `helm lint infra/tempo/helm-values.yaml`

- [x] 5. Loki deployment (logs) with filesystem storage

  **What to do**:
  - Create `infra/loki/helm-values.yaml`:
    - Single binary mode (`deploymentMode: SingleBinary`)
    - Storage: `filesystem` (PVC 10Gi at `/loki`)
    - Schema: v13 with TSDB/tsdb-shipper (the supported Loki 3.x single-binary store; boltdb-shipper is legacy)
    - Limits: `retention_period: 168h` (7 days), `ingestion_rate_mb: 50`, `ingestion_burst_size_mb: 100`
    - Service ports: 3100 (HTTP)
    - Auth: disabled
  - Deploy via Helm (`grafana/loki`)
  - Verify Loki HTTP API at `http://loki.observability:3100`

  **Must NOT do**:
  - Do not configure S3/GCS/Azure storage
  - Do not enable authentication
  - Do not use microservices mode (single binary is sufficient)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3, 4, 6, 7, 8)
  - **Blocks**: Grafana (Task 7)
  - **Blocked By**: Task 2 (cluster must exist)

  **References**:
  - Loki Helm chart: https://github.com/grafana/helm-charts/tree/main/charts/loki
  - Loki single binary: https://grafana.com/docs/loki/latest/installation/local/

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Loki pod is ready and accepts log queries
    Tool: Bash (kubectl + curl)
    Preconditions: Cluster running
    Steps:
      1. helm install loki grafana/loki -n observability -f infra/loki/helm-values.yaml
      2. kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=loki -n observability --timeout=120s
      3. curl -sf http://loki.observability:3100/ready | grep -q ready
      4. curl -G http://loki.observability:3100/loki/api/v1/labels --data-urlencode "query=%7B%7D"
    Expected Result: /ready returns ready, /labels returns list of labels (initially empty list is OK)
    Evidence: .omo/evidence/task-5-loki-ready.json

  Scenario: Loki accepts pushed logs
    Tool: Bash (curl + log push)
    Preconditions: Loki running
    Steps:
      1. POST a log entry to Loki push API
      2. curl -G http://loki.observability:3100/loki/api/v1/query --data-urlencode 'query={service="test"}'
    Expected Result: Query returns the pushed log line within 30s
    Evidence: .omo/evidence/task-5-loki-push.json
  ```

  **Commit**: YES
  - Message: `feat(observability): Loki log aggregation with filesystem storage + 7d retention`
  - Files: `infra/loki/helm-values.yaml`
  - Pre-commit: `helm lint infra/loki/helm-values.yaml`

- [x] 6. Prometheus + Alertmanager deployment with scrape configs and alert routing

  **What to do**:
  - Create `infra/prometheus/helm-values.yaml`:
    - `server.retention: 15d`
    - `server.persistentVolume.size: 10Gi`
    - `server.global.scrape_interval: 15s`
    - `server.configMapOverrideName: prometheus-config` (we provide custom config)
  - Create `infra/prometheus/configmap.yaml` with `prometheus.yml`:
    - Scrape jobs: `kubernetes-pods` (all pods in `ecom`, `observability`, `incident` namespaces with annotation `prometheus.io/scrape=true`)
    - Scrape jobs: `otel-collector` (metrics from Collector)
    - Scrape jobs: `kube-state-metrics` (if installed)
  - Create `infra/prometheus/alertmanager-config.yaml` with route to incident-engine:
    - `receiver: incident-engine`
    - `webhook_config: url=http://incident-engine.incident.svc:8088/webhook`
    - Group by: alertname, service
    - Group wait: 10s, group interval: 30s, repeat interval: 4h
    - Second receiver: `null` (drop) for testing
  - Enable kube-state-metrics subchart for pod-level metrics
  - Deploy via Helm (`prometheus-community/prometheus`)

  **Must NOT do**:
  - Do not enable remote write to external systems (we use OTel Collector → Prom remote_write)
  - Do not enable Pushgateway
  - Do not enable Thanos/Cortex/Mimir sidecars

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — Prometheus config + alert routing has subtle gotchas (label matchers, route trees, receiver config)
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3, 4, 5, 7, 8)
  - **Blocks**: SLO/alert rules (Task 16), synthetic monitor (Task 19), incident-engine (Task 22)
  - **Blocked By**: Task 2 (cluster must exist)

  **References**:
  - Prometheus Helm chart: https://github.com/prometheus-community/helm-charts/tree/main/charts/prometheus
  - Prometheus configuration: https://prometheus.io/docs/prometheus/latest/configuration/configuration/
  - Alertmanager config: https://prometheus.io/docs/alerting/latest/configuration/

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Prometheus and Alertmanager pods are ready
    Tool: Bash (kubectl + curl)
    Preconditions: Cluster running
    Steps:
      1. helm install prometheus prometheus-community/prometheus -n observability -f infra/prometheus/helm-values.yaml
      2. kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=prometheus -n observability --timeout=180s
      3. kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=alertmanager -n observability --timeout=60s
      4. curl -sf http://prometheus.observability:9090/-/ready | grep -q "Prometheus is ready"
      5. curl -sf http://alertmanager.observability:9093/-/ready | grep -q "OK"
    Expected Result: Both pods Ready, both readiness endpoints OK
    Evidence: .omo/evidence/task-6-prom-am-ready.txt

  Scenario: Prometheus scrapes OTel Collector
    Tool: Bash (curl Prom API)
    Preconditions: Prom + Collector running
    Steps:
      1. curl -G http://prometheus.observability:9090/api/v1/query --data-urlencode 'query=up{job="otel-collector"}'
    Expected Result: Vector returned with value=1 for Collector pod
    Evidence: .omo/evidence/task-6-prom-scrape.json

  Scenario: Alertmanager configured with incident-engine route
    Tool: Bash (curl AM API)
    Preconditions: Prom + AM running
    Steps:
      1. curl http://alertmanager.observability:9093/api/v1/receivers | jq '.[].name'
      2. curl http://alertmanager.observability:9093/api/v1/routes | jq '.[].receiver'
    Expected Result: "incident-engine" receiver present, route tree has incident-engine as terminal
    Evidence: .omo/evidence/task-6-am-routes.json
  ```

  **Commit**: YES
  - Message: `feat(observability): Prometheus + Alertmanager with 15d retention + kube-state-metrics + incident-engine route`
  - Files: `infra/prometheus/helm-values.yaml`, `infra/prometheus/configmap.yaml`, `infra/prometheus/alertmanager-config.yaml`
  - Pre-commit: `helm lint infra/prometheus/helm-values.yaml`

- [x] 7. Grafana deployment with provisioned datasources and base config

  **What to do**:
  - Create `infra/grafana/helm-values.yaml`:
    - `adminPassword: admin` (demo only)
    - `service.type: ClusterIP`
    - `persistence.enabled: true, size: 5Gi`
    - `sidecar.datasources.enabled: true, label: grafana_datasource`
    - `sidecar.dashboards.enabled: true, label: grafana_dashboard, folder: /var/lib/grafana/dashboards`
  - Create `infra/grafana/datasources.yaml` with 4 datasources:
    - Prometheus (default, http://prometheus.observability:9090)
    - Tempo (http://tempo.observability:3200, with trace-to-logs link)
    - Loki (http://loki.observability:3100)
    - Alertmanager (http://alertmanager.observability:9093)
  - Configure trace/log/metric correlation:
    - Tempo datasource: `jsonData.tracesToLogsV2` linking to Loki with `traceId` tag
    - Prometheus datasource: `jsonData.tracesToLogsV2` linking to Tempo/Loki
  - Add a base dashboard folder `/var/lib/grafana/dashboards/ecom/` (dashboards added in Task 17)
  - Deploy via Helm (`grafana/grafana`)

  **Must NOT do**:
  - Do not enable OAuth/SSO (admin/admin is fine for demo)
  - Do not enable alerting in Grafana (we use Alertmanager)
  - Do not configure external image rendering

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — datasource provisioning JSON has subtle gotchas (secureJsonData, UID uniqueness, correlation link JSON paths)
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3, 4, 5, 6, 8)
  - **Blocks**: Dashboards (Task 17)
  - **Blocked By**: Tasks 4, 5, 6 (Tempo, Loki, Prom must exist)

  **References**:
  - Grafana Helm chart: https://github.com/grafana/helm-charts/tree/main/charts/grafana
  - Grafana provisioning: https://grafana.com/docs/grafana/latest/administration/provisioning/
  - Trace-to-logs: https://grafana.com/docs/grafana/latest/datasources/tempo/#trace-to-logs

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Grafana pod is ready and admin login works
    Tool: Bash (kubectl + curl)
    Preconditions: Cluster running, Tempo/Loki/Prom all running
    Steps:
      1. helm install grafana grafana/grafana -n observability -f infra/grafana/helm-values.yaml
      2. kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=grafana -n observability --timeout=120s
      3. kubectl port-forward -n observability svc/grafana 3000:80 &
      4. sleep 3
      5. curl -sf -u admin:admin http://localhost:3000/api/datasources | jq '.[].name'
    Expected Result: 4 datasources returned: Prometheus, Tempo, Loki, Alertmanager
    Evidence: .omo/evidence/task-7-grafana-datasources.json

  Scenario: Trace-to-logs correlation is configured
    Tool: Bash (curl Grafana API)
    Preconditions: Grafana running with datasources
    Steps:
      1. curl -sf -u admin:admin http://localhost:3000/api/datasources/uid/PromUID | jq '.jsonData.tracesToLogsV2'
    Expected Result: tracesToLogsV2 config contains datasourceUid pointing to Loki + tags[traceId]
    Evidence: .omo/evidence/task-7-grafana-correlation.json
  ```

  **Commit**: YES
  - Message: `feat(observability): Grafana with 4 provisioned datasources + trace/log/metric correlation`
  - Files: `infra/grafana/helm-values.yaml`, `infra/grafana/datasources.yaml`
  - Pre-commit: `helm lint infra/grafana/helm-values.yaml`

- [x] 8. Umbrella Helm chart skeleton (base templates for all services)

  **What to do**:
  - Create `charts/svc-template/` Helm chart with templates:
    - `deployment.yaml` — Deployment with standard labels, resource limits (256MB req / 512MB limit), liveness/readiness probes, OTel env vars
    - `service.yaml` — ClusterIP service with standard ports
    - `serviceaccount.yaml` — minimal SA
    - `_helpers.tpl` — standard naming helpers, label helpers
    - `configmap.yaml` — for non-secret env vars (only if needed)
    - `NOTES.txt` — minimal
  - Create `charts/svc-template/values.yaml` with documented defaults:
    - `image.repository`, `image.tag`, `image.pullPolicy`
    - `service.port`, `service.targetPort`
    - `resources`, `replicas`, `env`
  - Add `Makefile` target `lint-chart` that lints this chart
  - Document in `charts/README.md` how to consume: `helm template my-svc charts/svc-template -f my-values.yaml`

  **Must NOT do**:
  - Do not add ingress templates (port-forward only)
  - Do not add HPA / PDB (single-replica demo services)
  - Do not add NetworkPolicy templates (added later if needed)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3, 4, 5, 6, 7)
  - **Blocks**: Tasks 9-14 (services use this chart as base)
  - **Blocked By**: None

  **References**:
  - Helm chart best practices: https://helm.sh/docs/chart_best_practices/
  - OTel env vars: https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Chart lints clean
    Tool: Bash (helm)
    Preconditions: Repo cloned
    Steps:
      1. devenv shell -- helm lint charts/svc-template
      2. devenv shell -- helm template test charts/svc-template --set image.repository=foo --set image.tag=bar
    Expected Result: Lint returns 0 errors, template renders valid YAML
    Evidence: .omo/evidence/task-8-chart-lint.txt

  Scenario: Chart produces standard deployment with OTel env
    Tool: Bash (helm template + yq/jq)
    Preconditions: Chart present
    Steps:
      1. devenv shell -- helm template test charts/svc-template --set image.repository=foo --set image.tag=bar > /tmp/rendered.yaml
      2. yq '.spec.template.spec.containers[0].env' /tmp/rendered.yaml | grep OTEL
    Expected Result: OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_SERVICE_NAME, OTEL_RESOURCE_ATTRIBUTES all present
    Evidence: .omo/evidence/task-8-chart-otel-env.txt
  ```

  **Commit**: YES
  - Message: `feat(charts): svc-template umbrella chart with OTel env + standard probes`
  - Files: `charts/svc-template/*`, `charts/README.md`
  - Pre-commit: `helm lint charts/svc-template`

- [x] 9. catalog-service (Python/FastAPI + SQLite + OTel auto-instrumentation)

  **What to do**:
  - Initialize `services/catalog-service/` with `uv init`, Python 3.12
  - Dependencies: `fastapi`, `uvicorn[standard]`, `pydantic`, `opentelemetry-distro[otlp]`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-sqlite3`, `opentelemetry-instrumentation-requests`, `pytest`, `pytest-asyncio`, `httpx`
  - Endpoints:
    - `GET /healthz` → `{"status": "ok"}`
    - `GET /readyz` → checks SQLite connectivity
    - `GET /products` → list all products
    - `GET /products/{id}` → single product
    - `POST /products` → create (for seed)
    - `GET /products/search?q=` → search by name
  - SQLite schema: `products(id TEXT PRIMARY KEY, name TEXT, description TEXT, price_cents INT, sku TEXT UNIQUE)`
  - Seed file: 20 sample products on startup if table empty
  - OTel: auto-instrument FastAPI + SQLite3 + requests; manual spans only for business-critical operations
  - Dockerfile (multi-stage, Python 3.12-slim)
  - Chart: `charts/catalog-service/` wrapping `svc-template` with overrides
  - Tests: pytest covering happy path + 404 + DB error + slow query (latency injection)

  **Must NOT do**:
  - Do not use SQLAlchemy (use raw `sqlite3` stdlib — minimal abstraction)
  - Do not implement caching (Redis, etc.)
  - Do not implement auth
  - Do not generate proto files (gRPC is not used here)

  **Recommended Agent Profile**:
  - **Category**: `quick` — straightforward FastAPI CRUD + OTel auto-instrumentation
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 10, 11, 12, 13, 14)
  - **Blocks**: checkout-service (Task 13), E2E verification (Task 23)
  - **Blocked By**: Tasks 1 (devenv), 8 (chart template)

  **References**:
  - FastAPI OTel: https://opentelemetry.io/docs/languages/python/automatic/
  - OTel Python contrib: https://github.com/open-telemetry/opentelemetry-python-contrib/tree/main/instrumentation
  - uv: https://docs.astral.sh/uv/

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Service starts and serves health endpoint
    Tool: Bash (uv + uvicorn + curl)
    Preconditions: devenv shell, port 8000 free
    Steps:
      1. cd services/catalog-service && uv run uvicorn app.main:app --port 8000 &
      2. sleep 3
      3. curl -sf http://localhost:8000/healthz | jq -e '.status == "ok"'
    Expected Result: Returns 200 with status ok
    Evidence: .omo/evidence/task-9-catalog-healthz.json

  Scenario: Products CRUD works
    Tool: Bash (curl)
    Preconditions: Service running with seeded data
    Steps:
      1. curl -sf http://localhost:8000/products | jq '. | length' (expect 20)
      2. curl -sf http://localhost:8000/products/p001 | jq '.sku'
      3. curl -sf http://localhost:8000/products/search?q=laptop | jq '.[0].name'
      4. curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/products/nonexistent (expect 404)
    Expected Result: 20 products, valid product, search hit, 404 for missing
    Evidence: .omo/evidence/task-9-catalog-crud.json

  Scenario: OTel auto-instrumentation emits spans
    Tool: Bash (curl + otel-cli or local Tempo query)
    Preconditions: Service running, OTel Collector running
    Steps:
      1. curl http://localhost:8000/products/p001
      2. Query Collector logs / Tempo for trace containing service.name=catalog-service
    Expected Result: Spans emitted: HTTP server span + sqlite3 client span (visible in Collector logs)
    Evidence: .omo/evidence/task-9-catalog-otel.txt

  Scenario: Pytest passes
    Tool: Bash (uv run pytest)
    Preconditions: Test files present
    Steps:
      1. cd services/catalog-service && uv run pytest -v
    Expected Result: All tests pass (≥4 tests: healthz, list, get 404, search)
    Evidence: .omo/evidence/task-9-catalog-pytest.txt

  Scenario: Ruff + ty pass
    Tool: Bash (uv run)
    Preconditions: Code complete
    Steps:
      1. cd services/catalog-service && uv run ruff check .
      2. cd services/catalog-service && uv run ty check .
    Expected Result: Both exit 0 with no findings
    Evidence: .omo/evidence/task-9-catalog-lint.txt

  Scenario: Docker image builds and pushes
    Tool: Bash (docker + helm)
    Preconditions: Dockerfile present
    Steps:
      1. docker build -t k3d-observability-registry:5000/catalog-service:dev services/catalog-service/
      2. docker push k3d-observability-registry:5000/catalog-service:dev
      3. helm install catalog charts/catalog-service -n ecom --create-namespace
      4. kubectl wait --for=condition=ready pod -l app=catalog-service -n ecom --timeout=60s
    Expected Result: Image builds, pushes, pod ready
    Evidence: .omo/evidence/task-9-catalog-deploy.txt
  ```

  **Commit**: YES
  - Message: `feat(services): catalog-service with FastAPI, SQLite, OTel auto-instrumentation`
  - Files: `services/catalog-service/*`, `charts/catalog-service/*`, `Dockerfile`
  - Pre-commit: `ruff check . && ty check . && pytest`

- [x] 10. inventory-service (Python/FastAPI + SQLite + OTel)

  **What to do**:
  - Same structure as catalog-service, but endpoints are stock-related:
    - `GET /healthz`, `GET /readyz`
    - `GET /inventory/{sku}` → `{"sku": "X", "available": 50, "reserved": 5}`
    - `POST /inventory/{sku}/reserve` → atomic decrement with row-level lock
    - `POST /inventory/{sku}/release` → atomic increment
    - `GET /inventory` → list all stock
  - SQLite schema: `inventory(sku TEXT PRIMARY KEY, available INT, reserved INT, updated_at TIMESTAMP)`
  - Seed: inventory for all 20 catalog products
  - gRPC server (port 50051) for internal service-to-service calls from checkout
    - Proto: `inventory.proto` with `CheckStock`, `ReserveStock`, `ReleaseStock` RPCs
    - Generate Python stubs with `uv run python -m grpc_tools.protoc`
  - OTel auto-instrumentation: FastAPI + SQLite + grpcio (server + client)
  - Same test/lint/build pattern as catalog-service

  **Must NOT do**:
  - Do not use Redis (SQLite only)
  - Do not use SQLAlchemy
  - Do not implement optimistic locking (SQLite row-level lock is sufficient for demo)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 9, 11, 12, 13, 14)
  - **Blocks**: checkout-service (Task 13)
  - **Blocked By**: Tasks 1, 8

  **References**:
  - gRPC Python quickstart: https://grpc.io/docs/languages/python/quickstart/
  - gRPC OTel: https://opentelemetry.io/docs/languages/python/instrumentation/#grpc-instrumentation

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Inventory REST endpoints work
    Tool: Bash (curl)
    Preconditions: Service running
    Steps:
      1. curl -sf http://localhost:8001/inventory/SKU-001 | jq '.available'
      2. curl -sf -X POST http://localhost:8001/inventory/SKU-001/reserve -d '{"quantity":3}' -H "Content-Type: application/json" | jq '.available'
      3. curl -sf -X POST http://localhost:8001/inventory/SKU-001/release -d '{"quantity":3}' -H "Content-Type: application/json" | jq '.available'
    Expected Result: Decrements and increments correctly
    Evidence: .omo/evidence/task-10-inventory-rest.json

  Scenario: gRPC CheckStock works
    Tool: Bash (grpcurl)
    Preconditions: Service running with reflection enabled
    Steps:
      1. grpcurl -plaintext localhost:50051 list
      2. grpcurl -plaintext -d '{"sku":"SKU-001"}' localhost:50051 inventory.InventoryService/CheckStock | jq '.available'
    Expected Result: List shows InventoryService, CheckStock returns available count
    Evidence: .omo/evidence/task-10-inventory-grpc.txt

  Scenario: Concurrent reservation doesn't oversell
    Tool: Bash (parallel curl)
    Preconditions: Service running, inventory SKU-001 = 10
    Steps:
      1. for i in $(seq 1 20); do curl -X POST http://localhost:8001/inventory/SKU-001/reserve -d '{"quantity":1}' -H "Content-Type: application/json" & done; wait
      2. curl http://localhost:8001/inventory/SKU-001 | jq '.available'
    Expected Result: available = -10 (some requests fail with 409, but DB state remains consistent)
    Evidence: .omo/evidence/task-10-inventory-race.json
    NOTE: Implement proper 409 if insufficient stock; this scenario verifies both the success and the rejection paths.

  Scenario: OTel emits gRPC server spans
    Tool: Bash (grpcurl + Collector logs)
    Preconditions: Service running, Collector running
    Steps:
      1. grpcurl -plaintext -d '{"sku":"SKU-001"}' localhost:50051 inventory.InventoryService/CheckStock
      2. Check Collector logs for grpc.server span
    Expected Result: grpc.server span visible in Collector output with service.name=inventory-service
    Evidence: .omo/evidence/task-10-inventory-otel.txt

  Scenario: Lint + test pass
    Tool: Bash (uv)
    Preconditions: Code complete
    Steps:
      1. cd services/inventory-service && uv run ruff check . && uv run ty check . && uv run pytest -v
    Expected Result: All pass
    Evidence: .omo/evidence/task-10-inventory-lint.txt
  ```

  **Commit**: YES
  - Message: `feat(services): inventory-service with FastAPI + gRPC + SQLite + OTel`
  - Files: `services/inventory-service/*`, `services/inventory-service/proto/*.proto`, `charts/inventory-service/*`
  - Pre-commit: `ruff check . && ty check . && pytest`

- [x] 11. cart-service (Python/FastAPI + in-memory + OTel)

  **What to do**:
  - Same structure as catalog-service, but state is in-memory dict (no DB):
    - `GET /healthz`, `GET /readyz`
    - `GET /cart/{user_id}` → `{"user_id": "X", "items": [{"sku": "Y", "qty": 2}], "total_cents": 5000}`
    - `POST /cart/{user_id}/items` → add item
    - `DELETE /cart/{user_id}/items/{sku}` → remove item
    - `DELETE /cart/{user_id}` → clear cart
  - OTel auto-instrumentation: FastAPI + requests (for any outbound calls)
  - Note: in-memory state means pod restart loses cart data — document this as a demo limitation
  - Same test/lint/build pattern

  **Must NOT do**:
  - Do not use Redis
  - Do not persist to disk
  - Do not implement cart expiration TTL

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 9, 10, 12, 13, 14)
  - **Blocks**: checkout-service (Task 13)
  - **Blocked By**: Tasks 1, 8

  **References**: (same as catalog)

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Cart CRUD works
    Tool: Bash (curl)
    Preconditions: Service running
    Steps:
      1. curl -sf -X POST http://localhost:8002/cart/user123/items -d '{"sku":"SKU-001","qty":2}' -H "Content-Type: application/json" | jq '.items | length'
      2. curl -sf http://localhost:8002/cart/user123 | jq '.items[0].sku'
      3. curl -sf -X DELETE http://localhost:8002/cart/user123/items/SKU-001 | jq '.items | length'
      4. curl -sf -X DELETE http://localhost:8002/cart/user123 | jq '.items | length'
    Expected Result: Add (length=1), get (sku matches), delete (length=0), clear (length=0)
    Evidence: .omo/evidence/task-11-cart-crud.json

  Scenario: Multiple users have separate carts
    Tool: Bash (curl)
    Preconditions: Service running
    Steps:
      1. curl -sf -X POST http://localhost:8002/cart/alice/items -d '{"sku":"SKU-001","qty":1}' -H "Content-Type: application/json"
      2. curl -sf -X POST http://localhost:8002/cart/bob/items -d '{"sku":"SKU-002","qty":1}' -H "Content-Type: application/json"
      3. curl http://localhost:8002/cart/alice | jq '.[0].sku'
      4. curl http://localhost:8002/cart/bob | jq '.[0].sku'
    Expected Result: Each user has independent cart
    Evidence: .omo/evidence/task-11-cart-multi.json

  Scenario: Lint + test pass
    Tool: Bash (uv)
    Preconditions: Code complete
    Steps:
      1. cd services/cart-service && uv run ruff check . && uv run ty check . && uv run pytest -v
    Expected Result: All pass
    Evidence: .omo/evidence/task-11-cart-lint.txt
  ```

  **Commit**: YES
  - Message: `feat(services): cart-service with FastAPI + in-memory state + OTel`
  - Files: `services/cart-service/*`, `charts/cart-service/*`
  - Pre-commit: `ruff check . && ty check . && pytest`

- [x] 12. order-service (Python/FastAPI + SQLite + OTel + gRPC client)

  **What to do**:
  - Same structure as catalog-service:
    - `GET /healthz`, `GET /readyz`
    - `GET /orders` → list all orders (optionally filtered by user_id)
    - `GET /orders/{id}` → single order
    - `POST /orders` → create order from checkout payload
    - Schema: `orders(id TEXT PRIMARY KEY, user_id TEXT, items JSON, total_cents INT, status TEXT, created_at TIMESTAMP)`
    - Statuses: `pending`, `paid`, `failed`, `shipped`
  - gRPC client to payment-gateway for status queries (NOT for charging — that's checkout's job)
    - Canonical gateway RPC `payment.v1.PaymentService.GetStatus(charge_id)`; order persists the returned charge ID for status lookups
  - OTel auto-instrumentation: FastAPI + SQLite + requests + grpcio
  - Same test/lint/build pattern

  **Must NOT do**:
  - Do not implement payment processing (only consumes status)
  - Do not implement refunds
  - Do not use SQLAlchemy

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 9, 10, 11, 13, 14)
  - **Blocks**: checkout-service (Task 13)
  - **Blocked By**: Tasks 1, 8

  **References**: (same as catalog)

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Order CRUD works
    Tool: Bash (curl)
    Preconditions: Service running
    Steps:
      1. curl -sf -X POST http://localhost:8004/orders -d '{"user_id":"u1","items":[{"sku":"SKU-001","qty":2}],"total_cents":5000}' -H "Content-Type: application/json" | jq '.id'
      2. curl -sf http://localhost:8004/orders/<ID> | jq '.status'
      3. curl -sf 'http://localhost:8004/orders?user_id=u1' | jq '. | length'
    Expected Result: Order created with id, status=pending, filter returns 1 order
    Evidence: .omo/evidence/task-12-order-crud.json

  Scenario: Lint + test pass
    Tool: Bash (uv)
    Preconditions: Code complete
    Steps:
      1. cd services/order-service && uv run ruff check . && uv run ty check . && uv run pytest -v
    Expected Result: All pass
    Evidence: .omo/evidence/task-12-order-lint.txt

  Scenario: gRPC client to payment-gateway works (will fail if payment-gateway not yet deployed — that's OK)
    Tool: Bash (uv run + grpcurl)
    Preconditions: order-service has gRPC client configured, payment-gateway may not be running yet
    Steps:
      1. cd services/order-service && uv run python -c "from app.payment_client import get_payment_status; print('imports ok')"
    Expected Result: Imports succeed (test of compile/link only — runtime test deferred to E2E)
    Evidence: .omo/evidence/task-12-order-grpc-import.txt
  ```

  **Commit**: YES
  - Message: `feat(services): order-service with FastAPI + SQLite + OTel + gRPC client`
  - Files: `services/order-service/*`, `charts/order-service/*`
  - Pre-commit: `ruff check . && ty check . && pytest`

- [x] 13. checkout-service (Python/FastAPI orchestrator + OTel)

  **What to do**:
  - Orchestrator service that calls all other services in sequence to complete checkout
  - Endpoints:
    - `GET /healthz`, `GET /readyz` (readiness = can reach all downstream services)
    - `POST /checkout` → input `{"user_id": "X", "payment_method": "card"}`, output `{"order_id": "...", "status": "paid"|"failed", "total_cents": ...}`
  - Orchestration flow:
    1. `GET cart-service/cart/{user_id}` (HTTP)
    2. For each item: `gRPC inventory.InventoryService/ReserveStock` (synchronous)
    3. `gRPC payment-gateway.PaymentService/Charge` (synchronous)
    4. On payment success: `POST order-service/orders` (HTTP)
    5. On any failure: rollback (release inventory, refund payment if charged)
  - OTel: auto-instrument FastAPI + requests + grpcio. Add manual span around the entire checkout flow with attribute `checkout.user_id`, `checkout.item_count`
  - Add feature flag via env var `FAILURE_INJECTION_RATE` (0.0-1.0) — if >0, randomly fail checkout at specified rate for chaos demonstration
  - Tests:
    - Happy path (mock all downstream services via httpx mock + grpcio mock)
    - Insufficient inventory
    - Payment declined
    - Cart empty
    - Rollback verification (each step's compensation is called)

  **Must NOT do**:
  - Do not implement async event-driven saga (synchronous orchestration is sufficient for demo)
  - Do not implement distributed transactions
  - Do not add retry logic (let chaos demonstrate failures)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — multi-service orchestration with rollback logic is non-trivial
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 9, 10, 11, 12, 14)
  - **Blocks**: E2E verification (Task 23), incident-engine tests (Task 22)
  - **Blocked By**: Tasks 1, 8, AND must be after Tasks 9-12, 14 in spirit (but implementer needs only stubs of interfaces; can mock in tests)

  **References**:
  - Saga pattern: https://microservices.io/patterns/data/saga.html
  - Compensation: https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Happy path checkout succeeds end-to-end
    Tool: Bash (curl + multiple services running)
    Preconditions: All 6 services running locally OR mocked
    Steps:
      1. Add item to cart via cart-service
      2. POST http://localhost:8003/checkout -d '{"user_id":"alice","payment_method":"card"}' -H "Content-Type: application/json" | jq '.status'
      3. Verify order-service has the order
      4. Verify payment-gateway has the charge record
    Expected Result: status=paid, order exists, payment record exists
    Evidence: .omo/evidence/task-13-checkout-happy.json

  Scenario: Insufficient inventory fails gracefully
    Tool: Bash (curl with seeded inventory=0)
    Preconditions: A SKU with 0 available stock
    Steps:
      1. Add that SKU to cart
      2. POST /checkout
    Expected Result: Returns 409 or 500 with descriptive error, NO order created, NO payment charged
    Evidence: .omo/evidence/task-13-checkout-inv-fail.json

  Scenario: Payment decline rolls back inventory reservation
    Tool: Bash (curl with decline-triggering payment)
    Preconditions: Payment service configured to decline if amount > some threshold
    Steps:
      1. Add high-value item to cart
      2. POST /checkout
      3. Query inventory service — verify stock is restored
    Expected Result: status=failed, inventory back to original level
    Evidence: .omo/evidence/task-13-checkout-rollback.json

  Scenario: Distributed trace shows full checkout flow
    Tool: Bash (curl + Tempo query)
    Preconditions: All services running, OTel Collector + Tempo running
    Steps:
      1. POST /checkout
      2. Extract trace_id from response headers (X-Trace-Id or similar)
      3. Query Tempo API for that trace_id
    Expected Result: Trace contains spans from: checkout-service (parent), cart-service, inventory-service, payment-gateway, order-service (with correct parent-child relationships)
    Evidence: .omo/evidence/task-13-checkout-trace.json

  Scenario: Failure injection flag causes failures
    Tool: Bash (env var)
    Preconditions: FAILURE_INJECTION_RATE=0.5
    Steps:
      1. POST /checkout 10 times
      2. Count successes vs failures
    Expected Result: ~50% failure rate (allow ±15% margin)
    Evidence: .omo/evidence/task-13-checkout-fault-injection.txt

  Scenario: Pytest with mocked downstream services passes
    Tool: Bash (uv run pytest)
    Preconditions: pytest fixtures mock all downstream services
    Steps:
      1. cd services/checkout-service && uv run pytest -v
    Expected Result: ≥4 tests pass (happy, inv fail, payment fail, rollback)
    Evidence: .omo/evidence/task-13-checkout-pytest.txt

  Scenario: Lint + ty pass
    Tool: Bash (uv)
    Steps:
      1. cd services/checkout-service && uv run ruff check . && uv run ty check .
    Expected Result: Both pass
    Evidence: .omo/evidence/task-13-checkout-lint.txt
  ```

  **Commit**: YES
  - Message: `feat(services): checkout-service orchestrator with rollback + failure injection`
  - Files: `services/checkout-service/*`, `charts/checkout-service/*`
  - Pre-commit: `ruff check . && ty check . && pytest`

- [x] 14. payment-gateway (Go/Chi + OTel Go SDK + gRPC server)

  **What to do**:
  - Initialize Go module: `services/payment-gateway/` with `go mod init github.com/example/payment-gateway`
  - Go 1.22, dependencies:
    - `github.com/go-chi/chi/v5`
    - `go.opentelemetry.io/otel` + `go.opentelemetry.io/otel/sdk` + `go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc`
    - `go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp`
    - `go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc`
    - `google.golang.org/grpc` + `google.golang.org/protobuf`
    - `github.com/stretchr/testify` (testing)
    - `golangci-lint` compatible code
  - HTTP server (port 8005):
    - `GET /healthz`, `GET /readyz`
    - `POST /charge` → `{"order_id": "...", "amount_cents": 5000, "card_token": "tok_test"}` → `{"status": "approved"|"declined", "charge_id": "..."}`
    - Logic: approve if `amount_cents < 100000`, else decline (or random ~10% decline via `FAILURE_RATE` env var)
  - gRPC server (port 50052):
    - Service: `payment.v1.PaymentService`
    - RPCs: `Charge`, `GetStatus`, `Refund`
    - Proto: `proto/payment.proto`
  - State: in-memory map `charges[charge_id] = Charge{...}`
  - OTel: full instrumentation of HTTP + gRPC server + gRPC client (for refund calls). Resource attributes include `service.name=payment-gateway`
  - Dockerfile (multi-stage golang:1.22 → distroless/static)
  - Helm chart: `charts/payment-gateway/`
  - Tests: unit tests for charge approval logic, gRPC client tests using in-memory gRPC server

  **Must NOT do**:
  - Do not integrate with real Stripe/Adyen
  - Do not use a real database (in-memory map)
  - Do not implement PCI-compliant card storage (token-only)
  - Do not implement retry/backoff (let failures be visible)

  **Recommended Agent Profile**:
  - **Category**: `quick` — Go service with standard OTel SDK usage
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 9, 10, 11, 12, 13)
  - **Blocks**: checkout-service E2E test (Task 13 verifies gRPC interop), polyglot trace verification (Task 23)
  - **Blocked By**: Task 1 (devenv with Go 1.22 must exist)

  **References**:
  - OTel Go quickstart: https://opentelemetry.io/docs/languages/go/getting-started/
  - OTel Go gRPC: https://github.com/open-telemetry/opentelemetry-go-contrib/tree/main/instrumentation/google.golang.org/grpc/otelgrpc
  - Chi router: https://github.com/go-chi/chi

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: HTTP charge endpoint works
    Tool: Bash (curl)
    Preconditions: Service running
    Steps:
      1. curl -sf -X POST http://localhost:8005/charge -d '{"order_id":"o1","amount_cents":5000,"card_token":"tok"}' -H "Content-Type: application/json" | jq '.status'
      2. curl -sf -X POST http://localhost:8005/charge -d '{"order_id":"o2","amount_cents":200000,"card_token":"tok"}' -H "Content-Type: application/json" | jq '.status'
      3. curl -sf http://localhost:8005/healthz | jq '.status'
    Expected Result: small amount=approved, large amount=declined, healthz=ok
    Evidence: .omo/evidence/task-14-pay-http.json

  Scenario: gRPC Charge RPC works
    Tool: Bash (grpcurl)
    Preconditions: Service running with reflection enabled
    Steps:
      1. grpcurl -plaintext localhost:50052 list
      2. grpcurl -plaintext -d '{"order_id":"o3","amount_cents":5000}' localhost:50052 payment.v1.PaymentService/Charge | jq '.status'
    Expected Result: payment.v1.PaymentService listed, Charge returns status=APPROVED
    Evidence: .omo/evidence/task-14-pay-grpc.txt

  Scenario: Failure rate env var works
    Tool: Bash (restart service with env var)
    Preconditions: FAILURE_RATE=1.0
    Steps:
      1. Restart service with FAILURE_RATE=1.0
      2. POST /charge 5 times
    Expected Result: All 5 declined
    Evidence: .omo/evidence/task-14-pay-failrate.txt

  Scenario: go test passes
    Tool: Bash (go test)
    Preconditions: Code complete
    Steps:
      1. cd services/payment-gateway && go test ./... -v
    Expected Result: ≥3 tests pass (charge logic, gRPC server, OTel init)
    Evidence: .omo/evidence/task-14-pay-test.txt

  Scenario: golangci-lint + go vet pass
    Tool: Bash (golangci-lint + go vet)
    Preconditions: Code complete
    Steps:
      1. cd services/payment-gateway && go vet ./...
      2. cd services/payment-gateway && golangci-lint run
    Expected Result: Both pass
    Evidence: .omo/evidence/task-14-pay-lint.txt

  Scenario: Docker image builds
    Tool: Bash (docker build)
    Preconditions: Dockerfile present
    Steps:
      1. docker build -t k3d-observability-registry:5000/payment-gateway:dev services/payment-gateway/
      2. docker push k3d-observability-registry:5000/payment-gateway:dev
    Expected Result: Image builds, multi-stage final image < 30MB
    Evidence: .omo/evidence/task-14-pay-image.txt

  Scenario: Polyglot trace continuity (Python checkout → Go payment)
    Tool: Bash (checkout + Tempo query)
    Preconditions: All services + OTel + Tempo running
    Steps:
      1. POST /checkout to checkout-service
      2. Extract trace_id from response
      3. Query Tempo for that trace_id
      4. Verify trace contains spans from BOTH checkout-service (Python) and payment-gateway (Go) under same trace_id
    Expected Result: Single trace with cross-language spans, W3C traceparent header propagated correctly
    Evidence: .omo/evidence/task-14-pay-polyglot-trace.json
  ```

  **Commit**: YES
  - Message: `feat(services): payment-gateway in Go (Chi + gRPC + OTel Go SDK)`
  - Files: `services/payment-gateway/*`, `charts/payment-gateway/*`, `Dockerfile`
  - Pre-commit: `go vet ./... && golangci-lint run && go test ./...`

- [x] 15. SLO definitions per service with availability + latency objectives + error budgets

  **What to do**:
  - Create `slo/` directory with one YAML file per service:
    - `slo/catalog-service.yaml` — availability 99.95%, latency p99 < 200ms, error budget 21.6 min/30d
    - `slo/inventory-service.yaml` — availability 99.95%, latency p99 < 150ms
    - `slo/cart-service.yaml` — availability 99.9%, latency p99 < 100ms
    - `slo/checkout-service.yaml` — availability 99.9%, latency p99 < 500ms
    - `slo/order-service.yaml` — availability 99.9%, latency p99 < 300ms
    - `slo/payment-gateway.yaml` — availability 99.5%, latency p99 < 1000ms
    - `slo/end-to-end-checkout.yaml` — success rate 99%, latency p99 < 2000ms
  - YAML schema:
    ```yaml
    service: catalog-service
    slos:
      - name: availability
        sli:
          spec: http_server_requests_total{status!~"5.."}
          total_spec: http_server_requests_total
        objective: 0.9995
        window: 30d
        error_budget: 21m36s
      - name: latency_p99
        sli:
          spec: http_server_request_duration_seconds_bucket{le="0.2"}
          total_spec: http_server_request_duration_seconds_count
        objective: 0.99
        window: 30d
    ```
  - Create `slo/README.md` explaining the multi-window multi-burn-rate strategy and how to add a new service

  **Must NOT do**:
  - Do not generate Prometheus recording rules here (Task 16)
  - Do not generate alert rules here (Task 16)
  - Do not invent fictional SLO numbers; use the table in this plan as the source of truth

  **Recommended Agent Profile**:
  - **Category**: `quick` — YAML authoring, well-defined schema
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 16, 17, 18, 19, 20, 21, 26)
  - **Blocks**: Task 16 (alert rules reference these SLOs), Task 17 (dashboards reference SLOs)
  - **Blocked By**: Task 6 (Prom must exist for scrape targets)

  **References**:
  - Google SRE Workbook SLO chapter: https://sre.google/workbook/table-of-contributions/
  - sloth.dev SLO spec: https://docs.sloth.dev/

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: All SLO files validate against schema
    Tool: Bash (python + jsonschema or yamllint + yq)
    Preconditions: Schema validator script present
    Steps:
      1. uv run python scripts/validate_slo.py slo/*.yaml
    Expected Result: All 7 SLO files pass validation (no schema errors)
    Evidence: .omo/evidence/task-15-slo-validate.txt

  Scenario: SLO math is correct
    Tool: Bash (python)
    Preconditions: Schema validator
    Steps:
      1. python -c "
        for s in slo/*.yaml:
          import yaml
          d = yaml.safe_load(open(s))
          for slo in d['slos']:
            budget_sec = (1 - slo['objective']) * 30*86400
            assert slo['error_budget'] == f'{int(budget_sec//60)}m{int(budget_sec%60)}s'
        print('all SLO error budgets correctly computed')
        "
    Expected Result: Prints "all SLO error budgets correctly computed"
    Evidence: .omo/evidence/task-15-slo-math.txt
  ```

  **Commit**: YES
  - Message: `feat(slo): per-service SLI/SLO definitions with availability + latency objectives + error budgets`
  - Files: `slo/*.yaml`, `slo/README.md`, `scripts/validate_slo.py`
  - Pre-commit: `uv run python scripts/validate_slo.py slo/*.yaml`

- [x] 16. Prometheus recording rules + multi-window multi-burn-rate alert rules

  **What to do**:
  - Create `observability/prometheus-rules/sli_recording_rules.yaml`:
    - Per-service SLO recording rules: `sli:servicename:availability:ratio_30d`, `sli:servicename:latency_p99:ratio_30d`
    - Per-service error budget remaining: `slo:servicename:availability:error_budget_remaining:30d`
    - Burn-rate windows: `sli:servicename:availability:ratio_5m`, `...:ratio_30m`, `...:ratio_1h`, `...:ratio_6h`, `...:ratio_1d`, `...:ratio_3d`
  - Create `observability/prometheus-rules/alert_rules.yaml` using multi-window multi-burn-rate (MWMB):
    - Per-service alerts: `SLOAvailabilityBurnTooFast`, `SLOLatencyBurnTooFast`
    - Severity tiers:
      - Page (critical): 2% budget in 1h (1h short window, 6h long window)
      - Ticket (warning): 5% budget in 6h (6h short, 3d long)
    - Standard alerts: `ServiceDown` (up==0 for 1m), `HighErrorRate` (5xx > 5% for 5m), `HighLatencyP99` (p99 > SLO threshold for 5m)
  - Create `observability/prometheus-rules/kustomization.yaml` mounting both files as ConfigMap
  - Patch `infra/prometheus/helm-values.yaml` to mount these rules via `server.configMapOverrideName` (or use prometheus-operator pattern if simpler)
  - Verify rules load: `curl http://prometheus.observability:9090/api/v1/rules`

  **Must NOT do**:
  - Do not use simple threshold-based alerts (use MWMB exclusively for SLO alerts)
  - Do not invent alert severities; use only the two-tier model above
  - Do not configure PagerDuty / OpsGenie integration (use Alertmanager webhook only)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — Prometheus rule YAML syntax is finicky; MWMB math is easy to get wrong
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 15, 17, 18, 19, 20, 21, 26)
  - **Blocks**: Task 22 (incident-engine depends on alerts firing)
  - **Blocked By**: Task 6 (Prom must exist), Task 15 (SLOs defined), Tasks 9-14 (services scraped for SLI metrics)

  **References**:
  - MWMB alerting: https://sre.google/workbook/alerting-on-slos/
  - Prometheus rule syntax: https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/
  - Prometheus alert syntax: https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Recording rules load successfully
    Tool: Bash (curl Prom API)
    Preconditions: Prom running with rules mounted
    Steps:
      1. curl http://prometheus.observability:9090/api/v1/rules | jq '.data.groups[].rules[].name' | head -20
    Expected Result: Multiple recording rules listed per service (sli:*, slo:*)
    Evidence: .omo/evidence/task-16-rules-loaded.json

  Scenario: Alert rules are syntactically valid
    Tool: Bash (promtool)
    Preconditions: promtool available via `devenv shell`
    Steps:
      1. promtool check rules observability/prometheus-rules/alert_rules.yaml
      2. promtool check rules observability/prometheus-rules/sli_recording_rules.yaml
    Expected Result: Both files pass with 0 errors
    Evidence: .omo/evidence/task-16-rules-check.txt

  Scenario: Synthetic alert can fire
    Tool: Bash (kubectl exec into collector to generate failed scrape)
    Preconditions: All running
    Steps:
      1. Apply a ConfigMap patch that injects bogus target into Prom scrape config
      2. Wait 90s
      3. curl http://prometheus.observability:9090/api/v1/alerts | jq '.data.alerts[].labels.alertname'
    Expected Result: At least one alert fires (ServiceDown or similar)
    Evidence: .omo/evidence/task-16-alert-fires.json
    NOTE: This verifies alert routing end-to-end. Use a reversible change; rollback after verification.

  Scenario: MWMB alert math is correct
    Tool: Bash (python simulation)
    Preconditions: Rule files present
    Steps:
      1. python scripts/verify_mwmb.py observability/prometheus-rules/alert_rules.yaml
    Expected Result: Script confirms burn-rate windows match Google SRE Workbook spec (1h/6h for page, 6h/3d for ticket)
    Evidence: .omo/evidence/task-16-mwmb-verify.txt
  ```

  **Commit**: YES
  - Message: `feat(observability): MWMB recording + alert rules per service`
  - Files: `observability/prometheus-rules/*.yaml`, `scripts/verify_mwmb.py`
  - Pre-commit: `promtool check rules observability/prometheus-rules/*.yaml`

- [x] 17. 5 provisioned Grafana dashboards (Service Overview, RED per-service, Trace Explorer, Log Explorer, SLO Status)

  **What to do**:
  - Create `dashboards/` directory with 5 dashboard JSON files (exported from Grafana):
    - `01-service-overview.json` — fleet-wide health: up status grid, request rate by service, error rate by service, latency p99 by service
    - `02-red-catalog.json` — RED metrics for catalog-service specifically (Rate, Errors, Duration) with sparklines
    - `02-red-inventory.json`, `02-red-cart.json`, `02-red-checkout.json`, `02-red-order.json`, `02-red-payment.json` — same template, different service
    - `03-trace-explorer.json` — Tempo search panel with trace-to-logs link to Loki + trace-to-metrics link to Prometheus
    - `04-log-explorer.json` — Loki query panel with service label filter, log-to-trace link to Tempo
    - `05-slo-status.json` — Per-service error budget gauge, burn rate panel, recent alerts table
  - Each dashboard: stable UID, version, refresh interval, templating variables (`service` dropdown for RED dashboards)
  - Create `dashboards/kustomization.yaml` mounting all JSONs as ConfigMap labeled `grafana_dashboard: "1"` so the Grafana sidecar picks them up
  - Verify each dashboard via Grafana API: `GET /api/dashboards/uid/<uid>`

  **Must NOT do**:
  - Do not create dashboards via Grafana UI (must be JSON exports)
  - Do not include screenshots in JSON (no `image` panels)
  - Do not use Grafana variables that require datasource switching (keep datasource explicit)

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering` — dashboards are a UI/UX-heavy task
  - **Skills**: none required (but visual-engineering category recommended for layout quality)

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 15, 16, 18, 19, 20, 21, 26)
  - **Blocks**: Task 23 (E2E verification will visit dashboards)
  - **Blocked By**: Task 7 (Grafana must exist), Task 15 (SLOs for SLO dashboard), Tasks 9-14 (services for RED data)

  **References**:
  - Grafana dashboard JSON model: https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/view-dashboard-json-model/
  - Provisioning dashboards: https://grafana.com/docs/grafana/latest/administration/provisioning/#dashboards
  - Tempo panel docs: https://grafana.com/docs/grafana/latest/datasources/tempo/query-editor/

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: All 5 dashboards are provisioned and retrievable
    Tool: Bash (curl Grafana API)
    Preconditions: Grafana running with dashboard ConfigMap mounted
    Steps:
      1. for uid in service-overview red-catalog trace-explorer log-explorer slo-status; do
           curl -sf -u admin:admin http://localhost:3000/api/dashboards/uid/$uid | jq '.meta.slug'
         done
    Expected Result: All 5 dashboards return their slug (e.g., "service-overview", "red-catalog", etc.)
    Evidence: .omo/evidence/task-17-dashboards-list.txt

  Scenario: Each dashboard has working panels (no broken datasource references)
    Tool: Bash (curl Grafana API)
    Preconditions: Dashboards loaded
    Steps:
      1. for uid in service-overview red-catalog; do
           curl -sf -u admin:admin "http://localhost:3000/api/datasources/proxy/uid/${datasource_uid}" -o /dev/null -w "%{http_code}\n"
         done
    Expected Result: All HTTP 200 (panels can query datasources)
    Evidence: .omo/evidence/task-17-dashboards-panels.txt

  Scenario: RED dashboard renders real metrics
    Tool: Bash (curl Grafana + check query result)
    Preconditions: Service has been receiving traffic
    Steps:
      1. Use Grafana's API to query the rate panel: curl -X POST http://localhost:3000/api/ds/query with rate(http_server_requests_total{service="catalog-service"}[5m])
    Expected Result: Returns non-empty result
    Evidence: .omo/evidence/task-17-red-data.json

  Scenario: SLO dashboard shows error budget
    Tool: Bash (curl Grafana + check query)
    Preconditions: SLO rules loaded, services receiving traffic
    Steps:
      1. Query slo:catalog-service:availability:error_budget_remaining:30d
    Expected Result: Returns numeric value (positive = budget remaining)
    Evidence: .omo/evidence/task-17-slo-dashboard.json
  ```

  **Commit**: YES
  - Message: `feat(dashboards): 5 provisioned Grafana dashboards (Overview, RED, Trace, Log, SLO)`
  - Files: `dashboards/*.json`, `dashboards/kustomization.yaml`
  - Pre-commit: `curl -sf -u admin:admin http://localhost:3000/api/dashboards/uid/service-overview | jq .meta.slug`

- [x] 18. Alertmanager configuration (route to incident-engine + secondary receiver)

  **What to do**:
  - Refine `infra/prometheus/alertmanager-config.yaml` from Task 6:
    - Primary receiver: `incident-engine` (webhook to incident-engine.incident.svc:8088/webhook)
    - Secondary receiver: `null` (drops alerts — for demo, no Slack/Discord unless user provides webhook URL via env var)
    - Inhibit rule: if `ServiceDown` fires for a service, inhibit `HighErrorRate` and `HighLatencyP99` for that service (avoid alert spam)
  - Add `observability/alertmanager-templates/default.tmpl` for alert notification template (JSON format expected by incident-engine)
  - Document env var: `SLACK_WEBHOOK_URL` (optional) — if set, route a copy of alerts to Slack
  - Apply via Helm upgrade OR kustomize patch

  **Must NOT do**:
  - Do not configure PagerDuty, OpsGenie, VictorOps
  - Do not enable email notifications
  - Do not enable grouping by alertname only (must include service label)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 15, 16, 17, 19, 20, 21, 26)
  - **Blocks**: Task 22 (incident-engine depends on receiving properly formatted webhooks)
  - **Blocked By**: Task 6 (Alertmanager deployed)

  **References**:
  - Alertmanager config: https://prometheus.io/docs/alerting/latest/configuration/
  - Alertmanager templates: https://prometheus.io/docs/alerting/latest/notifications/

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Alertmanager config validates
    Tool: Bash (amtool)
    Preconditions: amtool available via devenv shell
    Steps:
      1. amtool check-config infra/prometheus/alertmanager-config.yaml
    Expected Result: Config validates (exit 0)
    Evidence: .omo/evidence/task-18-am-check.txt

  Scenario: Route tree shows incident-engine as primary
    Tool: Bash (curl AM API)
    Preconditions: AM running
    Steps:
      1. curl http://alertmanager.observability:9093/api/v1/routes | jq '.[].receiver'
    Expected Result: "incident-engine" listed
    Evidence: .omo/evidence/task-18-am-routes.json

  Scenario: Inhibit rule exists
    Tool: Bash (curl AM API)
    Preconditions: AM running
    Steps:
      1. curl http://alertmanager.observability:9093/api/v1/inhibitRules | jq '.[].source_match.labels.alertname'
    Expected Result: Contains "ServiceDown"
    Evidence: .omo/evidence/task-18-am-inhibit.json
  ```

  **Commit**: YES
  - Message: `feat(observability): Alertmanager routes to incident-engine + inhibit rules + templates`
  - Files: `infra/prometheus/alertmanager-config.yaml`, `observability/alertmanager-templates/default.tmpl`
  - Pre-commit: `amtool check-config infra/prometheus/alertmanager-config.yaml`

- [x] 19. Synthetic monitor (Python script running in-cluster, hits /healthz on all services)

  **What to do**:
  - Create `services/synthetic-monitor/`:
    - Python script using `httpx` async client
    - Polls `/healthz` on all 6 services every 30s
    - Reports `synthetic_check_success{service="X"}` and `synthetic_check_duration_seconds{service="X"}` metrics to Prom via OTel (Pushgateway NOT used — use OTel metrics SDK with periodic export)
    - On 3 consecutive failures: log error to Loki (via OTel) + emit a `synthetic_failure` event
  - Helm chart: `charts/synthetic-monitor/` — Deployment, single replica (state not critical), uses svc-template
  - ServiceMonitor OR annotation-based scrape config so Prom scrapes it
  - Tests: pytest with mocked httpx responses (success, failure, timeout)

  **Must NOT do**:
  - Do not use Pushgateway (use OTel metrics + Prom scrape)
  - Do not implement alerting from synthetic monitor itself (let Prom alerts handle that)
  - Do not implement multi-region failover (single synthetic point is sufficient)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 15, 16, 17, 18, 20, 21, 26)
  - **Blocks**: E2E verification (Task 23)
  - **Blocked By**: Tasks 1, 8, AND services (9-14) must exist

  **References**:
  - OTel Python metrics: https://opentelemetry.io/docs/languages/python/instrumentation/#metrics
  - httpx async: https://www.python-httpx.org/async/

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Synthetic monitor runs and reports metrics
    Tool: Bash (kubectl + curl Prom)
    Preconditions: synthetic-monitor deployed, services running
    Steps:
      1. kubectl wait --for=condition=ready pod -l app=synthetic-monitor -n observability --timeout=60s
      2. sleep 60
      3. curl -G http://prometheus.observability:9090/api/v1/query --data-urlencode 'query=synthetic_check_success'
    Expected Result: Vector returned with one sample per service, all value=1
    Evidence: .omo/evidence/task-19-synthetic-metrics.json

  Scenario: Synthetic monitor detects a down service
    Tool: Bash (kubectl delete + observe)
    Preconditions: Synthetic monitor running
    Steps:
      1. kubectl delete pod -l app=catalog-service -n ecom
      2. sleep 90 (3 polls × 30s)
      3. curl -G http://prometheus.observability:9090/api/v1/query --data-urlencode 'query=synthetic_check_success{service="catalog-service"}'
    Expected Result: value=0 for catalog-service
    Evidence: .omo/evidence/task-19-synthetic-detect.json
  ```

  **Commit**: YES
  - Message: `feat(synthetic): in-cluster health monitor with OTel metrics export`
  - Files: `services/synthetic-monitor/*`, `charts/synthetic-monitor/*`
  - Pre-commit: `ruff check . && ty check . && pytest`

- [x] 20. Chaos Mesh installation + RBAC + 4 experiment manifests

  **What to do**:
  - Install Chaos Mesh in `chaos-testing` namespace:
    - `helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-testing --create-namespace --set controllerManager.serviceAccount=rbac enabled=true`
  - Create `chaos/experiments/` with 4 manifests:
    - `pod-kill-checkout.yaml` — PodChaos targeting checkout-service, killing 1 pod every 60s for 5 minutes
    - `network-delay-payment.yaml` — NetworkChaos adding 2000ms latency to traffic going TO payment-gateway, lasting 5 minutes
    - `cpu-stress-catalog.yaml` — StressChaos consuming 80% CPU on one catalog-service pod for 3 minutes
    - `dns-chaos-inventory.yaml` — DNSChaos causing random DNS resolution failures for inventory-service for 2 minutes
  - Each manifest includes: namespace selector, label selector, duration, mode (one/all/fixed%), scheduling
  - Create `chaos/README.md` explaining how to apply each and what the expected blast radius is
  - Add `chaos/Makefile` target `chaos-all` that applies all 4 in sequence with pauses

  **Must NOT do**:
  - Do not use Litmus (Chaos Mesh only)
  - Do not target control-plane components (only user services)
  - Do not run experiments longer than 10 minutes without explicit user approval

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: none required

**Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 15, 16, 17, 18, 19, 21, 26)
  - **Blocks**: Task 24 (demo uses chaos), Task 23 (E2E verification includes chaos)
  - **Blocked By**: Task 2 (cluster must exist)

  **References**:
  - Chaos Mesh install: https://chaos-mesh.org/docs/production-installation/production-installation-by-helm/
  - PodChaos: https://chaos-mesh.org/docs/simulate-pod-chaos-on-kubernetes/
  - NetworkChaos: https://chaos-mesh.org/docs/simulate-network-chaos-on-kubernetes/

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Chaos Mesh controller is ready
    Tool: Bash (kubectl + helm)
    Preconditions: Cluster running
    Steps:
      1. helm repo add chaos-mesh https://charts.chaos-mesh.org
      2. helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-testing --create-namespace
      3. kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=chaos-mesh -n chaos-testing --timeout=180s
    Expected Result: chaos-controller-manager Ready
    Evidence: .omo/evidence/task-20-chaosmesh-ready.txt

  Scenario: PodChaos manifest validates
    Tool: Bash (kubectl apply --dry-run)
    Preconditions: Chaos Mesh installed
    Steps:
      1. kubectl apply -f chaos/experiments/pod-kill-checkout.yaml --dry-run=server
    Expected Result: Validated (no errors)
    Evidence: .omo/evidence/task-20-chaos-validate.txt

  Scenario: PodChaos actually kills a pod
    Tool: Bash (kubectl apply + observe)
    Preconditions: Chaos Mesh installed, checkout-service running with ≥2 replicas
    Steps:
      1. kubectl apply -f chaos/experiments/pod-kill-checkout.yaml
      2. sleep 90
      3. kubectl get pods -n ecom -l app=checkout-service
    Expected Result: At least one pod restart observed (RESTARTS column > 0)
    Evidence: .omo/evidence/task-20-chaos-podkill.txt

  Scenario: NetworkChaos adds latency
    Tool: Bash (kubectl apply + curl timing)
    Preconditions: Chaos Mesh installed, payment-gateway running
    Steps:
      1. Measure baseline: curl -w "%{time_total}" http://payment-gateway.ecom.svc:8005/healthz
      2. kubectl apply -f chaos/experiments/network-delay-payment.yaml
      3. sleep 30
      4. Measure again: curl -w "%{time_total}"
    Expected Result: Time increases by ≥1.5s
    Evidence: .omo/evidence/task-20-chaos-netdelay.txt
  ```

  **Commit**: YES
  - Message: `feat(chaos): Chaos Mesh with 4 experiment manifests (pod-kill, network-delay, cpu-stress, dns-chaos)`
  - Files: `chaos/experiments/*.yaml`, `chaos/README.md`, `chaos/Makefile`
  - Pre-commit: `kubectl apply --dry-run=server -f chaos/experiments/*.yaml`

- [x] 21. k6 scripts (smoke, load, stress, spike) + k6 Job template

  **What to do**:
  - Create `load/k6/` directory with 4 JS scripts:
    - `smoke.js` — 1 VU, 30s, hits `GET /healthz` on each service, asserts 200
    - `load.js` — 50 VUs, 5 min, ramps to 100 VUs, full checkout flow (POST /cart, POST /checkout), asserts p95 < 1s, error rate < 1%
    - `stress.js` — ramps 0 → 200 VUs over 10 min, finds breaking point, asserts system doesn't OOM
    - `spike.js` — baseline 10 VUs, instant spike to 500 VUs for 30s, returns to baseline, asserts error rate < 10% during spike
  - Create `load/k6-job.yaml` — Kubernetes Job template using `grafana/k6:latest` image, mounts scripts via ConfigMap, outputs to `/tmp/results.json`
  - Create `load/Makefile` targets: `load-smoke`, `load-load`, `load-stress`, `load-spike` that apply the Job
  - Each script exports: `http_req_duration`, `http_req_failed`, custom metric `checkout_success_rate`
  - Document in `load/README.md`: how to interpret k6 output, what thresholds mean

  **Must NOT do**:
  - Do not use Locust (rejected per plan decision)
  - Do not run k6 from host (must run as K8s Job)
  - Do not skip thresholds (must be defined and enforced in JS)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 15, 16, 17, 18, 19, 20, 26)
  - **Blocks**: Task 24 (demo includes load step)
  - **Blocked By**: Tasks 9-14 (services must exist to hit), Task 1 (k6 in devenv)

  **References**:
  - k6 scenarios: https://k6.io/docs/using-k6/scenarios/
  - k6 thresholds: https://k6.io/docs/using-k6/thresholds/
  - k6 in Kubernetes: https://k6.io/docs/testing-guides/running-distributed-tests/

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Smoke test passes
    Tool: Bash (kubectl + k6 in pod)
    Preconditions: All services running
    Steps:
      1. kubectl apply -f load/k6-job-smoke.yaml
      2. kubectl wait --for=condition=complete job/k6-smoke -n load --timeout=120s
      3. kubectl logs job/k6-smoke -n load | grep -E "http_req_duration|checks"
    Expected Result: All thresholds met (http_req_duration p95 < 100ms, checks pass rate 100%)
    Evidence: .omo/evidence/task-21-smoke.txt

  Scenario: Load test completes and reports metrics
    Tool: Bash (kubectl + k6)
    Preconditions: All services running
    Steps:
      1. kubectl apply -f load/k6-job-load.yaml
      2. kubectl wait --for=condition=complete job/k6-load -n load --timeout=600s
      3. kubectl logs job/k6-load -n load | tail -30
    Expected Result: p95 latency reported, error rate reported, no OOM kills
    Evidence: .omo/evidence/task-21-load.txt

  Scenario: k6 scripts validate JS syntax
    Tool: Bash (node or k6 archive)
    Preconditions: Scripts present
    Steps:
      1. for f in load/k6/*.js; do node --check "$f" || k6 archive "$f"; done
    Expected Result: No syntax errors
    Evidence: .omo/evidence/task-21-syntax.txt
  ```

  **Commit**: YES
  - Message: `feat(load): k6 scripts (smoke/load/stress/spike) + K8s Job templates`
  - Files: `load/k6/*.js`, `load/k6-job-*.yaml`, `load/Makefile`, `load/README.md`
  - Pre-commit: `node --check load/k6/*.js || true`

- [x] 22. incident-engine Python service (Alertmanager webhook → postmortem.md)

  **What to do**:
  - Create `services/incident-engine/`:
    - FastAPI on port 8088
    - Endpoint: `POST /webhook` accepts Alertmanager webhook payload (JSON)
    - Endpoint: `GET /incidents` returns list of all generated incidents
    - Endpoint: `GET /incidents/{id}` returns specific incident + postmortem content
    - Persistence: SQLite at `/data/incidents.db`
    - Filesystem: writes `postmortem-{timestamp}-{alertname}.md` to `/postmortems/`
  - On webhook receive:
    1. Parse alert labels (alertname, service, severity, slo)
    2. Query Tempo API for traces matching `service.name=<service>` and time window (last 5min)
    3. Query Loki API for logs matching `{service="<service>"}` and same window
    4. Query Prometheus for related metrics (error rate, latency p99) at alert time
    5. Build timeline: detect when alert started (Prom alert firing time), correlate with chaos experiment application time if any
    6. Generate postmortem markdown with sections:
       - Summary (1 paragraph)
       - Impact (which service, which users, error budget consumed)
       - Timeline (UTC timestamps + events)
       - Root Cause (heuristic: check chaos experiments active during window)
       - Resolution (auto-detected when alert stops firing)
       - Lessons Learned (template, to be filled by humans)
    7. Write postmortem.md to `/postmortems/`
    8. Send to optional webhook (Slack/Discord) if `INCIDENT_WEBHOOK_URL` env var set
    9. Persist incident metadata to SQLite
  - Configurable via env vars:
    - `TEMPO_URL`, `LOKI_URL`, `PROM_URL`
    - `POSTMORTEM_DIR` (default `/postmortems`)
    - `INCIDENT_WEBHOOK_URL` (optional)
    - `LOOKBACK_MINUTES` (default 5)
  - Helm chart: `charts/incident-engine/`
  - Tests:
    - Mock all upstream APIs (httpx_mock)
    - Test webhook payload parsing
    - Test timeline generation
    - Test postmortem markdown structure
    - Test persistence

  **Must NOT do**:
  - Do not implement auto-remediation (postmortems are generated, not acted on)
  - Do not implement a web UI
  - Do not require human input (full automation)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — multi-API orchestration + markdown generation is complex
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on alerts + AM config from Wave 3)
  - **Parallel Group**: Wave 4 (with Tasks 23, 24, 25)
  - **Blocks**: Task 24 (demo uses incident-engine), Task 23 (E2E verification)
  - **Blocked By**: Tasks 6, 16, 18 (Prom, alerts, AM config from Wave 1 and Wave 3)

  **References**:
  - Alertmanager webhook payload: https://prometheus.io/docs/alerting/latest/configuration/#webhook_config
  - Tempo HTTP API: https://grafana.com/docs/tempo/latest/api_docs/
  - Loki HTTP API: https://grafana.com/docs/loki/latest/api/
  - Google SRE postmortem template: https://sre.google/sre-postmortem-template/

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: incident-engine starts and exposes endpoints
    Tool: Bash (kubectl + curl)
    Preconditions: Service deployed
    Steps:
      1. kubectl wait --for=condition=ready pod -l app=incident-engine -n incident --timeout=60s
      2. kubectl port-forward -n incident svc/incident-engine 8088:8088 &
      3. sleep 3
      4. curl -sf http://localhost:8088/healthz | jq '.status'
      5. curl -sf http://localhost:8088/incidents | jq '. | length'
    Expected Result: healthz returns ok, incidents returns 0 (empty)
    Evidence: .omo/evidence/task-22-engine-up.json

  Scenario: Webhook creates an incident + postmortem
    Tool: Bash (curl Alertmanager webhook format)
    Preconditions: Service running
    Steps:
      1. POST a fake Alertmanager webhook payload to /webhook
      2. ls /postmortems/ on the pod
      3. cat one postmortem.md
    Expected Result: postmortem file exists with all sections (Summary, Impact, Timeline, Root Cause, Resolution, Lessons)
    Evidence: .omo/evidence/task-22-webhook-postmortem.md

  Scenario: Engine queries Tempo and Loki for context
    Tool: Bash (curl with real alerts + verify queries)
    Preconditions: Real alerts firing, Tempo + Loki have data
    Steps:
      1. Trigger a real alert (e.g., apply chaos experiment)
      2. Wait for AM webhook to fire
      3. Read the generated postmortem
      4. Verify Timeline section contains traces/logs (links or trace IDs)
    Expected Result: Timeline shows actual trace IDs and log entries with timestamps
    Evidence: .omo/evidence/task-22-real-postmortem.md

  Scenario: Pytest covers all branches
    Tool: Bash (uv run pytest)
    Preconditions: Tests written
    Steps:
      1. cd services/incident-engine && uv run pytest -v --cov=app
    Expected Result: Coverage ≥80%, ≥6 tests pass
    Evidence: .omo/evidence/task-22-pytest.txt

  Scenario: Lint + type-check pass
    Tool: Bash (uv)
    Steps:
      1. cd services/incident-engine && uv run ruff check . && uv run ty check .
    Expected Result: Both pass
    Evidence: .omo/evidence/task-22-lint.txt
  ```

  **Commit**: YES
  - Message: `feat(incident-engine): webhook receiver + Tempo/Loki/Prom correlation + postmortem.md generator`
  - Files: `services/incident-engine/*`, `charts/incident-engine/*`
  - Pre-commit: `ruff check . && ty check . && pytest`

- [x] 23. End-to-end deployment verification (devenv up → all services healthy → telemetry flowing)

  **What to do**:
  - Add to `Makefile`:
    - `make up` — full stack bringup: `terraform apply` + `helm install` all observability stack + `helm install` all services + `kubectl wait` for everything
    - `make down` — teardown: `helm uninstall` all + `terraform destroy`
    - `make verify` — comprehensive health check of every component
  - Create `scripts/verify.sh`:
    1. Check cluster exists (3 nodes Ready)
    2. Check all namespaces present
    3. Check all pods Ready (services + observability + incident + synthetic)
    4. Hit each service /healthz via ClusterIP DNS — expect 200
    5. Send a test trace via OTel Collector → query Tempo for it
    6. Send a test log via OTel Collector → query Loki for it
    7. Send a test metric via OTel Collector → query Prom for it
    8. Verify Grafana datasources respond
    9. Verify all 5 dashboards present
    10. Verify alert rules loaded in Prom
    11. Verify incident-engine /incidents responds
  - Output: pass/fail per check, exit non-zero on any failure
  - Document in `docs/SETUP.md` how to run and interpret

  **Must NOT do**:
  - Do not skip any check (all 11 must run)
  - Do not auto-fix failures (just report)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — orchestration script + comprehensive verification
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: NO (must run after everything is deployed)
  - **Parallel Group**: Wave 4 (with Tasks 22, 24, 25)
  - **Blocks**: F1-F4
  - **Blocked By**: All Tasks 1-22

  **References**:
  - Bash strict mode: https://mywiki.wooledge.org/BashGuide/Practices#Always_Quote, https://mywiki.wooledge.org/BashGuide/CompoundCommands#Bash_built-ins

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: make up succeeds from clean state
    Tool: Bash (make + terraform + helm)
    Preconditions: Clean Docker, clean .kube dir
    Steps:
      1. devenv shell -- make clean
      2. devenv shell -- make up 2>&1 | tee /tmp/make-up.log
      3. tail -50 /tmp/make-up.log
    Expected Result: Exits 0, prints "all components ready"
    Evidence: .omo/evidence/task-23-make-up.txt

  Scenario: verify.sh passes all 11 checks
    Tool: Bash (verify.sh)
    Preconditions: Stack is up
    Steps:
      1. devenv shell -- bash scripts/verify.sh 2>&1 | tee /tmp/verify.log
      2. grep -c "PASS" /tmp/verify.log
      3. grep "FAIL" /tmp/verify.log || echo "no failures"
    Expected Result: PASS count = 11, no FAIL lines
    Evidence: .omo/evidence/task-23-verify-pass.txt

  Scenario: Telemetry end-to-end flow works
    Tool: Bash (curl each layer)
    Preconditions: Stack is up
    Steps:
      1. curl catalog-service → produces trace + log + metric
      2. Query Tempo for that trace_id
      3. Query Loki for that trace_id (via log correlation)
      4. Query Prom for catalog_service_requests_total
    Expected Result: All 3 storage systems contain data for the same request
    Evidence: .omo/evidence/task-23-telemetry-flow.json

  Scenario: make down destroys cleanly
    Tool: Bash (make down)
    Preconditions: Stack is up
    Steps:
      1. devenv shell -- make down 2>&1
      2. k3d cluster list
    Expected Result: No observability-platform cluster in list
    Evidence: .omo/evidence/task-23-make-down.txt
  ```

  **Commit**: YES
  - Message: `feat(e2e): Makefile up/down targets + verify.sh comprehensive health check`
  - Files: `Makefile`, `scripts/verify.sh`, `docs/SETUP.md`
  - Pre-commit: `bash -n scripts/verify.sh`

- [x] 24. make demo orchestration script (load → chaos → wait → verify postmortem)

  **What to do**:
  - Add `make demo` target that orchestrates a full end-to-end demonstration:
    1. Verify stack is up (`make verify`)
    2. Start k6 load test in background (load profile, 100 VUs, 5 min)
    3. After 60s, apply chaos experiment (`pod-kill-checkout.yaml`)
    4. Wait 180s (alert should fire, webhook should hit incident-engine)
    5. Verify postmortem.md was created (check `/postmortems/` on incident-engine pod)
    6. Wait for k6 to complete
    7. Pull k6 results, print summary
    8. Copy generated postmortem from pod to `./docs/examples/postmortems/postmortem-demo-{timestamp}.md`
    9. Optionally fetch Grafana dashboard screenshots via Grafana API (`/render/dashboard/...`) for README
  - Script logs each step with timestamps
  - Total runtime: ~10 minutes
  - Document in `docs/DEMO.md` what to expect and how to interpret

  **Must NOT do**:
  - Do not run chaos on control plane
  - Do not run for more than 15 minutes total

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high` — orchestration script that depends on multiple subsystems
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on full stack being up and tested)
  - **Parallel Group**: Wave 4 (with Tasks 22, 23, 25)
  - **Blocks**: Task 25 (docs reference the demo), F3 (real QA scenario)
  - **Blocked By**: Tasks 1-23

  **References**: (none beyond what was referenced in earlier tasks)

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: make demo runs to completion
    Tool: Bash (make)
    Preconditions: Stack up + verified
    Steps:
      1. devenv shell -- make demo 2>&1 | tee /tmp/demo.log
      2. grep -E "postmortem|generated|complete" /tmp/demo.log
    Expected Result: Exits 0, prints "demo complete", postmortem file path
    Evidence: .omo/evidence/task-24-demo.log

  Scenario: Postmortem file exists after demo
    Tool: Bash (find)
    Preconditions: demo just completed
    Steps:
      1. find docs/examples/postmortems/ -name "*.md" -newer Makefile
      2. head -50 docs/examples/postmortems/postmortem-demo-*.md
    Expected Result: ≥1 postmortem file exists with all sections populated
    Evidence: .omo/evidence/task-24-postmortem-found.md

  Scenario: k6 results show realistic metrics
    Tool: Bash (grep + jq)
    Preconditions: demo just completed
    Steps:
      1. find /tmp -name "k6-results.json" -exec jq '.metrics.http_req_duration.values.p95' {} \;
    Expected Result: Numeric p95 latency reported (not null)
    Evidence: .omo/evidence/task-24-k6-results.json
  ```

  **Commit**: YES
  - Message: `feat(demo): make demo orchestration script (load → chaos → postmortem)`
  - Files: `Makefile` (update), `docs/DEMO.md`
  - Pre-commit: `bash -n Makefile`

- [x] 25. Documentation (README, ARCHITECTURE, RUNBOOK, postmortem examples)

  **What to do**:
  - `README.md` (repo root):
    - One-paragraph project description (the resume bullet, slightly expanded)
    - Screenshot or text representation of the architecture diagram
    - Quick Start (3 commands: `devenv shell`, `make up`, `make demo`)
    - Stack list (table)
    - Link to docs/ARCHITECTURE.md, docs/RUNBOOK.md, docs/DEMO.md, docs/SETUP.md
  - `docs/ARCHITECTURE.md`:
    - System diagram (text-based, ASCII or mermaid)
    - Per-service responsibility (catalog, inventory, cart, checkout, order, payment-gateway, incident-engine)
    - Inter-service protocols (HTTP vs gRPC, who calls whom)
    - Telemetry flow (services → OTel SDK → OTLP → Collector → Tempo/Loki/Prom)
    - Alerting flow (Prom rules → AM → incident-engine → postmortem.md)
    - SLO definitions and burn-rate strategy
    - Chaos + Load testing approach
  - `docs/RUNBOOK.md`:
    - Common operations: "service is down", "alert firing", "postmortem not generated", "Grafana is broken", "Prometheus is full"
    - Step-by-step recovery for each
    - How to add a new service
    - How to add a new SLO
    - How to add a new chaos experiment
  - `docs/examples/postmortems/template.md` — annotated template showing every section
  - `docs/examples/postmortems/incident-2026-07-09-checkout-failures.md` — at least one real example postmortem (copied from `make demo` output)
  - All docs must be plain Markdown (no external services like Notion/Confluence)
  - Add `docs/README.md` index

  **Must NOT do**:
  - Do not use external documentation platforms
  - Do not add emojis (unless user explicitly requests)
  - Do not include screenshots in markdown (use text representations or omit)

  **Recommended Agent Profile**:
  - **Category**: `writing` — documentation-heavy task
  - **Skills**: none required

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on demo to have run for example postmortem)
  - **Parallel Group**: Wave 4 (with Tasks 22, 23, 24)
  - **Blocks**: F1-F4 (final verification needs docs)
  - **Blocked By**: Tasks 1-24

  **References**: (none — straightforward writing task)

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: README renders correctly and links work
    Tool: Bash (markdown link checker)
    Preconditions: All docs written
    Steps:
      1. ls README.md docs/ARCHITECTURE.md docs/RUNBOOK.md docs/DEMO.md docs/SETUP.md docs/examples/postmortems/
      2. python -c "import re; [print(m.group()) for m in re.finditer(r'\\[([^\\]]+)\\]\\(([^)]+)\\)', open('README.md').read())]" | head -10
    Expected Result: All 6 files present, all relative links in README resolve to existing files
    Evidence: .omo/evidence/task-25-readme-links.txt

  Scenario: Architecture diagram is syntactically valid mermaid
    Tool: Bash (npx or skip — manually inspect)
    Preconditions: docs/ARCHITECTURE.md exists
    Steps:
      1. grep -A 5 "^\`\`\`mermaid" docs/ARCHITECTURE.md
    Expected Result: Mermaid block present with nodes and edges
    Evidence: .omo/evidence/task-25-arch-mermaid.txt

  Scenario: At least one real postmortem committed
    Tool: Bash (find)
    Preconditions: docs/examples/postmortems/ exists
    Steps:
      1. ls -la docs/examples/postmortems/*.md
      2. head -5 docs/examples/postmortems/incident-*.md
    Expected Result: At least 1 real postmortem from `make demo` output
    Evidence: .omo/evidence/task-25-postmortem-real.md

  Scenario: No broken internal links
    Tool: Bash (grep + test)
    Preconditions: All docs written
    Steps:
      1. for f in $(find docs -name '*.md'); do
           grep -oE '\]\(\.\./[^)]+\)|\]\(\./[^)]+\)' "$f" | while read link; do
             target=$(echo "$link" | sed -E 's/.*\(([^)]+)\)/\1/')
             [ -e "$(dirname $f)/$target" ] || echo "BROKEN: $f -> $target"
           done
         done
    Expected Result: No "BROKEN:" lines printed
    Evidence: .omo/evidence/task-25-links-intact.txt
  ```

  **Commit**: YES
  - Message: `docs: README, ARCHITECTURE, RUNBOOK, DEMO, SETUP + postmortem template and example`
  - Files: `README.md`, `docs/*.md`, `docs/examples/postmortems/*.md`
  - Pre-commit: `bash -c 'for f in $(find docs -name "*.md"); do test -s "$f"; done'`

- [x] 26. GitHub Actions workflows (lint, type-check, test, build images)

  **What to do**:
  - Create `.github/workflows/lint.yaml`:
    - Triggers: push to main, pull_request
    - Matrix: Python services (catalog, inventory, cart, checkout, order, incident-engine, synthetic-monitor) + Go (payment-gateway)
    - Steps: `uv sync`, `uv run ruff check .`, `uv run ty check .`
    - For Go: `go vet ./...`, `golangci-lint run`
    - For Terraform: `terraform -chdir=infra/cluster validate`
    - For Helm: `helm lint charts/*`
  - Create `.github/workflows/test.yaml`:
    - Triggers: push to main, pull_request
    - Matrix: Python services + Go
    - Steps: `uv run pytest` with coverage report, `go test ./... -race -coverprofile`
    - Cache uv and go module downloads
  - Create `.github/workflows/build-images.yaml`:
    - Triggers: push to main, manual dispatch
    - Matrix: all 7 service images
    - Steps: `docker buildx build --push -t ghcr.io/${{ github.repository }}/<service>:${{ github.sha }}`
    - Login to ghcr.io with `${{ secrets.GITHUB_TOKEN }}`
    - Build only (no deploy — demo runs locally)
  - Add `docker-bake.hcl` for parallel image builds
  - All workflows use `ubuntu-latest`

  **Must NOT do**:
  - Do not deploy from CI (no kubectl apply in CI)
  - Do not cache docker layers across services (cache per-service only)
  - Do not require any external secrets (use `GITHUB_TOKEN` only)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: none required (CI YAML is straightforward)

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Tasks 15, 16, 17, 18, 19, 20, 21)
  - **Blocks**: F1-F4 (CI must be configured for verification)
  - **Blocked By**: Tasks 9-14 (services must exist with working code)

  **References**:
  - GitHub Actions matrix: https://docs.github.com/en/actions/using-jobs/using-a-matrix-for-your-jobs
  - ghcr.io: https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry
  - docker buildx: https://docs.docker.com/buildx/working-with-buildx/

  **Acceptance Criteria**: Each QA Scenario below is an acceptance criterion. A task is DONE only when every scenario's Evidence file exists and shows the expected result.

  **QA Scenarios (MANDATORY)**:
  ```
  Scenario: Workflows are syntactically valid YAML
    Tool: Bash (yamllint + actionlint if available)
    Preconditions: Workflow files exist
    Steps:
      1. yamllint .github/workflows/*.yaml
      2. which actionlint && actionlint .github/workflows/*.yaml || echo "actionlint not installed, skipping"
    Expected Result: yamllint passes; if actionlint present, also passes
    Evidence: .omo/evidence/task-26-yaml-lint.txt

  Scenario: Workflows define correct triggers and matrix
    Tool: Bash (yq + manual inspection)
    Preconditions: Workflows present
    Steps:
      1. yq '.on' .github/workflows/lint.yaml
      2. yq '.jobs.<job>.strategy.matrix' .github/workflows/test.yaml
    Expected Result: Triggers include push+pull_request, matrix includes all services
    Evidence: .omo/evidence/task-26-workflow-triggers.txt

  Scenario: docker-bake.hcl is valid HCL
    Tool: Bash (docker buildx bake --print)
    Preconditions: docker-bake.hcl exists
    Steps:
      1. docker buildx bake -f docker-bake.hcl --print | head -50
    Expected Result: Prints valid bake plan
    Evidence: .omo/evidence/task-26-bake-validate.txt
  ```

  **Commit**: YES
  - Message: `ci: GitHub Actions workflows (lint, test, build) + docker-bake.hcl`
  - Files: `.github/workflows/*.yaml`, `docker-bake.hcl`
  - Pre-commit: `yamllint .github/workflows/*.yaml`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE, followed by the autonomous global review and debugging gates required by the active start-work workflow.
>
> Rejection or review feedback triggers a fix-and-rerun loop. Mark F1-F4 complete only after their independent evidence is current and approved.

- [x] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run command, curl endpoint). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in `.omo/evidence/`. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Run `ruff check`, `ty check`, `pytest`, `go vet`, `go test ./...`, `terraform validate`, `helm lint`. Review all changed files for: type suppression (no `# type: ignore` without justification), empty `except:` blocks, debug `print`/`console.log` in prod code, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names (`data`, `result`, `item`, `temp`).
  Output: `Ruff [PASS/FAIL] | ty [PASS/FAIL] | pytest [N pass/N fail] | go vet [PASS/FAIL] | go test [N pass/N fail] | terraform validate [PASS/FAIL] | helm lint [N/N pass] | Files [N clean/N issues] | VERDICT`

- [x] F3. **Real Manual QA** — `unspecified-high`
  Start from clean state (`devenv shell -- make clean && make up`). Execute EVERY QA scenario from EVERY task — follow exact steps, capture evidence. Test cross-task integration (services talking, telemetry flowing, alerts firing, postmortems generated). Test edge cases: OTel Collector down (kill pod, verify services retry), Tempo down (verify Prom still works), chaos with no recovery. Save to `.omo/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify 1:1 — everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT do" compliance. Detect cross-task contamination: Task N touching Task M's files. Flag unaccounted changes. Specifically check: no docker-compose, no real payment processor, no AuthN, no message broker, no API gateway.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

Atomic commits per task. Commit message format: `type(scope): description`.

Examples:
- `feat(infra): devenv.nix with Python 3.12, Go 1.22, k6, terraform, kubectl, helm`
- `feat(infra): terraform k3d cluster with 6GB server + 4GB agent memory`
- `feat(services): catalog-service with FastAPI, OTel auto-instrumentation, SQLite`
- `feat(services): payment-gateway in Go with Chi, OTel, gRPC server`
- `feat(observability): OTel Collector with traces/logs/metrics pipelines`
- `feat(observability): Tempo + Loki + Prometheus + Grafana Helm deployments`
- `feat(slo): per-service SLI/SLO definitions with error budgets`
- `feat(alerting): multi-window multi-burn-rate Prometheus alert rules`
- `feat(dashboards): 5 provisioned Grafana dashboards (Overview, RED, Trace, Log, SLO)`
- `feat(incident-engine): Python service consuming Alertmanager webhooks`
- `feat(chaos): Chaos Mesh with 4 experiment manifests`
- `feat(load): k6 scripts for smoke/load/stress/spike profiles`
- `feat(synthetic): in-cluster health monitor`
- `feat(e2e): make demo orchestration script`
- `docs: README, ARCHITECTURE, RUNBOOK + example postmortem`
- `ci: GitHub Actions workflows for lint, test, build`

Group commit strategy: Wave-level commits permitted for closely related tasks (e.g., `feat(services): all 6 e-commerce services with OTel instrumentation` if they were created in same wave by related agents).

---

## Success Criteria

### Verification Commands

```bash
# All commands run from inside `devenv shell` unless otherwise noted.

# Foundation
devenv shell -- terraform -chdir=infra/cluster init    # Expected: Terraform initialized
devenv shell -- k3d cluster list                       # Expected: observability-platform cluster exists
devenv shell -- kubectl get pods -A                    # Expected: all namespaces Ready

# Services
devenv shell -- kubectl get svc -n ecom                # Expected: 6 services listed
devenv shell -- curl http://catalog.ecom.svc.cluster.local:8000/healthz  # Expected: {"status": "ok"}

# Observability stack
devenv shell -- curl http://tempo.observability.svc:3200/api/search?tags=service.name=catalog-service  # Expected: 200 with traces
devenv shell -- curl http://loki.observability.svc:3100/loki/api/v1/query?query={service="catalog-service"}  # Expected: 200 with logs
devenv shell -- curl http://prometheus.observability.svc:9090/api/v1/query?query=up  # Expected: 200 with metrics
devenv shell -- curl http://grafana.observability.svc:3000/api/dashboards/uid/svc-overview  # Expected: 200 with dashboard JSON

# Alerting + incident engine
devenv shell -- curl http://alertmanager.observability.svc:9093/api/v1/alerts  # Expected: list of alerts
# After chaos experiment:
devenv shell -- ls docs/examples/postmortems/         # Expected: ≥1 generated postmortem.md

# Chaos + load
devenv shell -- kubectl apply -f chaos/experiments/pod-kill-checkout.yaml  # Expected: chaos applied
devenv shell -- kubectl wait --for=condition=complete job/k6-smoke -n load  # Expected: completed

# Tests
devenv shell -- ruff check .                           # Expected: All checks passed!
devenv shell -- ty check .                             # Expected: no errors found
devenv shell -- pytest                                 # Expected: all tests passed
devenv shell -- go vet ./...                           # Expected: clean
devenv shell -- go test ./...                          # Expected: PASS

# Full demo
devenv shell -- make demo                              # Expected: full 5-min demo completes with postmortem generated
```

### Final Checklist

- [ ] All "Must Have" items implemented and verified
- [ ] All "Must NOT Have" items absent (verified by grep)
- [ ] All tests pass (pytest + go test)
- [ ] All linting passes (ruff + ty + go vet + golangci-lint)
- [ ] `devenv shell -- make up` runs successfully from clean state
- [ ] `devenv shell -- make demo` produces a real postmortem.md
- [ ] 5 Grafana dashboards accessible and render data
- [ ] ≥1 example postmortem.md committed in `docs/examples/postmortems/`
- [ ] GitHub Actions workflow file committed under `.github/workflows/`
- [ ] README.md present at repo root
- [ ] ARCHITECTURE.md present at `docs/ARCHITECTURE.md`
- [ ] RUNBOOK.md present at `docs/RUNBOOK.md`
- [ ] No docker-compose.yml, no auth code, no payment-integration code, no message broker
