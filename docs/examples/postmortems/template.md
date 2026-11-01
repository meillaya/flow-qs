# Postmortem template

> Annotated template. Replace bracketed text with incident-specific values. The incident engine generates files that follow this shape.

## Summary

<!-- One sentence: what happened, to which service, and what the user impact was. -->

On **<date>** at **<time>**, **<service>** experienced **<symptom>** for **<duration>**, causing **<impact>**.

## Impact

<!-- Quantify the impact: duration, error rate, affected requests, SLO budget burned. -->

- **Duration**: <duration>
- **Error rate peak**: <percentage>
- **Affected requests**: <count> (estimated)
- **SLO budget burned**: <percentage> of monthly budget
- **Revenue/user impact**: <description>

## Timeline

<!-- UTC timestamps, 1-minute granularity if possible. Include detection, escalation, mitigation, and recovery. -->

| Time (UTC) | Event |
|------------|-------|
| <time> | <event> |
| <time> | <event> |
| <time> | <event> |

## Root Cause

<!-- Explain why the incident happened. Link to traces, logs, and metrics. -->

<description>

Evidence:

- Prometheus query: `<query>`
- Trace ID: `<trace-id>`
- Log query: `{service_namespace="ecom",service_name="<service>"}`

## Resolution

<!-- What fixed the incident? -->

<description>

## Lessons Learned

<!-- What went well, what went poorly, and what was surprising. -->

### What went well

- <item>
- <item>

### What went poorly

- <item>
- <item>

## Action Items

<!-- Each action item must have an owner and a due date. -->

| Action | Owner | Due Date | Priority |
|--------|-------|----------|----------|
| <action> | <owner> | <date> | <high/medium/low> |
| <action> | <owner> | <date> | <high/medium/low> |

## Appendix

<!-- Any extra context: links to dashboards, alert labels, related incidents. -->

- Alertmanager alert: `<link>`
- Grafana dashboard: `<link>`
- Related incident: `<link>`
