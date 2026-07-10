# Setup Guide

This guide walks through installing prerequisites, entering the development shell, provisioning the cluster, and verifying the stack.

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Docker | 24.x+ | Required by k3d. Allocate at least 6 GB RAM to Docker. |
| Nix | 2.18+ | Used by devenv to install Python, Go, k6, terraform, kubectl, helm. |
| devenv | latest | `nix profile install nixpkgs#devenv` or follow [devenv.sh](https://devenv.sh). |
| k3d | 5.x+ | Installed automatically via Terraform local-exec if `~/.local/bin/k3d` exists; otherwise install first. |
| shellcheck | 0.10+ | Optional, only needed if you re-lint `scripts/verify.sh`. `nix profile install nixpkgs#shellcheck --impure`. |
| Internet | n/a | Needed to pull Helm charts and container images on first boot. |

## Enter the Development Shell

```bash
cd /home/mei/projects/enten-v2
devenv shell
```

The shell sets:

- `KUBECONFIG=$HOME/.kube/config-observability`
- `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.observability.svc:4317`

Verify the tools:

```bash
python3 --version   # 3.12
go version          # 1.22
terraform version   # >= 1.5
kubectl version     # client 1.29+
helm version
k6 version
```

## Provision the Cluster and Stack

A single command now brings up **everything** (cluster + observability stack + services):

```bash
make up
```

`make up` performs, in order:

1. `terraform -chdir=infra/cluster apply -auto-approve`
   — creates the k3d cluster (`observability-platform`, 1 server + 2 agents), the local registry `k3d-observability-registry:5000`, and the five platform namespaces.
2. `helm repo add` for every required chart (idempotent).
3. `helm upgrade --install otel-collector ./infra/otel-collector/helm --namespace observability`
4. `helm upgrade --install tempo grafana/tempo` (with `infra/tempo/helm-values.yaml`)
5. `helm upgrade --install loki grafana/loki` (with `infra/loki/helm-values.yaml`)
6. `helm upgrade --install prometheus prometheus-community/prometheus` (with `infra/prometheus/helm-values.yaml`, includes the kube-state-metrics + Alertmanager subcharts)
7. `helm upgrade --install grafana grafana/grafana` (with `infra/grafana/helm-values.yaml`)
8. `kubectl apply` for the Prometheus scrape ConfigMap and Grafana datasource ConfigMap.
9. `kubectl apply -k observability/prometheus-rules/` (alert + SLI recording rules).
10. `helm upgrade --install <release> charts/<service>` for every entry in the `SERVICES` matrix in the Makefile:
    `cart-service`, `catalog-service`, `checkout-service`, `inventory-service`,
    `order-service`, `payment-gateway`, `synthetic-monitor`.
11. `kubectl wait --for=condition=Available deploy --all-namespaces` (5-minute timeout).

Total wall-clock time on a cold start is roughly 5–10 minutes (mostly image pulls).

## Verify the Deployment

The canonical health check is now `make verify`, which delegates to `scripts/verify.sh`:

```bash
make verify                  # all 11 checks
make verify CHECKS="1 2 3"   # subset (also: bash scripts/verify.sh 1 2 3)
```

`verify.sh` runs **11 mandatory checks** plus a `prerequisites` step. It never
auto-fixes failures; it only reports. Every check is one of:

- **PASS** — observable evidence matches the expectation.
- **FAIL** — observable evidence contradicts the expectation.
- **DEFERRED-PENDING-DEPLOY** — the component's source-of-truth exists (Helm values,
  ConfigMap, chart) but the release is not yet installed onto the cluster.
  This is the expected state for any check that depends on a Helm release that
  has not yet been `helm install`-ed.

### The 11 checks

| # | Name                  | What it does                                                                                  | PASS criterion                                                            |
|---|-----------------------|-----------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| 1 | cluster nodes         | `kubectl get nodes`                                                                           | `>=3` nodes, all `Ready`                                                  |
| 2 | namespaces            | `kubectl get ns`                                                                              | `observability`, `ecom`, `chaos-testing`, `load`, `incident` all present  |
| 3 | pods ready            | `kubectl get pods -A`                                                                         | non-zero count of pods in `Running / Ready / Ready`                       |
| 4 | service /healthz      | `curl http://<svc>.ecom:PORT/healthz` for the 6 services (with `kubectl port-forward` fallback) | every service returns HTTP 200                                            |
| 5 | OTLP trace            | Push a synthetic span to OTel Collector → `/api/search` on Tempo                              | push returns 2xx AND search returns 2xx                                   |
| 6 | Loki logs             | Push a synthetic entry to `/loki/api/v1/push` → query `/loki/api/v1/query`                    | push returns 2xx AND query returns 2xx                                    |
| 7 | Prometheus `up`       | `GET /api/v1/query?query=up` on Prometheus                                                    | HTTP 2xx AND `.data.result` non-empty                                     |
| 8 | Grafana datasources   | `GET /api/datasources` (admin/admin)                                                          | HTTP 2xx AND `>=1` datasource present                                     |
| 9 | Grafana svc-overview  | `GET /api/dashboards/uid/svc-overview` (admin/admin)                                          | HTTP 200                                                                  |
| 10 | Prometheus rules     | `GET /api/v1/rules`                                                                          | HTTP 2xx AND `>=5` alerting rules across all groups                       |
| 11 | incident-engine      | `GET /incidents`                                                                             | HTTP 2xx AND response is a JSON array                                    |

### Exit codes

| Code | Meaning                                                              |
|------|----------------------------------------------------------------------|
| 0    | All selected checks PASS (or only DEFERREDs were non-PASS)           |
| 1    | At least one selected check FAILed                                   |
| 2    | Invocation error (missing prerequisites, bad arguments)              |

A summary is appended to `.omo/evidence/task-23-verify-summary.txt` on every
run. View it with:

```bash
cat .omo/evidence/task-23-verify-summary.txt
```

### Reading the output

```
[1;34m=== verify.sh — Enten v2 end-to-end health check ===[0m
KUBECONFIG = /home/mei/.kube/config-observability

[1;34m=== Check 4: service /healthz (6 services in ecom) ===[0m
  ok   cart-service (port-forward 8002 -> 8002) -> 200
  ...
  FAIL checkout-service-checkout-service -> 000

[1;34m=== Summary ===[0m
  PASS         1.cluster-nodes
  DEFERRED     5.otel-trace  -- otel-collector service not present
  ...

Total: 11    PASS: 3    FAIL: 1    DEFERRED: 7
```

`FAIL` is a real, observable gap (e.g. a pod is alive but its `/healthz`
endpoint is unresponsive). `DEFERRED-PENDING-DEPLOY` is "the source-of-truth
exists; the deployment step has not run yet." Both are *reported*, never
auto-fixed.

### Running individual checks

```bash
bash scripts/verify.sh 7         # only check 7
bash scripts/verify.sh 4 5 6     # checks 4, 5, 6
```

Internally every check is its own function (`check_1_cluster_nodes`,
`check_2_namespaces`, ...) so it's straightforward to add a 12th check later.

## Manual Verification (after the stack is up)

Beyond `make verify`, you can poke individual pieces:

```bash
# Cluster nodes
kubectl get nodes

# Core namespaces
kubectl get pods -n ecom
kubectl get pods -n observability
kubectl get pods -n incident
kubectl get pods -n chaos-testing

# Prometheus targets
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[].labels.job'

# Tempo readiness
curl -s http://localhost:3200/ready

# Loki ready
curl -s http://localhost:3100/ready

# OTel Collector metrics
curl -s http://localhost:8888/metrics | head

# Port-forward Grafana if the load balancer is not reachable
kubectl port-forward -n observability svc/grafana 3000:80
```

Open http://localhost:3000 and log in with `admin / admin`.

## Telemetry Smoke Test

```bash
# Synthetic trace + log + metric from a service
curl -X POST http://catalog-service.ecom:8000/items

# Check Prometheus has the metric
curl -G http://prometheus.observability:9090/api/v1/query \
    --data-urlencode 'query=catalog_service_requests_total'

# Check Tempo has the trace
curl http://tempo.observability:3200/api/search?limit=20

# Check Loki has the log line
curl -G http://loki.observability:3100/loki/api/v1/query \
    --data-urlencode 'query={service_name="catalog-service"}'
```

## Build and Push Service Images

```bash
docker buildx bake
```

Or build a single service:

```bash
docker build -t k3d-observability-registry:5000/catalog-service:dev services/catalog-service
docker push k3d-observability-registry:5000/catalog-service:dev
```

## Run a Smoke Test

```bash
kubectl apply -f load/k6-job-smoke.yaml
kubectl wait -n ecom job/k6-smoke --for=condition=complete --timeout=120s
kubectl logs -n ecom job/k6-smoke
```

## Tear Down

```bash
make down
```

`make down` performs, in order:

1. `helm uninstall` for every observability release (`otel-collector`, `tempo`,
   `loki`, `prometheus`, `grafana`) and every service release. Both groups use
   `--ignore-not-found`, so partial teardowns are idempotent.
2. `kubectl delete namespace` for `ecom`, `load`, `chaos-testing`, `incident`.
3. `terraform -chdir=infra/cluster destroy -auto-approve` — removes the k3d
   cluster and the local registry.

If you only want to wipe local state (for a fresh `make up`), without touching
the cluster:

```bash
make clean
```

`make clean` removes the Terraform state files, the devenv cache, and any
`tmp-*` evidence files. The cluster, registry, and Helm releases are
untouched.

## Troubleshooting

- **k3d cluster fails to create**: ensure Docker has at least 6 GB RAM and that ports 3000, 9090, 9093, 3200, and 3100 are free.
- **Helm chart not found**: run `helm repo update`.
- **Image pull errors**: verify the image was pushed to `k3d-observability-registry:5000/<service>:dev` and that the registry container is running.
- **Pods stuck Pending**: check resource requests and node capacity with `kubectl describe node`.
- **`scripts/verify.sh` keeps reporting DEFERRED for checks 5–10**: the
  observability stack has been charted but not yet installed. Run `make up`
  end-to-end and re-run `make verify`.
- **`scripts/verify.sh` reports `FAIL` on a single service `/healthz`** (check 4):
  this is a real failure on the target pod (often a port-not-listening or
  readiness-probe mismatch), not a script bug. Inspect with
  `kubectl describe pod -n ecom <pod>` and `kubectl logs -n ecom <pod>`.

## Pre-commit

`scripts/verify.sh` is required to pass `bash -n` (syntax) and `shellcheck`
before being committed. From the repo root:

```bash
bash -n scripts/verify.sh                                  # syntax
shellcheck scripts/verify.sh                               # lint (install via nix)
```
