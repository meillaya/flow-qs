# Setup Guide

This guide walks through installing prerequisites, entering the development shell, provisioning the cluster, and verifying the stack.

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Docker | 24.x+ | Required by k3d. Allocate at least 6 GB RAM to Docker. |
| Nix | 2.18+ | Used by devenv to install Python, Go, k6, terraform, kubectl, helm. |
| devenv | latest | `nix profile install nixpkgs#devenv` or follow [devenv.sh](https://devenv.sh). |
| k3d | 5.x+ | Installed automatically via Terraform local-exec if `~/.local/bin/k3d` exists; otherwise install first. |
| Internet | n/a | Needed to pull Helm charts and container images on first boot. |

## Enter the Development Shell

```bash
cd /home/mei/projects/enten-v2
devenv shell
```

The shell sets:

- `KUBECONFIG=$HOME/.kube/config-observability`
- `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.observability.svc:4317`

Verify the tools:

```bash
python3 --version   # 3.12
go version          # 1.22
terraform version   # >= 1.5
kubectl version     # client 1.29+
helm version
k6 version
```

## Provision the Cluster and Stack

```bash
make up
```

This command:

1. Runs `terraform apply` to create a k3d cluster, registry, and namespaces.
2. Adds the required Helm repositories.
3. Updates Helm repository indexes.

After `make up`, deploy the observability stack and services. The exact sequence depends on the current Makefile; typically you run:

```bash
# Deploy observability stack
kubectl apply -k infra/otel-collector
kubectl apply -k infra/prometheus
kubectl apply -k infra/tempo
kubectl apply -k infra/loki
kubectl apply -k infra/grafana

# Deploy services
for svc in catalog-service inventory-service cart-service checkout-service order-service payment-gateway; do
  helm upgrade --install "$svc" "charts/$svc" -n ecom --create-namespace
done

# Deploy incident engine
helm upgrade --install incident-engine charts/svc-template -n incident --create-namespace \
  --set image.repository=k3d-observability-registry:5000/incident-engine
```

## Verify the Deployment

```bash
# Cluster nodes
kubectl get nodes

# Core namespaces
kubectl get pods -n ecom
kubectl get pods -n observability
kubectl get pods -n incident
kubectl get pods -n chaos-testing

# Port-forward Grafana if the load balancer is not reachable
kubectl port-forward -n observability svc/grafana 3000:3000
```

Open http://localhost:3000 and log in with `admin / admin`.

## Verify Telemetry

```bash
# Prometheus targets
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[].labels.job'

# Tempo readiness
curl -s http://localhost:3200/ready

# Loki ready
curl -s http://localhost:3100/ready

# OTel Collector metrics
curl -s http://localhost:8888/metrics | head
```

## Build and Push Service Images

```bash
docker buildx bake
```

Or build a single service:

```bash
docker build -t k3d-observability-registry:5000/catalog-service:dev services/catalog-service
docker push k3d-observability-registry:5000/catalog-service:dev
```

## Run a Smoke Test

```bash
kubectl apply -f load/k6-job-smoke.yaml
kubectl wait -n ecom job/k6-smoke --for=condition=complete --timeout=120s
kubectl logs -n ecom job/k6-smoke
```

## Tear Down

```bash
make down
```

This destroys the k3d cluster and registry. To remove only local state:

```bash
make clean
```

## Troubleshooting

- **k3d cluster fails to create**: ensure Docker has at least 6 GB RAM and that ports 3000, 9090, 9093, 3200, and 3100 are free.
- **Helm chart not found**: run `helm repo update`.
- **Image pull errors**: verify the image was pushed to `k3d-observability-registry:5000/<service>:dev` and that the registry container is running.
- **Pods stuck Pending**: check resource requests and node capacity with `kubectl describe node`.
