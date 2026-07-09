#!/usr/bin/env python3
"""Build Prometheus recording + alert rules for the observability platform.

Reads per-service SLO definitions from ``slo/*.yaml`` and emits two rule files:

  observability/prometheus-rules/sli_recording_rules.yaml
      - Per-service availability ratio SLIs over the multi-window set
        5m, 30m, 1h, 6h, 1d, 3d, 30d
      - Per-service latency p99 ratio SLIs over the same windows
      - Per-service availability error budget remaining percentage over 30d

  observability/prometheus-rules/alert_rules.yaml
      - Per-service MWMB alerts (page: 1h/6h, warning: 6h/3d) for both
        availability and latency SLOs
      - Per-service standard alerts:
        ServiceDown, HighErrorRate, HighLatencyP99

The MWMB thresholds follow the Google SRE Workbook (Chapter 5 — Alerting on
SLOs) multipliers: 14.4x / 6x / 1x for the 1h / 6h / 3d windows.

Run from the repo root::

    python3 scripts/build_prometheus_rules.py

No third-party dependencies; uses the stdlib only.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print(
        "PyYAML is required to parse slo/*.yaml. Install with `pip install pyyaml`.",
        file=sys.stderr,
    )
    raise

REPO_ROOT = Path(__file__).resolve().parent.parent
SLO_DIR = REPO_ROOT / "slo"
RULES_DIR = REPO_ROOT / "observability" / "prometheus-rules"
WINDOWS = ["5m", "30m", "1h", "6h", "1d", "3d", "30d"]


# ---------------------------------------------------------------------------
# SLO helpers
# ---------------------------------------------------------------------------

def load_slos(slo_dir: Path) -> list[dict]:
    """Load every ``*.yaml`` file under ``slo_dir`` (skipping README)."""
    slos: list[dict] = []
    for path in sorted(slo_dir.glob("*.yaml")):
        with path.open() as fh:
            slos.append(yaml.safe_load(fh))
    return slos


def split_spec(spec: str) -> tuple[str, dict[str, str]]:
    """Split ``metric{labels}`` into ``(metric, label_dict)``.

    Preserves Prometheus match operators (``=``, ``!=``, ``=~``, ``!~``) by
    keeping them attached to the value rather than separating on the first ``=``.
    """
    match = re.search(r"\{([^}]*)\}\s*$", spec)
    if not match:
        return spec, {}
    metric = spec[: match.start()].rstrip()
    raw_labels = match.group(1).strip()

    labels: dict[str, str] = {}
    if raw_labels:
        for item in raw_labels.split(","):
            stripped = item.strip()
            operator_match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)(!=|!~|=|=~)(.*)$", stripped)
            if operator_match:
                labels[operator_match.group(1)] = operator_match.group(2) + operator_match.group(3)
            else:
                key, _, value = stripped.partition("=")
                labels[key] = value
    return metric, labels


def merge_labels(base: dict[str, str], extra: dict[str, str]) -> str:
    """Render Prometheus label selector. ``extra`` wins on key collision."""
    merged = dict(base)
    merged.update(extra)
    if not merged:
        return ""
    parts = []
    for key, value in merged.items():
        if re.match(r"^(=~|!~|=|!=)", value):
            parts.append(f"{key}{value}")
        else:
            parts.append(f"{key}={value}")
    return "{" + ", ".join(parts) + "}"


# ---------------------------------------------------------------------------
# Recording rules
# ---------------------------------------------------------------------------

def recording_rules_for_service(slo: dict) -> list[dict]:
    """Produce per-service recording rule definitions."""
    service: str = slo["service"]
    extra = {"service_name": f'"{service}"'}
    rules: list[dict] = []

    for slo_def in slo["slos"]:
        kind: str = slo_def["name"]
        objective: float = float(slo_def["objective"])

        if kind in ("availability", "success_rate"):
            good_metric, good_labels = split_spec(slo_def["sli"]["spec"])
            good_selector = merge_labels(good_labels, extra)

            total_metric, total_labels = split_spec(slo_def["sli"]["total_spec"])
            total_selector = merge_labels(total_labels, extra)

            for window in WINDOWS:
                expr = (
                    f"sum(\n"
                    f"  rate({good_metric}{good_selector}[{window}])\n"
                    f")\n"
                    f"/\n"
                    f"sum(\n"
                    f"  rate({total_metric}{total_selector}[{window}])\n"
                    f")"
                )
                rules.append({
                    "record": f"sli:{service}:availability:ratio_{window}",
                    "expr": expr,
                })

            budget_expr = (
                f"(1 - (\n"
                f"  (1 - sli:{service}:availability:ratio_30d) / (1 - {objective})\n"
                f")) * 100"
            )
            rules.append({
                "record": f"slo:{service}:availability:error_budget_remaining:30d",
                "expr": budget_expr,
            })

        elif kind == "latency_p99":
            good_metric, good_labels = split_spec(slo_def["sli"]["spec"])
            good_selector = merge_labels(good_labels, extra)

            total_metric, total_labels = split_spec(slo_def["sli"]["total_spec"])
            total_selector = merge_labels(total_labels, extra)

            for window in WINDOWS:
                expr = (
                    f"sum(\n"
                    f"  rate({good_metric}{good_selector}[{window}])\n"
                    f")\n"
                    f"/\n"
                    f"sum(\n"
                    f"  rate({total_metric}{total_selector}[{window}])\n"
                    f")"
                )
                rules.append({
                    "record": f"sli:{service}:latency_p99:ratio_{window}",
                    "expr": expr,
                })

    return rules


def build_recording_groups(slos: list[dict]) -> list[dict]:
    groups: list[dict] = []
    for slo in slos:
        groups.append({
            "name": f"sli_recording_rules_{slo['service'].replace('-', '_')}",
            "interval": "30s",
            "rules": recording_rules_for_service(slo),
        })
    return groups


# ---------------------------------------------------------------------------
# MWMB thresholds (Google SRE Workbook, "Alerting on SLOs", Table 4.2)
# ---------------------------------------------------------------------------
# Page-tier (fast burn): 14.4x / 6x burn rates over 1h / 6h — equivalent to
# consuming ~2% / 5% of the monthly error budget in the short window.
# Ticket-tier (slow burn): 6x / 1x burn rates over 6h / 3d — catches
# sustained budget drains that have not yet triggered the fast-burn rule.
PAGE_FACTORS = {"1h": 14.4, "6h": 6.0}
TICKET_FACTORS = {"6h": 6.0, "3d": 1.0}


def threshold(sli_objective: float, burn_factor: float) -> float:
    """Convert a burn-rate factor into a SLI threshold.

    burn_factor = (1 - SLI_window) / (1 - SLO)
    ⇒ SLI_window = 1 - burn_factor * (1 - SLO)
    """
    return 1.0 - burn_factor * (1.0 - sli_objective)


# ---------------------------------------------------------------------------
# Alert rules
# ---------------------------------------------------------------------------

def fast_burn_alerts(slo: dict) -> list[dict]:
    """Page-severity MWMB alerts per SLO (1h/6h pair)."""
    service = slo["service"]
    alerts: list[dict] = []

    for slo_def in slo["slos"]:
        kind = slo_def["name"]
        objective = float(slo_def["objective"])
        if kind == "availability" or kind == "success_rate":
            slo_metric = "availability"
        elif kind == "latency_p99":
            slo_metric = "latency_p99"
        else:
            continue

        burn_1h = PAGE_FACTORS["1h"]
        burn_6h = PAGE_FACTORS["6h"]
        th_1h = threshold(objective, burn_1h)
        th_6h = threshold(objective, burn_6h)

        alerts.append({
            "alert": f"SLO{slo_metric.title().replace('_', '')}BurnTooFast",
            "expr": (
                f"sli:{service}:{slo_metric}:ratio_1h < {th_1h:.10f}\n"
                f"and\n"
                f"sli:{service}:{slo_metric}:ratio_6h < {th_6h:.10f}"
            ),
            "for": "2m",
            "labels": {
                "severity": "page",
                "service": service,
                "slo_kind": slo_metric,
                "burn_window_long": "6h",
                "burn_window_short": "1h",
            },
            "annotations": {
                "summary": f"{service} SLO {slo_metric} burn rate is unsustainable",
                "description": (
                    f"{service} {slo_metric} SLO ({objective:.4f}) is being "
                    f"consumed at >=14.4x (1h) and >=6x (6h) the normal rate."
                ),
                "runbook": "https://sre.google/workbook/alerting-on-slos/",
            },
        })
    return alerts


def slow_burn_alerts(slo: dict) -> list[dict]:
    """Warning-severity MWMB alerts per SLO (6h/3d pair)."""
    service = slo["service"]
    alerts: list[dict] = []

    for slo_def in slo["slos"]:
        kind = slo_def["name"]
        objective = float(slo_def["objective"])
        if kind == "availability" or kind == "success_rate":
            slo_metric = "availability"
        elif kind == "latency_p99":
            slo_metric = "latency_p99"
        else:
            continue

        burn_6h = TICKET_FACTORS["6h"]
        burn_3d = TICKET_FACTORS["3d"]
        th_6h = threshold(objective, burn_6h)
        th_3d = threshold(objective, burn_3d)

        alerts.append({
            "alert": f"SLO{slo_metric.title().replace('_', '')}DrainingBudget",
            "expr": (
                f"sli:{service}:{slo_metric}:ratio_6h < {th_6h:.10f}\n"
                f"and\n"
                f"sli:{service}:{slo_metric}:ratio_3d < {th_3d:.10f}"
            ),
            "for": "30m",
            "labels": {
                "severity": "warning",
                "service": service,
                "slo_kind": slo_metric,
                "burn_window_long": "3d",
                "burn_window_short": "6h",
            },
            "annotations": {
                "summary": f"{service} SLO {slo_metric} budget is draining steadily",
                "description": (
                    f"{service} {slo_metric} SLO ({objective:.4f}) is being "
                    f"consumed at >=6x (6h) and >=1x (3d) the normal rate."
                ),
                "runbook": "https://sre.google/workbook/alerting-on-slos/",
            },
        })
    return alerts


def standard_alerts(slo: dict) -> list[dict]:
    """Standard (non-MWMB) health alerts per service.

    - ServiceDown: up == 0 for 1m
    - HighErrorRate: 5xx ratio > 5% for 5m
    - HighLatencyP99: p99 > 2x SLO threshold for 5m
    """
    service = slo["service"]
    alerts: list[dict] = [
        {
            "alert": "ServiceDown",
            "expr": f'up{{service_name="{service}"}} == 0',
            "for": "1m",
            "labels": {
                "severity": "page",
                "service": service,
            },
            "annotations": {
                "summary": f"{service} is reporting as down",
                "description": (
                    f"Prometheus has not been able to scrape {service} for at "
                    "least one minute."
                ),
            },
        },
    ]

    for slo_def in slo["slos"]:
        if slo_def["name"] in {"availability", "success_rate"}:
            total_metric, total_labels = split_spec(slo_def["sli"]["total_spec"])
            err_selector = merge_labels(total_labels, {"status": '~"5.."', "service_name": f'"{service}"'})
            ok_selector = merge_labels(total_labels, {"service_name": f'"{service}"'})
            expr = (
                f"(\n"
                f"  sum(rate({total_metric}{err_selector}[5m]))\n"
                f"  /\n"
                f"  sum(rate({total_metric}{ok_selector}[5m]))\n"
                f") > 0.05"
            )
            alerts.append({
                "alert": "HighErrorRate",
                "expr": expr,
                "for": "5m",
                "labels": {
                    "severity": "page",
                    "service": service,
                },
                "annotations": {
                    "summary": f"{service} 5xx ratio > 5%",
                    "description": (
                        f"{service} emitted >5% 5xx responses for 5 minutes."
                    ),
                },
            })
        elif slo_def["name"] == "latency_p99":
            threshold_value = float(
                re.search(r'le="([^"]+)"', slo_def["sli"]["spec"]).group(1)
            )
            warnings = 2.0 * threshold_value
            critical = 4.0 * threshold_value
            bucket_metric, bucket_labels = split_spec(slo_def["sli"]["spec"])
            bucket_selector = merge_labels(bucket_labels, {"service_name": f'"{service}"'})
            alerts.append({
                "alert": "HighLatencyP99",
                "expr": (
                    f"histogram_quantile(0.99,\n"
                    f"  sum by (le) (rate({bucket_metric}{bucket_selector}[5m]))\n"
                    f") > {warnings:.4f}"
                ),
                "for": "5m",
                "labels": {
                    "severity": "warning",
                    "service": service,
                    "threshold_p99_s": f"{warnings:.4f}",
                    "critical_p99_s": f"{critical:.4f}",
                },
                "annotations": {
                    "summary": f"{service} p99 latency > {warnings*1000:.0f}ms",
                    "description": (
                        f"{service} p99 latency exceeded 2x the SLO threshold "
                        f"({threshold_value*1000:.0f}ms) for 5 minutes."
                    ),
                },
            })
    return alerts


def build_alert_groups(slos: list[dict]) -> list[dict]:
    groups: list[dict] = []
    for slo in slos:
        service = slo["service"]
        rules: list[dict] = []
        rules.extend(fast_burn_alerts(slo))
        rules.extend(slow_burn_alerts(slo))
        rules.extend(standard_alerts(slo))
        groups.append({
            "name": f"alert_rules_{service.replace('-', '_')}",
            "interval": "30s",
            "rules": rules,
        })
    return groups


# ---------------------------------------------------------------------------
# YAML emitter (preserves order, no PyYAML anchors)
# ---------------------------------------------------------------------------

def dump_yaml(path: Path, groups: list[dict]) -> None:
    """Emit a Prometheus rule_files document.

    We open-code the dumper so that scalar ``expr`` strings are emitted as
    block scalars (``|``) — yaml.safe_dump renders long expressions as
    folded scalars which promtool parses fine but is unreadable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        fh.write("groups:\n")
        for group in groups:
            fh.write(f"  - name: {group['name']}\n")
            fh.write(f"    interval: {group['interval']}\n")
            fh.write("    rules:\n")
            for rule in group["rules"]:
                if "record" in rule:
                    fh.write(f"      - record: {rule['record']}\n")
                else:
                    fh.write(f"      - alert: {rule['alert']}\n")
                fh.write("        expr: |\n")
                for line in rule["expr"].splitlines():
                    fh.write(f"          {line}\n" if line.strip() else "          \n")
                if "for" in rule:
                    fh.write(f"        for: {rule['for']}\n")
                if rule.get("labels"):
                    fh.write("        labels:\n")
                    for k, v in rule["labels"].items():
                        fh.write(f"          {k}: \"{v}\"\n")
                if rule.get("annotations"):
                    fh.write("        annotations:\n")
                    for k, v in rule["annotations"].items():
                        indented = v.replace("\n", "\n            ")
                        fh.write(f"          {k}: \"{indented}\"\n")
        fh.write("\n")
    print(f"wrote {path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    slos = load_slos(SLO_DIR)
    if not slos:
        print(f"no SLO files found under {SLO_DIR}", file=sys.stderr)
        return 1

    rec_groups = build_recording_groups(slos)
    alert_groups = build_alert_groups(slos)

    dump_yaml(RULES_DIR / "sli_recording_rules.yaml", rec_groups)
    dump_yaml(RULES_DIR / "alert_rules.yaml", alert_groups)
    return 0


if __name__ == "__main__":
    sys.exit(main())
