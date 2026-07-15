#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURE="${ROOT}/load/tests/demo-chaos-thresholds.json"
TMP="$(mktemp -d /tmp/k6-thresholds.XXXXXX)"
trap 'rm -rf "${TMP}"' EXIT

k6 inspect "${ROOT}/load/k6/load.js" >"${TMP}/strict.json"
k6 inspect -e CHAOS_AWARE=true "${ROOT}/load/k6/load.js" >"${TMP}/chaos.json"

jq -e '.thresholds.http_req_duration == ["p(95)<1000"] and .thresholds.http_req_failed == ["rate<0.01"] and .thresholds.checkout_success_rate == ["rate>0.95"]' "${TMP}/strict.json" >/dev/null
jq -e '.thresholds.http_req_duration == ["p(95)<3000"] and .thresholds.http_req_failed == ["rate<0.40"] and .thresholds.checkout_success_rate == ["rate>0.25"]' "${TMP}/chaos.json" >/dev/null
for value in false TRUE 1 yes; do
  k6 inspect -e CHAOS_AWARE="${value}" "${ROOT}/load/k6/load.js" >"${TMP}/strict-${value}.json"
  jq -e '.thresholds.http_req_duration == ["p(95)<1000"] and .thresholds.http_req_failed == ["rate<0.01"] and .thresholds.checkout_success_rate == ["rate>0.95"]' "${TMP}/strict-${value}.json" >/dev/null
done
jq -e '.observed as $o | .profiles.strict as $p | (($o.checkout_success_rate > $p.checkout_success_rate_min) and ($o.http_req_failed < $p.http_req_failed_max) and ($o.http_req_duration_p95_ms < $p.http_req_duration_p95_max_ms)) == false' "${FIXTURE}" >/dev/null
jq -e '.profiles.chaos as $p | all(.chaos_cases[]; (((.success > $p.checkout_success_rate_min) and (.failed < $p.http_req_failed_max) and (.p95_ms < $p.http_req_duration_p95_max_ms)) == .passes))' "${FIXTURE}" >/dev/null
