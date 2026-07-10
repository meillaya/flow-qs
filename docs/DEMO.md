# Demo Guide

This document explains what `make demo` does, how long it takes, and how to
read the artifacts it produces.

## What the Demo Does

`make demo` runs a scripted, repeatable incident lifecycle:

1. **Stack readiness probe** — verify the namespaces and key pods exist
   (services in `ecom`, Chaos Mesh in `chaos-testing`, k6 namespace in `load`,
   optional incident-engine in `incident`).
2. **Load generation** — apply the k6 load Job (`load.js`):
   50 → 100 VUs over ~5 minutes, then ramp down.
3. **Settle (60s)** — let the load profile ramp before injecting failure.
4. **Chaos injection** — apply `pod-kill-checkout` (PodChaos with
   `action: container-kill` on one `checkout-service` container, scoped to
   the `ecom` namespace only).
5. **Wait for alert (180s)** — Prometheus MWMB alerts fire when latency or
   error rate crosses the SLO threshold; Alertmanager routes the alert to the
   incident-engine webhook.
6. **Postmortem verification** — `kubectl exec` into the incident-engine pod
   and list `/postmortems/`. If the incident-engine is not yet deployed (Task
   22 is parallel Wave 4 work), the demo writes a **fallback postmortem** so
   the artifact pipeline still has something to validate.
7. **Wait for k6 to finish** — `kubectl wait --for=condition=complete`
   the k6-load Job.
8. **Summarize** — copy the postmortem to
   `docs/examples/postmortems/postmortem-demo-<UTC-timestamp>.md`, write
   `.omo/evidence/task-24-summary.txt`, and print a key-value summary.

The script is implemented in `scripts/demo.sh` and wired to `make demo`.

## Timing Expectations

Total wall-clock: **~9 minutes 30 seconds** (target). Hard cap **15 minutes**
enforced by a watchdog subshell that SIGTERMs the parent and triggers cleanup.

| Step | Phase | Expected wall-clock | Cumulative |
|------|-------|---------------------|------------|
| 1 | Stack readiness probe | 2 – 5 s | 5 s |
| 2 | k6 Job apply + pod scheduling | 5 – 30 s | 35 s |
| 3 | Settle before chaos | 60 s | ~1 min 35 s |
| 4 | Apply `pod-kill-checkout` | 1 s | ~1 min 36 s |
| 5 | Wait for alert → incident-engine → postmortem | 180 s | ~4 min 36 s |
| 6 | Verify postmortem on incident-engine pod | 5 – 10 s | ~4 min 46 s |
| 7 | Wait for k6 Job to complete | ~5 min | ~9 min 46 s |
| 8 | Collect k6 logs + results.json + summary | 10 s | ~9 min 56 s |

k6 load profile (`load/k6/load.js`):

- `2m` ramp 0 → 50 VUs
- `3m` sustain at 100 VUs (this is the steady-state load)
- `30s` ramp down to 0
- Thresholds: `p(95) < 1000 ms`, error rate `< 1%`, checkout success `> 95%`

Chaos experiment (`chaos/experiments/pod-kill-checkout.yaml`):

- Action: `container-kill`
- Mode: `one`
- Duration: `5m`
- Selector: `app.kubernetes.io/name=checkout-service` in `ecom`
- **Never targets control plane** (`kube-system`, `chaos-testing`, etc.)

## Prerequisites

- `devenv shell` is active so `KUBECONFIG` is exported.
- `make up` has finished and at least 2 of the 6 ecom deployments are
  Available. Verify with:

  ```bash
  kubectl get deploy -n ecom
  kubectl get pods -n chaos-testing
  ```

- The `incident` namespace does not have to exist for the demo to run; if
  `incident-engine` is missing, the demo falls back to writing a synthetic
  postmortem from k6 evidence (see [Fallback Behavior](#fallback-behavior)).

## Run the Demo

```bash
make demo 2>&1 | tee /tmp/demo.log
```

The script logs every step with a UTC timestamp. Look for these milestone
lines:

| Milestone log line | Means |
|--------------------|-------|
| `Step 1/8: Verifying stack readiness` | Probe started |
| `✓ <service> available` | A service passed the readiness wait |
| `Step 2/8: Applying k6 load job` | k6 Job manifest applied |
| `Step 3/8: Applying pod-kill-checkout PodChaos` | Chaos experiment applied |
| `✓ postmortem found: /postmortems/<file>` | Real postmortem from incident-engine |
| `Step 7/8: Collecting k6 results` | k6 Job completed; logs + results.json captured |
| `Demo complete.` | Run finished successfully |

Every log line is also written to `.omo/evidence/task-24-demo.log`.

## What to Expect

### During Load

The k6 Job sends checkout requests (`POST /cart/{user_id}/items` then
`POST /checkout`). In Grafana you can watch:

- **RED dashboard**: request rate, error rate, duration per service.
- **SLO Status dashboard**: remaining error budget.
- **Trace Explorer**: individual checkout traces.

### During Chaos

The `pod-kill-checkout` experiment terminates the `checkout-service`
container inside one pod. Because checkout-service runs with 2 replicas, the
deployment absorbs the failure: the replica set creates a fresh container,
RESTARTS increments, and clients see at most a single brief 503 spike. The
k6 success-rate threshold (`> 95%`) may briefly dip.

### After the Alert

When Prometheus + Alertmanager + incident-engine are all deployed:

1. Prometheus evaluates the MWMB recording rules for `checkout-service`
   every 30 s.
2. The latency / error-rate burn-rate alert transitions `Pending → Firing`.
3. Alertmanager receives the alert and POSTs it to
   `http://incident-engine.incident.svc:8088/webhook`.
4. The incident-engine queries Tempo, Loki, and Prometheus for context
   around the alert window and writes
   `postmortem-<alertname>-<ts>.md` to `/postmortems/`.

`make demo` then `kubectl cp`s that file out of the pod to
`docs/examples/postmortems/postmortem-demo-<ts>.md`.

## Interpreting the Postmortem

A generated postmortem contains:

- **Summary**: the service, alert, and SLO affected.
- **Impact**: duration, error rate, and requests affected (estimated from
  Prometheus).
- **Timeline**: alert fired, experiment applied, recovery observed.
- **Root cause**: the injected fault, for example network delay on
  payment-gateway.
- **Resolution**: auto-detected when the alert stops firing.
- **Lessons Learned**: human-fillable section.

The fallback postmortem is clearly labeled "Demo Postmortem (fallback)" in
its first heading and includes the same six sections plus a note explaining
why it was synthesized.

## Inspecting Logs and Traces

```bash
# Follow the k6 load Job's logs
kubectl logs -n load -l job-name=k6-load -f

# Find the trace ID from a failed checkout response
curl -s -X POST http://localhost:8003/checkout \
  -H "Content-Type: application/json" \
  -d '{"user_id":"u1","payment_method":"card"}' -i

# Search Tempo for that trace ID
curl -s "http://localhost:3200/api/traces/<trace-id>" | jq

# Search Loki for checkout-service logs
curl -s "http://localhost:3100/loki/api/v1/query_range?query=%7Bservice_name%3D%22checkout-service%22%7D" | jq
```

## Artifacts Produced

| File | Contents |
|------|----------|
| `.omo/evidence/task-24-demo.log` | Full timestamped stdout from the run |
| `.omo/evidence/task-24-summary.txt` | Key-value summary (run id, durations, flags) |
| `.omo/evidence/task-24-k6-job.log` | Captured `kubectl logs` of the k6-load Job |
| `.omo/evidence/task-24-k6-results.json` | Parsed k6 metrics (p95, error rate, checks) |
| `docs/examples/postmortems/postmortem-demo-<ts>.md` | The postmortem — either copied from the incident-engine pod or synthesized as a fallback |

## Aborting the Demo

Press `Ctrl+C` to stop the demo. The `trap cleanup EXIT` handler in
`scripts/demo.sh` runs and:

1. Kills the 15-minute watchdog subshell.
2. Deletes the `pod-kill-checkout` PodChaos (if applied).
3. Deletes the `k6-load` Job (if applied).

Neither the chaos experiment nor the k6 Job will be left running.

## Fallback Behavior

`make demo` is designed to be useful even before the full Wave 4 stack is
deployed. The following fallbacks are exercised:

- **Fewer than 2 ecom services ready**: the script exits 0 with
  `mode=fallback` written to `.omo/evidence/task-24-summary.txt`. No k6 Job
  or chaos experiment is applied.
- **Chaos Mesh not ready**: the script exits 1 with a clear error message
  (because k6 + chaos is the whole point of the demo).
- **incident-engine not deployed**: the demo runs the load and chaos phases
  normally, then synthesizes a fallback postmortem in step 8 with the same
  six sections plus an explanatory note.
- **k6 Job does not complete within 10 minutes**: the script dumps the
  partial logs to `.omo/evidence/task-24-k6-job.log`, marks
  `k6_completed=false` in the summary, and exits 0.

To force the fallback path explicitly for testing:

```bash
DEMO_FORCE_FALLBACK=1 make demo-fallback
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

- **`FATAL: Chaos Mesh controller not ready in chaos-testing`**:
  `make up` did not finish, or Chaos Mesh is not installed. Re-run
  `make up` or install Chaos Mesh manually
  (`helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-testing --create-namespace`).
- **k6 Job stays in `ContainerCreating`**: the k6 image is being pulled;
  wait up to 60s on the first run.
- **No postmortem found**: verify the incident-engine pod is running
  (`kubectl get pods -n incident`) and that Alertmanager can reach
  `http://incident-engine.incident.svc:8088/webhook`. The demo will fall
  back to a synthesized postmortem so the artifact pipeline still has
  content to validate.
- **k6 Job fails with `p95 latency > 1000ms`**: the cluster is
  undersized for 100 VUs. Reduce `K6_VUS` or run the spike profile
  (`load/k6-job-spike.yaml`) on a larger node pool.
- **Demo runs longer than 15 minutes**: the watchdog subshell sends
  `SIGTERM` to the parent and triggers cleanup. Reduce
  `SETTLE_BEFORE_CHAOS` or `WAIT_FOR_ALERT` in `scripts/demo.sh` to
  tighten the budget.