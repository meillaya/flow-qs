#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d /tmp/k6-summary.XXXXXX)"
DRIVER="$(mktemp "${ROOT}/load/k6/.summary-test.XXXXXX.js")"
trap 'rm -rf "${TMP}"; rm -f "${DRIVER}"' EXIT

DEMO_LIB_ONLY=1 DEMO_REPO_ROOT="${ROOT}" DEMO_LOG_DIR="${TMP}/logs" \
  DEMO_POSTMORTEM_OUT_DIR="${TMP}/postmortems" source "${ROOT}/scripts/demo.sh"

cp "${ROOT}/load/tests/demo-k6-summary.log" "${TMP}/valid.log"
printf '{"stale":true}\n' >"${TMP}/valid.json"
write_k6_summary "${TMP}/valid.log" "${TMP}/valid.json"
jq -e 'has("stale") | not' "${TMP}/valid.json" >/dev/null
jq -e '.schema == "flow-qs.k6.summary/v1" and .profile == "demo-chaos" and .metrics.checkout_success_rate == 0.9475 and .metrics.http_req_failed == 0.0262 and .metrics.http_req_duration_p95_ms == 55' "${TMP}/valid.json" >/dev/null

cat >"${DRIVER}" <<'JS'
const target = require("./load.js");
exports.options = { iterations: 1 };
exports.default = function () {};
exports.handleSummary = function () {
  return target.handleSummary({
    metrics: {
      checkout_success_rate: { values: { rate: 0.9475 } },
      http_req_failed: { values: { rate: 0.0262 } },
      http_req_duration: { values: { "p(95)": 55 } },
      checks: { values: { rate: 0.965 } },
      iterations: { values: { count: 1221 } },
    },
  });
};
JS
k6 run --quiet -e CHAOS_AWARE=true "${DRIVER}" >"${TMP}/real-k6.log"
printf '{"stale":true}\n' >"${TMP}/real-k6.json"
write_k6_summary "${TMP}/real-k6.log" "${TMP}/real-k6.json"
jq -e 'has("stale") | not' "${TMP}/real-k6.json" >/dev/null
jq -e '.profile == "demo-chaos" and .metrics.iterations == 1221' "${TMP}/real-k6.json" >/dev/null
[[ "$(yq -c 'select(.kind == "Job") | .spec.template.spec.containers[0].args' "${ROOT}/load/k6-job-demo.yaml")" == '["run","/scripts/load.js"]' ]]
[[ "$(yq -c 'select(.kind == "Job") | .spec.template.spec.containers[0].args' "${ROOT}/load/k6-job-load.yaml")" == '["run","--out","json=/tmp/results.json","/scripts/load.js"]' ]]
if grep -q '/tmp/results.json' "${ROOT}/scripts/demo.sh"; then exit 1; fi

expect_rejected_without_stale_output() {
  local log_path="$1" output_path="$2"
  printf '{"stale":true}\n' >"${output_path}"
  if write_k6_summary "${log_path}" "${output_path}" 2>/dev/null; then
    return 1
  fi
  [[ ! -e "${output_path}" ]]
}

printf 'no marker\n' >"${TMP}/missing.log"
expect_rejected_without_stale_output "${TMP}/missing.log" "${TMP}/missing.json"
printf 'FLOW_QS_K6_SUMMARY={bad json}\n' >"${TMP}/malformed.log"
expect_rejected_without_stale_output "${TMP}/malformed.log" "${TMP}/malformed.json"
sed 's/"demo-chaos"/"standalone-load"/' "${ROOT}/load/tests/demo-k6-summary.log" >"${TMP}/wrong-profile.log"
expect_rejected_without_stale_output "${TMP}/wrong-profile.log" "${TMP}/wrong-profile.json"
cat "${ROOT}/load/tests/demo-k6-summary.log" "${ROOT}/load/tests/demo-k6-summary.log" >"${TMP}/duplicate.log"
expect_rejected_without_stale_output "${TMP}/duplicate.log" "${TMP}/duplicate.json"
sed 's/0.9475/1.1/' "${ROOT}/load/tests/demo-k6-summary.log" >"${TMP}/range.log"
expect_rejected_without_stale_output "${TMP}/range.log" "${TMP}/range.json"

: >"${TMP}/kubectl-calls"
kubectl() {
  printf '%s\n' "$*" >>"${TMP}/kubectl-calls"
  if [[ "${1:-}" == "logs" ]]; then
    cat "${ROOT}/load/tests/demo-k6-summary.log"
    return 0
  fi
  return 99
}
printf '{"stale":true}\n' >"${TMP}/mock.json"
collect_k6_summary "completed-k6-pod" "${TMP}/mock.log" "${TMP}/mock.json"
jq -e 'has("stale") | not' "${TMP}/mock.json" >/dev/null
jq -e '.profile == "demo-chaos" and .metrics.iterations == 1221' "${TMP}/mock.json" >/dev/null
grep -q '^logs completed-k6-pod -n load$' "${TMP}/kubectl-calls"
if grep -q '^cp ' "${TMP}/kubectl-calls"; then exit 1; fi
if find "${TMP}" -maxdepth 1 -name '.*.tmp' -print -quit | grep -q .; then exit 1; fi

run_log="${TMP}/logs/task-24-k6-job-${RUN_ID}.log"
run_results="${TMP}/logs/task-24-k6-results-${RUN_ID}.json"
mkdir -p "${TMP}/logs"
printf 'stale summary\n' >"${SUMMARY_FILE}"
printf 'stale current log\n' >"${run_log}"
printf '{"stale":true}\n' >"${run_results}"
printf 'stale legacy log\n' >"${TMP}/logs/task-24-k6-job.log"
printf '{"stale":true}\n' >"${TMP}/logs/task-24-k6-results.json"
initialize_run_artifacts "${run_log}" "${run_results}"

wait_for_k6() { return 1; }
kubectl() {
  if [[ "${1:-}" == "logs" ]]; then
    printf 'current run failure log\n'
    return 0
  fi
  return 99
}
if (require_k6_success "failed-k6-pod" "${run_log}" "${run_results}" >/dev/null 2>&1); then
  exit 1
fi
grep -qx 'current run failure log' "${run_log}"
[[ ! -e "${run_results}" ]]
[[ ! -e "${SUMMARY_FILE}" ]]
[[ ! -e "${TMP}/logs/task-24-k6-job.log" ]]
[[ ! -e "${TMP}/logs/task-24-k6-results.json" ]]
