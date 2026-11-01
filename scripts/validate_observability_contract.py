# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# ─── How to run ───
# uv run scripts/validate_observability_contract.py

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

SERVICES = {
    "catalog-service": "0.9995",
    "inventory-service": "0.9995",
    "cart-service": "0.999",
    "checkout-service": "0.999",
    "order-service": "0.999",
    "payment-gateway": "0.995",
    "end-to-end-checkout": "0.99",
}
WINDOWS = ("5m", "30m", "1h", "6h", "1d", "3d", "30d")
BURN_WINDOWS = ("5m", "1h", "6h", "1d")
DASHBOARD_FILES = {
    "01-service-overview.json",
    "02-red-cart.json",
    "02-red-catalog.json",
    "02-red-checkout.json",
    "02-red-inventory.json",
    "02-red-order.json",
    "02-red-payment.json",
    "03-trace-explorer.json",
    "04-log-explorer.json",
    "05-slo-status.json",
}
PROMETHEUS_UID = "prometheus-observability"


def load_mapping(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: document must be an object")
    return {str(key): item for key, item in value.items()}


def collect_panels(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    panels: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        panel = {str(key): child for key, child in item.items()}
        panels.append(panel)
        panels.extend(collect_panels(panel.get("panels")))
    return panels


def datasource_type(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    data = {str(key): item for key, item in value.items()}
    kind = data.get("type")
    return kind if isinstance(kind, str) else ""


def datasource_uid(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    data = {str(key): item for key, item in value.items()}
    uid = data.get("uid")
    return uid if isinstance(uid, str) else ""


def collect_queries(document: dict[str, object]) -> list[tuple[str, str, str, str]]:
    queries: list[tuple[str, str, str, str]] = []
    for panel in collect_panels(document.get("panels")):
        title_value = panel.get("title")
        title = title_value if isinstance(title_value, str) else "<untitled>"
        panel_datasource = panel.get("datasource")
        targets = panel.get("targets")
        if not isinstance(targets, list):
            continue
        for target_value in targets:
            if not isinstance(target_value, dict):
                continue
            target = {str(key): item for key, item in target_value.items()}
            query_value = target.get("expr", target.get("query"))
            if not isinstance(query_value, str):
                continue
            target_datasource = target.get("datasource", panel_datasource)
            queries.append(
                (
                    title,
                    query_value,
                    datasource_type(target_datasource),
                    datasource_uid(target_datasource),
                )
            )
    return queries


def collect_variable_queries(document: dict[str, object]) -> list[str]:
    templating = document.get("templating")
    if not isinstance(templating, dict):
        return []
    data = {str(key): item for key, item in templating.items()}
    variables = data.get("list")
    if not isinstance(variables, list):
        return []
    queries: list[str] = []
    for variable_value in variables:
        if not isinstance(variable_value, dict):
            continue
        variable = {str(key): item for key, item in variable_value.items()}
        query = variable.get("query")
        if isinstance(query, str):
            queries.append(query)
    return queries


def validate_rules(repo_root: Path) -> list[str]:
    errors: list[str] = []
    rule_dir = repo_root / "observability/prometheus-rules"
    recording_text = (rule_dir / "sli_recording_rules.yaml").read_text(encoding="utf-8")
    alert_text = (rule_dir / "alert_rules.yaml").read_text(encoding="utf-8")
    record_names = re.findall(r"^\s*- record: (\S+)$", recording_text, re.MULTILINE)
    records = set(record_names)
    expected_records = {
        f"sli:{service}:{kind}:ratio_{window}"
        for service in SERVICES
        for kind in ("availability", "latency_p99")
        for window in WINDOWS
    }
    expected_records.update(
        f"slo:{service}:availability:error_budget_remaining:30d"
        for service in SERVICES
    )
    expected_records.add("synthetic_check_success")
    missing_records = expected_records - records
    extra_records = records - expected_records
    if missing_records:
        errors.append(f"recording rules missing {sorted(missing_records)}")
    if extra_records:
        errors.append(f"recording rules contain unexpected {sorted(extra_records)}")
    if len(record_names) != len(expected_records):
        errors.append(
            f"expected {len(expected_records)} recording rules, found {len(record_names)}"
        )
    duplicate_records = sorted(
        name for name, count in Counter(record_names).items() if count != 1
    )
    if duplicate_records:
        errors.append(f"duplicate recording rules {duplicate_records}")
    alert_names = re.findall(r"^\s*- alert: (\S+)$", alert_text, re.MULTILINE)
    expected_alert_names = Counter(
        {
            "ServiceDown": 6,
            "HighErrorRate": 7,
            "HighLatencyP99": 7,
            "SLOAvailabilityBurnTooFast": 14,
            "SLOLatencyBurnTooFast": 14,
        }
    )
    if Counter(alert_names) != expected_alert_names:
        errors.append(
            f"alert rule inventory mismatch: {dict(sorted(Counter(alert_names).items()))}"
        )
    for token in (
        "http_requests_total",
        "http_server_requests_total",
        "http_server_requests_duration_seconds",
        "checkout_end_to_end_",
    ):
        if token in recording_text or token in alert_text:
            errors.append(f"rules use stale metric family {token}")
    e2e_group = recording_text.split(
        "name: sli_recording_rules_end_to_end_checkout", 1
    )[-1].split("\n  - name:", 1)[0]
    if 'service_name="checkout-service"' not in e2e_group:
        errors.append("end-to-end rules do not select checkout-service")
    if 'http_route="/checkout"' not in e2e_group:
        errors.append("end-to-end rules do not select /checkout")
    p99_blocks = re.findall(
        r"- alert: HighLatencyP99\n(?P<body>.*?)(?=\n\s+- alert:|\n\s+- name:|\Z)",
        alert_text,
        re.DOTALL,
    )
    if len(p99_blocks) != 7:
        errors.append(f"expected 7 HighLatencyP99 rules, found {len(p99_blocks)}")
    for index, block in enumerate(p99_blocks):
        expression = block.split("for:", 1)[0]
        if 'le="' in expression:
            errors.append(f"HighLatencyP99 rule {index + 1} filters histogram buckets")
    return errors


def validate_dashboards(repo_root: Path) -> list[str]:
    errors: list[str] = []
    paths = sorted((repo_root / "dashboards").glob("*.json"))
    actual_names = {path.name for path in paths}
    if actual_names != DASHBOARD_FILES:
        errors.append(
            f"dashboard file set mismatch: missing={sorted(DASHBOARD_FILES - actual_names)} "
            f"extra={sorted(actual_names - DASHBOARD_FILES)}"
        )
    documents: dict[str, dict[str, object]] = {}
    for path in paths:
        try:
            documents[path.name] = load_mapping(path)
        except (ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
    all_queries = [
        (name, *query)
        for name, document in documents.items()
        for query in collect_queries(document)
    ]
    all_variable_queries = [
        (name, query)
        for name, document in documents.items()
        for query in collect_variable_queries(document)
    ]
    stale_patterns = {
        "unsupported HTTP request counter": re.compile(r"http_requests_total"),
        "http_server_requests_total": re.compile(r"http_server_requests_total"),
        "plural duration family": re.compile(r"http_server_requests_duration_seconds"),
        "namespace label": re.compile(r'(?<!service_)namespace="ecom"'),
        "service label": re.compile(r'(?<!_)service(?:=|=~)"'),
        "payment-service identity": re.compile(r"payment-service"),
        "masked no-data": re.compile(r"or\s+vector\(0\)"),
        "nonexistent burn record": re.compile(r"slo:availability:burn_rate_"),
        "nonexistent 24h window": re.compile(r"ratio_24h|burn_rate_24h"),
    }
    for file_name, title, query, _kind, _uid in all_queries:
        for label, pattern in stale_patterns.items():
            if pattern.search(query):
                errors.append(f"{file_name}/{title}: {label}: {query}")
    for file_name, query in all_variable_queries:
        for label, pattern in stale_patterns.items():
            if pattern.search(query):
                errors.append(f"{file_name}/variable: {label}: {query}")
    variable_contract = (
        'label_values(http_server_request_duration_seconds_count{service_namespace="ecom"}, '
        "service_name)"
    )
    for file_name in {
        "02-red-cart.json",
        "02-red-catalog.json",
        "02-red-checkout.json",
        "02-red-inventory.json",
        "02-red-order.json",
        "03-trace-explorer.json",
        "04-log-explorer.json",
    }:
        if variable_contract not in collect_variable_queries(documents.get(file_name, {})):
            errors.append(f"{file_name}: canonical service variable missing")
    payment_variable = (
        'label_values(http_server_request_duration_seconds_count{service_name="payment-gateway"}, '
        "service_name)"
    )
    if payment_variable not in collect_variable_queries(
        documents.get("02-red-payment.json", {})
    ):
        errors.append("02-red-payment.json: direct-scrape service variable missing")
    overview_queries = {query for _title, query, _kind, _uid in collect_queries(
        documents.get("01-service-overview.json", {})
    )}
    overview_health_query = (
        'max by (service) (synthetic_check_success{service_namespace="ecom"})'
    )
    if overview_health_query not in overview_queries:
        errors.append("service overview does not expose readable per-service health")
    overview_rate_query = (
        'sum by (service_name) (rate(http_server_request_duration_seconds_count'
        '{service_namespace="ecom"}[5m])) or sum by (service_name) '
        '(rate(http_server_request_duration_seconds_count'
        '{service_name="payment-gateway"}[5m]))'
    )
    if overview_rate_query not in overview_queries:
        errors.append("service overview request rate omits direct-scrape payment traffic")
    red_services = {
        "02-red-cart.json": "cart-service",
        "02-red-catalog.json": "catalog-service",
        "02-red-checkout.json": "checkout-service",
        "02-red-inventory.json": "inventory-service",
        "02-red-order.json": "order-service",
        "02-red-payment.json": "payment-gateway",
    }
    for file_name, service in red_services.items():
        prom_queries = [
            query
            for _title, query, kind, uid in collect_queries(documents.get(file_name, {}))
            if kind == "prometheus" and uid == PROMETHEUS_UID
        ]
        if len(prom_queries) != 11:
            errors.append(f"{file_name}: expected 11 Prometheus targets, found {len(prom_queries)}")
        for query in prom_queries:
            if not re.search(
                r"http_server_request_duration_seconds_(?:count|bucket)", query
            ):
                errors.append(f"{file_name}: RED query omits canonical histogram: {query}")
            if f'service_name="{service}"' not in query:
                errors.append(f"{file_name}: query omits canonical service identity: {query}")
            if service == "payment-gateway" and 'service_namespace="ecom"' in query:
                errors.append(f"{file_name}: direct-scrape query wrongly requires namespace: {query}")
            if service != "payment-gateway" and 'service_namespace="ecom"' not in query:
                errors.append(f"{file_name}: query omits service_namespace: {query}")
    slo_queries = collect_queries(documents.get("05-slo-status.json", {}))
    for service, objective in SERVICES.items():
        budget = f'{{__name__="slo:{service}:availability:error_budget_remaining:30d"}}'
        budget_count = sum(1 for _title, query, _kind, _uid in slo_queries if query == budget)
        if budget_count != 1:
            errors.append(f"SLO dashboard expected one budget {budget}, found {budget_count}")
        for window in BURN_WINDOWS:
            burn = (
                f'(1 - {{__name__="sli:{service}:availability:ratio_{window}"}}) / '
                f"(1 - {objective})"
            )
            burn_count = sum(1 for _title, query, _kind, _uid in slo_queries if query == burn)
            if burn_count != 1:
                errors.append(f"SLO dashboard expected one burn query {burn}, found {burn_count}")
    budget_panels = [
        panel
        for panel in collect_panels(documents.get("05-slo-status.json", {}).get("panels"))
        if any(
            query.startswith('{__name__="slo:')
            and query.endswith(':availability:error_budget_remaining:30d"}')
            for _title, query, _kind, _uid in collect_queries({"panels": [panel]})
        )
    ]
    if len(budget_panels) != 7:
        errors.append(f"SLO dashboard expected 7 budget panels, found {len(budget_panels)}")
    for panel in budget_panels:
        serialized = json.dumps(panel, sort_keys=True)
        title = panel.get("title")
        if not isinstance(title, str) or title not in SERVICES:
            errors.append(f"{title}: budget panel title must preserve its service identity")
        if '"unit": "percent"' not in serialized or '"max": 100' not in serialized:
            errors.append(f"{panel['title']}: budget must use a 0-100 percent scale")
        if '"value": 25' not in serialized or '"value": 50' not in serialized:
            errors.append(f"{panel['title']}: budget thresholds must be 25 and 50")
    alert_contract = 'ALERTS{alertstate="firing",severity=~"page|warning"}'
    alert_targets = [
        (kind, uid)
        for _title, query, kind, uid in slo_queries
        if query == alert_contract
    ]
    if alert_targets != [("prometheus", PROMETHEUS_UID)]:
        errors.append("SLO alerts target must use Prometheus with page|warning")
    log_queries = {query for _title, query, _kind, _uid in collect_queries(
        documents.get("04-log-explorer.json", {})
    )}
    if '{service_namespace="ecom", service_name=~"$service"} |= "$query"' not in log_queries:
        errors.append("Log Explorer does not use canonical OTLP stream labels")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    args = parser.parse_args()
    errors = validate_rules(args.repo_root) + validate_dashboards(args.repo_root)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: 106 recording rules, 48 alert rules, 10 dashboards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
