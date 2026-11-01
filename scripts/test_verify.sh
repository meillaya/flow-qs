#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERIFY="${REPO_ROOT}/scripts/verify.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

mkdir -p "${TMP_DIR}/home" "${TMP_DIR}/bin"
touch "${TMP_DIR}/kubeconfig"
ln -s "$(command -v jq)" "${TMP_DIR}/bin/jq"

# verify.sh needs Bash 4 for associative arrays. macOS ships Bash 3.2 at
# /bin/bash, so keep the interpreter that runs this test first on PATH.
BASH_BIN="$(command -v bash)"
TOOL_BIN="$(dirname "${BASH_BIN}")"

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
        case "${MOCK_POD_STATE:-valid}" in
            valid)
                printf '%s\n' \
                    'observability otel-collector-a 1/1 Running 0 1m' \
                    'observability otel-collector-b 1/1 Running 0 1m'
                ;;
            one_of_three)
                printf '%s\n' \
                    'observability pod-a 1/1 Running 0 1m' \
                    'observability pod-b 0/1 Running 0 1m' \
                    'observability pod-c 0/1 Pending 0 1m'
                ;;
            two_of_three)
                printf '%s\n' \
                    'observability pod-a 1/1 Running 0 1m' \
                    'observability pod-b 1/1 Running 0 1m' \
                    'observability pod-c 0/1 Running 0 1m'
                ;;
            malformed)
                printf '%s\n' \
                    'observability pod-a 1/1 Running 0 1m' \
                    'observability broken-row' \
                    'ecom unrelated 1/1 Running 0 1m'
                ;;
        esac
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
    *" get daemonset otel-collector-agent "*)
        case "${MOCK_AGENT_STATE:-valid}" in
            valid) printf '%s\n' '{"status":{"desiredNumberScheduled":2,"numberReady":2}}' ;;
            one_of_two) printf '%s\n' '{"status":{"desiredNumberScheduled":2,"numberReady":1}}' ;;
            malformed) printf '%s\n' '{"status":{"desiredNumberScheduled":"2","numberReady":null}}' ;;
        esac
        ;;
    *" get deployments -A -o json "*)
        case "${MOCK_IMAGE_STATE:-valid}" in
            valid) printf '%s\n' '{"items":[{"metadata":{"namespace":"ecom","name":"cart-service"},"spec":{"template":{"spec":{"containers":[{"image":"registry/cart-service:git-0123456789abcdef0123456789abcdef01234567"}]}}}},{"metadata":{"namespace":"ecom","name":"catalog-service"},"spec":{"template":{"spec":{"containers":[{"image":"registry/catalog-service:git-0123456789abcdef0123456789abcdef01234567"}]}}}},{"metadata":{"namespace":"ecom","name":"checkout-service"},"spec":{"template":{"spec":{"containers":[{"image":"registry/checkout-service:git-0123456789abcdef0123456789abcdef01234567"}]}}}},{"metadata":{"namespace":"ecom","name":"inventory-service"},"spec":{"template":{"spec":{"containers":[{"image":"registry/inventory-service:git-0123456789abcdef0123456789abcdef01234567"}]}}}},{"metadata":{"namespace":"ecom","name":"order-service"},"spec":{"template":{"spec":{"containers":[{"image":"registry/order-service:git-0123456789abcdef0123456789abcdef01234567"}]}}}},{"metadata":{"namespace":"ecom","name":"payment-gateway"},"spec":{"template":{"spec":{"containers":[{"image":"registry/payment-gateway:git-0123456789abcdef0123456789abcdef01234567"}]}}}},{"metadata":{"namespace":"ecom","name":"synthetic-monitor"},"spec":{"template":{"spec":{"containers":[{"image":"registry/synthetic-monitor:git-0123456789abcdef0123456789abcdef01234567"}]}}}},{"metadata":{"namespace":"incident","name":"incident-engine"},"spec":{"template":{"spec":{"containers":[{"image":"registry/incident-engine:git-0123456789abcdef0123456789abcdef01234567"}]}}}}]}' ;;
            mutable) printf '%s\n' '{"items":[{"metadata":{"namespace":"ecom","name":"cart-service"},"spec":{"template":{"spec":{"containers":[{"image":"registry/cart-service:dev"}]}}}}]}' ;;
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
            if [[ "$2" == query=* ]]; then
                query="$2"
            fi
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
    */api/traces/*)
        trace_id="${url##*/}"
        if [[ "${MOCK_FAILURE:-}" == tempo_empty ]]; then
            body='{"batches":[]}'
        else
            trace_id_b64=$(printf '%s' "${trace_id}" | xxd -r -p | base64 | tr -d '\n')
            body="{\"batches\":[{\"scopeSpans\":[{\"spans\":[{\"traceId\":\"${trace_id_b64}\"}]}]}]}"
        fi
        ;;
    */loki/api/v1/push)
        code=204
        body=''
        ;;
    */loki/api/v1/query_range)
        if [[ "${MOCK_FAILURE:-}" == loki ]]; then
            body='{"status":"error","data":{"result":[]}}'
        elif [[ "${query}" =~ service_name=\"([^\"]+)\" ]]; then
            service_name="${BASH_REMATCH[1]}"
            log_line='log'
            if [[ "${service_name}" == payment-gateway && "${MOCK_FAILURE:-}" != payment_stale ]]; then
                if [[ "${MOCK_FAILURE:-}" == payment_delayed && "${write_code}" == 0 ]]; then
                    delay_counter="${MOCK_STATE_DIR}/payment-delay-counter"
                    delay_count=$(cat "${delay_counter}" 2>/dev/null || printf '0')
                    printf '%s\n' "$((delay_count + 1))" >"${delay_counter}"
                    if [[ "${delay_count}" -eq 0 ]]; then
                        body='{"status":"success","data":{"resultType":"streams","result":[]}}'
                        if [[ -n "${output_file}" && "${output_file}" != /dev/null ]]; then
                            printf '%s' "${body}" >"${output_file}"
                        fi
                        if ((write_code)); then printf '%s' "${code}"; else printf '%s' "${body}"; fi
                        exit 0
                    fi
                fi
                log_line='time=now level=INFO msg=http_request path=/__verify_log__ status=404'
            fi
            body="{\"status\":\"success\",\"data\":{\"resultType\":\"streams\",\"result\":[{\"stream\":{\"service_name\":\"${service_name}\"},\"values\":[[\"1\",\"${log_line}\"]]}]}}"
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
        elif [[ "${query}" == *http_server_request_duration_seconds_count* ]]; then
            body='{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1,"6"]}]}}'
        elif [[ "${query}" == *'sli:.*:availability:ratio_5m'* ]]; then
            body='{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1,"7"]}]}}'
        elif [[ "${query}" == *'up{job="payment-gateway"}'* ]]; then
            body='{"status":"success","data":{"resultType":"vector","result":[{"metric":{"job":"payment-gateway"},"value":[1,"1"]}]}}'
        elif [[ "${query}" == *traces_service_graph_request_total* ]]; then
            body='{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1,"207"]}]}}'
        else
            body='{"status":"success","data":{"resultType":"vector","result":[{"metric":{"__name__":"up"},"value":[1,"1"]}]}}'
        fi
        ;;
    */api/datasources)
        if [[ "${MOCK_FAILURE:-}" == grafana_datasources ]]; then
            body='[{"name":"Prometheus"},{"name":"Tempo"},{"name":"Loki"}]'
        else
            body='[{"name":"Prometheus"},{"name":"Tempo"},{"name":"Loki"},{"name":"Alertmanager"}]'
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
    */api/dashboards/uid/*)
        uid="${url##*/}"
        body="{\"dashboard\":{\"uid\":\"${uid}\"}}"
        ;;
    */__verify_log__)
        code=404
        body='404 page not found'
        ;;
    */ready|*/-/ready)
        if [[ "${MOCK_FAILURE:-}" == tempo_ready ]]; then
            code=503
            body='not ready'
        else
            body='ready'
        fi
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
    local collector_state="${5:-valid}" missing_service="${6:-}" pod_state="${7:-valid}"
    local image_state="${8:-valid}"
    : >"${TMP_DIR}/kubectl-${check}.log"
    : >"${TMP_DIR}/kubectl-${check}.pids"
    : >"${TMP_DIR}/curl-${check}.log"
    set +e
    HOME="${TMP_DIR}/home" \
        PATH="${TMP_DIR}/bin:${TOOL_BIN}:/usr/bin:/bin" \
        KUBECONFIG="${TMP_DIR}/kubeconfig" \
        MOCK_KUBECTL_LOG="${TMP_DIR}/kubectl-${check}.log" \
        MOCK_KUBECTL_PID_LOG="${TMP_DIR}/kubectl-${check}.pids" \
        MOCK_CURL_LOG="${TMP_DIR}/curl-${check}.log" \
        MOCK_RULES_INVALID="${invalid_rules}" \
        MOCK_FAILURE="${mock_failure}" \
        MOCK_GRAFANA_BODY="${grafana_body}" \
        MOCK_STATE_DIR="${TMP_DIR}" \
        MOCK_COLLECTOR_STATE="${collector_state}" \
        MOCK_MISSING_SERVICE="${missing_service}" \
        MOCK_POD_STATE="${pod_state}" \
        MOCK_IMAGE_STATE="${image_state}" \
        VERIFY_METRIC_SETTLE_SECONDS=0 \
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
        PATH="${TMP_DIR}/bin:${TOOL_BIN}:/usr/bin:/bin" \
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

run_check 99
assert_status "${RUN_STATUS}" 2 "Verifier rejects selectors outside the documented 1-11 range"

run_check 3
assert_status "${RUN_STATUS}" 0 "Collector and log-agent readiness accept desired equals ready"
assert_contains "${TMP_DIR}/kubectl-3.log" "get daemonset otel-collector-agent" "Readiness verifies the node log-agent DaemonSet"
assert_contains "${TMP_DIR}/kubectl-3.log" "get deployments -A -o json" "Readiness verifies eight immutable workload images"

run_check 3 0 '' '' valid '' valid mutable
assert_status "${RUN_STATUS}" 1 "Readiness rejects mutable or incomplete workload images"

run_check 5
assert_status "${RUN_STATUS}" 0 "OTLP verification requires the exact generated trace"
assert_contains "${TMP_DIR}/curl-5.log" "/api/traces/" "Tempo lookup uses the generated trace ID"

run_check 5 0 tempo_empty
assert_status "${RUN_STATUS}" 1 "OTLP verification rejects an empty Tempo trace response"

run_check 5 0 tempo_ready
assert_status "${RUN_STATUS}" 1 "OTLP verification requires Tempo readiness"

for pod_state in one_of_three two_of_three malformed; do
    run_check 3 0 '' '' valid '' "${pod_state}"
    assert_status "${RUN_STATUS}" 1 "Observability pod readiness fails closed for ${pod_state}"
done

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
assert_contains "${TMP_DIR}/curl-6.log" 'service_name="cart-service"' "Loki verifies exact container-log identities"
assert_contains "${TMP_DIR}/curl-6.log" 'service_name="incident-engine"' "Loki verifies the incident namespace identity"
assert_status "$(grep -c 'data-urlencode=limit=1' "${TMP_DIR}/curl-6.log")" 16 "Loki queries each product identity independently without a global sample limit"
assert_contains "${TMP_DIR}/kubectl-6.log" 'port-forward svc/payment-gateway' "Loki verification primes the gateway through its real HTTP boundary"
assert_contains "${TMP_DIR}/curl-6.log" '/__verify_log__' "Loki verification creates a fresh non-probe payment log"
assert_contains "${TMP_DIR}/curl-6.log" 'data-urlencode=start=' "Payment log proof is bounded after the prime request"

run_check 6 0 payment_stale
assert_status "${RUN_STATUS}" 1 "Loki rejects a stale payment log without the prime path marker"

rm -f "${TMP_DIR}/payment-delay-counter"
run_check 6 0 payment_delayed
assert_status "${RUN_STATUS}" 0 "Loki waits for the fresh payment log to clear the collector batch"

run_check 6 0 loki
assert_status "${RUN_STATUS}" 1 "Loki check fails closed on an API error payload"

run_check 7
assert_status "${RUN_STATUS}" 0 "Prometheus product metric checks pass with complete API payloads"
assert_contains "${TMP_DIR}/kubectl-7.log" "port-forward svc/prometheus " "Prometheus Service is selected"
assert_contains "${TMP_DIR}/kubectl-7.log" ":80" "Prometheus forwards the rendered Service port 80"
assert_contains "${TMP_DIR}/curl-7.log" "http_server_request_duration_seconds_count" "Prometheus verifies the canonical histogram for six services"
assert_contains "${TMP_DIR}/curl-7.log" 'sli:.*:availability:ratio_5m' "Prometheus verifies seven generated SLO records"
assert_contains "${TMP_DIR}/curl-7.log" 'up{job="kubernetes-pods",app_kubernetes_io_name="payment-gateway"}' "Prometheus verifies the discovered payment scrape target"
assert_contains "${TMP_DIR}/curl-7.log" 'traces_service_graph_request_total' "Prometheus verifies a servicegraph edge"
assert_status "$(grep -c '/__verify_metric__' "${TMP_DIR}/curl-7.log" || true)" 12 "Prometheus primes canonical metrics with two requests to each service"
assert_status "$(grep -c '/checkout' "${TMP_DIR}/curl-7.log" || true)" 2 "Prometheus primes both checkout SLO series"

run_check 7 0 prometheus
assert_status "${RUN_STATUS}" 1 "Prometheus metric check fails closed on a malformed result"

run_check 9
assert_status "${RUN_STATUS}" 0 "All Grafana dashboard checks pass"
assert_status "$(grep -c '/api/dashboards/uid/' "${TMP_DIR}/curl-9.log")" 10 "Grafana verifies all 10 provisioned dashboard UIDs"

run_check 8
assert_status "${RUN_STATUS}" 0 "Grafana accepts exactly four provisioned datasources"

run_check 8 0 grafana_datasources
assert_status "${RUN_STATUS}" 1 "Grafana rejects an incomplete datasource set"

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
