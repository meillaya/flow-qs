# Service level objectives

The seven YAML files in this directory are the source of truth for SLO output
identity, raw metric selectors, objectives, rolling windows, and declared
error budgets. `scripts/build_prometheus_rules.py` generates the checked-in
recording and alert rules from them.

## Raw telemetry contract

Every SLI consumes the stable Prometheus histogram produced from
`http.server.request.duration` in seconds:

```text
http_server_request_duration_seconds_bucket
http_server_request_duration_seconds_count
http_server_request_duration_seconds_sum
```

Resource attributes become `service_name` and `service_namespace`. HTTP data
points use `http_response_status_code` and the low-cardinality route template
`http_route`. Never put raw URLs, IDs, trace IDs, or exception text in these
labels.

Availability is the rate of non-5xx duration-count samples divided by all
duration-count samples. Latency compliance is the configured threshold bucket
divided by the histogram count. The latter is a compliance ratio, not a
duration-valued p99.

```yaml
service: cart-service
slos:
  - name: availability
    sli:
      spec: http_server_request_duration_seconds_count{http_response_status_code!~"5.."}
      total_spec: http_server_request_duration_seconds_count
    objective: 0.999
    window: 30d
    error_budget: 43m12s
  - name: latency_p99
    sli:
      spec: http_server_request_duration_seconds_bucket{le="0.1"}
      total_spec: http_server_request_duration_seconds_count
    objective: 0.99
    window: 30d
    error_budget: 432m0s
```

`service` names generated records and alerts. Optional `metric_service`
selects a different producer resource. End-to-end checkout therefore retains
the output identity `end-to-end-checkout` while selecting
`service_name="checkout-service",http_route="/checkout"`; it does not create a
second raw metric family.

## Objectives

| SLO identity | Availability / success | Latency compliance threshold |
|---|---:|---:|
| catalog-service | 99.95% | 200 ms |
| inventory-service | 99.95% | 150 ms |
| cart-service | 99.9% | 100 ms |
| checkout-service | 99.9% | 500 ms |
| order-service | 99.9% | 300 ms |
| payment-gateway | 99.5% | 1000 ms |
| end-to-end-checkout | 99% | 2000 ms |

The generator creates availability and latency-compliance ratios for `5m`,
`30m`, `1h`, `6h`, `1d`, `3d`, and `30d`, plus one availability budget per SLO:

```text
sli:<service>:availability:ratio_<window>
sli:<service>:latency_p99:ratio_<window>
slo:<service>:availability:error_budget_remaining:30d
```

Budget values are percentage points: `100` is untouched, `0` is exhausted,
and negative values are overspent. Prometheus retains at least 31 days so the
30-day records have complete input history.

Page alerts use the `1h/6h` MWMB pair. Warning alerts use `6h/3d`.
`HighLatencyP99` evaluates the full bucket distribution; only the latency
compliance recording rule filters `le` to its objective threshold.

## Validate and regenerate

```bash
uv run --with pyyaml python scripts/validate_slo.py slo/*.yaml
uv run --with pyyaml python scripts/build_prometheus_rules.py
uv run --with pyyaml python scripts/verify_mwmb.py
uv run scripts/validate_observability_contract.py
promtool check rules observability/prometheus-rules/alert_rules.yaml \
  observability/prometheus-rules/sli_recording_rules.yaml
promtool test rules observability/prometheus-rules/*_test.yaml
git diff --exit-code -- observability/prometheus-rules
```

The semantic validator rejects unknown schema keys, invalid service names,
noncanonical metric families, malformed routes, record-count drift, dashboard
query drift, missing MWMB contracts, and p99 expressions that filter a single
bucket.
