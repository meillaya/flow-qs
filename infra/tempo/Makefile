# Makefile: Tempo deployment helpers
# Usage:
#   make lint    - validate Helm values against the grafana/tempo chart
#   make apply   - install/upgrade Tempo in the observability namespace
#   make reset   - uninstall Tempo and remove its PVCs

NAMESPACE := observability
RELEASE   := tempo
CHART     := grafana/tempo
VALUES    := $(dir $(lastword $(MAKEFILE_LIST)))helm-values.yaml
TEMPO_URL ?= http://127.0.0.1:3200
CHART_VERSION := 2.2.3

.PHONY: repo lint apply reset forward

repo:
	helm repo add --force-update grafana https://grafana-community.github.io/helm-charts
	helm repo update grafana

lint: repo
	helm template $(RELEASE) $(CHART) --version $(CHART_VERSION) \
	  --namespace $(NAMESPACE) -f $(VALUES) >/dev/null

apply: repo
	helm upgrade --install $(RELEASE) $(CHART) \
	  --namespace $(NAMESPACE) \
	  --create-namespace \
	  --version $(CHART_VERSION) \
	  -f $(VALUES) \
	  --wait --timeout 180s

reset:
	helm uninstall $(RELEASE) --namespace $(NAMESPACE) || true
	kubectl delete pvc -n $(NAMESPACE) -l app.kubernetes.io/name=tempo --ignore-not-found=true

forward:
	kubectl -n $(NAMESPACE) port-forward svc/tempo 3200:3200
