NAMESPACE := observability
RELEASE := loki
VALUES := $(dir $(lastword $(MAKEFILE_LIST)))helm-values.yaml
LOKI_URL ?= http://127.0.0.1:3100
CHART_VERSION := 18.5.0

.PHONY: repo apply reset ready forward push lint

repo:
	helm repo add --force-update grafana https://grafana-community.github.io/helm-charts
	helm repo update grafana

apply: repo
	helm upgrade --install $(RELEASE) grafana/loki \
		--namespace $(NAMESPACE) \
		--create-namespace \
		--version $(CHART_VERSION) \
		--values $(VALUES)

reset:
	helm uninstall $(RELEASE) --namespace $(NAMESPACE) || true
	kubectl delete pvc -l app.kubernetes.io/name=loki -n $(NAMESPACE) || true

lint: repo
	helm template $(RELEASE) grafana/loki --version $(CHART_VERSION) \
		--namespace $(NAMESPACE) --values $(VALUES) >/dev/null

forward:
	kubectl -n $(NAMESPACE) port-forward svc/loki 3100:3100

ready:
	kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=loki -n $(NAMESPACE) --timeout=120s
	curl -sf $(LOKI_URL)/ready
	curl -G -sf $(LOKI_URL)/loki/api/v1/labels --data-urlencode "query={}"

push:
	@now=$$(python3 -c 'import time; print(time.time_ns())'); \
	curl -X POST -H "Content-Type: application/json" \
		-sf $(LOKI_URL)/loki/api/v1/push \
		--data "{\"streams\":[{\"stream\":{\"service\":\"test\"},\"values\":[[\"$$now\",\"hello from loki\"]]}]}"
	sleep 2
	curl -G -sf $(LOKI_URL)/loki/api/v1/query --data-urlencode 'query={service="test"}'
