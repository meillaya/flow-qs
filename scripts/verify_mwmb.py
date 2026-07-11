#!/usr/bin/env python3
"""Validate the multi-window multi-burn-rate (MWMB) thresholds in
``observability/prometheus-rules/alert_rules.yaml`` against the Google SRE
Workbook spec (``https://sre.google/workbook/alerting-on-slos/``).

The script:

  1. Loads the SLO YAMLs under ``slo/*.yaml`` to recover the per-service
     SLO objective (e.g. 0.999 availability, 0.99 latency p99).
  2. Loads the rule files and parses every ``SLO*BurnTooFast`` alert
     expression to extract the SLI threshold for each (window, slo_kind) pair.
  3. Recomputes the expected MWMB thresholds:

       - Page-tier: 1h short / 6h long, burn-rate factors 14.4x and 6x.
         Consumes 2% / 5% of the 30-day error budget respectively.
       - Ticket-tier: 6h short / 3d long, burn-rate factors 6x and 1x.
         Consumes 5% of the 30-day error budget.

     The SLI threshold implied by a burn-rate factor is

         threshold = 1 - burn_factor * (1 - slo_objective)

  4. Asserts every parsed alert matches the expected threshold within an
     absolute tolerance (1e-6) and confirms the multi-window AND pattern.

Exits non-zero on any mismatch. Output is a human-readable table followed by
a final "all MWMB windows valid" line on success.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("PyYAML required (install via `pip install pyyaml`)", file=sys.stderr)
    raise

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALERTS = REPO_ROOT / "observability" / "prometheus-rules" / "alert_rules.yaml"
DEFAULT_SLO_DIR = REPO_ROOT / "slo"

# Google SRE Workbook burn-rate factors per window pair.
PAGE_FACTORS = {"1h": 14.4, "6h": 6.0}
TICKET_FACTORS = {"6h": 6.0, "3d": 1.0}
ABS_TOL = 1e-6

THRESHOLD_RE = re.compile(
    r'\{__name__="sli:([a-zA-Z0-9_-]+):([a-zA-Z0-9_]+):ratio_(1h|6h|3d)"\}'
    r"\s*<\s*([0-9.]+)"
)


def load_slo_objectives(slo_dir: Path) -> dict[tuple[str, str], float]:
    """Map (service, slo_kind) → objective ratio."""
    objectives: dict[tuple[str, str], float] = {}
    for path in sorted(slo_dir.glob("*.yaml")):
        with path.open() as fh:
            slo = yaml.safe_load(fh)
        for slo_def in slo["slos"]:
            kind = slo_def["name"]
            slo_kind = "availability" if kind in {"availability", "success_rate"} else kind
            objectives[(slo["service"], slo_kind)] = float(slo_def["objective"])
    return objectives


def expected_threshold(objective: float, burn_factor: float) -> float:
    """Convert a burn-rate factor into a SLI threshold."""
    return 1.0 - burn_factor * (1.0 - objective)


def parse_alerts(path: Path) -> list[tuple[str, str, str, dict[str, float]]]:
    """Return ``(alert_name, severity, service, {window: threshold})`` tuples."""
    with path.open() as fh:
        doc = yaml.safe_load(fh)

    parsed: list[tuple[str, str, str, dict[str, float]]] = []
    for group in doc["groups"]:
        for rule in group["rules"]:
            if "alert" not in rule:
                continue
            alert = rule["alert"]
            severity = rule.get("labels", {}).get("severity", "")
            service = rule.get("labels", {}).get("service", "")
            windows = {}
            for match in THRESHOLD_RE.finditer(rule["expr"]):
                _svc, slo_kind, window, value = match.groups()
                windows[window] = float(value)
            parsed.append((alert, severity, service, windows))
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alerts", type=Path, default=DEFAULT_ALERTS,
                        help="Path to alert_rules.yaml")
    parser.add_argument("--slo-dir", type=Path, default=DEFAULT_SLO_DIR,
                        help="Directory containing SLO YAMLs")
    args = parser.parse_args()

    objectives = load_slo_objectives(args.slo_dir)
    parsed = parse_alerts(args.alerts)

    print(f"{'alert':<35} {'service':<25} {'slo_kind':<12} "
          f"{'window':<6} {'actual':>12} {'expected':>12} {'diff':>12} ok")
    print("-" * 110)

    failures: list[str] = []

    for alert, severity, service, windows in parsed:
        if severity == "page" and "BurnTooFast" in alert:
            factors = PAGE_FACTORS
            expected_windows = {"1h", "6h"}
        elif severity == "warning" and "BurnTooFast" in alert:
            factors = TICKET_FACTORS
            expected_windows = {"6h", "3d"}
        else:
            continue  # not an MWMB alert

        slo_kind = "latency_p99" if "Latency" in alert else "availability"
        objective = objectives.get((service, slo_kind))
        if objective is None:
            failures.append(f"{service}/{slo_kind}: no SLO objective found")
            continue

        if set(windows) != expected_windows:
            failures.append(
                f"{alert} ({service}/{slo_kind}): windows {set(windows)} != {expected_windows}"
            )
            continue

        for window, actual in windows.items():
            expected = expected_threshold(objective, factors[window])
            diff = abs(actual - expected)
            ok = diff <= ABS_TOL
            print(f"{alert:<35} {service:<25} {slo_kind:<12} "
                  f"{window:<6} {actual:>12.6f} {expected:>12.6f} {diff:>12.2e} "
                  f"{'OK' if ok else 'FAIL'}")
            if not ok:
                failures.append(
                    f"{alert}/{service}/{slo_kind}/{window}: "
                    f"actual={actual} expected={expected}"
                )

    print("-" * 110)
    if failures:
        print(f"FAIL ({len(failures)} mismatch(es)):")
        for line in failures:
            print(f"  - {line}")
        return 1

    print("all MWMB windows valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
