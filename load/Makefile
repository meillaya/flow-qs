# Makefile for k6 load-test Kubernetes Jobs.
# Run from the repo root with: make -C load <target>

NAMESPACE := load

.PHONY: load-smoke load-load load-stress load-spike clean logs-smoke logs-load logs-stress logs-spike verify-thresholds verify-summary verify-causal-demo

load-smoke:
	kubectl apply -f k6-job-smoke.yaml

load-load:
	kubectl apply -f k6-job.yaml

load-stress:
	kubectl apply -f k6-job-stress.yaml

load-spike:
	kubectl apply -f k6-job-spike.yaml

verify-thresholds:
	./tests/verify-demo-thresholds.sh

verify-summary:
	./tests/verify-demo-summary.sh

verify-causal-demo:
	./tests/verify-causal-demo.sh

logs-smoke:
	kubectl logs job/k6-smoke -n $(NAMESPACE)

logs-load:
	kubectl logs job/k6-load -n $(NAMESPACE)

logs-stress:
	kubectl logs job/k6-stress -n $(NAMESPACE)

logs-spike:
	kubectl logs job/k6-spike -n $(NAMESPACE)

clean:
	kubectl delete -f k6-job-smoke.yaml --ignore-not-found
	kubectl delete -f k6-job-load.yaml --ignore-not-found
	kubectl delete -f k6-job-stress.yaml --ignore-not-found
	kubectl delete -f k6-job-spike.yaml --ignore-not-found
