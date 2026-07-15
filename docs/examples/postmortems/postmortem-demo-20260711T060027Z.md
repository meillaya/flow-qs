# Postmortem — `ServiceDown` on `checkout-service`
_Generated 2026-07-11T06:03:23.684822+00:00 by incident-engine._
## Summary
On 2026-07-11T06:03:13.370000+00:00, the alert `ServiceDown` fired against the `checkout-service` service at severity `page`. The incident duration is still being tracked. The incident-engine correlated traces from Tempo, logs from Loki, and metric snapshots from Prometheus, then produced this postmortem for human review.
## Impact
- **Affected service**: `checkout-service`
- **Alert**: `ServiceDown`
- **Severity**: `page`
- **Related SLO**: `n/a`
- **User impact**: requests to `checkout-service` were degraded while the alert was firing.
- **Error budget**: consumption depends on the alert duration; derive live burn from the service-qualified `sli:<service>:availability:ratio_{5m,1h,6h,1d}` records and inspect `slo:<service>:availability:error_budget_remaining:30d`.

## Timeline
| Timestamp (UTC) | Source | Event |
| --- | --- | --- |
| 0001-01-01T00:00:00+00:00 | alertmanager | Alert 'ServiceDown' resolved for service 'checkout-service' |
| 2026-07-11T06:03:13.370000+00:00 | alertmanager | Alert 'ServiceDown' started firing for service 'checkout-service' |

## Root Cause
The following chaos experiments are correlated with this incident:

- **demo-alert-checkout** (NetworkChaos) — started at 2026-07-11T06:01:32Z in namespace `chaos-testing` targeting `checkout-service`; spec: `{"action": "partition", "direction": "from", "duration": "3m", "mode": "all", "selector": {"labelSelectors": {"app.kubernetes.io/name": "checkout-service"}, "namespaces": ["ecom"]}, "target": {"mode": "all", "selector": {"labelSelectors": {"app.kubernetes.io/name": "synthetic-monitor"}, "namespaces": ["ecom"]}}}`.

Validate by replaying the experiment in a staging cluster and observing whether the same alert fires within the same window.

## Resolution
The alert `ServiceDown` stopped firing at 0001-01-01T00:00:00+00:00 and was auto-detected as resolved. No further action was required from the incident-engine; human reviewers should confirm whether the recovery is durable.

## Lessons Learned
_To be completed by the incident owner after review._

- **What went well**: 
- **What went poorly**: 
- **Where we got lucky**: 
- **Action items**:
  - [ ] Triage owner: 
  - [ ] Remediation tracking issue: 
  - [ ] Follow-up review date: 
