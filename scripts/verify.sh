#!/usr/bin/env bash

# We INTENTIONALLY do not use `set -e`: each of the 11 checks must complete
# even when earlier checks fail. We still want:
#   -u  : undefined variable is an error
#   -o pipefail : a failed command in a pipeline propagates
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
EVIDENCE_DIR="${REPO_ROOT}/.omo/evidence"
mkdir -p "${EVIDENCE_DIR}"

export KUBECONFIG="${KUBECONFIG:-${HOME}/.kube/config-observability}"
export PATH="${HOME}/.local/bin:${HOME}/.nix-profile/bin:${PATH}"

OBS_NS="observability"
ECOM_NS="ecom"
INCIDENT_NS="incident"

# Note: checkout-service chart's _helpers.tpl derives the service name as
# `<Release.Name>-<serviceName>` (the chart overrides fullname), so the
# Service object is named `checkout-service-checkout-service`.
SERVICES=(
    "cart-service            8002"
    "catalog-service         8000"
    "inventory-service       8001"
    "order-service           8004"
    "payment-gateway         8005"
    "checkout-service-checkout-service 8003"
)
PRODUCT_LOG_SERVICES=(
    cart-service
    catalog-service
    checkout-service
    inventory-service
    order-service
    payment-gateway
    synthetic-monitor
    incident-engine
)

PASS=0
FAIL=0
DEFERRED=0
TOTAL=0
declare -a RESULTS=()
declare -A ACTIVE_PORT_FORWARDS=()

track_port_forward() {
    ACTIVE_PORT_FORWARDS["$1"]=1
}

stop_port_forwards() {
    local pid
    for pid in "$@"; do
        kill "${pid}" 2>/dev/null || true
    done
    for pid in "$@"; do
        wait "${pid}" 2>/dev/null || true
        unset 'ACTIVE_PORT_FORWARDS[$pid]'
    done
}

cleanup_port_forwards() {
    local pids=("${!ACTIVE_PORT_FORWARDS[@]}")
    stop_port_forwards "${pids[@]}"
}

trap cleanup_port_forwards EXIT
trap 'cleanup_port_forwards; exit 130' INT
trap 'cleanup_port_forwards; exit 143' TERM

log()  { printf '%s\n' "$*"; }
hdr()  { printf '\n\033[1;34m=== %s ===\033[0m\n' "$*"; }

record() {
    local status="$1"
    local name="$2"
    local detail="${3:-}"
    TOTAL=$((TOTAL + 1))
    case "${status}" in
        PASS)                    PASS=$((PASS + 1));      RESULTS+=("PASS         ${name}") ;;
        DEFERRED-PENDING-DEPLOY) DEFERRED=$((DEFERRED + 1)); RESULTS+=("DEFERRED     ${name}  -- ${detail}") ;;
        FAIL|*)                  FAIL=$((FAIL + 1));      RESULTS+=("FAIL         ${name}  -- ${detail}") ;;
    esac
    case "${status}" in
        PASS)                    printf '  \033[32m%-9s\033[0m %s\n'  "PASS"     "${name}" ;;
        DEFERRED-PENDING-DEPLOY) printf '  \033[33m%-9s\033[0m %s\n'  "DEFERRED" "${name}  -- ${detail}" ;;
        FAIL|*)                  printf '  \033[31m%-9s\033[0m %s\n'  "FAIL"     "${name}  -- ${detail}" ;;
    esac
}

SELECTED=""
if [[ $# -gt 0 ]]; then
    SELECTED="$*"
fi
selected_check() {
    local n="$1"
    [[ -z "${SELECTED}" ]] && return 0
    for arg in ${SELECTED}; do
        [[ "${arg}" == "${n}" ]] && return 0
    done
    return 1
}

prerequisites() {
    hdr "Prerequisites"
    local ok=1
    for cmd in kubectl curl jq; do
        if command -v "${cmd}" >/dev/null 2>&1; then
            printf '  \033[32mOK\033[0m        %s -> %s\n' "${cmd}" "$(command -v "${cmd}")"
        else
            printf '  \033[31mMISSING\033[0m  %s\n' "${cmd}"
            ok=0
        fi
    done

    if [[ -f "${KUBECONFIG}" ]]; then
        printf '  \033[32mOK\033[0m        KUBECONFIG=%s exists\n' "${KUBECONFIG}"
    else
        printf '  \033[31mMISSING\033[0m  KUBECONFIG=%s (run "make up" first)\n' "${KUBECONFIG}"
        ok=0
    fi

    if [[ "${ok}" -ne 1 ]]; then
        log ""
        log "ERROR: prerequisites missing; aborting."
        exit 2
    fi
}

check_1_cluster_nodes() {
    hdr "Check 1: cluster nodes"
    local out
    if ! out="$(kubectl get nodes --no-headers 2>&1)"; then
        record FAIL "1.cluster-nodes" "kubectl error: ${out}"
        return
    fi
    local total ready
    total=$(printf '%s\n' "${out}" | wc -l | tr -d ' ')
    ready=$(printf '%s\n' "${out}" | awk '$2 == "Ready"' | wc -l | tr -d ' ')
    log "  nodes=${ready}/${total} Ready"
    log "${out}"
    if [[ "${total}" -lt 3 ]]; then
        record FAIL "1.cluster-nodes" "expected >=3 nodes, got ${total}"
    elif [[ "${ready}" -lt 3 ]]; then
        record FAIL "1.cluster-nodes" "expected >=3 nodes Ready, got ${ready}"
    else
        record PASS "1.cluster-nodes"
    fi
}

check_2_namespaces() {
    hdr "Check 2: namespaces"
    local want=(observability ecom chaos-testing load incident)
    local got
    got="$(kubectl get ns --no-headers -o custom-columns=NAME:.metadata.name 2>&1 | sort)"
    log "${got}"
    local missing=()
    for ns in "${want[@]}"; do
        if ! grep -qx "${ns}" <<<"${got}"; then
            missing+=("${ns}")
        fi
    done
    if [[ "${#missing[@]}" -eq 0 ]]; then
        record PASS "2.namespaces"
    else
        record FAIL "2.namespaces" "missing: ${missing[*]}"
    fi
}

check_3_pods_ready() {
    hdr "Check 3: pods Ready"
    local out collector_out agent_out deployments_out
    if ! out="$(kubectl get pods -A --no-headers 2>&1)"; then
        record FAIL "3.pods-ready" "kubectl error: ${out}"
        return
    fi
    if ! collector_out="$(kubectl -n "${OBS_NS}" get deployment otel-collector -o json 2>&1)"; then
        record FAIL "3.pods-ready" "collector deployment query error: ${collector_out}"
        return
    fi
    if ! agent_out="$(kubectl -n "${OBS_NS}" get daemonset otel-collector-agent -o json 2>&1)"; then
        record FAIL "3.pods-ready" "collector log-agent query error: ${agent_out}"
        return
    fi
    if ! deployments_out="$(kubectl get deployments -A -o json 2>&1)"; then
        record FAIL "3.pods-ready" "workload image query error: ${deployments_out}"
        return
    fi
    local total ready malformed
    read -r total ready malformed < <(
        printf '%s\n' "${out}" | awk -v target_ns="${OBS_NS}" '
            $1 != target_ns { next }
            {
                total++
                if (NF < 4) {
                    malformed++
                    next
                }
                count = split($3, containers, "/")
                if (count != 2 || containers[1] !~ /^[0-9]+$/ || containers[2] !~ /^[0-9]+$/ || containers[2] == 0) {
                    malformed++
                    next
                }
                if ($4 == "Running" && containers[1] == containers[2]) {
                    ready++
                }
            }
            END { printf "%d %d %d\n", total, ready, malformed }
        '
    )
    local collector_desired="invalid" collector_ready="invalid"
    read -r collector_desired collector_ready < <(
        printf '%s' "${collector_out}" | jq -er '
            if (.spec.replicas | type) == "number"
                and (.status.readyReplicas | type) == "number"
                and .spec.replicas == (.spec.replicas | floor)
                and .status.readyReplicas == (.status.readyReplicas | floor)
            then [.spec.replicas, .status.readyReplicas] | @tsv
            else error("collector replica counts must be integers")
            end
        ' 2>/dev/null || printf 'invalid\tinvalid\n'
    )
    local agent_desired="invalid" agent_ready="invalid"
    read -r agent_desired agent_ready < <(
        printf '%s' "${agent_out}" | jq -er '
            if (.status.desiredNumberScheduled | type) == "number"
                and (.status.numberReady | type) == "number"
                and .status.desiredNumberScheduled == (.status.desiredNumberScheduled | floor)
                and .status.numberReady == (.status.numberReady | floor)
            then [.status.desiredNumberScheduled, .status.numberReady] | @tsv
            else error("agent readiness counts must be integers")
            end
        ' 2>/dev/null || printf 'invalid\tinvalid\n'
    )
    local workload_count="invalid" invalid_image_count="invalid"
    read -r workload_count invalid_image_count < <(
        printf '%s' "${deployments_out}" | jq -er '
            ["cart-service", "catalog-service", "checkout-service-checkout-service", "inventory-service",
             "order-service", "payment-gateway", "synthetic-monitor", "incident-engine"] as $expected
            | [.items[]
               | select(.metadata.name as $name | $expected | index($name))
               | {name: .metadata.name, image: .spec.template.spec.containers[0].image}] as $workloads
            | [
                ($workloads | length),
                ([$workloads[]
                  | select((.image | type) != "string"
                    or (.image | test("(@sha256:[0-9a-f]{64}|:(git-)?[0-9a-f]{12,64})$")) | not)]
                 | length)
              ] | @tsv
        ' 2>/dev/null || printf 'invalid\tinvalid\n'
    )
    log "  observability pods=${ready}/${total} Ready+Running malformed=${malformed}  otel-collector=${collector_ready}/${collector_desired} Ready  log-agents=${agent_ready}/${agent_desired} Ready  immutable-workloads=${workload_count}/8 invalid-images=${invalid_image_count}"
    if [[ "${total}" -eq 0 ]]; then
        record FAIL "3.pods-ready" "no observability pods visible"
    elif [[ "${malformed}" -gt 0 ]]; then
        record FAIL "3.pods-ready" "malformed observability pod rows=${malformed}"
    elif [[ "${ready}" -ne "${total}" ]]; then
        record FAIL "3.pods-ready" "observability pods expected all Ready+Running, got ${ready}/${total}"
    elif [[ ! "${collector_desired}" =~ ^[1-9][0-9]*$ || "${collector_ready}" != "${collector_desired}" ]]; then
        record FAIL "3.pods-ready" "otel-collector expected ready=desired>0, got ${collector_ready}/${collector_desired}"
    elif [[ ! "${agent_desired}" =~ ^[1-9][0-9]*$ || "${agent_ready}" != "${agent_desired}" ]]; then
        record FAIL "3.pods-ready" "otel-collector-agent expected ready=desired>0, got ${agent_ready}/${agent_desired}"
    elif [[ "${workload_count}" != "8" || "${invalid_image_count}" != "0" ]]; then
        record FAIL "3.pods-ready" "expected 8 immutable workload images, got count=${workload_count} invalid=${invalid_image_count}"
    else
        record PASS "3.pods-ready"
    fi
}

check_4_service_health() {
    hdr "Check 4: service /healthz (6 services in ecom)"
    local failed=()
    for entry in "${SERVICES[@]}"; do
        read -r svc port <<<"${entry}"
        # Try ClusterIP DNS first (in-cluster), then loopback (when running
        # inside the cluster via a sidecar). Both are typically unreachable
        # from the host, so fallback to kubectl port-forward.
        local url="http://${svc}.${ECOM_NS}.svc.cluster.local:${port}/healthz"
        local code="000"
        if code=$(curl -sS -m 3 -o /dev/null -w '%{http_code}' "${url}" 2>/dev/null); then
            if [[ "${code}" == "200" ]]; then
                log "  ok   ${url} -> 200"
                continue
            fi
        fi
        local pf_pid='' port_local=0
        for port_local in "${port}" 9"${port}" 8"${port}"; do
            port_local=$((port_local % 65536))
            kubectl -n "${ECOM_NS}" port-forward "svc/${svc}" "${port_local}:${port}" >/dev/null 2>&1 &
            pf_pid=$!
            track_port_forward "${pf_pid}"
            sleep 0.5
            code=$(curl -sS -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${port_local}/healthz" 2>/dev/null) || code=000
            stop_port_forwards "${pf_pid}"
            if [[ "${code}" == "200" ]]; then
                log "  ok   ${svc} (port-forward ${port_local} -> ${port}) -> 200"
                break
            fi
        done
        if [[ "${code}" != "200" ]]; then
            log "  FAIL ${svc} -> ${code}"
            failed+=("${svc}")
        fi
    done
    if [[ "${#failed[@]}" -eq 0 ]]; then
        record PASS "4.service-healthz"
    else
        record FAIL "4.service-healthz" "down: ${failed[*]}"
    fi
}

pick_local_port() {
    # Use a high random port in the ephemeral range to avoid collisions.
    while :; do
        local p=$(( ( RANDOM % 1000 ) + 30000 ))
        if ! (echo > /dev/tcp/127.0.0.1/${p}) >/dev/null 2>&1; then
            printf '%s\n' "${p}"
            return 0
        fi
    done
}

check_5_otel_trace() {
    hdr "Check 5: OTLP trace -> Tempo"
    if ! kubectl -n "${OBS_NS}" get svc otel-collector >/dev/null 2>&1; then
        record DEFERRED-PENDING-DEPLOY "5.otel-trace" "otel-collector service not present"
        return
    fi
    if ! kubectl -n "${OBS_NS}" get svc tempo >/dev/null 2>&1; then
        record DEFERRED-PENDING-DEPLOY "5.otel-trace" "tempo service not present"
        return
    fi
    local otel_pf tempo_pf
    otel_pf=$(pick_local_port)
    tempo_pf=$(pick_local_port)
    kubectl -n "${OBS_NS}" port-forward svc/otel-collector "${otel_pf}:4318" >/dev/null 2>&1 &
    local otel_pid=$!
    track_port_forward "${otel_pid}"
    kubectl -n "${OBS_NS}" port-forward svc/tempo "${tempo_pf}:3200" >/dev/null 2>&1 &
    local tempo_pid=$!
    track_port_forward "${tempo_pid}"
    sleep 1

    local trace_id
    trace_id=$(printf '%032x' $((RANDOM*RANDOM*RANDOM)) | head -c 32)
    local now_ns=$(( $(date +%s) * 1000000000 ))
    local payload
    payload=$(cat <<EOF
{
  "resourceSpans":[{
    "resource":{"attributes":[{"key":"service.name","value":{"stringValue":"verify.sh"}}]},
    "scopeSpans":[{
      "scope":{"name":"verify.sh","version":"0.1.0"},
      "spans":[{
        "traceId":"${trace_id}",
        "spanId":"0000000000000001",
        "name":"verify-trace",
        "startTimeUnixNano":"${now_ns}",
        "endTimeUnixNano":"$((now_ns + 1000000))"
      }]
    }]
  }]
}
EOF
)
    local push_code
    push_code=$(curl -sS -m 5 -o /dev/null -w '%{http_code}' \
        -H 'Content-Type: application/json' \
        -d "${payload}" \
        "http://127.0.0.1:${otel_pf}/v1/traces" 2>/dev/null) || push_code=000

    local ready_code
    ready_code=$(curl -sS -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${tempo_pf}/ready" 2>/dev/null) || ready_code=000
    sleep 1
    local qcode qbody trace_match
    qbody=$(curl -sS -m 5 \
        "http://127.0.0.1:${tempo_pf}/api/traces/${trace_id}" 2>/dev/null) || qbody=''
    qcode=$(curl -sS -m 5 -o /dev/null -w '%{http_code}' \
        "http://127.0.0.1:${tempo_pf}/api/traces/${trace_id}" 2>/dev/null) || qcode=000
    trace_match=$(printf '%s' "${qbody}" | jq -er --arg trace_id "${trace_id}" '
        [.. | objects | .traceId? // empty] | any(. == $trace_id)
    ' 2>/dev/null) || trace_match=false

    stop_port_forwards "${otel_pid}" "${tempo_pid}"

    log "  otlp push http=${push_code} trace_id=${trace_id}"
    log "  tempo /ready=${ready_code} /api/traces/${trace_id} http=${qcode} exact_match=${trace_match}"
    log "  tempo trace body (head): $(printf '%s' "${qbody}" | head -c 300)"
    if [[ "${push_code}" =~ ^2[0-9][0-9]$ \
        && "${ready_code}" =~ ^2[0-9][0-9]$ \
        && "${qcode}" =~ ^2[0-9][0-9]$ \
        && "${trace_match}" == true ]]; then
        record PASS "5.otel-trace"
    else
        record FAIL "5.otel-trace" "push=${push_code} ready=${ready_code} trace=${qcode}/${trace_match}"
    fi
}

check_6_loki_logs() {
    hdr "Check 6: Loki log push + query"
    if ! kubectl -n "${OBS_NS}" get svc loki >/dev/null 2>&1; then
        record DEFERRED-PENDING-DEPLOY "6.loki-logs" "loki service not present"
        return
    fi
    local loki_pf
    loki_pf=$(pick_local_port)
    kubectl -n "${OBS_NS}" port-forward svc/loki "${loki_pf}:3100" >/dev/null 2>&1 &
    local pid=$!
    track_port_forward "${pid}"
    local payment_pf
    payment_pf=$(pick_local_port)
    kubectl -n "${ECOM_NS}" port-forward svc/payment-gateway \
        "${payment_pf}:8005" >/dev/null 2>&1 &
    local payment_pid=$!
    track_port_forward "${payment_pid}"
    sleep 1

    local payment_prime_code payment_prime_start_ns
    payment_prime_start_ns=$(( $(date +%s) * 1000000000 ))
    payment_prime_code=$(curl -sS -m 3 -o /dev/null -w '%{http_code}' \
        "http://127.0.0.1:${payment_pf}/__verify_log__" 2>/dev/null) || payment_prime_code=000
    sleep "${VERIFY_LOG_SETTLE_SECONDS:-5}"

    local now_ns=$(( $(date +%s) * 1000000000 ))
    local stream_tag="verify-sh-$RANDOM"
    local push_payload
    push_payload=$(cat <<EOF
{"streams":[{"stream":{"service":"verify.sh","job":"${stream_tag}"},"values":[["${now_ns}","hello from verify.sh log line"]]}]}
EOF
)
    local pcode
    pcode=$(curl -sS -m 5 -o /dev/null -w '%{http_code}' \
        -H 'Content-Type: application/json' \
        -d "${push_payload}" \
        "http://127.0.0.1:${loki_pf}/loki/api/v1/push" 2>/dev/null) || pcode=000

    sleep 1
    local ready_code qcode qbody api_status result_count
    ready_code=$(curl -sS -m 3 -o /dev/null -w '%{http_code}' \
        "http://127.0.0.1:${loki_pf}/ready" 2>/dev/null) || ready_code=000
    qbody=$(curl -sS -m 5 -G \
        --data-urlencode "query={job=\"${stream_tag}\"}" \
        "http://127.0.0.1:${loki_pf}/loki/api/v1/query_range" 2>/dev/null) || qbody=''
    qcode=$(curl -sS -m 5 -o /dev/null -w '%{http_code}' -G \
        --data-urlencode "query={job=\"${stream_tag}\"}" \
        "http://127.0.0.1:${loki_pf}/loki/api/v1/query_range" 2>/dev/null) || qcode=000

    read -r api_status result_count < <(
        printf '%s' "${qbody}" \
            | jq -er '[.status, (if (.data.result | type) == "array" then (.data.result | length) else error("result is not an array") end)] | @tsv' 2>/dev/null \
            || printf 'invalid\t0\n'
    )

    local product_code=200 product_status=success product_count=0
    local product_service product_namespace product_query product_body product_query_end_ns
    local service_code service_status service_match
    local -a product_time_args
    local -a missing_services=()
    product_query_end_ns=$(( $(date +%s) * 1000000000 ))
    for product_service in "${PRODUCT_LOG_SERVICES[@]}"; do
        product_namespace=ecom
        [[ "${product_service}" == incident-engine ]] && product_namespace=incident
        product_query="{service_namespace=\"${product_namespace}\",service_name=\"${product_service}\"}"
        product_time_args=()
        if [[ "${product_service}" == payment-gateway ]]; then
            product_query+=' |= "path=/__verify_log__"'
            product_time_args=(
                --data-urlencode "start=${payment_prime_start_ns}"
                --data-urlencode "end=${product_query_end_ns}"
            )
        fi
        product_body=$(curl -sS -m 5 -G \
            --data-urlencode "query=${product_query}" \
            --data-urlencode "limit=1" \
            "${product_time_args[@]}" \
            "http://127.0.0.1:${loki_pf}/loki/api/v1/query_range" 2>/dev/null) || product_body=''
        service_code=$(curl -sS -m 5 -o /dev/null -w '%{http_code}' -G \
            --data-urlencode "query=${product_query}" \
            --data-urlencode "limit=1" \
            "${product_time_args[@]}" \
            "http://127.0.0.1:${loki_pf}/loki/api/v1/query_range" 2>/dev/null) || service_code=000
        read -r service_status service_match < <(
            printf '%s' "${product_body}" | jq -er \
                --arg service "${product_service}" \
                --arg marker "path=/__verify_log__" '
                if .status == "success" and (.data.result | type) == "array"
                then [
                    .status,
                    any(.data.result[];
                        .stream.service_name == $service and
                        (.values | type) == "array" and
                        (.values | length) > 0 and
                        ($service != "payment-gateway" or
                            any(.values[];
                                (length >= 2) and (.[1] | contains($marker)))))
                ] | @tsv
                else error("invalid Loki range result")
                end
            ' 2>/dev/null || printf 'invalid\tfalse\n'
        )
        if [[ "${service_code}" =~ ^2[0-9][0-9]$ \
            && "${service_status}" == success \
            && "${service_match}" == true ]]; then
            product_count=$((product_count + 1))
        else
            product_code="${service_code}"
            product_status="${service_status}"
            missing_services+=("${product_service}")
        fi
    done

    stop_port_forwards "${pid}" "${payment_pid}"

    log "  loki /ready=${ready_code}"
    log "  payment log prime http=${payment_prime_code}"
    log "  push http=${pcode}  query_range http=${qcode} status=${api_status} results=${result_count}"
    log "  product logs http=${product_code} status=${product_status} service identities=${product_count}/8 missing=${missing_services[*]:-none}"
    if [[ "${payment_prime_code}" == 404 \
        && "${pcode}" =~ ^2[0-9][0-9]$ \
        && "${qcode}" =~ ^2[0-9][0-9]$ \
        && "${api_status}" == "success" \
        && "${result_count}" =~ ^[0-9]+$ \
        && "${result_count}" -gt 0 \
        && "${product_code}" =~ ^2[0-9][0-9]$ \
        && "${product_status}" == "success" \
        && "${product_count}" == "8" ]]; then
        record PASS "6.loki-logs"
    else
        record FAIL "6.loki-logs" "push=${pcode} canary=${qcode}/${api_status}/${result_count} product=${product_code}/${product_status}/${product_count}"
    fi
}

check_7_prom_metric() {
    hdr "Check 7: Prometheus product metrics"
    if ! kubectl -n "${OBS_NS}" get svc prometheus >/dev/null 2>&1; then
        # Fallback to kube-prometheus-stack's `prometheus-operated`
        if ! kubectl -n "${OBS_NS}" get svc prometheus-operated >/dev/null 2>&1; then
            record DEFERRED-PENDING-DEPLOY "7.prom-metric" "prometheus service not present"
            return
        fi
    fi

    local settle_seconds="${VERIFY_METRIC_SETTLE_SECONDS:-35}"
    local -a metric_pids=() metric_ports=() metric_services=()
    local entry service service_port local_port metric_pid
    for entry in "${SERVICES[@]}"; do
        read -r service service_port <<<"${entry}"
        local_port=$(pick_local_port)
        kubectl -n "${ECOM_NS}" port-forward "svc/${service}" \
            "${local_port}:${service_port}" >/dev/null 2>&1 &
        metric_pid=$!
        track_port_forward "${metric_pid}"
        metric_pids+=("${metric_pid}")
        metric_ports+=("${local_port}")
        metric_services+=("${service}")
    done
    sleep 1

    local index
    for _ in 1 2; do
        for index in "${!metric_ports[@]}"; do
            curl -sS -m 3 -o /dev/null \
                "http://127.0.0.1:${metric_ports[$index]}/__verify_metric__" 2>/dev/null || true
            if [[ "${metric_services[$index]}" == "checkout-service-checkout-service" ]]; then
                curl -sS -m 3 -o /dev/null -X POST \
                    -H 'Content-Type: application/json' -d '{}' \
                    "http://127.0.0.1:${metric_ports[$index]}/checkout" 2>/dev/null || true
            fi
        done
        sleep "${settle_seconds}"
    done
    stop_port_forwards "${metric_pids[@]}"

    local prom_svc="prometheus"
    local prom_port=80
    if ! kubectl -n "${OBS_NS}" get svc prometheus >/dev/null 2>&1; then
        prom_svc="prometheus-operated"
        prom_port=9090
    fi

    local prom_pf
    prom_pf=$(pick_local_port)
    kubectl -n "${OBS_NS}" port-forward "svc/${prom_svc}" "${prom_pf}:${prom_port}" >/dev/null 2>&1 &
    local pid=$!
    track_port_forward "${pid}"
    sleep 1

    local ready_code
    ready_code=$(curl -sS -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${prom_pf}/-/ready" 2>/dev/null) || ready_code=000

    local -a queries=(
        'count(count by (service_name) (http_server_request_duration_seconds_count{service_name=~"cart-service|catalog-service|checkout-service|inventory-service|order-service|payment-gateway"}))'
        'count({__name__=~"sli:.*:availability:ratio_5m"})'
        'up{job="kubernetes-pods",app_kubernetes_io_name="payment-gateway"}'
        'sum(traces_service_graph_request_total)'
    )
    local -a expected=("6" "7" "1" "1")
    local -a comparisons=("equal" "equal" "equal" "positive")
    local -a names=("canonical-services" "slo-records" "payment-target" "servicegraph-edge")
    local check_failed=0 index query qbody api_status result_value
    for index in "${!queries[@]}"; do
        query="${queries[$index]}"
        qbody=$(curl -sS -m 5 -G --data-urlencode "query=${query}" \
            "http://127.0.0.1:${prom_pf}/api/v1/query" 2>/dev/null) || qbody=''
        read -r api_status result_value < <(
            printf '%s' "${qbody}" | jq -er '
                if .status == "success"
                    and (.data.result | type) == "array"
                    and (.data.result | length) == 1
                    and (.data.result[0].value | type) == "array"
                then [.status, .data.result[0].value[1]] | @tsv
                else error("invalid vector result")
                end
            ' 2>/dev/null || printf 'invalid\tinvalid\n'
        )
        local expected_display="${expected[$index]}"
        if [[ "${comparisons[$index]}" == "positive" ]]; then
            expected_display=">0"
        fi
        log "  ${names[$index]} status=${api_status} value=${result_value} expected=${expected_display}"
        if [[ "${api_status}" != "success" ]]; then
            check_failed=1
        elif [[ "${comparisons[$index]}" == "equal" && "${result_value}" != "${expected[$index]}" ]]; then
            check_failed=1
        elif [[ "${comparisons[$index]}" == "positive" ]] \
            && ! jq -en --arg value "${result_value}" '($value | tonumber) > 0' >/dev/null 2>&1; then
            check_failed=1
        fi
    done

    stop_port_forwards "${pid}"

    log "  prom service: ${prom_svc}:${prom_port}"
    log "  /-/ready=${ready_code}"
    if [[ "${ready_code}" =~ ^2[0-9][0-9]$ && "${check_failed}" -eq 0 ]]; then
        record PASS "7.prom-metric"
    else
        record FAIL "7.prom-metric" "ready=${ready_code} product_signals_complete=$((1 - check_failed))"
    fi
}

check_8_grafana_ds() {
    hdr "Check 8: Grafana datasources"
    if ! kubectl -n "${OBS_NS}" get svc grafana >/dev/null 2>&1; then
        record DEFERRED-PENDING-DEPLOY "8.grafana-datasources" "grafana service not present"
        return
    fi
    local graf_pf
    graf_pf=$(pick_local_port)
    kubectl -n "${OBS_NS}" port-forward svc/grafana "${graf_pf}:80" >/dev/null 2>&1 &
    local pid=$!
    track_port_forward "${pid}"
    sleep 1

    local code body ds_count ds_names response_file
    response_file=$(mktemp)
    code=$(curl -sS -m 5 -u admin:admin -o "${response_file}" -w '%{http_code}' \
        "http://127.0.0.1:${graf_pf}/api/datasources" 2>/dev/null) || code=000
    body=$(cat "${response_file}" 2>/dev/null || echo "")
    rm -f "${response_file}"
    read -r ds_count ds_names < <(
        printf '%s' "${body}" | jq -er '
            if type == "array" and all(.[]; (.name | type) == "string")
            then [length, ([.[].name] | sort | join(","))] | @tsv
            else error("datasources must be an array with names")
            end
        ' 2>/dev/null || printf '0\tinvalid\n'
    )

    stop_port_forwards "${pid}"

    log "  /api/datasources http=${code}  count=${ds_count} names=${ds_names}"
    if [[ "${code}" =~ ^2[0-9][0-9]$ \
        && "${ds_count}" == "4" \
        && "${ds_names}" == "Alertmanager,Loki,Prometheus,Tempo" ]]; then
        record PASS "8.grafana-datasources"
    else
        record FAIL "8.grafana-datasources" "http=${code} count=${ds_count} names=${ds_names}"
    fi
}

check_9_grafana_dashboard() {
    hdr "Check 9: Grafana dashboards"
    if ! kubectl -n "${OBS_NS}" get svc grafana >/dev/null 2>&1; then
        record DEFERRED-PENDING-DEPLOY "9.grafana-dashboard" "grafana service not present"
        return
    fi
    local graf_pf
    graf_pf=$(pick_local_port)
    kubectl -n "${OBS_NS}" port-forward svc/grafana "${graf_pf}:80" >/dev/null 2>&1 &
    local pid=$!
    track_port_forward "${pid}"
    sleep 1

    local -a dashboard_uids=(
        service-overview
        red-cart
        red-catalog
        red-checkout
        red-inventory
        red-order
        red-payment
        trace-explorer
        log-explorer
        slo-status
    )
    local code body dashboard_uid expected_uid response_file check_failed=0
    for expected_uid in "${dashboard_uids[@]}"; do
        response_file=$(mktemp)
        code=$(curl -sS -m 5 -u admin:admin -o "${response_file}" -w '%{http_code}' \
            "http://127.0.0.1:${graf_pf}/api/dashboards/uid/${expected_uid}" 2>/dev/null) || code=000
        body=$(cat "${response_file}" 2>/dev/null || echo "")
        rm -f "${response_file}"
        dashboard_uid=$(printf '%s' "${body}" | jq -er '.dashboard.uid | select(type == "string")' 2>/dev/null || printf 'invalid')
        log "  dashboard=${expected_uid} http=${code} uid=${dashboard_uid}"
        if [[ "${code}" != "200" || "${dashboard_uid}" != "${expected_uid}" ]]; then
            check_failed=1
        fi
    done

    stop_port_forwards "${pid}"

    if [[ "${check_failed}" -eq 0 ]]; then
        record PASS "9.grafana-dashboard"
    else
        record FAIL "9.grafana-dashboard" "one or more of 10 dashboard UIDs are unavailable"
    fi
}

check_10_prom_rules() {
    hdr "Check 10: Prometheus rules / alert rules loaded"
    local prom_svc="prometheus"
    local prom_port=80
    if ! kubectl -n "${OBS_NS}" get svc prometheus >/dev/null 2>&1; then
        prom_svc="prometheus-operated"
        prom_port=9090
    fi
    if ! kubectl -n "${OBS_NS}" get svc "${prom_svc}" >/dev/null 2>&1; then
        record DEFERRED-PENDING-DEPLOY "10.prom-rules" "prometheus service not present"
        return
    fi
    local pf
    pf=$(pick_local_port)
    kubectl -n "${OBS_NS}" port-forward "svc/${prom_svc}" "${pf}:${prom_port}" >/dev/null 2>&1 &
    local pid=$!
    track_port_forward "${pid}"
    sleep 1

    local code body api_status group_count rule_count recording_count alert_count response_file
    response_file=$(mktemp)
    code=$(curl -sS -m 5 -o "${response_file}" -w '%{http_code}' \
        "http://127.0.0.1:${pf}/api/v1/rules" 2>/dev/null) || code=000
    body=$(cat "${response_file}" 2>/dev/null || echo "")
    rm -f "${response_file}"
    read -r api_status group_count rule_count recording_count alert_count < <(
        printf '%s' "${body}" | jq -er '
            if (.data.groups | type) != "array" then error("groups is not an array") else
                [
                    .status,
                    (.data.groups | length),
                    ([.data.groups[].rules[]] | length),
                    ([.data.groups[].rules[] | select(.type == "recording")] | length),
                    ([.data.groups[].rules[] | select(.type == "alerting")] | length)
                ] | @tsv
            end
        ' 2>/dev/null || printf 'invalid\t0\t0\t0\t0\n'
    )

    stop_port_forwards "${pid}"

    log "  prom service: ${prom_svc}:${prom_port}"
    log "  /api/v1/rules http=${code}  status=${api_status}  groups=${group_count}  rules=${rule_count}  recordings=${recording_count}  alerts=${alert_count}"
    # The plan calls for 5 alert rules; the in-repo alert_rules.yaml actually
    # defines 7 distinct alert kinds applied per-service (49 total for 7
    # services). Treat >=5 unique alerting rules across all groups as PASS.
    if [[ "${code}" =~ ^2[0-9][0-9]$ \
        && "${api_status}" == "success" \
        && "${group_count}" =~ ^[0-9]+$ \
        && "${group_count}" -gt 0 \
        && "${rule_count}" =~ ^[0-9]+$ \
        && "${recording_count}" =~ ^[0-9]+$ \
        && "${recording_count}" -gt 0 \
        && "${alert_count}" =~ ^[0-9]+$ \
        && "${alert_count}" -ge 5 ]]; then
        record PASS "10.prom-rules"
    else
        record FAIL "10.prom-rules" "http=${code} status=${api_status} groups=${group_count} rules=${rule_count} recordings=${recording_count} alerts=${alert_count}"
    fi
}

check_11_incident_engine() {
    hdr "Check 11: incident-engine /incidents"
    local svc_name=""
    if kubectl -n "${INCIDENT_NS}" get svc incident-engine >/dev/null 2>&1; then
        svc_name="incident-engine"
    elif kubectl -n "${INCIDENT_NS}" get svc incident-engine-incident-engine >/dev/null 2>&1; then
        svc_name="incident-engine-incident-engine"
    else
        record DEFERRED-PENDING-DEPLOY "11.incident-engine" "incident-engine service not present (charts/incident-engine/templates is empty in Wave 4)"
        return
    fi

    local pf
    pf=$(pick_local_port)
    kubectl -n "${INCIDENT_NS}" port-forward "svc/${svc_name}" "${pf}:8088" >/dev/null 2>&1 &
    local pid=$!
    track_port_forward "${pid}"
    sleep 1

    local code body arr_len response_file
    response_file=$(mktemp)
    code=$(curl -sS -m 5 -o "${response_file}" -w '%{http_code}' \
        "http://127.0.0.1:${pf}/incidents" 2>/dev/null) || code=000
    body=$(cat "${response_file}" 2>/dev/null || echo "")
    rm -f "${response_file}"
    arr_len=$(printf '%s' "${body}" | jq 'if type == "array" then length else -1 end' 2>/dev/null || echo -1)

    stop_port_forwards "${pid}"

    log "  svc=${svc_name}  /incidents http=${code}  json_type=$(printf '%s' "${body}" | jq -r 'type' 2>/dev/null)"
    if [[ "${code}" =~ ^2[0-9][0-9]$ && "${arr_len}" -ge 0 ]]; then
        record PASS "11.incident-engine"
    else
        record FAIL "11.incident-engine" "http=${code} body_type=${arr_len}"
    fi
}

main() {
    hdr "verify.sh — Enten v2 end-to-end health check"
    log "KUBECONFIG = ${KUBECONFIG}"
    log "Timestamp  = $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    log "Selected   = ${SELECTED:-all}"
    prerequisites

    if selected_check 1; then check_1_cluster_nodes; fi
    if selected_check 2; then check_2_namespaces;   fi
    if selected_check 3; then check_3_pods_ready;   fi
    if selected_check 4; then check_4_service_health; fi
    if selected_check 5; then check_5_otel_trace;   fi
    if selected_check 6; then check_6_loki_logs;    fi
    if selected_check 7; then check_7_prom_metric;  fi
    if selected_check 8; then check_8_grafana_ds;   fi
    if selected_check 9; then check_9_grafana_dashboard; fi
    if selected_check 10; then check_10_prom_rules; fi
    if selected_check 11; then check_11_incident_engine; fi

    hdr "Summary"
    for r in "${RESULTS[@]}"; do
        printf '  %s\n' "${r}"
    done
    log ""
    log "Total: ${TOTAL}    PASS: ${PASS}    FAIL: ${FAIL}    DEFERRED: ${DEFERRED}"

    local evidence_file="${EVIDENCE_DIR}/task-23-verify-summary.txt"
    {
        echo "Enten v2 verify.sh -- $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
        echo "KUBECONFIG=${KUBECONFIG}"
        echo "Selected:    ${SELECTED:-all}"
        echo "Total=${TOTAL}  PASS=${PASS}  FAIL=${FAIL}  DEFERRED=${DEFERRED}"
        for r in "${RESULTS[@]}"; do
            echo "  ${r}"
        done
    } > "${evidence_file}"
    log "Evidence:  ${evidence_file}"

    if [[ "${FAIL}" -gt 0 || "${DEFERRED}" -gt 0 ]]; then
        cleanup_port_forwards
        exit 1
    fi
    cleanup_port_forwards
    exit 0
}

main "$@"
