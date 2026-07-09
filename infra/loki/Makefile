NAMESPACE := observability
RELEASE := loki
VALUES := infra/loki/helm-values.yaml
LOKI_URL := http://loki.observability:3100

.PHONY: apply reset ready push lint

apply:
	helm repo add grafana https://grafana.github.io/helm-charts
	helm repo update grafana
	helm upgrade --install $(RELEASE) grafana/loki \
		--namespace $(NAMESPACE) \
		--create-namespace \
		--values $(VALUES)

reset:
	helm uninstall $(RELEASE) --namespace $(NAMESPACE) || true
	kubectl delete pvc -l app.kubernetes.io/name=loki -n $(NAMESPACE) || true

lint:
	helm lint /tmp/opencode/loki -f $(VALUES)

ready:
	kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=loki -n $(NAMESPACE) --timeout=120s
	curl -sf $(LOKI_URL)/ready
	curl -G -sf $(LOKI_URL)/loki/api/v1/labels --data-urlencode "query={}"

push:
	curl -X POST -H "Content-Type: application/json" \
		-sf $(LOKI_URL)/loki/api/v1/push \
		--data '{"streams":[{"stream":{"service":"test"},"values":[["'"$$(date +%s%N)"'","hello from loki task 5"]]}]}'
	sleep 2
	curl -G -sf $(LOKI_URL)/loki/api/v1/query --data-urlencode 'query={service="test"}'
