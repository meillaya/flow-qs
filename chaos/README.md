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
| `pod-kill-checkout.yaml` | Workflow / PodChaos | checkout-service | Kills 1 pod every 60s | 5 min |
| `network-delay-payment.yaml` | Schedule / NetworkChaos | payment-gateway | Adds 2000ms inbound latency every 60s | 5 min per run |
| `cpu-stress-catalog.yaml` | Schedule / StressChaos | catalog-service | Consumes 80% CPU on 1 pod every 60s | 3 min per run |
| `dns-chaos-inventory.yaml` | Schedule / DNSChaos | inventory-service | Returns random DNS answers every 60s | 2 min per run |
| `demo-alert-checkout.yaml` | NetworkChaos | synthetic-monitor → checkout-service | Partitions only the monitoring path for checkout `ServiceDown` | 3 min |

### pod-kill-checkout

Uses a five-minute Chaos Mesh `Workflow` deadline around an `@every 60s`
`Schedule` template. Each scheduled child is `PodChaos` with action `pod-kill`
and mode `one`, while the checkout-service Deployment restores capacity.

### network-delay-payment

Uses a Chaos Mesh `Schedule` with `@every 60s`, `concurrencyPolicy: Forbid`,
and a five-entry history limit. Its embedded `NetworkChaos` uses action
`delay`: the source selector matches checkout-service pods and the target
selector matches payment-gateway pods. `direction: from` installs the delay on
payment responses addressed to the checkout Pod IP, adding 2000ms end-to-end
even though checkout opens its request to the payment Service ClusterIP. A
`direction: to` Pod-IP filter would not match that ClusterIP request path.

`NetworkChaos` requires the worker's netfilter modules. If injection is not
supported, treat the experiment as failed; do not substitute a manual fault.

### cpu-stress-catalog

Uses the same bounded-concurrency 60-second `Schedule` contract with an
embedded `StressChaos`. The stressor uses one worker and targets 80% CPU load
on one catalog-service pod for three minutes per run.

### dns-chaos-inventory

Uses the same bounded-concurrency 60-second `Schedule` contract with an
embedded `DNSChaos` action `random`. DNS queries from inventory-service pods
for `inventory-service.ecom.svc.cluster.local` receive random IP answers,
simulating corrupt DNS responses for two minutes per run.

### demo-alert-checkout

The strict demo first applies this bounded `NetworkChaos` partition to checkout
pods with `direction: from` and synthetic-monitor as the peer target. Only
synthetic-monitor traffic to checkout is blocked; k6 traffic from `load` does
not match the peer selector. After a fresh checkout `ServiceDown` postmortem
names this exact NetworkChaos, the demo starts the recurring checkout pod-kill
Workflow while k6 is still active.

## Running experiments

Apply a single experiment:

```bash
kubectl apply -f chaos/experiments/pod-kill-checkout.yaml
```

Apply all experiments in sequence with pauses:

```bash
cd chaos && make chaos-all
```

Validate all four Task 20 scheduling structures and their exact embedded fault
specs against pinned Chaos Mesh 2.7.0 CRDs:

```bash
cd chaos && make verify-schedules
```

Live acceptance must measure the checkout application path, not payment's
health endpoint. Capture exactly three successful baseline and three injected
checkout samples in `enten.networkchaos.measurement/v1` JSON, including HTTP
status, paid status, 32-character trace ID, and total seconds. Then run:

```bash
cd chaos
make verify-network-delay-measurement MEASUREMENT=/path/to/measurement.json
```

The verifier uses each set's median and requires
`injected_median - baseline_median >= 1.5` seconds. `verify-schedules` locks the
exact 1.5-second boundary and rejects a just-below result offline.

Remove an experiment:

```bash
kubectl delete -f chaos/experiments/pod-kill-checkout.yaml
```

## Safety rules

- Experiments are scoped to the `ecom` namespace only.
- No experiment runs longer than 10 minutes.
- Control-plane pods are never selected.
- Each manifest sets a `duration` so the experiment self-terminates.
- Standalone schedules forbid overlapping children and retain only five
  history entries. Delete the schedule when recurring injection is no longer
  wanted; `make clean` deletes all four Task 20 scheduled resources.
