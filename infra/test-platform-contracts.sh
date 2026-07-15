#!/usr/bin/env bash
set -u

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root" || exit
failures=0

pass_or_record() {
  local description=$1
  shift
  if ! "$@"; then
    printf 'FAIL: %s\n' "$description" >&2
    failures=$((failures + 1))
  fi
}

contains() { grep -Fq -- "$2" "$1"; }
not_contains() { ! grep -Fq -- "$2" "$1"; }

pass_or_record 'immutable image tag is commit-derived' contains Makefile "IMAGE_TAG ?= git-\$(shell git rev-parse --verify HEAD)"
pass_or_record 'service matrix carries direct image key' contains Makefile 'cart-service,ecom,charts/cart-service,image.tag'
pass_or_record 'service matrix carries wrapper image key' contains Makefile 'catalog-service,ecom,charts/catalog-service,svc-template.image.tag'
pass_or_record 'chart docs reject floating latest tags' not_contains charts/README.md 'tag: latest'
pass_or_record 'chart docs show commit-derived injection' contains charts/README.md "git-\$(git rev-parse --verify HEAD)"
pass_or_record 'immutable build rejects staged service changes' contains Makefile 'git diff --cached --quiet -- services docker-bake.hcl'
pass_or_record 'synthetic-only restart removed' not_contains Makefile 'rollout restart deployment/synthetic-monitor'
pass_or_record 'routine terraform init does not upgrade' not_contains Makefile "terraform -chdir=\$(TF_DIR) init -upgrade"
pass_or_record 'stale demo fallback target removed' not_contains Makefile 'demo-fallback:'
pass_or_record 'maintained grafana community repository' contains Makefile 'https://grafana-community.github.io/helm-charts'
pass_or_record 'old grafana repository absent' not_contains Makefile 'https://grafana.github.io/helm-charts'

for contract in \
  'PROMETHEUS_CHART_VERSION := 29.16.0' \
  'GRAFANA_CHART_VERSION := 12.7.2' \
  'LOKI_CHART_VERSION := 18.5.0' \
  'TEMPO_CHART_VERSION := 2.2.3' \
  'CHAOS_MESH_CHART_VERSION := 2.8.3'; do
  pass_or_record "Makefile pin $contract" contains Makefile "$contract"
done
pass_or_record 'Chaos control server disabled' contains Makefile 'controllerManager.enableCtrlServer=false'
pass_or_record 'Chaos dashboard is ClusterIP' contains Makefile 'dashboard.service.type=ClusterIP'
pass_or_record 'Chaos schedule test uses deployed chart pin' contains chaos/tests/verify-schedules.sh '--version 2.8.3'
pass_or_record 'causal demo test uses deployed chart pin' contains load/tests/verify-causal-demo.sh '--version 2.8.3'

pass_or_record 'K3s is pinned current' contains infra/cluster/terraform.tfvars 'rancher/k3s:v1.35.6-k3s1'
pass_or_record 'host port input removed' not_contains infra/cluster/variables.tf 'variable "host_ports"'
pass_or_record 'host loadbalancer args removed' not_contains infra/cluster/main.tf '@loadbalancer'
pass_or_record 'k3d resolves from PATH' contains infra/cluster/variables.tf 'variable "k3d_bin"'
pass_or_record 'k3d 5.9 preflight is declared' contains infra/cluster/variables.tf 'default     = "v5.9.0"'
pass_or_record 'untested k3d K3s pairing has explicit smoke acknowledgement' contains Makefile 'ACK_UNTESTED_K3D_K3S_PAIRING'
pass_or_record 'k3d K3s smoke validates three Ready nodes' contains Makefile 'kubectl wait node --all --for=condition=Ready'

pass_or_record 'Prometheus retention covers 30d SLO' contains infra/prometheus/helm-values.yaml 'retention: "31d"'
pass_or_record 'Prometheus storage is 20Gi' contains infra/prometheus/helm-values.yaml 'size: 20Gi'
pass_or_record 'obsolete synthetic static scrape removed' not_contains infra/prometheus/configmap.yaml 'job_name: synthetic-monitor'
pass_or_record 'Loki retention compactor enabled' contains infra/loki/helm-values.yaml 'retention_enabled: true'
pass_or_record 'Loki uses chart compactor merge point' contains infra/loki/helm-values.yaml '  compactor:'
pass_or_record 'Loki full structured override removed' not_contains infra/loki/helm-values.yaml 'structuredConfig:'
pass_or_record 'Grafana disables mutable startup plugin installs' contains infra/grafana/helm-values.yaml 'preinstall_disabled: true'
pass_or_record 'Grafana has dashboard-query headroom' yq -e '.resources.limits.cpu == "1" and .resources.limits.memory == "1Gi"' infra/grafana/helm-values.yaml
pass_or_record 'Grafana dashboard ConfigMap has a stable identity' contains dashboards/kustomization.yaml 'disableNameSuffixHash: true'

pass_or_record 'Collector is one gateway replica' contains infra/otel-collector/helm/values.yaml 'replicaCount: 1'
pass_or_record 'Collector gateway limiter is 400MiB' contains infra/otel-collector/helm/values.yaml 'limit_mib: 400'
pass_or_record 'Collector gateway spike is 100MiB' contains infra/otel-collector/helm/values.yaml 'spike_limit_mib: 100'
pass_or_record 'Collector enrichment enabled' contains infra/otel-collector/helm/values.yaml 'passthrough: false'
pass_or_record 'Collector servicegraph configured' contains infra/otel-collector/helm/values.yaml 'servicegraph:'
pass_or_record 'Collector filelog agent configured' contains infra/otel-collector/helm/values.yaml 'agentCollectorConfig: |'
pass_or_record 'Collector filelog identity associates logs by pod UID' contains infra/otel-collector/helm/values.yaml 'name: k8s.pod.uid'
pass_or_record 'Collector filelog DaemonSet template exists' test -f infra/otel-collector/helm/templates/agent-daemonset.yaml
pass_or_record 'Collector gateway rolls on config changes' contains infra/otel-collector/helm/templates/deployment.yaml 'checksum/config:'
pass_or_record 'Collector filelog agents roll on config changes' contains infra/otel-collector/helm/templates/agent-daemonset.yaml 'checksum/config:'
pass_or_record 'Collector agent has no Service template' test ! -f infra/otel-collector/helm/templates/agent-service.yaml
pass_or_record 'duplicate standalone collector config removed' test ! -e infra/otel-collector/configmap.yaml
pass_or_record 'duplicate standalone collector kustomize removed' test ! -e infra/otel-collector/kustomization.yaml

pass_or_record 'payment chart opts into Prometheus scrape' contains charts/payment-gateway/templates/deployment.yaml 'prometheus.io/scrape'

k6_ref='grafana/k6:2.1.0@sha256:65c920dc067d5e2e00befbf982af6ad6ad0117034e8b1c65817c7975c52d4669'
for manifest in load/k6-job-*.yaml; do
  pass_or_record "$manifest pins k6 v2 digest" contains "$manifest" "$k6_ref"
done

pass_or_record 'Terraform cache is ignored' contains .gitignore '.terraform/'
pass_or_record 'Terraform state is ignored' contains .gitignore '*.tfstate'
pass_or_record 'Terraform lock is present' test -s infra/cluster/.terraform.lock.hcl
pass_or_record 'k3d is in devenv toolchain' contains devenv.nix '    k3d'

while IFS= read -r use_line; do
  if [[ ! $use_line =~ uses:[[:space:]]+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}[[:space:]]+#[[:space:]]+v[0-9] ]]; then
    printf 'FAIL: GitHub Action is not full-SHA pinned with version comment: %s\n' "$use_line" >&2
    failures=$((failures + 1))
  fi
done < <(grep -hE '^[[:space:]]+uses:' .github/workflows/*.yaml)

for action_pin in \
  'actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0' \
  'docker/setup-buildx-action@bb05f3f5519dd87d3ba754cc423b652a5edd6d2c # v4.2.0' \
  'docker/login-action@af1e73f918a031802d376d3c8bbc3fe56130a9b0 # v4.4.0' \
  'docker/build-push-action@53b7df96c91f9c12dcc8a07bcb9ccacbed38856a # v7.3.0' \
  'astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2' \
  'actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0' \
  'actions/setup-go@924ae3a1cded613372ab5595356fb5720e22ba16 # v6.5.0' \
  'golangci/golangci-lint-action@ba0d7d2ec06a0ea1cb5fa41b2e4a3ab91d21278a # v9.3.0' \
  'azure/setup-helm@9bc31f4ebc9c6b171d7bfbaa5d006ae7abdb4310 # v5.0.1' \
  'hashicorp/setup-terraform@dfe3c3f87815947d99a8997f908cb6525fc44e9e # v4.0.1' \
  'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1'; do
  pass_or_record "action pin $action_pin" grep -R -Fq -- "$action_pin" .github/workflows
 done
pass_or_record 'golangci action uses compatible v2 binary' contains .github/workflows/lint.yaml 'version: v2.12.2'
pass_or_record 'GitHub Actions updates are automated' contains .github/dependabot.yml 'package-ecosystem: "github-actions"'

if ((failures > 0)); then
  printf 'platform contract failures: %d\n' "$failures" >&2
  exit 1
fi
printf 'platform contracts passed\n'
