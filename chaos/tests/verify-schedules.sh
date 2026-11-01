#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHAOS_ROOT="${ROOT}/chaos"
TMP="$(mktemp -d /tmp/chaos-schedules.XXXXXX)"
trap 'rm -rf "${TMP}"' EXIT

network="${CHAOS_ROOT}/experiments/network-delay-payment.yaml"
stress="${CHAOS_ROOT}/experiments/cpu-stress-catalog.yaml"
dns="${CHAOS_ROOT}/experiments/dns-chaos-inventory.yaml"
pod="${CHAOS_ROOT}/experiments/pod-kill-checkout.yaml"

yq -e '.kind == "Schedule" and .spec.schedule == "@every 60s" and .spec.historyLimit == 5 and .spec.concurrencyPolicy == "Forbid" and .spec.type == "NetworkChaos" and .spec.networkChaos.action == "delay" and .spec.networkChaos.mode == "all" and .spec.networkChaos.duration == "5m" and (.spec.networkChaos.selector.namespaces | length) == 1 and .spec.networkChaos.selector.namespaces[0] == "ecom" and .spec.networkChaos.selector.labelSelectors["app.kubernetes.io/name"] == "checkout-service" and .spec.networkChaos.delay.latency == "2000ms" and .spec.networkChaos.delay.correlation == "100" and .spec.networkChaos.delay.jitter == "0ms" and .spec.networkChaos.direction == "from" and .spec.networkChaos.target.mode == "all" and (.spec.networkChaos.target.selector.namespaces | length) == 1 and .spec.networkChaos.target.selector.namespaces[0] == "ecom" and .spec.networkChaos.target.selector.labelSelectors["app.kubernetes.io/name"] == "payment-gateway"' "${network}" >/dev/null
yq -e '.kind == "Schedule" and .spec.schedule == "@every 60s" and .spec.historyLimit == 5 and .spec.concurrencyPolicy == "Forbid" and .spec.type == "StressChaos" and .spec.stressChaos.duration == "3m" and .spec.stressChaos.mode == "fixed" and .spec.stressChaos.value == "1" and (.spec.stressChaos.selector.namespaces | length) == 1 and .spec.stressChaos.selector.namespaces[0] == "ecom" and .spec.stressChaos.selector.labelSelectors["app.kubernetes.io/name"] == "catalog-service" and .spec.stressChaos.stressors.cpu.workers == 1 and .spec.stressChaos.stressors.cpu.load == 80' "${stress}" >/dev/null
yq -e '.kind == "Schedule" and .spec.schedule == "@every 60s" and .spec.historyLimit == 5 and .spec.concurrencyPolicy == "Forbid" and .spec.type == "DNSChaos" and .spec.dnsChaos.action == "random" and .spec.dnsChaos.mode == "all" and .spec.dnsChaos.duration == "2m" and (.spec.dnsChaos.selector.namespaces | length) == 1 and .spec.dnsChaos.selector.namespaces[0] == "ecom" and .spec.dnsChaos.selector.labelSelectors["app.kubernetes.io/name"] == "inventory-service" and (.spec.dnsChaos.patterns | length) == 2 and .spec.dnsChaos.patterns[0] == "inventory-service.ecom.svc.cluster.local" and .spec.dnsChaos.patterns[1] == "*.inventory-service.ecom.svc.cluster.local"' "${dns}" >/dev/null
yq -e '.kind == "Workflow" and .spec.entry == "scheduled-pod-kill" and .spec.templates[0].templateType == "Schedule" and .spec.templates[0].deadline == "5m" and .spec.templates[0].schedule.schedule == "@every 60s" and .spec.templates[0].schedule.historyLimit == 5 and .spec.templates[0].schedule.concurrencyPolicy == "Forbid" and .spec.templates[0].schedule.type == "PodChaos" and .spec.templates[0].schedule.podChaos.action == "pod-kill" and .spec.templates[0].schedule.podChaos.mode == "one" and (.spec.templates[0].schedule.podChaos.selector.namespaces | length) == 1 and .spec.templates[0].schedule.podChaos.selector.namespaces[0] == "ecom" and .spec.templates[0].schedule.podChaos.selector.labelSelectors["app.kubernetes.io/name"] == "checkout-service"' "${pod}" >/dev/null

helm show crds chaos-mesh/chaos-mesh --version 2.8.3 >"${TMP}/crds.yaml"
for kind in Schedule Workflow; do
  yq -o=json "select(.kind == \"CustomResourceDefinition\" and .spec.names.kind == \"${kind}\") | .spec.versions[] | select(.name == \"v1alpha1\") | .schema.openAPIV3Schema" \
    "${TMP}/crds.yaml" >"${TMP}/${kind}_v1alpha1.json"
  jq -e '.properties.spec' "${TMP}/${kind}_v1alpha1.json" >/dev/null
done
kubeconform -strict \
  -schema-location "${TMP}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" \
  "${network}" "${stress}" "${dns}" "${pod}"

cat >"${TMP}/measurement-boundary.json" <<'JSON'
{
  "schema": "flow-qs.networkchaos.measurement/v1",
  "experiment": "network-delay-payment",
  "samples": {
    "baseline": [
      {"http_status": 200, "status": "paid", "trace_id": "00000000000000000000000000000001", "time_total_seconds": 0.1},
      {"http_status": 200, "status": "paid", "trace_id": "00000000000000000000000000000002", "time_total_seconds": 0.2},
      {"http_status": 200, "status": "paid", "trace_id": "00000000000000000000000000000003", "time_total_seconds": 0.3}
    ],
    "injected": [
      {"http_status": 200, "status": "paid", "trace_id": "00000000000000000000000000000004", "time_total_seconds": 1.6},
      {"http_status": 200, "status": "paid", "trace_id": "00000000000000000000000000000005", "time_total_seconds": 1.7},
      {"http_status": 200, "status": "paid", "trace_id": "00000000000000000000000000000006", "time_total_seconds": 1.8}
    ]
  }
}
JSON
"${CHAOS_ROOT}/tests/verify-network-delay-measurement.sh" \
  "${TMP}/measurement-boundary.json" >/dev/null
jq '.samples.injected[1].time_total_seconds = 1.699' \
  "${TMP}/measurement-boundary.json" >"${TMP}/measurement-below.json"
if "${CHAOS_ROOT}/tests/verify-network-delay-measurement.sh" \
  "${TMP}/measurement-below.json" >/dev/null 2>&1; then
  exit 1
fi

mkdir -p "${TMP}/bin"
cat >"${TMP}/bin/kubectl" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${CHAOS_MOCK_CALLS}"
MOCK
cat >"${TMP}/bin/sleep" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
chmod +x "${TMP}/bin/kubectl" "${TMP}/bin/sleep"
CHAOS_MOCK_CALLS="${TMP}/kubectl-calls" PATH="${TMP}/bin:${PATH}" \
  make -s -C "${CHAOS_ROOT}" chaos-all dry-run clean >/dev/null
[[ "$(grep -c '^apply -f experiments/' "${TMP}/kubectl-calls")" -eq 4 ]]
grep -q '^apply --dry-run=server -f experiments/pod-kill-checkout.yaml' "${TMP}/kubectl-calls"
grep -q '^delete -f experiments/pod-kill-checkout.yaml' "${TMP}/kubectl-calls"
