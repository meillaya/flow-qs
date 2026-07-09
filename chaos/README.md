# Chaos Engineering with Chaos Mesh

This directory contains Chaos Mesh experiment manifests for the observability
platform demo services. All experiments target user workloads in the `ecom`
namespace; control-plane components are never targeted.

## Prerequisites

- Chaos Mesh installed in the `chaos-testing` namespace:
  ```bash
  helm repo add chaos-mesh https://charts.chaos-mesh.org
  helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-testing \
    --create-namespace --version 2.7.0
  kubectl wait --for=condition=ready pod \
    -l app.kubernetes.io/name=chaos-mesh -n chaos-testing --timeout=180s
  ```
- Target services must be running in `ecom` before applying an experiment.

## Experiments

| File | Kind | Target | Blast radius | Duration |
|------|------|--------|--------------|----------|
| `pod-kill-checkout.yaml` | PodChaos | checkout-service | Kills 1 pod every 60s | 5 min |
| `network-delay-payment.yaml` | NetworkChaos | payment-gateway | Adds 2000ms inbound latency | 5 min |
| `cpu-stress-catalog.yaml` | StressChaos | catalog-service | Consumes 80% CPU on 1 pod | 3 min |
| `dns-chaos-inventory.yaml` | DNSChaos | inventory-service | Returns random DNS answers | 2 min |

### pod-kill-checkout

Uses `PodChaos` with action `container-kill`, mode `fixed` value `1`, and a
`duration` of five minutes. The `checkout-service` container inside one pod is
terminated each minute for five minutes, causing the pod's `RESTARTS` count to
increment while the deployment keeps the replica count steady.

### network-delay-payment

Uses `NetworkChaos` with action `delay`. The selector matches all pods in `ecom`
and `direction: to` adds 2000ms of latency to outbound traffic destined for
`payment-gateway.ecom.svc.cluster.local`.

> **Environment note**: `NetworkChaos` requires the `ip_tables` kernel module to
> be loaded on the worker nodes. Some containerized Kubernetes distributions
> (including this k3d environment) do not expose it. The manifest validates and
> applies, but injection may fail on such nodes. The QA evidence demonstrates the
> intended latency effect using an equivalent `tc netem` delay when needed.

### cpu-stress-catalog

Uses `StressChaos` to run a CPU stressor on one catalog-service pod. The
stressor uses one worker and targets 80% CPU load for three minutes.

### dns-chaos-inventory

Uses `DNSChaos` with action `random`. DNS queries from inventory-service pods
for `inventory-service.ecom.svc.cluster.local` receive random IP answers,
simulating corrupt DNS responses for two minutes.

## Running experiments

Apply a single experiment:

```bash
kubectl apply -f chaos/experiments/pod-kill-checkout.yaml
```

Apply all experiments in sequence with pauses:

```bash
cd chaos && make chaos-all
```

Remove an experiment:

```bash
kubectl delete -f chaos/experiments/pod-kill-checkout.yaml
```

## Safety rules

- Experiments are scoped to the `ecom` namespace only.
- No experiment runs longer than 10 minutes.
- Control-plane pods are never selected.
- Each manifest sets a `duration` so the experiment self-terminates.
