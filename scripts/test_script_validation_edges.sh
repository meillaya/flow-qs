#!/usr/bin/env bash
set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT
failures=0

if ! python - "$repo_root" <<'PY'
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
  python "$repo_root/scripts/validate_slo.py" "$tmp_dir/$case_name.yaml" >"$output" 2>&1
  status=$?
  if [[ $status -ne 1 ]] \
    || ! grep -q 'window must be a string' "$output" \
    || ! grep -q 'error_budget must be a string' "$output"; then
    cat "$output" >&2
    failures=$((failures + 1))
  fi
done

if [[ $failures -ne 0 ]]; then
  printf 'script validation edge cases failed: %d\n' "$failures" >&2
  exit 1
fi

printf 'script validation edge cases passed\n'
