#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="${DEMO_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "${REPO_ROOT}"

export KUBECONFIG="${KUBECONFIG:-/home/mei/.kube/config-observability}"
LOAD_NAMESPACE="${LOAD_NAMESPACE:-load}"
CHAOS_NAMESPACE="${CHAOS_NAMESPACE:-chaos-testing}"
INCIDENT_NAMESPACE="${INCIDENT_NAMESPACE:-incident}"
INCIDENT_SERVICE_ACCOUNT="${INCIDENT_SERVICE_ACCOUNT:-incident-engine}"
INCIDENT_POD_SELECTOR="${INCIDENT_POD_SELECTOR:-app.kubernetes.io/name=incident-engine}"
INCIDENT_POSTMORTEM_DIR="${INCIDENT_POSTMORTEM_DIR:-/postmortems}"
CHAOS_MANIFEST="${CHAOS_MANIFEST:-chaos/experiments/pod-kill-checkout.yaml}"
ALERT_CHAOS_MANIFEST="${ALERT_CHAOS_MANIFEST:-chaos/experiments/demo-alert-checkout.yaml}"
K6_LOAD_MANIFEST="${K6_LOAD_MANIFEST:-load/k6-job-demo.yaml}"
K6_JOB_NAME="${K6_JOB_NAME:-k6-load}"
CHAOS_NAME="${CHAOS_NAME:-pod-kill-checkout}"
CHAOS_RESOURCE_KIND="${CHAOS_RESOURCE_KIND:-workflow}"
ALERT_CHAOS_NAME="${ALERT_CHAOS_NAME:-demo-alert-checkout}"
ALERT_CHAOS_RESOURCE_KIND="${ALERT_CHAOS_RESOURCE_KIND:-networkchaos}"
EXPECTED_INCIDENT_SERVICE="${EXPECTED_INCIDENT_SERVICE:-checkout-service}"
EXPECTED_INCIDENT_ALERT="${EXPECTED_INCIDENT_ALERT:-ServiceDown}"
EXPECTED_POSTMORTEM_HEADER="# Postmortem — \`${EXPECTED_INCIDENT_ALERT}\` on \`${EXPECTED_INCIDENT_SERVICE}\`"
EXPECTED_CHAOS_MARKER="**${ALERT_CHAOS_NAME}** (NetworkChaos)"
PROM_RULES_PATH="${PROM_RULES_PATH:-/api/v1/namespaces/observability/services/http:prometheus:80/proxy/api/v1/rules}"
ALERTMANAGER_READY_PATH="${ALERTMANAGER_READY_PATH:-/api/v1/namespaces/observability/services/http:alertmanager:9093/proxy/-/ready}"

SETTLE_BEFORE_CHAOS="${SETTLE_BEFORE_CHAOS:-60}"
WAIT_FOR_ALERT="${WAIT_FOR_ALERT:-240}"
K6_TIMEOUT="${K6_TIMEOUT:-420}"
SERVICE_READY_TIMEOUT="${SERVICE_READY_TIMEOUT:-120}"
DEMO_TIMEOUT_SECONDS="${DEMO_TIMEOUT_SECONDS:-900}"
CLEANUP_COMMAND_TIMEOUT_SECONDS="${CLEANUP_COMMAND_TIMEOUT_SECONDS:-5}"
CLEANUP_WAIT_SECONDS="${CLEANUP_WAIT_SECONDS:-20}"

TS="${DEMO_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_ID="${TS}"
START_SEC="$(date -u +%s)"
LOG_DIR="${DEMO_LOG_DIR:-${REPO_ROOT}/.omo/evidence}"
LOG_FILE="${LOG_DIR}/task-24-demo.log"
SUMMARY_FILE="${LOG_DIR}/task-24-summary.txt"
POSTMORTEM_OUT_DIR="${DEMO_POSTMORTEM_OUT_DIR:-${REPO_ROOT}/docs/examples/postmortems}"
POSTMORTEM_OUT="${POSTMORTEM_OUT_DIR}/postmortem-demo-${RUN_ID}.md"
POSTMORTEM_MARKER="/tmp/demo-${RUN_ID}.marker"

K6_APPLIED=0
CHAOS_APPLIED=0
ALERT_CHAOS_APPLIED=0
INCIDENT_POD=""
CLEANUP_DONE=0
ACTIVE_SLEEP_PID=""
CLEANUP_SCHEDULE_NAMES=""
MATCHED_POSTMORTEM=""

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "${LOG_FILE}"
}

bounded_kubectl() {
  timeout --signal=TERM --kill-after=1s \
    "${CLEANUP_COMMAND_TIMEOUT_SECONDS}s" kubectl "$@"
}

cleanup_request() {
  local step="$1"
  shift
  if bounded_kubectl "$@" >/dev/null 2>&1; then
    log "cleanup_receipt step=${step} requested=true"
    return 0
  fi
  log "cleanup_receipt step=${step} requested=false"
  return 1
}

collect_cleanup_remaining() {
  local failed=0
  if [[ "${CHAOS_APPLIED}" -eq 1 ]]; then
    collect_chaos_remaining || failed=1
  fi
  if [[ "${ALERT_CHAOS_APPLIED}" -eq 1 ]]; then
    bounded_kubectl get "${ALERT_CHAOS_RESOURCE_KIND}" "${ALERT_CHAOS_NAME}" \
      -n "${CHAOS_NAMESPACE}" --ignore-not-found -o name || failed=1
  fi
  if [[ "${K6_APPLIED}" -eq 1 ]]; then
    bounded_kubectl get job "${K6_JOB_NAME}" -n "${LOAD_NAMESPACE}" \
      --ignore-not-found -o name || failed=1
  fi
  return "${failed}"
}

collect_chaos_remaining() {
  local schedule_name failed=0
  bounded_kubectl get workflow "${CHAOS_NAME}" -n "${CHAOS_NAMESPACE}" \
    --ignore-not-found -o name || failed=1
  bounded_kubectl get workflow,workflownode,schedule,podchaos \
    -n "${CHAOS_NAMESPACE}" -l "chaos-mesh.org/workflow=${CHAOS_NAME}" \
    -o name || failed=1
  while IFS= read -r schedule_name; do
    [[ -z "${schedule_name}" ]] || bounded_kubectl get podchaos \
      -n "${CHAOS_NAMESPACE}" -l "managed-by=${schedule_name}" -o name \
      || failed=1
  done <<<"${CLEANUP_SCHEDULE_NAMES:-}"
  bounded_kubectl get "${ALERT_CHAOS_RESOURCE_KIND}" "${ALERT_CHAOS_NAME}" \
    -n "${CHAOS_NAMESPACE}" --ignore-not-found -o name || failed=1
  return "${failed}"
}

delete_workflow_tree() {
  local workflow_name="" schedule_name failed=0
  workflow_name="$(bounded_kubectl get workflow "${CHAOS_NAME}" \
    -n "${CHAOS_NAMESPACE}" --ignore-not-found -o name)" || failed=1
  CLEANUP_SCHEDULE_NAMES="$(bounded_kubectl get schedule -n "${CHAOS_NAMESPACE}" \
    -l "chaos-mesh.org/workflow=${CHAOS_NAME}" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')" || failed=1
  if [[ -n "${workflow_name}" ]]; then
    cleanup_request pause_workflow annotate workflow "${CHAOS_NAME}" \
      -n "${CHAOS_NAMESPACE}" experiment.chaos-mesh.org/pause=true \
      --overwrite || failed=1
  fi
  if [[ -n "${CLEANUP_SCHEDULE_NAMES}" ]]; then
    cleanup_request pause_schedules annotate schedule -n "${CHAOS_NAMESPACE}" \
      -l "chaos-mesh.org/workflow=${CHAOS_NAME}" \
      experiment.chaos-mesh.org/pause=true --overwrite || failed=1
  fi
  cleanup_request delete_workflow delete workflow "${CHAOS_NAME}" \
    -n "${CHAOS_NAMESPACE}" --ignore-not-found --cascade=background \
    --wait=false || failed=1
  cleanup_request delete_schedules delete schedule -n "${CHAOS_NAMESPACE}" \
    -l "chaos-mesh.org/workflow=${CHAOS_NAME}" --ignore-not-found \
    --cascade=background --wait=false || failed=1
  cleanup_request delete_workflow_podchaos delete podchaos \
    -n "${CHAOS_NAMESPACE}" \
    -l "chaos-mesh.org/workflow=${CHAOS_NAME}" --ignore-not-found \
    --cascade=background --wait=false || failed=1
  while IFS= read -r schedule_name; do
    if [[ -n "${schedule_name}" ]]; then
      cleanup_request "delete_managed_podchaos_${schedule_name}" \
        delete podchaos -n "${CHAOS_NAMESPACE}" \
        -l "managed-by=${schedule_name}" \
        --ignore-not-found --cascade=background --wait=false || failed=1
    fi
  done <<<"${CLEANUP_SCHEDULE_NAMES}"
  return "${failed}"
}

delete_alert_chaos() {
  cleanup_request delete_alert_chaos delete "${ALERT_CHAOS_RESOURCE_KIND}" \
    "${ALERT_CHAOS_NAME}" -n "${CHAOS_NAMESPACE}" --ignore-not-found \
    --cascade=background --wait=false
}

delete_workflow_nodes() {
  cleanup_request delete_workflownodes delete workflownode \
    -n "${CHAOS_NAMESPACE}" \
    -l "chaos-mesh.org/workflow=${CHAOS_NAME}" --ignore-not-found \
    --cascade=background --wait=false
}

cleanup() {
  local rc="${1:-$?}" cleanup_failed=0 watchdog_reaped=true
  local active_sleep_reaped=true
  local remaining="" collect_rc=0
  [[ "${CLEANUP_DONE}" -eq 0 ]] || return 0
  CLEANUP_DONE=1
  if [[ -n "${WATCHDOG_PID:-}" ]]; then
    kill "${WATCHDOG_PID}" 2>/dev/null || true
    wait "${WATCHDOG_PID}" 2>/dev/null || true
    if kill -0 "${WATCHDOG_PID}" 2>/dev/null; then
      watchdog_reaped=false
      cleanup_failed=1
    fi
  fi
  log "cleanup_receipt watchdog_reaped=${watchdog_reaped}"
  if [[ -n "${ACTIVE_SLEEP_PID}" ]]; then
    kill "${ACTIVE_SLEEP_PID}" 2>/dev/null || true
    wait "${ACTIVE_SLEEP_PID}" 2>/dev/null || true
    if kill -0 "${ACTIVE_SLEEP_PID}" 2>/dev/null; then
      active_sleep_reaped=false
      cleanup_failed=1
    fi
  fi
  log "cleanup_receipt active_sleep_reaped=${active_sleep_reaped}"

  if [[ "${CHAOS_APPLIED}" -eq 1 ]]; then
    delete_workflow_tree || cleanup_failed=1
  fi
  if [[ "${ALERT_CHAOS_APPLIED}" -eq 1 ]]; then
    delete_alert_chaos || cleanup_failed=1
  fi
  if [[ "${CHAOS_APPLIED}" -eq 1 ]]; then
    delete_workflow_nodes || cleanup_failed=1
  fi
  if [[ "${K6_APPLIED}" -eq 1 ]]; then
    cleanup_request delete_k6_job delete job "${K6_JOB_NAME}" \
      -n "${LOAD_NAMESPACE}" --ignore-not-found --cascade=background \
      --wait=false || cleanup_failed=1
  fi
  local cleanup_deadline=$((SECONDS + CLEANUP_WAIT_SECONDS))
  while (( SECONDS < cleanup_deadline )); do
    if remaining="$(collect_cleanup_remaining 2>/dev/null)"; then
      collect_rc=0
    else
      collect_rc=$?
    fi
    [[ "${collect_rc}" -eq 0 && -z "${remaining}" ]] && break
    sleep 1
  done
  [[ "${collect_rc}" -eq 0 && -z "${remaining}" ]] || cleanup_failed=1
  log "cleanup_receipt remaining=${remaining:-none} bounded=true verification_ok=$([[ "${collect_rc}" -eq 0 ]] && echo true || echo false)"
  if [[ "${cleanup_failed}" -eq 0 ]]; then
    log "cleanup_receipt complete=true exit=${rc}"
  else
    log "cleanup_receipt complete=false original_exit=${rc}"
  fi
  return "${cleanup_failed}"
}

preclean_demo_chaos() {
  local failed=0 remaining="" collect_rc=0
  delete_workflow_tree || failed=1
  delete_alert_chaos || failed=1
  delete_workflow_nodes || failed=1
  local deadline=$((SECONDS + CLEANUP_WAIT_SECONDS))
  while (( SECONDS < deadline )); do
    if remaining="$(collect_chaos_remaining 2>/dev/null)"; then
      collect_rc=0
    else
      collect_rc=$?
    fi
    [[ "${collect_rc}" -eq 0 && -z "${remaining}" ]] && break
    sleep 1
  done
  [[ "${collect_rc}" -eq 0 && -z "${remaining}" ]] || failed=1
  log "precleanup_receipt remaining=${remaining:-none} bounded=true"
  return "${failed}"
}

on_exit() {
  local rc=$? cleanup_rc
  set +e
  cleanup "${rc}"
  cleanup_rc=$?
  set -e
  trap - EXIT
  if [[ "${cleanup_rc}" -ne 0 && "${rc}" -eq 0 ]]; then
    rm -f "${SUMMARY_FILE}"
    log "FATAL: cleanup incomplete; success summary removed"
    rc=1
  elif [[ "${rc}" -eq 0 ]]; then
    log "Demo complete. Postmortem: ${POSTMORTEM_OUT}"
  fi
  exit "${rc}"
}

die() {
  log "FATAL: $*"
  exit 1
}

on_timeout() {
  log "FATAL: demo exceeded ${DEMO_TIMEOUT_SECONDS}s"
  exit 124
}

fresh_remote_postmortems() {
  kubectl exec -n "${INCIDENT_NAMESPACE}" "${INCIDENT_POD}" -- \
    find "${INCIDENT_POSTMORTEM_DIR}" -type f -name '*.md' \
      -newer "${POSTMORTEM_MARKER}" -print 2>/dev/null
}

postmortem_matches_file() {
  local path="$1"
  grep -Fqx -- "${EXPECTED_POSTMORTEM_HEADER}" "${path}" \
    && grep -Fq -- "${EXPECTED_CHAOS_MARKER}" "${path}"
}

postmortem_matches_remote() {
  local path="$1"
  kubectl exec -n "${INCIDENT_NAMESPACE}" "${INCIDENT_POD}" -- \
    grep -Fqx -- "${EXPECTED_POSTMORTEM_HEADER}" "${path}" \
    && kubectl exec -n "${INCIDENT_NAMESPACE}" "${INCIDENT_POD}" -- \
      grep -Fq -- "${EXPECTED_CHAOS_MARKER}" "${path}"
}

interruptible_sleep() {
  local seconds="$1" rc had_errexit=0
  [[ "$-" == *e* ]] && had_errexit=1
  sleep "${seconds}" &
  ACTIVE_SLEEP_PID=$!
  set +e
  wait "${ACTIVE_SLEEP_PID}"
  rc=$?
  if [[ "${had_errexit}" -eq 1 ]]; then
    set -e
  else
    set +e
  fi
  ACTIVE_SLEEP_PID=""
  return "${rc}"
}

wait_for_k6() {
  local deadline=$((SECONDS + K6_TIMEOUT)) succeeded failed
  while (( SECONDS < deadline )); do
    succeeded="$(kubectl get job "${K6_JOB_NAME}" -n "${LOAD_NAMESPACE}" \
      -o jsonpath='{.status.succeeded}' 2>/dev/null || true)"
    failed="$(kubectl get job "${K6_JOB_NAME}" -n "${LOAD_NAMESPACE}" \
      -o jsonpath='{.status.failed}' 2>/dev/null || true)"
    [[ "${succeeded:-0}" -lt 1 ]] || return 0
    [[ "${failed:-0}" -lt 1 ]] || return 1
    interruptible_sleep 5
  done
  return 124
}

k6_job_is_active() {
  local active succeeded failed
  active="$(kubectl get job "${K6_JOB_NAME}" -n "${LOAD_NAMESPACE}" \
    -o jsonpath='{.status.active}' 2>/dev/null)" || return 1
  succeeded="$(kubectl get job "${K6_JOB_NAME}" -n "${LOAD_NAMESPACE}" \
    -o jsonpath='{.status.succeeded}' 2>/dev/null)" || return 1
  failed="$(kubectl get job "${K6_JOB_NAME}" -n "${LOAD_NAMESPACE}" \
    -o jsonpath='{.status.failed}' 2>/dev/null)" || return 1
  [[ "${active:-0}" -ge 1 && "${succeeded:-0}" -lt 1 && "${failed:-0}" -lt 1 ]]
}

wait_for_postmortem() {
  local deadline=$((SECONDS + WAIT_FOR_ALERT)) candidate=""
  while (( SECONDS < deadline )); do
    while IFS= read -r candidate; do
      if [[ -n "${candidate}" ]] && postmortem_matches_remote "${candidate}"; then
        MATCHED_POSTMORTEM="${candidate}"
        return 0
      fi
    done < <(fresh_remote_postmortems | sort -r)
    interruptible_sleep 5
  done
  return 1
}

verify_demo_alert_rule() {
  kubectl get --raw "${PROM_RULES_PATH}" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
expected_alert, expected_service = sys.argv[1:]
rules = (
    rule
    for group in payload.get("data", {}).get("groups", [])
    for rule in group.get("rules", [])
)
if not any(
    rule.get("name") == expected_alert
    and rule.get("labels", {}).get("service") == expected_service
    for rule in rules
):
    raise SystemExit(1)
' "${EXPECTED_INCIDENT_ALERT}" "${EXPECTED_INCIDENT_SERVICE}"
}

write_k6_summary() {
  python3 - "$1" "$2" <<'PY'
import json
import math
import sys
from pathlib import Path

log_path, output_path = map(Path, sys.argv[1:])
temporary_path = output_path.with_name(f".{output_path.name}.tmp")
output_path.unlink(missing_ok=True)
temporary_path.unlink(missing_ok=True)
prefix = "ENTEN_K6_SUMMARY="
markers = [
    line.removeprefix(prefix)
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if line.startswith(prefix)
]
if len(markers) != 1:
    raise SystemExit("expected exactly one k6 summary marker")
try:
    payload = json.loads(markers[0])
except json.JSONDecodeError as error:
    raise SystemExit(f"invalid k6 summary marker: {error}") from error
if payload.get("schema") != "enten.k6.summary/v1":
    raise SystemExit("unexpected k6 summary schema")
if payload.get("profile") != "demo-chaos":
    raise SystemExit("unexpected k6 summary profile")
metrics = payload.get("metrics")
if not isinstance(metrics, dict):
    raise SystemExit("k6 summary metrics missing")
required = {
    "checkout_success_rate",
    "http_req_failed",
    "http_req_duration_p95_ms",
    "checks_rate",
    "iterations",
}
if set(metrics) != required:
    raise SystemExit("k6 summary metrics do not match contract")
for name, value in metrics.items():
    if type(value) not in (int, float) or not math.isfinite(value):
        raise SystemExit(f"k6 summary metric {name} is not finite")
if not 0 <= metrics["checkout_success_rate"] <= 1:
    raise SystemExit("checkout success rate outside [0,1]")
if not 0 <= metrics["http_req_failed"] <= 1:
    raise SystemExit("request failure rate outside [0,1]")
if not 0 <= metrics["checks_rate"] <= 1:
    raise SystemExit("check rate outside [0,1]")
if metrics["http_req_duration_p95_ms"] < 0 or metrics["iterations"] < 0:
    raise SystemExit("negative k6 summary metric")
try:
    temporary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
finally:
    temporary_path.unlink(missing_ok=True)
PY
}

collect_k6_summary() {
  local pod="$1" log_path="$2" output_path="$3"
  kubectl logs "${pod}" -n "${LOAD_NAMESPACE}" >"${log_path}" 2>&1 \
    || return 1
  write_k6_summary "${log_path}" "${output_path}"
}

initialize_run_artifacts() {
  local k6_log="$1" k6_results="$2"
  mkdir -p "${LOG_DIR}" "${POSTMORTEM_OUT_DIR}"
  rm -f "${SUMMARY_FILE}" "${k6_log}" "${k6_results}" \
    "${LOG_DIR}/task-24-k6-job.log" \
    "${LOG_DIR}/task-24-k6-results.json" \
    "${LOG_DIR}/.task-24-k6-results.json.tmp" \
    "$(dirname "${k6_results}")/.${k6_results##*/}.tmp"
  : >"${LOG_FILE}"
}

require_k6_success() {
  local pod="$1" log_path="$2" results_path="$3" status=0
  if wait_for_k6; then
    return 0
  else
    status=$?
  fi
  rm -f "${results_path}" "${SUMMARY_FILE}"
  kubectl logs "${pod}" -n "${LOAD_NAMESPACE}" >"${log_path}" 2>&1 || true
  [[ "${status}" -ne 124 ]] || die "k6 job timed out"
  die "k6 job failed"
}

start_causal_chaos() {
  log "Step 3/8: waiting ${SETTLE_BEFORE_CHAOS}s before causal chaos"
  interruptible_sleep "${SETTLE_BEFORE_CHAOS}"
  grep -qE 'kube-system|control-plane' "${ALERT_CHAOS_MANIFEST}" \
    && die "alert chaos targets control plane"
  log "Step 3/8: applying monitoring-path NetworkChaos"
  kubectl apply -f "${ALERT_CHAOS_MANIFEST}" >/dev/null \
    || die "alert chaos manifest apply failed"
  ALERT_CHAOS_APPLIED=1
  kubectl get "${ALERT_CHAOS_RESOURCE_KIND}" "${ALERT_CHAOS_NAME}" \
    -n "${CHAOS_NAMESPACE}" >/dev/null 2>&1 \
    || die "alert chaos was not admitted"

  log "Step 4/8: waiting up to ${WAIT_FOR_ALERT}s for matching checkout ServiceDown"
  wait_for_postmortem || die "no fresh causally correlated checkout postmortem"
  k6_job_is_active || die "k6 is not active before recurring checkout chaos"
  grep -qE 'kube-system|control-plane' "${CHAOS_MANIFEST}" \
    && die "chaos workflow targets control plane"
  log "Step 5/8: starting recurring checkout pod-kill workflow"
  kubectl apply -f "${CHAOS_MANIFEST}" >/dev/null \
    || die "chaos workflow apply failed"
  CHAOS_APPLIED=1
  kubectl get "${CHAOS_RESOURCE_KIND}" "${CHAOS_NAME}" \
    -n "${CHAOS_NAMESPACE}" >/dev/null 2>&1 \
    || die "chaos workflow was not admitted"
}

main() {
  local running_services=0 k6_pod="" postmortem_remote=""
  local k6_log="${LOG_DIR}/task-24-k6-job-${RUN_ID}.log"
  local k6_results="${LOG_DIR}/task-24-k6-results-${RUN_ID}.json"
  local -a services=(catalog-service inventory-service cart-service
    checkout-service-checkout-service order-service payment-gateway)

  mkdir -p "${LOG_DIR}" "${POSTMORTEM_OUT_DIR}"
  [[ "${RUN_ID}" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid demo run id"
  initialize_run_artifacts "${k6_log}" "${k6_results}"
  trap on_exit EXIT
  trap on_timeout TERM
  python3 -c 'import os,signal,sys,time; time.sleep(int(sys.argv[1])); os.kill(int(sys.argv[2]), signal.SIGTERM)' \
    "${DEMO_TIMEOUT_SECONDS}" "$$" &
  WATCHDOG_PID=$!

  log "Demo run id: ${RUN_ID}"
  log "Step 1/8: verifying all required services and controllers"
  for namespace in "${LOAD_NAMESPACE}" "${CHAOS_NAMESPACE}" ecom \
    "${INCIDENT_NAMESPACE}" observability; do
    kubectl get ns "${namespace}" >/dev/null 2>&1 || die "namespace ${namespace} is missing"
  done
  for service in "${services[@]}"; do
    if kubectl wait --for=condition=available --timeout="${SERVICE_READY_TIMEOUT}s" \
      "deploy/${service}" -n ecom >/dev/null 2>&1; then
      running_services=$((running_services + 1))
      log "service_ready name=${service}"
    else
      die "required deployment ${service} is unavailable"
    fi
  done
  [[ "${running_services}" -eq "${#services[@]}" ]] || die "not all services are ready"
  kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=chaos-mesh \
    -n "${CHAOS_NAMESPACE}" --timeout=60s >/dev/null 2>&1 || die "Chaos Mesh is not ready"

  INCIDENT_POD="$(kubectl get pod -n "${INCIDENT_NAMESPACE}" -l "${INCIDENT_POD_SELECTOR}" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
  [[ -n "${INCIDENT_POD}" ]] || die "incident-engine pod is missing"
  kubectl wait --for=condition=ready "pod/${INCIDENT_POD}" -n "${INCIDENT_NAMESPACE}" \
    --timeout=60s >/dev/null 2>&1 || die "incident-engine pod is not ready"
  kubectl auth can-i list networkchaos.chaos-mesh.org \
    -n "${CHAOS_NAMESPACE}" \
    --as="system:serviceaccount:${INCIDENT_NAMESPACE}:${INCIDENT_SERVICE_ACCOUNT}" \
    | grep -qx yes || die "incident-engine cannot list NetworkChaos"
  preclean_demo_chaos || die "stale demo chaos resources could not be removed"
  kubectl exec -n "${INCIDENT_NAMESPACE}" "${INCIDENT_POD}" -- \
    sh -c ": > '${POSTMORTEM_MARKER}'" >/dev/null || die "could not create run marker"
  verify_demo_alert_rule >/dev/null 2>&1 \
    || die "checkout ServiceDown alert rule is not loaded"
  kubectl get --raw "${ALERTMANAGER_READY_PATH}" >/dev/null 2>&1 \
    || die "Alertmanager is not ready"

  log "Step 2/8: starting k6 load"
  kubectl delete job "${K6_JOB_NAME}" -n "${LOAD_NAMESPACE}" --ignore-not-found >/dev/null
  kubectl apply -f "${K6_LOAD_MANIFEST}" >/dev/null || die "k6 manifest apply failed"
  K6_APPLIED=1
  kubectl wait --for=condition=ready pod -l job-name="${K6_JOB_NAME}" \
    -n "${LOAD_NAMESPACE}" --timeout=120s >/dev/null 2>&1 || die "k6 pod did not become ready"
  k6_pod="$(kubectl get pod -n "${LOAD_NAMESPACE}" -l job-name="${K6_JOB_NAME}" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
  [[ -n "${k6_pod}" ]] || die "k6 pod could not be identified"

  start_causal_chaos
  postmortem_remote="${MATCHED_POSTMORTEM}"

  log "Step 5/8: copying the causally correlated postmortem"
  kubectl cp "${INCIDENT_NAMESPACE}/${INCIDENT_POD}:${postmortem_remote}" \
    "${POSTMORTEM_OUT}" >/dev/null || die "fresh postmortem copy failed"
  postmortem_matches_file "${POSTMORTEM_OUT}" \
    || die "copied postmortem lost incident or chaos correlation"

  log "Step 6/8: waiting for k6 success"
  require_k6_success "${k6_pod}" "${k6_log}" "${k6_results}"

  log "Step 7/8: collecting k6 results"
  collect_k6_summary "${k6_pod}" "${k6_log}" "${k6_results}" \
    || die "k6 summary marker is missing or invalid"

  log "Step 8/8: writing provisional run summary"
  cat >"${SUMMARY_FILE}" <<EOF
demo_run_id=${RUN_ID}
started_at=${TS}
ended_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
total_duration_sec=$(($(date -u +%s) - START_SEC))
ecom_services_ready=${running_services}
k6_completed=1
k6_failed=0
chaos_applied=1
postmortem_source=incident-engine
postmortem_remote=${postmortem_remote}
postmortem_path=${POSTMORTEM_OUT}
incident_alert=${EXPECTED_INCIDENT_ALERT}
incident_service=${EXPECTED_INCIDENT_SERVICE}
incident_chaos=${ALERT_CHAOS_NAME}
incident_chaos_kind=NetworkChaos
checkout_podkill_workflow=${CHAOS_NAME}
EOF
  log "Run checks passed; finalizing cleanup"
}

if [[ "${DEMO_LIB_ONLY:-0}" -ne 1 ]]; then
  main "$@"
fi
