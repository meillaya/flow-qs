# Postmortem: `ServiceDown` on `checkout-service`
_Generated 2026-07-16T05:27:54.202794+00:00 by incident-engine._
## Summary
On 2026-07-16T05:27:43.370000+00:00, the alert `ServiceDown` fired against the `checkout-service` service at severity `page`. The incident duration is still being tracked. The incident-engine queried Tempo, Loki, and Prometheus for available context, then produced this postmortem for human review.
## Impact
- **Affected service**: `checkout-service`
- **Alert**: `ServiceDown`
- **Severity**: `page`
- **Related SLO**: `n/a`
- **User impact**: requests to `checkout-service` were degraded while the alert was firing.
- **Error budget**: consumption depends on the alert duration; check the recording rule `slo:availability:burn_rate_{5m,1h,6h,24h}` for live burn-rate data.

## Timeline
| Timestamp (UTC) | Source | Event |
| --- | --- | --- |
| 2026-07-16T05:22:43.370000+00:00 | prometheus | Metric snapshot: synthetic_check_success=1.0 |
| 2026-07-16T05:22:43.370000+00:00 | prometheus | Metric snapshot: availability_ratio_5m=1.0 |
| 2026-07-16T05:22:48.468048+00:00 | tempo | Trace 8c36bbd415de8880e26ef81223ff088d observed for service 'checkout-service' (duration 2.0ms) |
| 2026-07-16T05:22:48.471308+00:00 | tempo | Trace 6035c0174ae590a46f3306e3e20c1241 observed for service 'checkout-service' (duration 0.0ms) |
| 2026-07-16T05:22:48.472084+00:00 | tempo | Trace 1fbf9bc8eda3e2067ef624af0bad9c46 observed for service 'checkout-service' (duration 2.0ms) |
| 2026-07-16T05:22:58.468273+00:00 | tempo | Trace 99e7fa3162c00137bc474095d36478fe observed for service 'checkout-service' (duration 3.0ms) |
| 2026-07-16T05:22:58.472365+00:00 | tempo | Trace 0c866bf9d71556b164293a57cf93de98 observed for service 'checkout-service' (duration 0.0ms) |
| 2026-07-16T05:22:58.473185+00:00 | tempo | Trace cda66b18e0f00dc745f5603437542f65 observed for service 'checkout-service' (duration 2.0ms) |
| 2026-07-16T05:23:08.324823+00:00 | tempo | Trace d45e4942c7f612c6de5288a29cbe0cd3 observed for service 'synthetic-monitor' (duration 11.0ms) |
| 2026-07-16T05:23:08.335515+00:00 | loki | Log entry (trace d45e4942c7f612c6de5288a29cbe0cd3): HTTP Request: GET http://checkout-service.ecom.svc.cluster.local:8003/healthz "HTTP/1.1 200 OK" |
| 2026-07-16T05:23:08.468130+00:00 | tempo | Trace e1fbb0d186d72f515aa97d21bb263231 observed for service 'checkout-service' (duration 2.0ms) |
| 2026-07-16T05:23:08.471126+00:00 | tempo | Trace 3505ea6fa322a5b5969b5c6effb35a23 observed for service 'checkout-service' (duration 0.0ms) |
| 2026-07-16T05:23:08.471850+00:00 | tempo | Trace 9676d290d335dfb6d304d166f663f79b observed for service 'checkout-service' (duration 2.0ms) |
| 2026-07-16T05:25:10+00:00 | chaos-mesh | Chaos experiment 'demo-alert-checkout' of kind 'PodChaos' applied to 'checkout-service' |
| 2026-07-16T05:27:43.370000+00:00 | alertmanager | Alert 'ServiceDown' started firing for service 'checkout-service' |

## Root Cause
The following chaos experiments are correlated with this incident:

- **demo-alert-checkout** (PodChaos), started at 2026-07-16T05:25:10Z in namespace `chaos-testing` targeting `checkout-service`; spec: `{"action": "pod-failure", "duration": "3m", "mode": "all", "selector": {"labelSelectors": {"app.kubernetes.io/name": "checkout-service"}, "namespaces": ["ecom"]}}`.

Validate by replaying the experiment in a staging cluster and observing whether the same alert fires within the same window.

## Resolution
The alert `ServiceDown` is still firing as of 2026-07-16T05:27:53.557187+00:00. This postmortem was generated on alert start; re-evaluate once the alert transitions to `resolved`.

## Lessons Learned
_To be completed by the incident owner after review._

- **What went well**: 
- **What went poorly**: 
- **Where we got lucky**: 
- **Action items**:
  - [ ] Triage owner: 
  - [ ] Remediation tracking issue: 
  - [ ] Follow-up review date: 
