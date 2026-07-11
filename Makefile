
SHELL              := bash
.SHELLFLAGS        := -eu -o pipefail -c
export KUBECONFIG  ?= $(HOME)/.kube/config-observability

CLUSTER_NAME       := observability-platform
REGISTRY_NAME      := k3d-observability-registry
TF_DIR             := infra/cluster
OBS_NS             := observability
ECOM_NS            := ecom

SERVICES := \
    incident-engine,incident,charts/incident-engine \
    cart-service,ecom,charts/cart-service \
    catalog-service,ecom,charts/catalog-service \
    inventory-service,ecom,charts/inventory-service \
    order-service,ecom,charts/order-service \
    payment-gateway,ecom,charts/payment-gateway \
    checkout-service,ecom,charts/checkout-service \
    synthetic-monitor,ecom,charts/synthetic-monitor

OBS_RELEASES := otel-collector tempo loki prometheus grafana
IMAGE_SERVICES := cart-service catalog-service checkout-service incident-engine \
    inventory-service order-service payment-gateway synthetic-monitor
IMAGE_TAG := dev
HELM ?= helm
PYTHON_SERVICES := services/cart-service services/catalog-service \
    services/checkout-service services/incident-engine services/inventory-service \
    services/order-service services/synthetic-monitor
PYTHON_EXTRA_DEV_SERVICES := services/catalog-service services/checkout-service \
    services/order-service services/synthetic-monitor
LOCAL_CHARTS := $(sort $(patsubst %/Chart.yaml,%,$(wildcard \
    charts/*/Chart.yaml infra/otel-collector/helm/Chart.yaml)))

.PHONY: help up down verify clean clean-runtime demo lint lint-chart test validate-infra \
        build-push-images \
        cluster-up cluster-down cluster-list \
        helm-repos helm-add-repos helm-obs helm-chaos helm-services kubectl-wait

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} \
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
	terraform -chdir=$(TF_DIR) init -upgrade
	terraform -chdir=$(TF_DIR) apply -auto-approve

cluster-down: ## Tear down k3d cluster via Terraform (kept for symmetry)
	terraform -chdir=$(TF_DIR) destroy -auto-approve

cluster-list: ## Show k3d clusters + Kubernetes nodes
	@echo "k3d clusters:"
	@k3d cluster list 2>/dev/null || true
	@echo ""
	@echo "Kubernetes nodes:"
	@KUBECONFIG=$(KUBECONFIG) kubectl get nodes -o wide 2>/dev/null || true

build-push-images: ## Build and push all current service images to the local registry
	@set -euo pipefail; \
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

helm-repos: ## helm repo add (idempotent, || true is intentional)
	@helm repo add open-telemetry        https://open-telemetry.github.io/opentelemetry-helm-charts 2>/dev/null || true
	@helm repo add prometheus-community  https://prometheus-community.github.io/helm-charts          2>/dev/null || true
	@helm repo add grafana               https://grafana.github.io/helm-charts                       2>/dev/null || true
	@helm repo add chaos-mesh            https://charts.chaos-mesh.org                              2>/dev/null || true

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
	    --namespace $(OBS_NS) --create-namespace \
	    -f infra/tempo/helm-values.yaml --wait --timeout 3m
	@echo "  -> loki"
	@KUBECONFIG=$(KUBECONFIG) helm upgrade --install loki grafana/loki \
	    --namespace $(OBS_NS) --create-namespace \
	    -f infra/loki/helm-values.yaml --wait --timeout 3m
	@echo "  -> preparing Prometheus config and rule ConfigMaps"
	@KUBECONFIG=$(KUBECONFIG) kubectl apply -f infra/prometheus/configmap.yaml
	@KUBECONFIG=$(KUBECONFIG) kubectl apply -k observability/prometheus-rules
	@echo "  -> prometheus (+ kube-state-metrics + alertmanager subchart)"
	@KUBECONFIG=$(KUBECONFIG) helm upgrade --install prometheus prometheus-community/prometheus \
	    --namespace $(OBS_NS) --create-namespace \
	    -f infra/prometheus/helm-values.yaml --wait --timeout 5m
	@echo "  -> preparing Grafana datasource and dashboard ConfigMaps"
	@KUBECONFIG=$(KUBECONFIG) kubectl apply -k infra/grafana
	@KUBECONFIG=$(KUBECONFIG) kubectl apply -k dashboards
	@echo "  -> grafana"
	@KUBECONFIG=$(KUBECONFIG) helm upgrade --install grafana grafana/grafana \
	    --namespace $(OBS_NS) --create-namespace \
	    -f infra/grafana/helm-values.yaml --wait --timeout 3m

helm-chaos: ## Install/upgrade Chaos Mesh and its RBAC
	@KUBECONFIG=$(KUBECONFIG) helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh \
	    --namespace chaos-testing --create-namespace --version 2.7.0 \
	    --set controllerManager.serviceAccount=rbac \
	    --set chaosDaemon.runtime=containerd \
	    --set chaosDaemon.socketPath=/run/k3s/containerd/containerd.sock \
	    --set rbac.create=true --wait --timeout 5m

helm-services: ## Install/upgrade all application services in ecom
	@set -euo pipefail; \
	for record in $(SERVICES); do \
	    IFS=',' read -r release ns chart <<< "$$record"; \
	    echo "  -> $$release (chart=$$chart, ns=$$ns)"; \
	    KUBECONFIG=$(KUBECONFIG) helm upgrade --install "$$release" "$$chart" \
	        --namespace "$$ns" --create-namespace --wait --timeout 3m; \
	done; \
	KUBECONFIG=$(KUBECONFIG) kubectl rollout restart deployment/synthetic-monitor \
	    --namespace $(ECOM_NS); \
	KUBECONFIG=$(KUBECONFIG) kubectl rollout status deployment/synthetic-monitor \
	    --namespace $(ECOM_NS) --timeout=3m

kubectl-wait: ## kubectl wait for all deployments to be Available
	@set -euo pipefail; \
	for ns in ecom incident observability chaos-testing; do \
	    echo "Waiting for Deployments in $$ns..."; \
	    KUBECONFIG=$(KUBECONFIG) kubectl wait --for=condition=Available deployment --all \
	        --namespace "$$ns" --timeout=5m; \
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
	    IFS=',' read -r release ns _ <<< "$$record"; \
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
	@rm -rf .devenv/state .venv .direnv
	@echo "Removing generated evidence..."
	@find .omo/evidence -type f -name 'tmp-*' -delete 2>/dev/null || true
	@echo "Clean complete."

clean-runtime: ## Idempotently remove project releases, cluster, and registry
	@set -euo pipefail; \
	if command -v k3d >/dev/null 2>&1 && \
	   k3d cluster list 2>/dev/null | grep -q '^$(CLUSTER_NAME)\b'; then \
	    if [ -f "$(KUBECONFIG)" ] && \
	       KUBECONFIG=$(KUBECONFIG) kubectl cluster-info --request-timeout=5s >/dev/null 2>&1; then \
	        make --no-print-directory helm-uninstall-all; \
	    fi; \
	fi; \
	if [ -s "$(TF_DIR)/terraform.tfstate" ]; then \
	    terraform -chdir=$(TF_DIR) init -input=false; \
	    terraform -chdir=$(TF_DIR) destroy -auto-approve; \
	fi; \
	if command -v k3d >/dev/null 2>&1 && \
	   k3d cluster list 2>/dev/null | grep -q '^$(CLUSTER_NAME)\b'; then \
	    k3d cluster delete $(CLUSTER_NAME); \
	fi; \
	if command -v k3d >/dev/null 2>&1 && \
	   k3d registry list 2>/dev/null | grep -q '^$(REGISTRY_NAME)\b'; then \
	    k3d registry delete $(REGISTRY_NAME); \
	fi; \
	rm -f "$(KUBECONFIG)"

validate-infra: ## Validate rendered deployment contracts without cluster access
	@bash infra/validate-rendered.sh

demo: ## End-to-end demo: load (100 VUs) → chaos → wait → postmortem (~10 min)
	@bash scripts/demo.sh

demo-fallback: ## Run only the fallback path of the demo (no k6 / chaos)
	@DEMO_FORCE_FALLBACK=1 bash scripts/demo.sh

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
