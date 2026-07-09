# Makefile: Tempo deployment helpers
# Usage:
#   make lint    - validate Helm values against the grafana/tempo chart
#   make apply   - install/upgrade Tempo in the observability namespace
#   make reset   - uninstall Tempo and remove its PVCs

NAMESPACE := observability
RELEASE   := tempo
CHART     := grafana/tempo
VALUES    := helm-values.yaml

.PHONY: lint apply reset

lint:
	@mkdir -p /tmp/tempo-chart
	helm pull $(CHART) --untar --untardir /tmp/tempo-chart
	helm lint /tmp/tempo-chart/tempo -f $(VALUES)

apply:
	helm upgrade --install $(RELEASE) $(CHART) \
	  --namespace $(NAMESPACE) \
	  --create-namespace \
	  -f $(VALUES) \
	  --wait --timeout 180s

reset:
	helm uninstall $(RELEASE) --namespace $(NAMESPACE) || true
	kubectl delete pvc -n $(NAMESPACE) -l app.kubernetes.io/name=tempo --ignore-not-found=true
