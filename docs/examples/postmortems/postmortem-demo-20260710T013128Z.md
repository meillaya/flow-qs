# Postmortem — `HighErrorRate` on `cart-service`
_Generated 2026-07-10T01:18:11.630996+00:00 by incident-engine._
## Summary
On 2026-07-09T21:00:00+00:00, the alert `HighErrorRate` fired against the `cart-service` service at severity `page`. The incident lasted approximately 7.0 minutes. The incident-engine correlated traces from Tempo, logs from Loki, and metric snapshots from Prometheus, then produced this postmortem for human review.
## Impact
- **Affected service**: `cart-service`
- **Alert**: `HighErrorRate`
- **Severity**: `critical`
- **Related SLO**: `availability`
- **User impact**: requests to `cart-service` were degraded while the alert was firing.
- **Error budget**: consumption depends on the alert duration; derive live burn from the service-qualified `sli:<service>:availability:ratio_{5m,1h,6h,1d}` records and inspect `slo:<service>:availability:error_budget_remaining:30d`.

## Timeline
| Timestamp (UTC) | Source | Event |
| --- | --- | --- |
| 2026-07-09T21:00:00+00:00 | alertmanager | Alert 'HighErrorRate' started firing for service 'cart-service' |
| 2026-07-09T21:07:00+00:00 | alertmanager | Alert 'HighErrorRate' resolved for service 'cart-service' |

## Root Cause
No chaos experiments with selectors matching the affected service were active in the lookback window. The most likely root cause is an organic change (deploy, dependency failure, traffic spike) — investigate the deployment log and recent commits.

## Resolution
The alert `HighErrorRate` stopped firing at 2026-07-09T21:07:00+00:00 and was auto-detected as resolved. No further action was required from the incident-engine; human reviewers should confirm whether the recovery is durable.

## Lessons Learned
_To be completed by the incident owner after review._

- **What went well**: 
- **What went poorly**: 
- **Where we got lucky**: 
- **Action items**:
  - [ ] Triage owner: 
  - [ ] Remediation tracking issue: 
  - [ ] Follow-up review date: 
