# Setup and verification

flow-qs is a local k3d demo. It does not install Ingress, TLS, public load
balancers, production authentication, backups, or highly available storage.

## Tool and runtime pins

Enter `devenv shell` before running project commands. The deployment contract
pins these external artifacts:

| Component | Pin |
|---|---|
| k3d | `v5.9.0` |
| K3s | `rancher/k3s:v1.35.6-k3s1` |
| Prometheus chart | `29.16.0` |
| Grafana chart | `12.7.2` |
| Loki chart | `18.5.0` |
| Tempo chart | `2.2.3` |
| Chaos Mesh chart | `2.8.3` |
| k6 | `grafana/k6:2.1.0@sha256:65c920dc067d5e2e00befbf982af6ad6ad0117034e8b1c65817c7975c52d4669` |

Prerequisites are Docker 24 or newer with at least 6 GiB assigned, Nix 2.18 or
newer, the devenv CLI, and internet access for the first chart/image pull.

```bash
devenv shell
docker version
k3d version
terraform version
kubectl version --client
helm version
promtool --version
```

k3d 5.9.0's published matrix covers K3s 1.30 through 1.33, not 1.35.6.
The pairing is therefore a repository-owned compatibility exception: cluster
creation, node readiness, CNI/DNS, registry pull, local-path PVC, Chaos Mesh,
and restart persistence smoke evidence must pass. Do not describe it as an
upstream-tested pairing.

## Provision

```bash
make up
```

The command creates one server and two agent nodes, the local registry, and
the `observability`, `ecom`, `incident`, `load`, and `chaos-testing`
namespaces. It installs exact chart versions, one Collector gateway, one log
agent on every schedulable node, Tempo, Loki, Prometheus/Alertmanager,
Grafana, Chaos Mesh, the application releases, generated rules, datasources,
and all ten dashboard JSON files.

Application images use a run-specific immutable tag. A second deployment must
change each workload pod template and create a new ReplicaSet; a mutable
development tag is not a deployment identity. Inspect and roll back with:

```bash
kubectl get deploy -A -o custom-columns='NAMESPACE:.metadata.namespace,NAME:.metadata.name,IMAGE:.spec.template.spec.containers[*].image'
kubectl rollout history -n ecom deployment/<service>
kubectl rollout undo -n ecom deployment/<service>
```

## Local access

All platform Services remain `ClusterIP`. Run each required forward in its own
terminal and stop it with Ctrl+C when finished:

```bash
kubectl -n observability port-forward svc/grafana 3000:80
kubectl -n observability port-forward svc/prometheus 9090:80
kubectl -n observability port-forward svc/alertmanager 9093:9093
kubectl -n observability port-forward svc/loki 3100:3100
kubectl -n observability port-forward svc/tempo 3200:3200
kubectl -n observability port-forward svc/otel-collector 4317:4317 4318:4318
```

| Surface | Local URL | Notes |
|---|---|---|
| Grafana | `http://127.0.0.1:3000` | `admin / admin`, local demo only |
| Prometheus | `http://127.0.0.1:9090` | query/rule/target APIs |
| Alertmanager | `http://127.0.0.1:9093` | alert and routing APIs |
| Loki | `http://127.0.0.1:3100` | readiness/query APIs |
| Tempo | `http://127.0.0.1:3200` | readiness/search APIs |
| OTLP | `127.0.0.1:4317`, `http://127.0.0.1:4318` | temporary test ingestion only |

These URLs do not work until the matching forward is running. There is no
k3d load-balancer shortcut.

## Verify

```bash
make verify
bash scripts/verify.sh 3 6 7 8 9 10
```

All selected checks must pass. A missing deployed component is a failure, not
a successful defer. The verifier checks:

1. three Ready cluster nodes;
2. all five namespaces;
3. all observability pods, Collector desired/ready replicas, node log-agent desired/ready counts, and eight immutable application image references;
4. all six application health endpoints;
5. an OTLP trace accepted by Tempo;
6. a Loki backend canary plus real container-log identities for the six applications, synthetic monitor, and incident engine;
7. the canonical histogram for six services, seven generated availability records, payment's scrape target, and a servicegraph edge;
8. exactly the Prometheus, Tempo, Loki, and Alertmanager Grafana datasources;
9. all ten provisioned dashboard UIDs;
10. loaded Prometheus recording and alert rules;
11. the incident-engine API.

The verifier owns its port-forward children and temporary response files. INT,
TERM, normal success, and failure all reap those processes.

## Telemetry contract checks

With the Prometheus forward running:

```bash
curl -fsS -G http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=count by (service_name) (http_server_request_duration_seconds_count{service_namespace="ecom"})' | jq

curl -fsS -G http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query={__name__="sli:end-to-end-checkout:availability:ratio_5m"}' | jq

curl -fsS -G http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query={__name__="slo:catalog-service:availability:error_budget_remaining:30d"}' | jq
```

The RED contract is
`http_server_request_duration_seconds_{bucket,count,sum}` in seconds with
`service_name`, `service_namespace`, `http_response_status_code`, and
`http_route`. Python services send it through OTLP. Payment exposes the same
schema through the bounded Prometheus scrape exception. The checkout route
feeds both `checkout-service` and output records named
`end-to-end-checkout`.

Prometheus retains 31 days on a 20 GiB local PVC so the 30-day ratio and error
budget records have complete input. Loki and Tempo retain 168 hours. These are
demo-grade local filesystem stores, not backup or recovery guarantees.

## Validate repository artifacts

```bash
uv run --with pyyaml python scripts/validate_slo.py slo/*.yaml
uv run --with pyyaml python scripts/build_prometheus_rules.py
uv run --with pyyaml python scripts/verify_mwmb.py
uv run scripts/validate_observability_contract.py
promtool check rules observability/prometheus-rules/alert_rules.yaml \
  observability/prometheus-rules/sli_recording_rules.yaml
promtool test rules observability/prometheus-rules/*_test.yaml
bash scripts/test_script_validation_edges.sh
bash scripts/test_verify.sh
```

Regeneration must leave no diff in the two generated rule files. `make rules`
runs the generation, the semantic validator, promtool, and an idempotence
check in one step.

The static gate runs every contract test that needs no cluster:

```bash
make contracts
```

That target needs `yq`, `k6`, and `kubeconform` from the development shell.
`make check` adds lint, tests, and the prose gate.

## Load and demo

Exactly four reusable profiles exist: smoke, load, stress, and spike. Their
Jobs run in `load`, while application Services run in `ecom`.

```bash
make -C load load-smoke
kubectl -n load wait --for=condition=complete job/k6-smoke --timeout=120s
kubectl -n load logs job/k6-smoke
make -C load clean
```

`make demo` uses the load profile plus existing Chaos Mesh experiments. Its k6
Job exit, alert/chaos correlation, postmortem validation, and bounded cleanup
are authoritative. It never fabricates success or weakens a failed threshold.

## Storage changes and reset

Kubernetes does not apply every PVC field change in place. Retention or size
changes must be planned with the data lifecycle. For a disposable local reset:

```bash
make down
make up
```

Deleting Prometheus, Loki, or Tempo PVCs permanently deletes local telemetry.
There is no automatic backup or in-place migration promise.

`make down` is idempotent and removes project releases, namespaces, the owned
k3d cluster, registry, containers, network, volumes, kubeconfig, load Jobs,
and owned storage. It refuses to auto-delete an unknown cluster whose identity
does not match the project contract. Demo and verifier port-forwards are reaped
by their own signal-safe traps. `make clean` performs the same ownership-gated
runtime teardown and removes Terraform state plus repository-local environment,
Python, test, coverage, and temporary evidence caches.

## Troubleshooting

- **Cluster creation fails:** confirm Docker capacity and capture the k3d/K3s
  versions before changing a pin.
- **Registry pull fails:** inspect the current registry host/port and CoreDNS
  rewrite; do not substitute a floating image tag.
- **A 30-day record has no value:** confirm traffic exists and Prometheus
  runtime retention is 31 days; a new deployment cannot synthesize history.
- **Logs are absent:** compare `otel-collector-agent` desired/ready counts and
  query Loki's `service_name` label values.
- **A RED panel is empty:** query the exact canonical histogram and resource
  labels; dashboards intentionally do not turn missing data into zero.
