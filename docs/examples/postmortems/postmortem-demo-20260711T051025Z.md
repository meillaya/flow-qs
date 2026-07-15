# Postmortem — `ServiceDown` on `catalog-service`
_Generated 2026-07-11T05:15:15.397059+00:00 by incident-engine._
## Summary
On 2026-07-11T05:13:05.125000+00:00, the alert `ServiceDown` fired against the `catalog-service` service at severity `page`. The incident lasted approximately 2.0 minutes. The incident-engine correlated traces from Tempo, logs from Loki, and metric snapshots from Prometheus, then produced this postmortem for human review.
## Impact
- **Affected service**: `catalog-service`
- **Alert**: `ServiceDown`
- **Severity**: `page`
- **Related SLO**: `n/a`
- **User impact**: requests to `catalog-service` were degraded while the alert was firing.
- **Error budget**: consumption depends on the alert duration; derive live burn from the service-qualified `sli:<service>:availability:ratio_{5m,1h,6h,1d}` records and inspect `slo:<service>:availability:error_budget_remaining:30d`.

## Timeline
| Timestamp (UTC) | Source | Event |
| --- | --- | --- |
| 2026-07-11T05:13:05.125000+00:00 | alertmanager | Alert 'ServiceDown' started firing for service 'catalog-service' |
| 2026-07-11T05:15:05.125000+00:00 | alertmanager | Alert 'ServiceDown' resolved for service 'catalog-service' |

## Root Cause
No chaos experiments with selectors matching the affected service were active in the lookback window. The most likely root cause is an organic change (deploy, dependency failure, traffic spike) — investigate the deployment log and recent commits.

## Resolution
The alert `ServiceDown` stopped firing at 2026-07-11T05:15:05.125000+00:00 and was auto-detected as resolved. No further action was required from the incident-engine; human reviewers should confirm whether the recovery is durable.

## Lessons Learned
_To be completed by the incident owner after review._

- **What went well**: 
- **What went poorly**: 
- **Where we got lucky**: 
- **Action items**:
  - [ ] Triage owner: 
  - [ ] Remediation tracking issue: 
  - [ ] Follow-up review date: 
