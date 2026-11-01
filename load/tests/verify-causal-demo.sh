#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d /tmp/causal-demo.XXXXXX)"
trap 'rm -rf "${TMP}"' EXIT

DEMO_LIB_ONLY=1 DEMO_REPO_ROOT="${ROOT}" DEMO_LOG_DIR="${TMP}/logs" \
  DEMO_POSTMORTEM_OUT_DIR="${TMP}/postmortems" source "${ROOT}/scripts/demo.sh"

cat >"${TMP}/incident-pods.json" <<'JSON'
{"items":[
  {"metadata":{"name":"completed-old","creationTimestamp":"2026-07-15T08:00:00Z"},"status":{"phase":"Succeeded","conditions":[{"type":"Ready","status":"False"}]}},
  {"metadata":{"name":"ready-current","creationTimestamp":"2026-07-15T08:10:00Z"},"status":{"phase":"Running","conditions":[{"type":"Ready","status":"True"}]}}
]}
JSON
[[ "$(select_ready_pod_from_json <"${TMP}/incident-pods.json")" == "ready-current" ]]

: >"${TMP}/pod-polls"
kubectl() {
  printf '.\n' >>"${TMP}/pod-polls"
  if [[ "$(wc -l <"${TMP}/pod-polls")" -ge 3 ]]; then
    printf 'k6-new\n'
  fi
}
sleep() { :; }
[[ "$(wait_for_pod_creation load job-name=k6-load 3)" == "k6-new" ]]
[[ "$(wc -l <"${TMP}/pod-polls")" -eq 3 ]]
unset -f sleep

manifest="${ROOT}/chaos/experiments/demo-alert-checkout.yaml"
yq -e '.kind == "PodChaos" and .metadata.name == "demo-alert-checkout" and .metadata.namespace == "chaos-testing" and .spec.action == "pod-failure" and .spec.mode == "all" and .spec.duration == "3m" and (.spec.selector.namespaces | length) == 1 and .spec.selector.namespaces[0] == "ecom" and .spec.selector.labelSelectors["app.kubernetes.io/name"] == "checkout-service"' "${manifest}" >/dev/null
[[ "${ALERT_CHAOS_NAME}" == "demo-alert-checkout" ]]
[[ "${ALERT_CHAOS_RESOURCE_KIND}" == "podchaos" ]]
[[ "${EXPECTED_INCIDENT_SERVICE}" == "checkout-service" ]]

valid="${TMP}/valid.md"
wrong_service="${TMP}/wrong-service.md"
wrong_chaos="${TMP}/wrong-chaos.md"
missing_correlation="${TMP}/missing-correlation.md"
printf '%s\n' "# Postmortem: \`ServiceDown\` on \`checkout-service\`" \
  '**demo-alert-checkout** (PodChaos)' >"${valid}"
printf '%s\n' "# Postmortem: \`ServiceDown\` on \`catalog-service\`" \
  '**demo-alert-checkout** (PodChaos)' >"${wrong_service}"
printf '%s\n' "# Postmortem: \`ServiceDown\` on \`checkout-service\`" \
  '**other-chaos** (PodChaos)' >"${wrong_chaos}"
printf '%s\n' "# Postmortem: \`ServiceDown\` on \`checkout-service\`" \
  >"${missing_correlation}"
postmortem_matches_file "${valid}"
if postmortem_matches_file "${wrong_service}"; then exit 1; fi
if postmortem_matches_file "${wrong_chaos}"; then exit 1; fi
if postmortem_matches_file "${missing_correlation}"; then exit 1; fi

kubectl() {
  while [[ "$#" -gt 0 && "$1" != "--" ]]; do shift; done
  shift
  "$@"
}
postmortem_matches_remote "${valid}"
if postmortem_matches_remote "${wrong_service}"; then exit 1; fi
if postmortem_matches_remote "${wrong_chaos}"; then exit 1; fi
if postmortem_matches_remote "${missing_correlation}"; then exit 1; fi

env -u KUBECONFIG helm show crds chaos-mesh/chaos-mesh --version 2.8.3 \
  >"${TMP}/crds.yaml"
yq -o=json 'select(.kind == "CustomResourceDefinition" and .spec.names.kind == "PodChaos") | .spec.versions[] | select(.name == "v1alpha1") | .schema.openAPIV3Schema' \
  "${TMP}/crds.yaml" >"${TMP}/PodChaos_v1alpha1.json"
kubeconform -strict \
  -schema-location "${TMP}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" \
  "${manifest}"

settle_line="$(grep -n 'waiting .* before causal chaos' "${ROOT}/scripts/demo.sh" | cut -d: -f1)"
alert_line="$(grep -n 'applying causal checkout PodChaos' "${ROOT}/scripts/demo.sh" | cut -d: -f1)"
accept_line="$(grep -n 'waiting up to .*matching checkout ServiceDown' "${ROOT}/scripts/demo.sh" | cut -d: -f1)"
active_line="$(grep -n 'k6_job_is_active || die' "${ROOT}/scripts/demo.sh" | cut -d: -f1)"
workflow_line="$(grep -n 'starting recurring checkout pod-kill workflow' "${ROOT}/scripts/demo.sh" | cut -d: -f1)"
[[ "${settle_line}" -lt "${alert_line}" && "${alert_line}" -lt "${accept_line}" && "${accept_line}" -lt "${active_line}" && "${active_line}" -lt "${workflow_line}" ]]
grep -q 'auth can-i list podchaos.chaos-mesh.org' "${ROOT}/scripts/demo.sh"

mkdir -p "${TMP}/logs"
: >"${LOG_FILE}"
: >"${TMP}/causal-order"
interruptible_sleep() { printf 'settle:%s\n' "$1" >>"${TMP}/causal-order"; }
wait_for_postmortem() {
  printf 'accept-correlated-postmortem\n' >>"${TMP}/causal-order"
  MATCHED_POSTMORTEM="/postmortems/correlated.md"
}
k6_job_is_active() { printf 'k6-active\n' >>"${TMP}/causal-order"; }
kubectl() {
  if [[ "$1" == "apply" ]]; then
    printf 'apply:%s\n' "$3" >>"${TMP}/causal-order"
  elif [[ "$1" == "get" ]]; then
    printf 'admit:%s/%s\n' "$2" "$3" >>"${TMP}/causal-order"
  fi
}
start_causal_chaos >/dev/null
cat >"${TMP}/expected-order" <<EOF
settle:${SETTLE_BEFORE_CHAOS}
apply:${ALERT_CHAOS_MANIFEST}
admit:${ALERT_CHAOS_RESOURCE_KIND}/${ALERT_CHAOS_NAME}
accept-correlated-postmortem
k6-active
apply:${CHAOS_MANIFEST}
admit:${CHAOS_RESOURCE_KIND}/${CHAOS_NAME}
EOF
diff -u "${TMP}/expected-order" "${TMP}/causal-order"

CLEANUP_SCHEDULE_NAMES="pod-kill-schedule"
: >"${TMP}/kubectl-calls"
bounded_kubectl() {
  printf '%s\n' "$*" >>"${TMP}/kubectl-calls"
  return 0
}
preclean_demo_chaos >/dev/null
grep -q '^delete workflow pod-kill-checkout ' "${TMP}/kubectl-calls"
grep -q '^delete podchaos demo-alert-checkout ' "${TMP}/kubectl-calls"
: >"${TMP}/kubectl-calls"
cleanup 1 >/dev/null
workflow_delete="$(grep -n '^delete workflow pod-kill-checkout ' "${TMP}/kubectl-calls" | cut -d: -f1)"
alert_delete="$(grep -n '^delete podchaos demo-alert-checkout ' "${TMP}/kubectl-calls" | cut -d: -f1)"
nodes_delete="$(grep -n '^delete workflownode ' "${TMP}/kubectl-calls" | cut -d: -f1)"
[[ "${workflow_delete}" -lt "${alert_delete}" && "${alert_delete}" -lt "${nodes_delete}" ]]
grep -q '^delete job k6-load ' "${TMP}/kubectl-calls"

CLEANUP_DONE=0
CLEANUP_WAIT_SECONDS=1
printf 'stale success\n' >"${SUMMARY_FILE}"
bounded_kubectl() {
  if [[ "$1" == "get" && "$2" == "podchaos" ]]; then
    printf 'podchaos/demo-alert-checkout\n'
  fi
  return 0
}
set +e
(on_exit >/dev/null 2>&1)
finalizer_rc=$?
set -e
[[ "${finalizer_rc}" -eq 1 ]]
[[ ! -e "${SUMMARY_FILE}" ]]
