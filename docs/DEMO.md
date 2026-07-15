# Demo Guide

`make demo` runs a strict, causally correlated load-and-chaos incident demo.
Partial readiness, unrelated postmortems, missing Chaos Mesh correlation, k6
threshold failures, and incomplete cleanup all fail the run. There is no
synthetic alert or fallback postmortem path.

## Causal flow

1. Verify all six application deployments, Chaos Mesh, Alertmanager, and
   incident-engine are ready.
2. Verify incident-engine can list `PodChaos` in `chaos-testing` and that
   the checkout `ServiceDown` rule is loaded.
3. Bounded-delete any same-name PodChaos, Workflow, Schedules,
   WorkflowNodes, and generated PodChaos left by an interrupted prior run.
4. Create a run-specific postmortem freshness marker and start the
   chaos-aware k6 load Job.
5. After 60 seconds, apply `demo-alert-checkout`, a three-minute PodChaos
   failure against checkout. This works on local kernels that do not provide
   the legacy `ip_tables` modules required by Chaos Mesh NetworkChaos.
6. Wait for a fresh postmortem containing both:

   ```text
   # Postmortem — `ServiceDown` on `checkout-service`
   **demo-alert-checkout** (PodChaos)
   ```

   Fresh unrelated, wrong-service, and uncorrelated postmortems are ignored.
7. Confirm k6 is still active, then start the `pod-kill-checkout` Workflow.
   It schedules one checkout pod kill every 60 seconds for five minutes.
8. Copy and revalidate the correlated postmortem, require k6 success, parse its
   compact stdout summary, and write the provisional run summary.
9. Pause and bounded-delete the Workflow tree, delete the PodChaos to
   restore checkout, delete WorkflowNodes and the k6 Job, then
   verify every resource is absent before printing `Demo complete`.

This ordering keeps the alert, Chaos Mesh experiment, incident, and postmortem
on checkout while waiting for `ServiceDown` correlation before recurring pod
replacement begins.

## Safety and load contract

`demo-alert-checkout` selects only checkout pods in `ecom`, uses
`action: pod-failure`, and never targets the control plane. The outage is
intentional and visible to k6 traffic from the `load` namespace.

The dedicated demo Job sets the exact value `CHAOS_AWARE=true` and keeps these
strict thresholds:

- checkout success rate greater than 25%;
- request failure rate below 40%;
- request-duration p95 below 3000 ms.

Standalone load remains greater than 95%, below 1%, and below 1000 ms. The k6
Job exit is authoritative. The demo profile allows the deliberate three-minute
checkout outage while still rejecting total failure; it never rewrites a
threshold after a run begins.

## Prerequisites

- All six services are Available in `ecom`.
- Chaos Mesh 2.8.3 is ready in `chaos-testing` with its controller endpoint disabled.
- Prometheus contains checkout's `ServiceDown` rule and Alertmanager is ready.
- incident-engine is ready in `incident`, has persistent postmortem storage,
  and its ServiceAccount can list PodChaos:

  ```bash
  kubectl auth can-i list podchaos.chaos-mesh.org \
    -n chaos-testing \
    --as=system:serviceaccount:incident:incident-engine
  ```

The command must print `yes`.

## Run

```bash
mkdir -p .omo/evidence
make demo 2>&1 | tee .omo/evidence/manual-demo.log
```

The total run has a 15-minute watchdog. Alert correlation has a 240-second
budget after PodChaos admission. Pressing Ctrl+C or any failure invokes the
same bounded cleanup path.

Important progress lines are:

```text
Step 3/8: applying causal checkout PodChaos
Step 4/8: waiting up to 240s for matching checkout ServiceDown
Step 5/8: starting recurring checkout pod-kill workflow
Step 5/8: copying the causally correlated postmortem
Run checks passed; finalizing cleanup
Demo complete.
```

## Artifacts

| File | Contents |
|---|---|
| `.omo/evidence/task-24-demo.log` | Timestamped orchestration and cleanup receipts |
| `.omo/evidence/task-24-summary.txt` | Success-only incident identity and run summary |
| `.omo/evidence/task-24-k6-job-<run-id>.log` | Current run's k6 stdout |
| `.omo/evidence/task-24-k6-results-<run-id>.json` | Validated compact k6 metrics |
| `docs/examples/postmortems/postmortem-demo-<run-id>.md` | Fresh checkout postmortem with exact PodChaos correlation |

The success summary records `incident_alert=ServiceDown`,
`incident_service=checkout-service`, `incident_chaos=demo-alert-checkout`,
`incident_chaos_kind=PodChaos`, and
`checkout_podkill_workflow=pod-kill-checkout`.

## Failure and cleanup behavior

The demo exits nonzero for missing services, RBAC, alert rules, admission,
correlation, active k6 load, postmortem copy validation, k6 thresholds, timeout,
or cleanup. A failed run never retains a success summary or stale k6 result.

Cleanup first pauses the Workflow and generated Schedules, then requests
bounded `--wait=false` deletion of the Workflow, Schedules, and generated
PodChaos. It next deletes `podchaos/demo-alert-checkout`, WorkflowNodes,
and the k6 Job, then performs a bounded absence poll. Finalizers are never
stripped. Remaining resources make cleanup and the demo fail closed.
