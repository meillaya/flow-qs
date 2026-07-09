.PHONY: up down clean demo lint test cluster-up cluster-down cluster-list lint-chart

cluster-up:
	terraform -chdir=infra/cluster init
	terraform -chdir=infra/cluster apply -auto-approve

cluster-down:
	terraform -chdir=infra/cluster destroy -auto-approve

cluster-list:
	@echo "k3d clusters:"
	@k3d cluster list || true
	@echo ""
	@echo "Kubernetes nodes:"
	@kubectl get nodes || true

up:
	terraform -chdir=infra/cluster apply -auto-approve
	helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts || true
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts || true
	helm repo add grafana https://grafana.github.io/helm-charts || true
	helm repo add chaos-mesh https://charts.chaos-mesh.org || true
	helm repo update

down:
	terraform -chdir=infra/cluster destroy -auto-approve

clean:
	rm -rf infra/cluster/.terraform infra/cluster/terraform.tfstate* infra/cluster/terraform.tfstate.d
	rm -rf .devenv/state .venv

demo:
	@echo "Demo: load -> chaos -> incident -> postmortem"
	@echo "Run make up first, then execute k6 smoke, chaos experiments, and incident-engine."

lint:
	@echo "Lint: ruff, pyright, golangci-lint"
	@ruff check . || true
	@pyright . || true
	@golangci-lint run ./... || true

lint-chart:
	@echo "Linting svc-template Helm chart..."
	helm lint charts/svc-template

test:
	@echo "Test: pytest, go test"
	@find services -name pyproject.toml -maxdepth 2 -execdir pytest \; || true
	@find services -name go.mod -maxdepth 2 -execdir go test ./... \; || true
