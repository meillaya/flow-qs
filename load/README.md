# k6 Load Tests

This directory contains [k6](https://k6.io/) scripts and Kubernetes Job
manifests for load-testing the observability-platform demo services.

All tests run **inside the cluster** as Kubernetes Jobs using the
`grafana/k6:latest` image. Scripts are mounted via ConfigMap so the image does
not need to be rebuilt when the test changes.

## Structure

| File | Purpose |
|------|---------|
| `k6/smoke.js` | 1 VU, 30s, health-checks every service `/healthz` |
| `k6/load.js` | Ramp 50 → 100 VUs, full checkout flow, p95 < 1s, errors < 1% |
| `k6/stress.js` | Ramp 0 → 200 VUs over 10 min, find breaking point |
| `k6/spike.js` | Baseline 10 VUs → instant 500 VUs spike, errors < 10% |
| `k6-job-*.yaml` | Kubernetes Job + ConfigMap for each script |
| `Makefile` | Convenience targets to apply each Job |

## Quick Start

```bash
# Smoke test
make -C load load-smoke

# Follow logs
make -C load logs-smoke

# Wait for completion
kubectl wait --for=condition=complete job/k6-smoke -n load --timeout=120s
```

## Available Makefile Targets

- `load-smoke` — apply the smoke-test Job
- `load-load` — apply the sustained-load Job
- `load-stress` — apply the stress-test Job
- `load-spike` — apply the spike-test Job
- `logs-smoke`, `logs-load`, `logs-stress`, `logs-spike` — stream Job logs
- `clean` — delete all k6 Jobs and ConfigMaps

## Interpreting k6 Output

Key lines in the Job logs:

- `http_req_duration` — request latency; the `p(95)` value is compared against
  the threshold for the test.
- `http_req_failed` — percentage of requests that failed (non-2xx or network
  error); must stay below the test threshold.
- `checks` — pass rate of explicit `check()` assertions.
- `checkout_success_rate` — custom metric for completed checkouts in
  `load.js` and `stress.js`.

A test exits non-zero if any threshold is breached, causing the Kubernetes Job
Pod to fail and making failures visible in `kubectl get jobs -n load`.

## Thresholds

| Test | Thresholds |
|------|------------|
| `smoke` | p95 latency < 100ms, errors < 1%, checks == 100% |
| `load` | p95 latency < 1000ms, errors < 1%, checkout success > 95% |
| `stress` | errors < 10%, checkout success > 80% |
| `spike` | errors < 10% |

## Notes

- Tests assume services are reachable at their cluster DNS names
  (`cart-service`, `checkout-service`, etc.) on the ports defined in the Helm
  values.
- The checkout flow posts to `cart-service:8002/cart/{user_id}/items` and then
  `checkout-service:8003/checkout`.
- Each Job writes raw JSON metrics to `/tmp/results.json` inside the pod.
- k6 is intentionally **not** executed from the host; all traffic originates
  from within the cluster.
