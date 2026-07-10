# =============================================================================
# Makefile: Enten v2 — End-to-End Observability Platform
#
# Targets:
#   make up      : full stack bring-up (cluster + observability + services)
#   make down    : teardown (helm uninstall + terraform destroy)
#   make verify  : run scripts/verify.sh (11 checks)
#   make clean   : remove Terraform state + local artifacts
#   make demo    : placeholder stub (Task 24)
#   make lint    : python + go + helm lint
#   make test    : pytest + go test
#
# Conventions:
#   - KUBECONFIG is exported to ~/.kube/config-observability throughout
#   - helm releases are idempotent via `helm upgrade --install`
#   - every release has a timeout; kubectl waits after each layer
#   - `make down` removes ALL helm releases, not just the cluster
# =============================================================================

# ------- Environment ----------------------------------------------------------
SHELL              := /usr/bin/env bash
export KUBECONFIG  ?= $(HOME)/.kube/config-observability
export PATH        := $(HOME)/.local/bin:$(HOME)/.nix-profile/bin:$(PATH)

CLUSTER_NAME       := observability-platform
TF_DIR             := infra/cluster
OBS_NS             := observability
ECOM_NS            := ecom

# Service catalog (name : namespace : release : chart path)
SERVICES := \
    cart-service          ecom cart-service          charts/cart-service          \
    catalog-service       ecom catalog-service       charts/catalog-service       \
    checkout-service      ecom checkout-service      charts/checkout-service      \
    inventory-service     ecom inventory-service     charts/inventory-service     \
    order-service         ecom order-service         charts/order-service         \
    payment-gateway       ecom payment-gateway       charts/payment-gateway       \
    synthetic-monitor     ecom synthetic-monitor     charts/synthetic-monitor

# Observability Helm releases
OBS_RELEASES := \
    otel-collector:$(OBS_NS):infra/otel-collector/helm        \
    tempo:$(OBS_NS):grafana/tempo:infra/tempo/helm-values.yaml \
    loki:$(OBS_NS):grafana/loki:infra/loki/helm-values.yaml    \
    prometheus:$(OBS_NS):prometheus-community/prometheus:infra/prometheus/helm-values.yaml \
    grafana:$(OBS_NS):grafana/grafana:infra/grafana/helm-values.yaml

# ------- Phony declarations ---------------------------------------------------
.PHONY: help up down verify clean demo lint test \
        cluster-up cluster-down cluster-list \
        helm-repos helm-add-repos helm-obs helm-services kubectl-wait

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} \
		/^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST) | sort

# =============================================================================
# make up — full-stack bring-up
# =============================================================================
up: cluster-up helm-add-repos ## Bring up everything: cluster + observability stack + services
	@echo ""
	@echo "==> Deploying observability stack"
	$(MAKE) helm-obs
	@echo ""
	@echo "==> Deploying application services"
	$(MAKE) helm-services
	@echo ""
	@echo "==> Waiting for all components to be Ready"
	$(MAKE) kubectl-wait
	@echo ""
	@echo "==> Stack is up. Run 'make verify' to validate."

# =============================================================================
# Cluster provisioning (delegated to Terraform)
# =============================================================================
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

# =============================================================================
# Helm repository management
# =============================================================================
helm-add-repos: helm-repos ## Add + update all required Helm repos (idempotent)
	@helm repo update

helm-repos: ## helm repo add (idempotent, || true is intentional)
	@helm repo add open-telemetry        https://open-telemetry.github.io/opentelemetry-helm-charts 2>/dev/null || true
	@helm repo add prometheus-community  https://prometheus-community.github.io/helm-charts          2>/dev/null || true
	@helm repo add grafana               https://grafana.github.io/helm-charts                       2>/dev/null || true
	@helm repo add chaos-mesh            https://charts.chaos-mesh.org                              2>/dev/null || true
	@helm repo add ingress-nginx         https://kubernetes.github.io/ingress-nginx                 2>/dev/null || true

# =============================================================================
# Observability stack: OTel Collector -> Tempo, Loki, Prom+Alertmanager, Grafana
# =============================================================================
helm-obs: ## Install/upgrade the observability stack
	@set -euo pipefail; \
	trap 'echo "ERROR: helm-obs aborted on $$LINENO"' ERR; \
	$(MAKE) --no-print-directory _helm-obs

# Parallelization is unsafe here (helm releases can race on CRDs / RBAC), so
# iterate sequentially.
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
	@echo "  -> prometheus (+ kube-state-metrics + alertmanager subchart)"
	@KUBECONFIG=$(KUBECONFIG) helm upgrade --install prometheus prometheus-community/prometheus \
	    --namespace $(OBS_NS) --create-namespace \
	    -f infra/prometheus/helm-values.yaml --wait --timeout 5m
	@echo "  -> grafana"
	@KUBECONFIG=$(KUBECONFIG) helm upgrade --install grafana grafana/grafana \
	    --namespace $(OBS_NS) --create-namespace \
	    -f infra/grafana/helm-values.yaml --wait --timeout 3m
	@echo "  -> applying observability ConfigMaps (Prometheus scrape config)"
	@KUBECONFIG=$(KUBECONFIG) kubectl apply -f infra/prometheus/configmap.yaml
	@KUBECONFIG=$(KUBECONFIG) kubectl apply -f infra/grafana/datasources.yaml
	@echo "  -> applying Prometheus alert + SLI rules (ConfigMap + kustomization)"
	@cd observability/prometheus-rules && KUBECONFIG=$(KUBECONFIG) kubectl apply -k . ; cd -

# =============================================================================
# Application services (one Helm release per service)
# =============================================================================
helm-services: ## Install/upgrade all application services in ecom
	@set -euo pipefail; \
	for triple in $(SERVICES); do \
	    set -- $$triple; \
	    svc=$$1; ns=$$2; release=$$3; chart=$$4; \
	    echo "  -> $$release (chart=$$chart, ns=$$ns)"; \
	    KUBECONFIG=$(KUBECONFIG) helm upgrade --install "$$release" "$$chart" \
	        --namespace $$ns --create-namespace --wait --timeout 3m || \
	        { echo "WARN: helm upgrade of $$release failed; continuing" >&2; }; \
	done

# =============================================================================
# kubectl wait — wait for everything to be Ready
# =============================================================================
kubectl-wait: ## kubectl wait for all deployments to be Available
	@set -euo pipefail; \
	echo "Waiting for Deployments (ecom + observability)..."; \
	KUBECONFIG=$(KUBECONFIG) kubectl wait --for=condition=Available deploy --all-namespaces \
	    --timeout=5m || echo "WARN: some Deployments did not become Available within 5m"; \
	echo "Waiting for StatefulSets..."; \
	KUBECONFIG=$(KUBECONFIG) kubectl wait --for=condition=Ready sts --all-namespaces \
	    --timeout=5m || true

# =============================================================================
# make down — clean teardown
# =============================================================================
down: helm-uninstall-all ## Tear down: helm uninstall all releases + destroy cluster
	terraform -chdir=$(TF_DIR) destroy -auto-approve

helm-uninstall-all: ## helm uninstall every observability release + every service release
	@set -uo pipefail; \
	for triple in $(SERVICES); do \
	    set -- $$triple; \
	    release=$$3; ns=$$2; \
	    echo "  x uninstalling $$release from $$ns"; \
	    KUBECONFIG=$(KUBECONFIG) helm uninstall $$release -n $$ns --ignore-not-found 2>/dev/null || true; \
	done; \
	for triple in $(OBS_RELEASES); do \
	    set -- $$triple; \
	    release=$$1; ns=$$2; \
	    echo "  x uninstalling $$release from $$ns"; \
	    KUBECONFIG=$(KUBECONFIG) helm uninstall $$release -n $$ns --ignore-not-found 2>/dev/null || true; \
	done; \
	echo "  x deleting ecom/load/chaos-testing/incident namespaces"; \
	for ns in ecom load chaos-testing incident; do \
	    KUBECONFIG=$(KUBECONFIG) kubectl delete namespace $$ns --ignore-not-found 2>/dev/null || true; \
	done

# =============================================================================
# make verify — comprehensive health check
# =============================================================================
verify: ## Run the 11-check verification script
	@bash scripts/verify.sh

# =============================================================================
# make clean — local-state cleanup
# =============================================================================
clean: ## Remove Terraform state + local artifacts (does NOT touch cluster)
	@echo "Removing Terraform state..."
	@rm -rf $(TF_DIR)/.terraform $(TF_DIR)/terraform.tfstate $(TF_DIR)/terraform.tfstate.d
	@rm -f  $(TF_DIR)/terraform.tfstate.backup $(TF_DIR)/terraform.tfstate.*.backup
	@echo "Removing devenv state..."
	@rm -rf .devenv/state .venv .direnv
	@echo "Removing generated evidence..."
	@find .omo/evidence -type f -name 'tmp-*' -delete 2>/dev/null || true
	@echo "Clean complete."

# =============================================================================
# make demo — orchestrate the end-to-end incident lifecycle
# =============================================================================
demo: ## End-to-end demo: load (100 VUs) → chaos → wait → postmortem (~10 min)
	@bash scripts/demo.sh

demo-fallback: ## Run only the fallback path of the demo (no k6 / chaos)
	@DEMO_FORCE_FALLBACK=1 bash scripts/demo.sh

lint: ## Python + Go + Helm lint
	@echo "==> ruff"
	-@ruff check .
	@echo "==> ty / mypy"
	-@ty check .   2>/dev/null || mypy .
	@echo "==> golangci-lint"
	-@golangci-lint run ./... 2>/dev/null || true
	@echo "==> helm lint (svc-template)"
	-@helm lint charts/svc-template 2>/dev/null || true

test: ## pytest + go test
	@echo "==> pytest (services)"
	-@find services -name pyproject.toml -maxdepth 2 -execdir pytest -q \;
	@echo "==> go test (services)"
	-@find services -name go.mod -maxdepth 2 -execdir go test ./... \;
