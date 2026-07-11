#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cd "$root"

sed -n '/^clean-runtime:/,/^$/p' Makefile >"$tmp/clean"
grep -q '^clean: clean-runtime' Makefile
grep -q 'helm-uninstall-all' "$tmp/clean"
grep -q 'terraform -chdir=.* destroy' "$tmp/clean"
grep -q 'k3d cluster delete' "$tmp/clean"
grep -q 'k3d registry delete' "$tmp/clean"

expected='incident-engine
cart-service
catalog-service
inventory-service
order-service
payment-gateway
checkout-service
synthetic-monitor'
records=$(sed -n '/^SERVICES :=/,/^$/s/^    \([^,]*\),.*/\1/p' Makefile)
test "$records" = "$expected"

sed -n '/^helm-services:/,/^$/p' Makefile >"$tmp/helm-services"
grep -q 'rollout restart deployment/synthetic-monitor' "$tmp/helm-services"
grep -q 'rollout status deployment/synthetic-monitor' "$tmp/helm-services"

sed -n '/^lint-chart:/,/^$/p' Makefile >"$tmp/lint-chart"
grep -q '^lint-chart:' "$tmp/lint-chart"
grep -q 'HELM.*lint' "$tmp/lint-chart"
grep -q 'test ".*count.*" -eq 10' "$tmp/lint-chart"
test "$(find charts infra/otel-collector/helm -name Chart.yaml -type f | wc -l)" -eq 10

sed -n '/^kubectl-wait:/,/^$/p' Makefile >"$tmp/kubectl-wait"
if grep -q 'rollout status statefulset --all' "$tmp/kubectl-wait"; then
  exit 1
fi
grep -q 'kubectl get statefulset' "$tmp/kubectl-wait"
grep -q 'statefulset/.*statefulset' "$tmp/kubectl-wait"

sed -n '/^helm-chaos:/,/^$/p' Makefile >"$tmp/helm-chaos"
grep -q 'chaosDaemon.runtime=containerd' "$tmp/helm-chaos"
grep -q 'chaosDaemon.socketPath=/run/k3s/containerd/containerd.sock' "$tmp/helm-chaos"

sed -n '/^up:/,/^$/p' Makefile >"$tmp/up"
grep -q '^up: cluster-up' "$tmp/up"
grep -q 'build-push-images' "$tmp/up"
build_line=$(grep -n 'build-push-images' "$tmp/up" | head -1 | cut -d: -f1)
services_line=$(grep -n 'helm-services' "$tmp/up" | head -1 | cut -d: -f1)
test "$build_line" -lt "$services_line"
sed -n '/^build-push-images:/,/^$/p' Makefile >"$tmp/build-push-images"
grep -q 'docker buildx bake' "$tmp/build-push-images"
grep -q 'docker inspect' "$tmp/build-push-images"
grep -q 'timeout 20m' "$tmp/build-push-images"
grep -q -- '--push' "$tmp/build-push-images"
sed -n '/^IMAGE_SERVICES :=/,/^IMAGE_TAG/p' Makefile >"$tmp/image-services"
bake_args=()
for service in $expected; do
  grep -q "$service" "$tmp/image-services"
  bake_args+=(--set "$service.tags=localhost:5000/$service:dev")
done
docker buildx bake --print "${bake_args[@]}" \
  --set '*.cache-from=' --set '*.cache-to=' >"$tmp/bake"
for service in $expected; do
  grep -q "localhost:5000/$service:dev" "$tmp/bake"
  test -f "services/$service/Dockerfile"
  grep -q "k3d-observability-registry:5000/$service" "charts/$service/values.yaml"
  grep -q 'tag:.*dev' "charts/$service/values.yaml"
done
python3 - "$tmp/bake" <<'PY'
import json
import sys

expected = {
    "cart-service",
    "catalog-service",
    "checkout-service",
    "incident-engine",
    "inventory-service",
    "order-service",
    "payment-gateway",
    "synthetic-monitor",
}
with open(sys.argv[1]) as stream:
    rendered = json.load(stream)
targets = rendered["target"]
assert set(targets) == expected
for service in expected:
    target = targets[service]
    assert target["context"] == f"services/{service}"
    assert target["dockerfile"] == "Dockerfile"
    assert target["tags"] == [f"localhost:5000/{service}:dev"]
PY

helm template prometheus prometheus-community/prometheus \
  --namespace observability -f infra/prometheus/helm-values.yaml >"$tmp/prometheus"
helm template otel-collector ./infra/otel-collector/helm \
  --namespace observability >"$tmp/otel-collector"
helm template synthetic-monitor charts/synthetic-monitor \
  --namespace ecom >"$tmp/synthetic-monitor"
helm template chaos-mesh chaos-mesh/chaos-mesh \
  --namespace chaos-testing --version 2.7.0 \
  --set controllerManager.serviceAccount=rbac \
  --set chaosDaemon.runtime=containerd \
  --set chaosDaemon.socketPath=/run/k3s/containerd/containerd.sock \
  --set rbac.create=true >"$tmp/chaos-mesh"
helm template tempo grafana/tempo \
  --namespace observability -f infra/tempo/helm-values.yaml >"$tmp/tempo"
helm template loki grafana/loki \
  --namespace observability -f infra/loki/helm-values.yaml >"$tmp/loki"
kubectl kustomize infra/grafana >"$tmp/grafana-inputs"

awk 'BEGIN { RS="---" } /kind: Service/ && /name: prometheus/ { print }' \
  "$tmp/prometheus" >"$tmp/prometheus-service"
grep -q 'port: 80' "$tmp/prometheus-service"
grep -q 'endpoint: http://prometheus.observability:80/api/v1/write' \
  infra/otel-collector/configmap.yaml
grep -q 'endpoint: http://prometheus.observability:80/api/v1/write' \
  infra/otel-collector/helm/values.yaml
grep -q 'endpoint: http://prometheus.observability:80/api/v1/write' \
  "$tmp/otel-collector"
if grep -q 'prometheus.observability:9090/api/v1/write' \
  infra/otel-collector/configmap.yaml infra/otel-collector/helm/values.yaml \
  "$tmp/otel-collector"; then
  exit 1
fi
grep -q 'image: "k3d-observability-registry:5000/synthetic-monitor:dev"' \
  "$tmp/synthetic-monitor"
grep -q 'imagePullPolicy: Always' "$tmp/synthetic-monitor"
awk 'BEGIN { RS="---" } /kind: DaemonSet/ && /name: chaos-daemon/ { print }' \
  "$tmp/chaos-mesh" >"$tmp/chaos-daemon"
grep -A1 -- '- --runtime$' "$tmp/chaos-daemon" | grep -q -- '- containerd$'
grep -A1 -- '- --runtime-socket-path$' "$tmp/chaos-daemon" \
  | grep -q -- '- /host-run/containerd.sock$'
grep -q 'path: /run/k3s/containerd$' "$tmp/chaos-daemon"
if grep -qE 'docker|/var/run/docker.sock' "$tmp/chaos-daemon"; then
  exit 1
fi
awk 'BEGIN { RS="---" } /kind: Service/ && /name: tempo/ { print }' \
  "$tmp/tempo" >"$tmp/tempo-service"
grep -q 'port: 3200' "$tmp/tempo-service"
awk 'BEGIN { RS="---" } /kind: Service/ && /name: alertmanager/ { print }' \
  "$tmp/prometheus" >"$tmp/alertmanager-service"
grep -q 'port: 9093' "$tmp/alertmanager-service"
awk 'BEGIN { RS="---" } /kind: Service/ && /name: loki/ { print }' \
  "$tmp/loki" >"$tmp/loki-service"
grep -q 'port: 3100' "$tmp/loki-service"

grep -q 'url: http://prometheus.observability:80' "$tmp/grafana-inputs"
grep -q 'url: http://tempo.observability:3200' "$tmp/grafana-inputs"
grep -q 'url: http://loki.observability:3100' "$tmp/grafana-inputs"
grep -q 'url: http://alertmanager.observability:9093' "$tmp/grafana-inputs"
grep -q 'implementation: prometheus' "$tmp/grafana-inputs"
if grep -q 'streamingEnabled:' "$tmp/grafana-inputs"; then
  exit 1
fi
test "$(grep -c '^  - name:' infra/grafana/datasources.yaml)" -eq 4
test "$(grep -c '^    access: proxy' infra/grafana/datasources.yaml)" -eq 4
test "$(grep -c "url: '\$\${__value}'" infra/grafana/datasources.yaml)" -eq 2

echo 'rendered infrastructure contracts OK'
