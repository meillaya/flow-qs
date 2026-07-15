#!/usr/bin/env bash
set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT
failures=0

if ! uv run --with pyyaml python - "$repo_root" <<'PY'
import runpy
import sys
from pathlib import Path

namespace = runpy.run_path(str(Path(sys.argv[1]) / "scripts" / "build_prometheus_rules.py"))
standard_alerts = namespace["standard_alerts"]
malformed_slo = {
    "service": "broken-service",
    "slos": [
        {
            "name": "latency_p99",
            "objective": 0.99,
            "sli": {
                "spec": 'request_duration_seconds_bucket{service="broken-service"}',
                "total_spec": 'request_duration_seconds_count{service="broken-service"}',
            },
        }
    ],
}
try:
    standard_alerts(malformed_slo)
except ValueError as exc:
    if "latency SLI spec must contain a numeric le label" in str(exc):
        raise SystemExit(0)
raise SystemExit("malformed latency SLI spec did not raise the expected ValueError")
PY
then
  failures=$((failures + 1))
fi

if ! uv run --with pyyaml python - "$repo_root" <<'PY'
import runpy
import sys
from pathlib import Path

import yaml

repo_root = Path(sys.argv[1])
namespace = runpy.run_path(str(repo_root / "scripts" / "build_prometheus_rules.py"))

for path in sorted((repo_root / "slo").glob("*.yaml")):
    document = yaml.safe_load(path.read_text())
    for slo in document["slos"]:
        assert slo["sli"]["spec"].startswith("http_server_request_duration_seconds_")
        assert slo["sli"]["total_spec"].startswith(
            "http_server_request_duration_seconds_count"
        )

end_to_end = yaml.safe_load(
    (repo_root / "slo" / "end-to-end-checkout.yaml").read_text()
)
assert end_to_end["metric_service"] == "checkout-service"
recording_rules = namespace["recording_rules_for_service"](end_to_end)
expressions = "\n".join(rule["expr"] for rule in recording_rules)
assert 'service_name="checkout-service"' in expressions
assert 'http_route="/checkout"' in expressions
assert 'service_name="end-to-end-checkout"' not in expressions
assert "checkout_end_to_end_" not in expressions

catalog = yaml.safe_load((repo_root / "slo" / "catalog-service.yaml").read_text())
standard_alerts = namespace["standard_alerts"](catalog)
p99 = next(rule for rule in standard_alerts if rule["alert"] == "HighLatencyP99")
assert "sum by (le)" in p99["expr"]
assert 'service_name="catalog-service"' in p99["expr"]
assert 'le="0.2"' not in p99["expr"]
PY
then
  failures=$((failures + 1))
fi

cat >"$tmp_dir/missing-values.yaml" <<'YAML'
service: broken-service
slos:
  - name: availability
    sli:
      spec: http_requests_total{status!~="5.."}
      total_spec: http_requests_total
    objective: 0.999
    window:
    error_budget:
YAML

cat >"$tmp_dir/malformed-values.yaml" <<'YAML'
service: broken-service
slos:
  - name: availability
    sli:
      spec: http_requests_total{status!~="5.."}
      total_spec: http_requests_total
    objective: 0.999
    window: [30d]
    error_budget: {minutes: 43, seconds: 12}
YAML

for case_name in missing-values malformed-values; do
  output="$tmp_dir/$case_name.out"
  uv run --with pyyaml python "$repo_root/scripts/validate_slo.py" "$tmp_dir/$case_name.yaml" >"$output" 2>&1
  status=$?
  if [[ $status -ne 1 ]] \
    || ! grep -q 'window must be a string' "$output" \
    || ! grep -q 'error_budget must be a string' "$output"; then
    cat "$output" >&2
    failures=$((failures + 1))
  fi
done

cat >"$tmp_dir/invalid-metric-service.yaml" <<'YAML'
service: checkout-slo
metric_service: Checkout Service
slos:
  - name: availability
    sli:
      spec: http_server_request_duration_seconds_count{http_response_status_code!~"5.."}
      total_spec: http_server_request_duration_seconds_count
    objective: 0.99
    window: 30d
    error_budget: 432m0s
YAML

cat >"$tmp_dir/invalid-route.yaml" <<'YAML'
service: checkout-slo
metric_service: checkout-service
slos:
  - name: availability
    sli:
      spec: http_server_request_duration_seconds_count{http_route="checkout",http_response_status_code!~"5.."}
      total_spec: http_server_request_duration_seconds_count{http_route="checkout"}
    objective: 0.99
    window: 30d
    error_budget: 432m0s
YAML

for case_name in invalid-metric-service invalid-route; do
  output="$tmp_dir/$case_name.out"
  uv run --with pyyaml python "$repo_root/scripts/validate_slo.py" "$tmp_dir/$case_name.yaml" >"$output" 2>&1
  status=$?
  expected_field=metric_service
  if [[ "$case_name" == invalid-route ]]; then
    expected_field=http_route
  fi
  if [[ $status -ne 1 ]] || ! grep -q "$expected_field" "$output"; then
    cat "$output" >&2
    failures=$((failures + 1))
  fi
done

contract_output="$tmp_dir/observability-contract.out"
uv run --with pyyaml python "$repo_root/scripts/validate_observability_contract.py" \
  --repo-root "$repo_root" >"$contract_output" 2>&1
status=$?
if [[ $status -ne 0 ]] \
  || ! grep -q '105 recording rules, 48 alert rules, 10 dashboards' "$contract_output"; then
  cat "$contract_output" >&2
  failures=$((failures + 1))
fi

uv run --with pyyaml python - "$repo_root" "$tmp_dir" <<'PY'
import sys
from pathlib import Path

import yaml

repo_root = Path(sys.argv[1])
tmp_dir = Path(sys.argv[2])
source = repo_root / "observability/prometheus-rules/alert_rules.yaml"
document = yaml.safe_load(source.read_text())
for group in document["groups"]:
    group["rules"] = [
        rule for rule in group["rules"] if "BurnTooFast" not in rule.get("alert", "")
    ]
(tmp_dir / "missing-mwmb.yaml").write_text(yaml.safe_dump(document, sort_keys=False))

mismatch = source.read_text().replace(
    '__name__="sli:cart-service:availability:ratio_1h"',
    '__name__="sli:catalog-service:availability:ratio_1h"',
    1,
)
(tmp_dir / "mismatched-mwmb-service.yaml").write_text(mismatch)
PY

for case_name in missing-mwmb mismatched-mwmb-service; do
  output="$tmp_dir/$case_name.out"
  uv run --with pyyaml python "$repo_root/scripts/verify_mwmb.py" \
    --alerts "$tmp_dir/$case_name.yaml" --slo-dir "$repo_root/slo" >"$output" 2>&1
  status=$?
  if [[ $status -ne 1 ]]; then
    cat "$output" >&2
    failures=$((failures + 1))
  fi
done

if [[ $failures -ne 0 ]]; then
  printf 'script validation edge cases failed: %d\n' "$failures" >&2
  exit 1
fi

printf 'script validation edge cases passed\n'
