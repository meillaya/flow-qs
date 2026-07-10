# Demo Guide

This document explains what `make demo` does and how to read the artifacts it produces.

## What the Demo Does

`make demo` runs a scripted, repeatable incident lifecycle:

1. **Smoke test**: verify every service responds on `/healthz`.
2. **Load generation**: run a short k6 load profile against `/checkout`.
3. **Chaos injection**: apply a Chaos Mesh experiment to a service.
4. **Alert firing**: wait until Prometheus reports an active alert.
5. **Incident response**: Alertmanager routes the alert to the incident engine.
6. **Postmortem generation**: the incident engine writes a Markdown postmortem.

Total runtime is about 5-10 minutes, depending on how quickly Prometheus evaluates the alert rules.

## Prerequisites

- `devenv shell` is active.
- `make up` has finished and all pods are running.
- You can verify readiness with:

  ```bash
  kubectl get pods -n ecom
  kubectl get pods -n observability
  kubectl get pods -n incident
  ```

## Run the Demo

```bash
devenv shell -- make demo 2>&1 | tee /tmp/demo.log
```

The script prints each step with a timestamp. Look for:

- `smoke complete`
- `load job started`
- `chaos experiment applied: <name>`
- `alert firing: <alertname>`
- `postmortem generated: <path>`
- `demo complete`

## What to Expect

### During Load

The k6 Job sends checkout requests. In Grafana you can watch:

- **RED dashboard**: request rate, error rate, duration per service.
- **SLO Status dashboard**: remaining error budget.
- **Trace Explorer**: individual checkout traces.

### During Chaos

One of these experiments runs:

| Experiment | Expected symptom |
|------------|------------------|
| pod-kill-checkout | checkout-service pods restart; readiness fails; 503s spike. |
| network-delay-payment | Payment calls time out; checkout latency jumps; SLO burn alert fires. |
| cpu-stress-catalog | catalog-service slows down; product search p99 rises. |
| dns-chaos-inventory | inventory-service lookups fail; checkout stock reservations fail. |

### After the Alert

Prometheus sends the alert to Alertmanager. Alertmanager groups it and forwards it to the incident engine webhook. The engine queries Prometheus for the alert timestamp, value, and labels, then writes a postmortem to a path like:

```text
docs/examples/postmortems/postmortem-demo-2026-07-09T14-32-00.md
```

## Interpreting the Postmortem

A generated postmortem contains:

- **Summary**: the service, alert, and SLO affected.
- **Impact**: duration, error rate, and requests affected (estimated from Prometheus).
- **Timeline**: alert fired, experiment applied, recovery observed.
- **Root cause**: the injected fault, for example network delay on payment-gateway.
- **Remediation**: immediate fix (delete the chaos experiment) and longer-term hardening.
- **Follow-ups**: actions such as adding retries, circuit breakers, or tighter SLOs.

## Inspecting Logs and Traces

```bash
# Find the trace ID from a failed checkout response
curl -s -X POST http://localhost:8003/checkout \
  -H "Content-Type: application/json" \
  -d '{"user_id":"u1","payment_method":"card"}' \
  -i

# Search Tempo for that trace ID
curl -s "http://localhost:3200/api/traces/<trace-id>" | jq

# Search Loki for checkout-service logs
curl -s "http://localhost:3100/loki/api/v1/query_range?query=%7Bservice_name%3D%22checkout-service%22%7D" | jq
```

## Cleaning Up

The demo does not tear down the cluster. After you finish:

```bash
make down
```

To remove only the chaos experiments:

```bash
make -C chaos clean
```

## Troubleshooting

- **No alert fires**: check that Prometheus rules are loaded (`http://localhost:9090/rules`) and that the chaos experiment is actually affecting the service.
- **No postmortem generated**: verify the incident engine pod is running and that Alertmanager can reach `http://incident-engine.incident.svc:8088/webhook`.
- **k6 Job fails**: check the Job logs with `kubectl logs -n ecom job/k6-smoke` (or `k6-load`).
