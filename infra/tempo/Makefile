# Makefile: Tempo deployment helpers
# Usage:
#   make lint    - validate Helm values against the grafana/tempo chart
#   make apply   - install/upgrade Tempo in the observability namespace
#   make reset   - uninstall Tempo and remove its PVCs

NAMESPACE := observability
RELEASE   := tempo
CHART     := grafana/tempo
VALUES    := helm-values.yaml
CHART_VERSION := 2.2.3

.PHONY: lint apply reset

lint:
	helm template $(RELEASE) $(CHART) --version $(CHART_VERSION) \
	  --namespace $(NAMESPACE) -f $(VALUES) >/dev/null

apply:
	helm upgrade --install $(RELEASE) $(CHART) \
	  --namespace $(NAMESPACE) \
	  --create-namespace \
	  --version $(CHART_VERSION) \
	  -f $(VALUES) \
	  --wait --timeout 180s

reset:
	helm uninstall $(RELEASE) --namespace $(NAMESPACE) || true
	kubectl delete pvc -n $(NAMESPACE) -l app.kubernetes.io/name=tempo --ignore-not-found=true
