# Service Level Objectives (SLOs)

This directory contains per-service SLO definitions used by the observability
platform. Each file describes the service, its SLIs (Prometheus selectors), the
objective, the rolling window, and the derived error budget.

## Layout

```text
slo/
├── README.md
├── catalog-service.yaml
├── inventory-service.yaml
├── cart-service.yaml
├── checkout-service.yaml
├── order-service.yaml
├── payment-gateway.yaml
└── end-to-end-checkout.yaml
```

Each YAML file follows the same schema:

```yaml
service: <service-name>
slos:
  - name: availability          # or latency_p99 / success_rate
    sli:
      spec: <metric>{<selector>} # numerator selector in PromQL
      total_spec: <metric>       # denominator selector in PromQL
    objective: 0.999             # target ratio (0..1]
    window: 30d                  # rolling evaluation window
    error_budget: 43m12s         # (1 - objective) * window in minutes/seconds
```

The SLI query is assembled by Prometheus recording rules (Task 16) as:

```promql
sum(rate(<spec>[30d])) / sum(rate(<total_spec>[30d]))
```

## Default SLO table

| Service | Availability / Success | Latency p99 |
|---------|------------------------|-------------|
| catalog-service | 99.95% | < 200 ms |
| inventory-service | 99.95% | < 150 ms |
| cart-service | 99.9% | < 100 ms |
| checkout-service | 99.9% | < 500 ms |
| order-service | 99.9% | < 300 ms |
| payment-gateway | 99.5% | < 1000 ms |
| end-to-end-checkout | 99% | < 2000 ms |

## Multi-window multi-burn-rate (MWMB) strategy

Error budgets are consumed at different speeds. A small outage can burn the
entire monthly budget in hours, while a steady low-level failure can burn it
over days. MWMB pairs burn-rate windows with severity tiers so pages fire only
when the budget is at real risk:

- **Fast burn** (high burn rate over a short window, e.g. 5m/30m/1h): page the
  on-call because the error budget will be exhausted soon unless the failure
  stops.
- **Slow burn** (moderate burn rate over a long window, e.g. 6h/1d/3d): open a
  ticket for investigation; the budget is draining steadily but not
  immediately.

The SLOs in this directory supply the inputs for the MWMB alerts in
`observability/prometheus-rules/alert_rules.yaml` (Task 16). The recording
rules there compute ratio SLIs over multiple windows and compare them against
objective-derived burn-rate thresholds.

## How to add a new service

1. Copy `slo/catalog-service.yaml` to `slo/<new-service>.yaml`.
2. Set `service` to the exact service name.
3. Choose `objective` and `window` from the plan defaults.
4. Set latency `spec` `le="<threshold>"` to the p99 target in seconds.
5. Compute the error budget:

   ```python
   budget_sec = (1 - objective) * 30 * 86400
   print(f"{int(budget_sec // 60)}m{int(budget_sec % 60)}s")
   ```

6. Run the validator:

   ```bash
   uv run --with pyyaml python scripts/validate_slo.py slo/*.yaml
   ```

## Validation

Run `scripts/validate_slo.py` against the SLO files. It checks the YAML schema,
the PromQL selector shape, and that each `error_budget` matches the math
`(1 - objective) * window_seconds`.
