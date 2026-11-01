
SHELL              := bash
.SHELLFLAGS        := -eu -o pipefail -c
override export KUBECONFIG := $(HOME)/.kube/config-observability

CLUSTER_NAME       := flow-qs
REGISTRY_NAME      := k3d-observability-registry
TF_DIR             := infra/cluster
OBS_NS             := observability
ECOM_NS            := ecom

SERVICES := \
    incident-engine,incident,charts/incident-engine,image.tag \
    cart-service,ecom,charts/cart-service,image.tag \
    catalog-service,ecom,charts/catalog-service,svc-template.image.tag \
    inventory-service,ecom,charts/inventory-service,image.tag \
    order-service,ecom,charts/order-service,image.tag \
    payment-gateway,ecom,charts/payment-gateway,image.tag \
    checkout-service,ecom,charts/checkout-service,image.tag \
    synthetic-monitor,ecom,charts/synthetic-monitor,svc-template.image.tag

OBS_RELEASES := otel-collector tempo loki prometheus grafana
IMAGE_SERVICES := cart-service catalog-service checkout-service incident-engine \
    inventory-service order-service payment-gateway synthetic-monitor
IMAGE_TAG ?= git-$(shell git rev-parse --verify HEAD)
PROMETHEUS_CHART_VERSION := 29.16.0
GRAFANA_CHART_VERSION := 12.7.2
LOKI_CHART_VERSION := 18.5.0
TEMPO_CHART_VERSION := 2.2.3
CHAOS_MESH_CHART_VERSION := 2.8.3
HELM ?= helm
PYTHON_SERVICES := services/cart-service services/catalog-service \
    services/checkout-service services/incident-engine services/inventory-service \
    services/order-service services/synthetic-monitor
PYTHON_EXTRA_DEV_SERVICES := services/catalog-service services/checkout-service \
    services/order-service services/synthetic-monitor
LOCAL_CHARTS := $(sort $(patsubst %/Chart.yaml,%,$(wildcard \
    charts/*/Chart.yaml infra/otel-collector/helm/Chart.yaml)))
SHELL_SCRIPTS := $(wildcard scripts/*.sh) $(wildcard chaos/tests/*.sh) \
    $(wildcard load/tests/*.sh) $(wildcard infra/*.sh) $(wildcard infra/cluster/*.sh)
FIGURES := request-path telemetry-path incident-path
EXCALIDRAW ?= excalidraw

.PHONY: help up down verify clean clean-runtime demo lint lint-chart test validate-infra \
        build-push-images prose contracts check rules shellcheck figures \
        cluster-up cluster-down cluster-list cluster-smoke \
        helm-repos helm-add-repos helm-obs helm-chaos helm-services kubectl-wait

help: ## Show available targets
	@printf 'Targets:\n'
	@awk 'BEGIN {FS = ":.*##"} \
		/^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST) | sort

up: cluster-up helm-add-repos ## Bring up everything: cluster + observability stack + services
	@echo ""
	@echo "==> Building and pushing current service images"
	$(MAKE) build-push-images
	@echo ""
	@echo "==> Deploying observability stack"
	$(MAKE) helm-obs
	@echo ""
	@echo "==> Deploying application services"
	$(MAKE) helm-services
	@echo ""
	@echo "==> Deploying Chaos Mesh with RBAC"
	$(MAKE) helm-chaos
	@echo ""
	@echo "==> Waiting for all components to be Ready"
	$(MAKE) kubectl-wait
	@echo ""
	@echo "==> Stack is up. Run 'make verify' to validate."

cluster-up: ## Provision k3d cluster + namespaces via Terraform
	terraform -chdir=$(TF_DIR) init -input=false
	terraform -chdir=$(TF_DIR) apply -auto-approve

cluster-down: ## Tear down k3d cluster via Terraform (kept for symmetry)
	terraform -chdir=$(TF_DIR) destroy -auto-approve

cluster-list: ## Show k3d clusters + Kubernetes nodes
	@echo "k3d clusters:"
	@k3d cluster list 2>/dev/null || true
	@echo ""
	@echo "Kubernetes nodes:"
	@KUBECONFIG=$(KUBECONFIG) kubectl get nodes -o wide 2>/dev/null || true

cluster-smoke: ## Opt-in lifecycle gate for the repo-owned k3d 5.9/K3s 1.35 compatibility caveat
	@set -euo pipefail; \
	test "$${ACK_UNTESTED_K3D_K3S_PAIRING:-}" = 1 || { \
	    echo 'Refusing: k3d 5.9 does not publish a K3s 1.35 compatibility guarantee.' >&2; \
	    echo 'Run only on a disposable host with ACK_UNTESTED_K3D_K3S_PAIRING=1.' >&2; \
	    exit 2; \
	}; \
	if k3d cluster list 2>/dev/null | awk -v expected='$(CLUSTER_NAME)' '$$1 == expected {found=1} END {exit !found}'; then \
	    echo 'Refusing to smoke-test over an existing project cluster.' >&2; exit 2; \
	fi; \
	trap '$(MAKE) --no-print-directory clean-runtime' EXIT; \
	$(MAKE) --no-print-directory cluster-up; \
	KUBECONFIG=$(KUBECONFIG) kubectl wait node --all --for=condition=Ready --timeout=5m; \
	test "$$(KUBECONFIG=$(KUBECONFIG) kubectl get nodes --no-headers | wc -l)" -eq 3; \
	KUBECONFIG=$(KUBECONFIG) kubectl rollout status deployment/coredns -n kube-system --timeout=2m; \
	echo 'k3d 5.9.0 / K3s 1.35.6 repo-owned lifecycle smoke passed.'

build-push-images: ## Build and push all current service images to the local registry
	@set -euo pipefail; \
	git diff --quiet -- services docker-bake.hcl || { echo 'ERROR: commit service changes before building immutable images' >&2; exit 1; }; \
	git diff --cached --quiet -- services docker-bake.hcl || { echo 'ERROR: commit staged service changes before building immutable images' >&2; exit 1; }; \
	test -z "$$(git ls-files --others --exclude-standard -- services docker-bake.hcl)" || { echo 'ERROR: commit untracked service changes before building immutable images' >&2; exit 1; }; \
	test "$$(docker inspect --format '{{.State.Running}}' $(REGISTRY_NAME))" = true; \
	registry_port=$$(docker inspect --format '{{(index (index .NetworkSettings.Ports "5000/tcp") 0).HostPort}}' $(REGISTRY_NAME)); \
	[[ "$$registry_port" =~ ^[0-9]+$$ ]]; \
	endpoint="localhost:$$registry_port"; \
	bake_args=(); \
	for service in $(IMAGE_SERVICES); do \
	    bake_args+=(--set "$$service.tags=$$endpoint/$$service:$(IMAGE_TAG)"); \
	done; \
	timeout 20m docker buildx bake "$${bake_args[@]}" \
	    --set '*.cache-from=' --set '*.cache-to=' --push

helm-add-repos: helm-repos ## Add + update all required Helm repos (idempotent)
	@helm repo update

helm-repos: ## Add or refresh the required upstream Helm repositories
	@helm repo add --force-update open-telemetry        https://open-telemetry.github.io/opentelemetry-helm-charts 2>/dev/null
	@helm repo add --force-update prometheus-community  https://prometheus-community.github.io/helm-charts          2>/dev/null
	@helm repo add --force-update grafana               https://grafana-community.github.io/helm-charts             2>/dev/null
	@helm repo add --force-update chaos-mesh            https://charts.chaos-mesh.org                              2>/dev/null

helm-obs: ## Install/upgrade the observability stack
	@set -euo pipefail; \
	trap 'echo "ERROR: helm-obs aborted on $$LINENO"' ERR; \
	$(MAKE) --no-print-directory _helm-obs

_helm-obs:
	@echo "  -> otel-collector (Helm wrapper chart)"
	@KUBECONFIG=$(KUBECONFIG) helm upgrade --install otel-collector ./infra/otel-collector/helm \
	    --namespace $(OBS_NS) --create-namespace --wait --timeout 3m
	@echo "  -> tempo"
	@KUBECONFIG=$(KUBECONFIG) helm upgrade --install tempo grafana/tempo \
	    --namespace $(OBS_NS) --create-namespace --version $(TEMPO_CHART_VERSION) \
	    -f infra/tempo/helm-values.yaml --wait --timeout 3m
	@echo "  -> loki"
	@KUBECONFIG=$(KUBECONFIG) helm upgrade --install loki grafana/loki \
	    --namespace $(OBS_NS) --create-namespace --version $(LOKI_CHART_VERSION) \
	    -f infra/loki/helm-values.yaml --wait --timeout 3m
	@echo "  -> preparing Prometheus config and rule ConfigMaps"
	@KUBECONFIG=$(KUBECONFIG) kubectl apply -f infra/prometheus/configmap.yaml
	@KUBECONFIG=$(KUBECONFIG) kubectl apply -k observability/prometheus-rules
	@echo "  -> prometheus (+ kube-state-metrics + alertmanager subchart)"
	@KUBECONFIG=$(KUBECONFIG) helm upgrade --install prometheus prometheus-community/prometheus \
	    --namespace $(OBS_NS) --create-namespace --version $(PROMETHEUS_CHART_VERSION) \
	    -f infra/prometheus/helm-values.yaml --wait --timeout 5m
	@echo "  -> preparing Grafana datasource and dashboard ConfigMaps"
	@KUBECONFIG=$(KUBECONFIG) kubectl apply -k infra/grafana
	@KUBECONFIG=$(KUBECONFIG) kubectl apply -k dashboards
	@echo "  -> grafana"
	@KUBECONFIG=$(KUBECONFIG) helm upgrade --install grafana grafana/grafana \
	    --namespace $(OBS_NS) --create-namespace --version $(GRAFANA_CHART_VERSION) \
	    -f infra/grafana/helm-values.yaml --wait --timeout 3m

helm-chaos: ## Install/upgrade Chaos Mesh and its RBAC
	@KUBECONFIG=$(KUBECONFIG) helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh \
	    --namespace chaos-testing --create-namespace --version $(CHAOS_MESH_CHART_VERSION) \
	    --set controllerManager.serviceAccount=rbac \
	    --set controllerManager.enableCtrlServer=false \
	    --set dashboard.service.type=ClusterIP \
	    --set chaosDaemon.runtime=containerd \
	    --set chaosDaemon.socketPath=/run/k3s/containerd/containerd.sock \
	    --set rbac.create=true --wait --timeout 5m

helm-services: ## Install/upgrade all application services in ecom
	@set -euo pipefail; \
	for record in $(SERVICES); do \
	    IFS=',' read -r release ns chart image_key <<< "$$record"; \
	    echo "  -> $$release (chart=$$chart, ns=$$ns)"; \
	    KUBECONFIG=$(KUBECONFIG) helm upgrade --install "$$release" "$$chart" \
	        --namespace "$$ns" --create-namespace \
	        --set-string "$$image_key=$(IMAGE_TAG)" --wait --timeout 3m; \
	done

kubectl-wait: ## kubectl wait for all deployments to be Available
	@set -euo pipefail; \
	for ns in ecom incident observability chaos-testing; do \
	    echo "Waiting for Deployments in $$ns..."; \
	    KUBECONFIG=$(KUBECONFIG) kubectl wait --for=condition=Available deployment --all \
	        --namespace "$$ns" --timeout=5m; \
	done; \
	echo "Waiting for observability DaemonSets..."; \
	for daemonset in $$(KUBECONFIG=$(KUBECONFIG) kubectl get daemonset -n observability -o name); do \
	    KUBECONFIG=$(KUBECONFIG) kubectl rollout status "$$daemonset" -n observability --timeout=5m; \
	done; \
	echo "Waiting for observability StatefulSets..."; \
	statefulset_names=$$(KUBECONFIG=$(KUBECONFIG) kubectl get statefulset \
	    --namespace observability \
	    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'); \
	if [[ -z "$$statefulset_names" ]]; then \
	    echo "No StatefulSets found in observability."; \
	else \
	    while IFS= read -r statefulset; do \
	        [[ -n "$$statefulset" ]] || continue; \
	        KUBECONFIG=$(KUBECONFIG) kubectl rollout status "statefulset/$$statefulset" \
	            --namespace observability --timeout=5m; \
	    done <<< "$$statefulset_names"; \
	fi

down: clean-runtime ## Tear down all project runtime resources
	@echo "Runtime resources removed."

helm-uninstall-all: ## helm uninstall every observability release + every service release
	@set -uo pipefail; \
	for record in $(SERVICES); do \
	    IFS=',' read -r release ns _ _ <<< "$$record"; \
	    echo "  x uninstalling $$release from $$ns"; \
	    KUBECONFIG=$(KUBECONFIG) helm uninstall "$$release" -n "$$ns" --ignore-not-found 2>/dev/null || true; \
	done; \
	for release in $(OBS_RELEASES); do \
	    ns=$(OBS_NS); \
	    echo "  x uninstalling $$release from $$ns"; \
	    KUBECONFIG=$(KUBECONFIG) helm uninstall "$$release" -n "$$ns" --ignore-not-found 2>/dev/null || true; \
	done; \
	KUBECONFIG=$(KUBECONFIG) helm uninstall chaos-mesh -n chaos-testing --ignore-not-found 2>/dev/null || true; \
	echo "  x deleting ecom/load/chaos-testing/incident namespaces"; \
	for ns in ecom load chaos-testing incident; do \
	    KUBECONFIG=$(KUBECONFIG) kubectl delete namespace $$ns --ignore-not-found 2>/dev/null || true; \
	done

verify: ## Run the 11-check verification script
	@bash scripts/verify.sh

clean: clean-runtime ## Remove runtime resources, Terraform state, and local artifacts
	@echo "Removing Terraform state..."
	@rm -rf $(TF_DIR)/.terraform $(TF_DIR)/terraform.tfstate $(TF_DIR)/terraform.tfstate.d
	@rm -f  $(TF_DIR)/terraform.tfstate.backup $(TF_DIR)/terraform.tfstate.*.backup
	@echo "Removing devenv state..."
	@if [[ -d .devenv/state ]]; then chmod -R u+w .devenv/state; fi
	@rm -rf .devenv/state .venv .direnv .ruff_cache
	@echo "Removing service and test caches..."
	@find services -type d \( -name .venv -o -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache -o -name .mypy_cache -o -name '*.egg-info' \) -prune -exec rm -rf {} +
	@find services -type f \( -name .coverage -o -name 'coverage.out' -o -name orders.db \) -delete
	@echo "Removing generated evidence..."
	@find .omo/evidence -type f -name 'tmp-*' -delete 2>/dev/null || true
	@echo "Clean complete."

clean-runtime: ## Idempotently remove project releases, cluster, and registry
	@CLUSTER_NAME=$(CLUSTER_NAME) REGISTRY_NAME=$(REGISTRY_NAME) TF_DIR=$(TF_DIR) \
	 KUBECONFIG=$(KUBECONFIG) bash infra/cluster/clean-runtime.sh

validate-infra: ## Validate rendered deployment contracts without cluster access
	@bash infra/validate-rendered.sh

demo: ## End-to-end demo: load (100 VUs) → chaos → wait → postmortem (~10 min)
	@bash scripts/demo.sh

lint-chart: ## Lint all local Helm charts
	@set -euo pipefail; \
	count=0; \
	for chart in $(LOCAL_CHARTS); do \
	    echo "==> $$chart: helm lint"; \
	    $(HELM) lint "$$chart"; \
	    ((count += 1)); \
	done; \
	test "$$count" -eq 10; \
	echo "Linted $$count/10 local Helm charts."

lint: lint-chart ## Python + Go + Helm lint
	@set -euo pipefail; \
	for service in $(PYTHON_SERVICES); do \
	    echo "==> $$service: ruff + ty"; \
	    if [[ " $(PYTHON_EXTRA_DEV_SERVICES) " == *" $$service "* ]]; then \
	        (cd "$$service" && uv run --frozen --extra dev ruff check . && uv run --frozen ty check .); \
	    else \
	        (cd "$$service" && uv run --frozen ruff check . && uv run --frozen ty check .); \
	    fi; \
	done; \
	echo "==> services/payment-gateway: gofmt + vet + golangci-lint"; \
	unformatted=$$(cd services/payment-gateway && gofmt -l .); \
	if [[ -n "$$unformatted" ]]; then printf '%s\n' "$$unformatted" >&2; exit 1; fi; \
	(cd services/payment-gateway && go vet ./... && golangci-lint run ./...)

test: ## pytest + go test
	@set -euo pipefail; \
	for service in $(PYTHON_SERVICES); do \
	    echo "==> $$service: pytest"; \
	    if [[ " $(PYTHON_EXTRA_DEV_SERVICES) " == *" $$service "* ]]; then \
	        (cd "$$service" && uv run --frozen --extra dev pytest -q); \
	    else \
	        (cd "$$service" && uv run --frozen pytest -q); \
	    fi; \
	done; \
	echo "==> services/payment-gateway: go test"; \
	(cd services/payment-gateway && go test ./...)

prose: ## Check prose against the house style
	@python3 tools/check-prose.py

contracts: helm-add-repos ## Run the static contract tests that need no cluster
	@bash scripts/test_verify.sh
	@bash scripts/test_script_validation_edges.sh
	@bash scripts/test_docs_contracts.sh
	@bash infra/test-platform-contracts.sh
	@bash infra/cluster/test-clean-runtime.sh
	@bash chaos/tests/verify-schedules.sh
	@bash load/tests/verify-demo-thresholds.sh
	@bash load/tests/verify-demo-summary.sh
	@bash load/tests/verify-causal-demo.sh

check: lint test contracts rules prose shellcheck ## Run every local gate

rules: ## Validate SLOs, regenerate rules, and run promtool
	@set -euo pipefail; \
	hash() { if command -v sha256sum >/dev/null 2>&1; then \
	    sha256sum "$$@"; else shasum -a 256 "$$@"; fi; }; \
	before="$$(hash observability/prometheus-rules/alert_rules.yaml \
	    observability/prometheus-rules/sli_recording_rules.yaml)"; \
	uv run --with pyyaml python scripts/validate_slo.py slo/*.yaml; \
	uv run --with pyyaml python scripts/build_prometheus_rules.py; \
	after="$$(hash observability/prometheus-rules/alert_rules.yaml \
	    observability/prometheus-rules/sli_recording_rules.yaml)"; \
	if [[ "$$before" != "$$after" ]]; then \
	    echo "generated rules changed; review and commit them" >&2; exit 1; \
	fi; \
	uv run scripts/validate_observability_contract.py; \
	promtool check rules observability/prometheus-rules/alert_rules.yaml \
	    observability/prometheus-rules/sli_recording_rules.yaml; \
	promtool test rules observability/prometheus-rules/*_test.yaml

shellcheck: ## Check shell scripts for errors and warnings
	@shellcheck -S warning $(SHELL_SCRIPTS)

figures: ## Regenerate the Excalidraw scenes and render the PNGs
	@python3 docs/figures/build_figures.py
	@set -euo pipefail; \
	browser_args=(); \
	if [[ -n "$${EXCALIDRAW_BROWSER_PATH:-}" ]]; then \
	    browser_args=(--browser-path "$$EXCALIDRAW_BROWSER_PATH"); \
	fi; \
	for figure in $(FIGURES); do \
	    echo "==> docs/figures/$$figure.png"; \
	    $(EXCALIDRAW) render "docs/figures/$$figure.excalidraw" \
	        --out "docs/figures/$$figure.png" --scale 2 --max-width 3200 \
	        "$${browser_args[@]}"; \
	done
