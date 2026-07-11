#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERIFY="${REPO_ROOT}/scripts/verify.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

mkdir -p "${TMP_DIR}/home" "${TMP_DIR}/bin"
touch "${TMP_DIR}/kubeconfig"
ln -s "$(command -v jq)" "${TMP_DIR}/bin/jq"

cat >"${TMP_DIR}/bin/kubectl" <<'MOCK_KUBECTL'
#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >>"${MOCK_KUBECTL_LOG}"

case " $* " in
    *" port-forward "*)
        printf '%s\n' "$$" >>"${MOCK_KUBECTL_PID_LOG}"
        while :; do sleep 60; done
        ;;
    *" get pods -A --no-headers "*)
        printf '%s\n' \
            'observability otel-collector-a 1/1 Running 0 1m' \
            'observability otel-collector-b 1/1 Running 0 1m'
        ;;
    *" get deployment otel-collector "*)
        case "${MOCK_COLLECTOR_STATE:-valid}" in
            valid) printf '%s\n' '{"spec":{"replicas":2},"status":{"readyReplicas":2}}' ;;
            one_of_two) printf '%s\n' '{"spec":{"replicas":2},"status":{"readyReplicas":1}}' ;;
            two_of_three) printf '%s\n' '{"spec":{"replicas":3},"status":{"readyReplicas":2}}' ;;
            malformed) printf '%s\n' '{"spec":{"replicas":"2"},"status":{"readyReplicas":null}}' ;;
            empty) : ;;
        esac
        ;;
    *" get svc ${MOCK_MISSING_SERVICE:-__none__} "*) exit 1 ;;
    *" get svc prometheus-operated "*) exit 1 ;;
    *" get svc prometheus "*|*" get svc loki "*|*" get svc grafana "*|*" get svc otel-collector "*|*" get svc tempo "*) exit 0 ;;
    *) exit 1 ;;
esac
MOCK_KUBECTL

cat >"${TMP_DIR}/bin/curl" <<'MOCK_CURL'
#!/usr/bin/env bash
set -u

output_file=""
write_code=0
url=""
query=""
while (($#)); do
    case "$1" in
        -o)
            output_file="$2"
            shift 2
            ;;
        -w)
            write_code=1
            shift 2
            ;;
        --data-urlencode)
            query="$2"
            printf 'data-urlencode=%s\n' "$2" >>"${MOCK_CURL_LOG}"
            shift 2
            ;;
        -m|-H|-d|-u)
            shift 2
            ;;
        http://*)
            url="$1"
            shift
            ;;
        *)
            shift
            ;;
    esac
done
printf 'url=%s\n' "${url}" >>"${MOCK_CURL_LOG}"

code=200
body='{}'
case "${url}" in
    */loki/api/v1/push)
        code=204
        body=''
        ;;
    */loki/api/v1/query_range)
        if [[ "${MOCK_FAILURE:-}" == loki ]]; then
            body='{"status":"error","data":{"result":[]}}'
        elif [[ "${query}" =~ ^query=\{job=\"verify-sh-[0-9]+\"\}$ ]]; then
            body='{"status":"success","data":{"resultType":"streams","result":[{"stream":{"job":"verify"},"values":[["1","hello"]]}]}}'
        else
            code=400
            body='{"status":"error","error":"bad query encoding"}'
        fi
        ;;
    */loki/api/v1/query)
        code=400
        body='{"status":"error","error":"log queries require a range endpoint"}'
        ;;
    */api/v1/query)
        if [[ "${MOCK_FAILURE:-}" == prometheus ]]; then
            body='{"status":"success","data":{"result":"not-an-array"}}'
        else
            body='{"status":"success","data":{"resultType":"vector","result":[{"metric":{"__name__":"up"},"value":[1,"1"]}]}}'
        fi
        ;;
    */api/v1/rules)
        if [[ "${MOCK_RULES_INVALID:-0}" == 1 ]]; then
            body='{"status":"success","data":{"groups":"not-an-array"}}'
        else
            body='{"status":"success","data":{"groups":[{"name":"observability","rules":[{"name":"sli:a","type":"recording"},{"name":"A1","type":"alerting"},{"name":"A2","type":"alerting"},{"name":"A3","type":"alerting"},{"name":"A4","type":"alerting"},{"name":"A5","type":"alerting"}]}]}}'
        fi
        ;;
    */api/dashboards/uid/service-overview)
        if [[ -n "${MOCK_GRAFANA_BODY:-}" ]]; then
            body="${MOCK_GRAFANA_BODY}"
        else
            case "${MOCK_FAILURE:-}" in
                grafana)
                    code=404
                    body='{"message":"Dashboard not found"}'
                    ;;
                grafana_wrong_uid)
                    body='{"dashboard":{"uid":"wrong-dashboard"}}'
                    ;;
                grafana_invalid_json)
                    body='not-json'
                    ;;
                *)
                    body='{"dashboard":{"uid":"service-overview"}}'
                    ;;
            esac
        fi
        ;;
    */api/dashboards/uid/svc-overview)
        code=404
        body='{"message":"Dashboard not found"}'
        ;;
    */ready|*/-/ready)
        body='ready'
        ;;
esac

if [[ -n "${output_file}" && "${output_file}" != /dev/null ]]; then
    printf '%s' "${body}" >"${output_file}"
fi
if ((write_code)); then
    printf '%s' "${code}"
else
    printf '%s' "${body}"
fi
MOCK_CURL

chmod +x "${TMP_DIR}/bin/kubectl" "${TMP_DIR}/bin/curl"

failures=0
assert_contains() {
    local file="$1" expected="$2" label="$3"
    if grep -Fq -- "${expected}" "${file}"; then
        printf 'PASS: %s\n' "${label}"
    else
        printf 'FAIL: %s (missing %q in %s)\n' "${label}" "${expected}" "${file}" >&2
        failures=$((failures + 1))
    fi
}

assert_status() {
    local actual="$1" expected="$2" label="$3"
    if [[ "${actual}" == "${expected}" ]]; then
        printf 'PASS: %s\n' "${label}"
    else
        printf 'FAIL: %s (expected status %s, got %s)\n' "${label}" "${expected}" "${actual}" >&2
        failures=$((failures + 1))
    fi
}

run_check() {
    local check="$1" invalid_rules="${2:-0}" mock_failure="${3:-}" grafana_body="${4:-}"
    local collector_state="${5:-valid}" missing_service="${6:-}"
    : >"${TMP_DIR}/kubectl-${check}.log"
    : >"${TMP_DIR}/kubectl-${check}.pids"
    : >"${TMP_DIR}/curl-${check}.log"
    set +e
    HOME="${TMP_DIR}/home" \
        PATH="${TMP_DIR}/bin:/usr/bin:/bin" \
        KUBECONFIG="${TMP_DIR}/kubeconfig" \
        MOCK_KUBECTL_LOG="${TMP_DIR}/kubectl-${check}.log" \
        MOCK_KUBECTL_PID_LOG="${TMP_DIR}/kubectl-${check}.pids" \
        MOCK_CURL_LOG="${TMP_DIR}/curl-${check}.log" \
        MOCK_RULES_INVALID="${invalid_rules}" \
        MOCK_FAILURE="${mock_failure}" \
        MOCK_GRAFANA_BODY="${grafana_body}" \
        MOCK_COLLECTOR_STATE="${collector_state}" \
        MOCK_MISSING_SERVICE="${missing_service}" \
        bash "${VERIFY}" "${check}" >"${TMP_DIR}/check-${check}-${invalid_rules}.out" 2>&1
    RUN_STATUS=$?
    set -e
}

run_signal_check() {
    local signal="$1" round="$2" expected_status="$3" check="${4:-9}" expected_children="${5:-1}"
    local prefix="${TMP_DIR}/signal-${signal}-${round}"
    : >"${prefix}.kubectl.log"
    : >"${prefix}.pids"
    : >"${prefix}.curl.log"

    HOME="${TMP_DIR}/home" \
        PATH="${TMP_DIR}/bin:/usr/bin:/bin" \
        KUBECONFIG="${TMP_DIR}/kubeconfig" \
        MOCK_KUBECTL_LOG="${prefix}.kubectl.log" \
        MOCK_KUBECTL_PID_LOG="${prefix}.pids" \
        MOCK_CURL_LOG="${prefix}.curl.log" \
        VERIFY_UNDER_TEST="${VERIFY}" \
        SIGNAL_UNDER_TEST="${signal}" \
        SIGNAL_PREFIX="${prefix}" \
        SIGNAL_CHECK="${check}" \
        EXPECTED_CHILDREN="${expected_children}" \
        python3 - <<'PY'
import os
import signal
import subprocess
import time

prefix = os.environ["SIGNAL_PREFIX"]
with open(f"{prefix}.out", "wb") as output:
    verifier = subprocess.Popen(
        ["bash", os.environ["VERIFY_UNDER_TEST"], os.environ["SIGNAL_CHECK"]],
        env=os.environ.copy(),
        stdout=output,
        stderr=subprocess.STDOUT,
    )
    child_pids = []
    for _ in range(100):
        try:
            with open(f"{prefix}.pids", encoding="utf-8") as pid_file:
                child_pids = [line.strip() for line in pid_file if line.strip()]
        except FileNotFoundError:
            pass
        if len(child_pids) >= int(os.environ["EXPECTED_CHILDREN"]):
            break
        time.sleep(0.02)
    if len(child_pids) < int(os.environ["EXPECTED_CHILDREN"]):
        verifier.terminate()
        verifier.wait(timeout=5)
        raise SystemExit("expected port-forward children did not start")
    signal_number = getattr(signal, f"SIG{os.environ['SIGNAL_UNDER_TEST']}")
    verifier.send_signal(signal_number)
    status = verifier.wait(timeout=5)
    if status < 0:
        status = 128 - status
    alive_pids = []
    for child_pid in child_pids:
        try:
            os.kill(int(child_pid), 0)
            alive_pids.append(child_pid)
        except ProcessLookupError:
            pass
with open(f"{prefix}.result", "w", encoding="utf-8") as result:
    result.write(f"{status} {len(child_pids)} {','.join(alive_pids) or 'none'}\n")
PY

    local verifier_status child_count alive_pids
    read -r verifier_status child_count alive_pids <"${prefix}.result"
    assert_status "${verifier_status}" "${expected_status}" "${signal} round ${round} returns the signal exit status"
    assert_status "${child_count}" "${expected_children}" "${signal} round ${round} tracks every port-forward child"

    if [[ "${alive_pids}" != none ]]; then
        printf 'FAIL: %s round %s left port-forward children %s alive\n' "${signal}" "${round}" "${alive_pids}" >&2
        failures=$((failures + 1))
        local child_pid
        for child_pid in ${alive_pids//,/ }; do
            kill "${child_pid}" 2>/dev/null || true
        done
    else
        printf 'PASS: %s round %s reaps all %s port-forward child(ren)\n' "${signal}" "${round}" "${child_count}"
    fi
}

run_check 3
assert_status "${RUN_STATUS}" 0 "Collector readiness accepts exactly 2/2 desired and ready replicas"

for collector_state in one_of_two two_of_three malformed empty; do
    run_check 3 0 '' '' "${collector_state}"
    assert_status "${RUN_STATUS}" 1 "Collector readiness fails closed for ${collector_state}"
done

run_check 6 0 '' '' valid loki
assert_status "${RUN_STATUS}" 1 "A deferred selected check makes the verifier exit nonzero"

run_check 6
assert_status "${RUN_STATUS}" 0 "Loki push/range-query check passes"
assert_contains "${TMP_DIR}/curl-6.log" "/loki/api/v1/query_range" "Loki uses the range-query API"
assert_contains "${TMP_DIR}/curl-6.log" 'data-urlencode=query={job="verify-sh-' "Loki sends LogQL through --data-urlencode"

run_check 6 0 loki
assert_status "${RUN_STATUS}" 1 "Loki check fails closed on an API error payload"

run_check 7
assert_status "${RUN_STATUS}" 0 "Prometheus metric check passes with a valid API payload"
assert_contains "${TMP_DIR}/kubectl-7.log" "port-forward svc/prometheus " "Prometheus Service is selected"
assert_contains "${TMP_DIR}/kubectl-7.log" ":80" "Prometheus forwards the rendered Service port 80"

run_check 7 0 prometheus
assert_status "${RUN_STATUS}" 1 "Prometheus metric check fails closed on a malformed result"

run_check 9
assert_status "${RUN_STATUS}" 0 "Grafana dashboard check passes"
assert_contains "${TMP_DIR}/curl-9.log" "/api/dashboards/uid/service-overview" "Grafana uses the provisioned dashboard UID"

run_check 9 0 grafana
assert_status "${RUN_STATUS}" 1 "Grafana dashboard check fails closed on HTTP 404"

run_check 9 0 grafana_wrong_uid
assert_status "${RUN_STATUS}" 1 "Grafana dashboard check fails closed on a wrong UID over HTTP 200"

run_check 9 0 grafana_invalid_json
assert_status "${RUN_STATUS}" 1 "Grafana dashboard check fails closed on invalid JSON over HTTP 200"

grafana_fuzz_bodies=(
    '{}'
    '[]'
    'null'
    '{"dashboard":{}}'
    '{"dashboard":{"uid":7}}'
    '{"dashboard":{"uid":null}}'
    '{"dashboard":{"uid":"service-overview-extra"}}'
)
for grafana_body in "${grafana_fuzz_bodies[@]}"; do
    run_check 9 0 '' "${grafana_body}"
    assert_status "${RUN_STATUS}" 1 "Grafana dashboard fuzz payload fails closed: ${grafana_body}"
done

run_check 10
assert_status "${RUN_STATUS}" 0 "Prometheus rule check accepts the deployed API shape"
assert_contains "${TMP_DIR}/kubectl-10.log" ":80" "Prometheus rule check forwards Service port 80"
assert_contains "${TMP_DIR}/check-10-0.out" "status=success" "Prometheus rule check asserts API status"
assert_contains "${TMP_DIR}/check-10-0.out" "groups=1  rules=6  recordings=1  alerts=5" "Prometheus rule counts are derived from the API payload"

run_check 10 1
assert_status "${RUN_STATUS}" 1 "Prometheus rule check fails closed on a malformed API payload"
assert_contains "${TMP_DIR}/check-10-1.out" "FAIL" "Malformed Prometheus rules are reported as failure"

run_signal_check TERM 1 143 5 2
run_signal_check TERM 2 143 5 2
run_signal_check INT 1 130

if ((failures > 0)); then
    printf '\n%d verify.sh contract test(s) failed.\n' "${failures}" >&2
    exit 1
fi
printf '\nAll verify.sh contract tests passed.\n'
