"""Postmortem markdown generation following the Google SRE template."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from .storage import IncidentRecord
from .timeline import TimelineEvent

logger = logging.getLogger(__name__)

REQUIRED_SECTIONS = (
    "Summary",
    "Impact",
    "Timeline",
    "Root Cause",
    "Resolution",
    "Lessons Learned",
)


def _iso(value: datetime | str | None) -> str:
    """Return an ISO-8601 UTC string for a datetime or pass through a string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _duration_minutes(start: str, end: str | None) -> float:
    """Return the duration between two ISO timestamps in minutes."""
    if not start:
        return 0.0
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat((end or start).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (end_dt - start_dt).total_seconds() / 60.0)


def _format_timeline(events: list[TimelineEvent]) -> str:
    """Render a list of timeline events as a markdown table."""
    if not events:
        return "_No timeline events were collected._\n"
    lines = ["| Timestamp (UTC) | Source | Event |", "| --- | --- | --- |"]
    for event in events:
        safe_message = event.message.replace("|", "\\|")
        lines.append(f"| {event.timestamp} | {event.source} | {safe_message} |")
    return "\n".join(lines) + "\n"


def _build_summary(record: IncidentRecord) -> str:
    """Build the summary paragraph (1 paragraph)."""
    duration_min = _duration_minutes(record.starts_at, record.ends_at)
    duration_text = (
        f"lasted approximately {duration_min:.1f} minutes"
        if duration_min > 0
        else "duration is still being tracked"
    )
    return (
        f"On {record.starts_at}, the alert `{record.alertname}` fired against the "
        f"`{record.service}` service at severity `{record.severity}`. "
        f"The incident {duration_text}. "
        f"The incident-engine queried Tempo, Loki, and Prometheus for available "
        f"context, then produced this postmortem for "
        f"human review."
    )


def _build_impact(record: IncidentRecord) -> str:
    """Build the impact section (which service, error budget)."""
    slo = record.slo or "n/a"
    lines = [
        f"- **Affected service**: `{record.service}`",
        f"- **Alert**: `{record.alertname}`",
        f"- **Severity**: `{record.severity}`",
        f"- **Related SLO**: `{slo}`",
        f"- **User impact**: requests to `{record.service}` were degraded while the alert was firing.",
        f"- **Error budget**: consumption depends on the alert duration; check the recording rule `slo:availability:burn_rate_{'{5m,1h,6h,24h}'}` for live burn-rate data.",
    ]
    return "\n".join(lines) + "\n"


def _build_root_cause(record: IncidentRecord) -> str:
    """Build the root cause section using chaos correlation heuristics."""
    chaos = record.correlated_chaos
    if not chaos:
        return (
            "No chaos experiments with selectors matching the affected service "
            "were active in the lookback window. The most likely root cause is "
            "an organic change (deploy, dependency failure, traffic spike) — "
            "investigate the deployment log and recent commits.\n"
        )
    lines = [
        "The following chaos experiments are correlated with this incident:",
        "",
    ]
    for experiment in chaos:
        lines.append(
            f"- **{experiment.get('name', 'unknown')}** "
            f"({experiment.get('kind', 'unknown')}) — "
            f"started at {experiment.get('start_time', 'unknown')} "
            f"in namespace `{experiment.get('namespace', 'unknown')}` "
            f"targeting `{experiment.get('target', record.service)}`; "
            f"spec: `{json.dumps(experiment.get('spec', {}), sort_keys=True)}`."
        )
    lines.append("")
    lines.append(
        "Validate by replaying the experiment in a staging cluster and observing "
        "whether the same alert fires within the same window."
    )
    return "\n".join(lines) + "\n"


def _build_resolution(record: IncidentRecord) -> str:
    """Build the resolution section based on alert end time."""
    if record.status == "resolved" and record.ends_at:
        return (
            f"The alert `{record.alertname}` stopped firing at {record.ends_at} "
            f"and was auto-detected as resolved. No further action was required "
            f"from the incident-engine; human reviewers should confirm whether "
            f"the recovery is durable.\n"
        )
    if record.status == "firing":
        return (
            f"The alert `{record.alertname}` is still firing as of "
            f"{record.updated_at}. This postmortem was generated on alert "
            f"start; re-evaluate once the alert transitions to `resolved`.\n"
        )
    return (
        f"Resolution status is `{record.status}`. The incident-engine does "
        f"not auto-remediate; a human operator must close the incident.\n"
    )


def _build_lessons(record: IncidentRecord) -> str:
    """Build a fill-in-the-blank lessons-learned section."""
    return (
        "_To be completed by the incident owner after review._\n\n"
        "- **What went well**: \n"
        "- **What went poorly**: \n"
        "- **Where we got lucky**: \n"
        "- **Action items**:\n"
        "  - [ ] Triage owner: \n"
        "  - [ ] Remediation tracking issue: \n"
        "  - [ ] Follow-up review date: \n"
    )


def render_postmortem(record: IncidentRecord, events: list[TimelineEvent]) -> str:
    """Render the full postmortem markdown for the given incident + timeline."""
    sections: list[str] = []
    sections.append(f"# Postmortem — `{record.alertname}` on `{record.service}`\n")
    sections.append(f"_Generated {datetime.now(UTC).isoformat()} by incident-engine._\n")
    sections.append("## Summary\n")
    sections.append(_build_summary(record) + "\n")
    sections.append("## Impact\n")
    sections.append(_build_impact(record) + "\n")
    sections.append("## Timeline\n")
    sections.append(_format_timeline(events) + "\n")
    sections.append("## Root Cause\n")
    sections.append(_build_root_cause(record) + "\n")
    sections.append("## Resolution\n")
    sections.append(_build_resolution(record) + "\n")
    sections.append("## Lessons Learned\n")
    sections.append(_build_lessons(record))
    return "".join(sections)


def has_all_sections(content: str) -> bool:
    """Return True when every required section header is present."""
    for section in REQUIRED_SECTIONS:
        if f"## {section}" not in content:
            logger.warning("postmortem missing section: %s", section)
            return False
    return True


def build_summary(record: IncidentRecord) -> str:
    """Expose the summary builder for reuse in API responses."""
    return _build_summary(record)


def to_summary_payload(record: IncidentRecord, events: list[TimelineEvent]) -> dict[str, Any]:
    """Build a structured summary for the incident-engine API."""
    return {
        "id": record.id,
        "alertname": record.alertname,
        "service": record.service,
        "severity": record.severity,
        "slo": record.slo,
        "status": record.status,
        "starts_at": record.starts_at,
        "ends_at": record.ends_at,
        "fingerprint": record.fingerprint,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "file_path": record.file_path,
        "summary": record.summary,
        "labels": record.labels,
        "annotations": record.annotations,
        "timeline": [event.to_dict() for event in events],
        "correlated_chaos": record.correlated_chaos,
    }
