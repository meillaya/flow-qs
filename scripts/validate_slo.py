#!/usr/bin/env python3
"""Validate SLO YAML files against the project schema and error-budget math."""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "PyYAML is required. Run: uv run --with pyyaml python scripts/validate_slo.py ..."
    ) from exc

WINDOW_RE = re.compile(r"^(\d+)d$")
BUDGET_RE = re.compile(r"^(\d+)m(\d+)s$")
SERVICE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
ROUTE_RE = re.compile(r'http_route="([^"]+)"')
LATENCY_THRESHOLD_RE = re.compile(r'le="([0-9]+(?:\.[0-9]+)?)"')

REQUIRED_TOP_KEYS = {"service", "slos"}
ALLOWED_TOP_KEYS = REQUIRED_TOP_KEYS | {"metric_service"}
REQUIRED_SLO_KEYS = {"name", "sli", "objective", "window", "error_budget"}
REQUIRED_SLI_KEYS = {"spec", "total_spec"}


def _error(path: Path, message: str) -> str:
    return f"{path}: {message}"


def _parse_window(value: str) -> int:
    match = WINDOW_RE.match(value)
    if not match:
        raise ValueError(f"window must match '<days>d', got {value!r}")
    return int(match.group(1)) * 86400


def _parse_budget(value: str) -> int:
    match = BUDGET_RE.match(value)
    if not match:
        raise ValueError(f"error_budget must match '<minutes>m<seconds>s', got {value!r}")
    return int(match.group(1)) * 60 + int(match.group(2))


def _validate_metric_contract(
    path: Path,
    prefix: str,
    name: object,
    spec: str,
    total_spec: str,
    route_required: bool,
) -> list[str]:
    errors: list[str] = []
    routes: list[str] = []
    for field, value in (("spec", spec), ("total_spec", total_spec)):
        route_match = ROUTE_RE.search(value)
        if route_match is not None:
            route = route_match.group(1)
            routes.append(route)
            if not route.startswith("/"):
                errors.append(
                    _error(path, f"{prefix}.sli.{field} http_route must start with '/'")
                )
        elif route_required:
            errors.append(
                _error(
                    path,
                    f"{prefix}.sli.{field} must select http_route when metric_service "
                    "differs from service",
                )
            )

    if len(routes) == 2 and routes[0] != routes[1]:
        errors.append(_error(path, f"{prefix}.sli http_route selectors must match"))

    if name in {"availability", "success_rate"}:
        if not spec.startswith("http_server_request_duration_seconds_count{"):
            errors.append(
                _error(path, f"{prefix}.sli.spec must use the canonical count metric")
            )
        if not total_spec.startswith("http_server_request_duration_seconds_count"):
            errors.append(
                _error(path, f"{prefix}.sli.total_spec must use the canonical count metric")
            )
        if 'http_response_status_code!~"5.."' not in spec:
            errors.append(
                _error(
                    path,
                    f"{prefix}.sli.spec must select non-5xx http_response_status_code",
                )
            )
    elif name == "latency_p99":
        if not spec.startswith("http_server_request_duration_seconds_bucket{"):
            errors.append(
                _error(path, f"{prefix}.sli.spec must use the canonical bucket metric")
            )
        if LATENCY_THRESHOLD_RE.search(spec) is None:
            errors.append(_error(path, f"{prefix}.sli.spec must contain a numeric le label"))
        if not total_spec.startswith("http_server_request_duration_seconds_count"):
            errors.append(
                _error(path, f"{prefix}.sli.total_spec must use the canonical count metric")
            )
    elif isinstance(name, str):
        errors.append(_error(path, f"{prefix}.name {name!r} is not supported"))
    return errors


def _validate_file(path: Path) -> list[str]:
    errors: list[str] = []

    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        return [_error(path, f"invalid YAML: {exc}")]

    if not isinstance(data, dict):
        return [_error(path, "document must be a mapping")]

    missing_top = REQUIRED_TOP_KEYS - data.keys()
    if missing_top:
        errors.append(_error(path, f"missing top-level keys: {sorted(missing_top)}"))

    unknown_top = data.keys() - ALLOWED_TOP_KEYS
    if unknown_top:
        errors.append(_error(path, f"unknown top-level keys: {sorted(unknown_top)}"))

    service = data.get("service")
    if service is not None and not isinstance(service, str):
        errors.append(_error(path, "'service' must be a string"))
    elif isinstance(service, str) and SERVICE_RE.fullmatch(service) is None:
        errors.append(_error(path, "'service' must be a lowercase DNS-style name"))

    metric_service = data.get("metric_service", service)
    if metric_service is not None and not isinstance(metric_service, str):
        errors.append(_error(path, "'metric_service' must be a string"))
    elif (
        isinstance(metric_service, str)
        and SERVICE_RE.fullmatch(metric_service) is None
    ):
        errors.append(
            _error(path, "'metric_service' must be a lowercase DNS-style name")
        )

    slos = data.get("slos")
    if slos is None:
        return errors
    if not isinstance(slos, list):
        errors.append(_error(path, "'slos' must be a list"))
        return errors

    for idx, slo in enumerate(slos):
        prefix = f"slos[{idx}]"
        if not isinstance(slo, dict):
            errors.append(_error(path, f"{prefix} must be a mapping"))
            continue

        missing_slo = REQUIRED_SLO_KEYS - slo.keys()
        if missing_slo:
            errors.append(_error(path, f"{prefix} missing keys: {sorted(missing_slo)}"))

        name = slo.get("name")
        if name is not None and not isinstance(name, str):
            errors.append(_error(path, f"{prefix}.name must be a string"))

        sli = slo.get("sli")
        if sli is not None:
            if not isinstance(sli, dict):
                errors.append(_error(path, f"{prefix}.sli must be a mapping"))
            else:
                missing_sli = REQUIRED_SLI_KEYS - sli.keys()
                if missing_sli:
                    errors.append(
                        _error(path, f"{prefix}.sli missing keys: {sorted(missing_sli)}")
                    )
                for key in REQUIRED_SLI_KEYS:
                    value = sli.get(key)
                    if value is not None and not isinstance(value, str):
                        errors.append(
                            _error(path, f"{prefix}.sli.{key} must be a string")
                        )
                spec = sli.get("spec")
                total_spec = sli.get("total_spec")
                if isinstance(spec, str) and isinstance(total_spec, str):
                    errors.extend(
                        _validate_metric_contract(
                            path,
                            prefix,
                            name,
                            spec,
                            total_spec,
                            isinstance(metric_service, str)
                            and isinstance(service, str)
                            and metric_service != service,
                        )
                    )

        objective = slo.get("objective")
        if objective is not None:
            if not isinstance(objective, (int, float)):
                errors.append(_error(path, f"{prefix}.objective must be a number"))
            elif not 0 < objective <= 1:
                errors.append(
                    _error(path, f"{prefix}.objective must be in (0, 1], got {objective}")
                )

        window = slo.get("window")
        window_seconds: int | None = None
        if "window" in slo and not isinstance(window, str):
            errors.append(_error(path, f"{prefix}.window must be a string"))
        elif isinstance(window, str):
            try:
                window_seconds = _parse_window(window)
            except ValueError as exc:
                errors.append(_error(path, f"{prefix}.window {exc}"))

        error_budget = slo.get("error_budget")
        budget_seconds: int | None = None
        if "error_budget" in slo and not isinstance(error_budget, str):
            errors.append(_error(path, f"{prefix}.error_budget must be a string"))
        elif isinstance(error_budget, str):
            try:
                budget_seconds = _parse_budget(error_budget)
            except ValueError as exc:
                errors.append(_error(path, f"{prefix}.error_budget {exc}"))

        if (
            objective is not None
            and isinstance(objective, (int, float))
            and window_seconds is not None
            and budget_seconds is not None
        ):
            expected = round((1 - float(objective)) * window_seconds)
            if budget_seconds != expected:
                errors.append(
                    _error(
                        path,
                        f"{prefix}.error_budget {error_budget!r} does not match "
                        f"(1 - {objective}) * {window} = "
                        f"{expected // 60}m{expected % 60}s",
                    )
                )

    return errors


def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <slo-file>...", file=sys.stderr)
        return 2

    all_errors: list[str] = []
    passed = 0
    failed = 0

    for arg in sys.argv[1:]:
        path = Path(arg)
        if not path.is_file():
            all_errors.append(f"{path}: not a file")
            failed += 1
            continue

        errors = _validate_file(path)
        if errors:
            all_errors.extend(errors)
            failed += 1
            print(f"FAIL {path}")
        else:
            passed += 1
            print(f"PASS {path}")

    print(f"\n{passed} passed, {failed} failed")
    if all_errors:
        print("\nErrors:")
        for error in all_errors:
            print(f"  - {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
