#!/usr/bin/env bash
set -euo pipefail

: "${CLUSTER_NAME:?CLUSTER_NAME is required}"
: "${REGISTRY_NAME:?REGISTRY_NAME is required}"
: "${TF_DIR:?TF_DIR is required}"
: "${KUBECONFIG:?KUBECONFIG is required}"
K3D_BIN=${K3D_BIN:-k3d}
DOCKER_BIN=${DOCKER_BIN:-docker}
KUBECTL_BIN=${KUBECTL_BIN:-kubectl}
TERRAFORM_BIN=${TERRAFORM_BIN:-terraform}
MAKE_BIN=${MAKE_BIN:-make}
NETWORK_NAME="k3d-${CLUSTER_NAME}"
SECONDARY_KUBECONFIG="$HOME/.config/kubeconfig-${CLUSTER_NAME}.yaml"
DOCKER_ENDPOINT_FORMAT="{{ range \$id, \$_ := .Containers }}{{ \$id }}{{ \"\\n\" }}{{ end }}"

manifest=$(mktemp "${TMPDIR:-/tmp}/enten-runtime-owned.XXXXXX")
trap 'rm -f -- "$manifest"' EXIT

is_allowed_address() {
  case $1 in
    null_resource.coredns_registry_rewrite|null_resource.k3d_cluster|null_resource.k3d_registry|null_resource.namespaces) return 0 ;;
    *) return 1 ;;
  esac
}

state_exists=false
[[ -s "$TF_DIR/terraform.tfstate" ]] && state_exists=true
state_addresses=()
if $state_exists; then
  mapfile -t state_addresses < <("$TERRAFORM_BIN" -chdir="$TF_DIR" state list)
  for address in "${state_addresses[@]}"; do
    is_allowed_address "$address" || { echo "Unexpected Terraform state address: $address" >&2; exit 1; }
  done
fi

cluster_exists=false
registry_exists=false
"$K3D_BIN" cluster list 2>/dev/null | awk -v expected="$CLUSTER_NAME" '$1 == expected {found=1} END {exit !found}' && cluster_exists=true
"$K3D_BIN" registry list 2>/dev/null | awk -v expected="$REGISTRY_NAME" '$1 == expected {found=1} END {exit !found}' && registry_exists=true

cluster_ids=()
registry_ids=()
if $cluster_exists; then
  mapfile -t cluster_ids < <("$DOCKER_BIN" ps -aq --filter "label=k3d.cluster=$CLUSTER_NAME")
  ((${#cluster_ids[@]} > 0)) || { echo "No Docker containers prove ownership of cluster $CLUSTER_NAME" >&2; exit 1; }
  for id in "${cluster_ids[@]}"; do
    label=$("$DOCKER_BIN" inspect --format '{{ index .Config.Labels "k3d.cluster" }}' "$id")
    [[ $label == "$CLUSTER_NAME" ]] || { echo "Cluster container $id has unexpected ownership label" >&2; exit 1; }
  done
fi
if $registry_exists; then
  mapfile -t registry_ids < <("$DOCKER_BIN" ps -aq --filter "name=^/${REGISTRY_NAME}$")
  ((${#registry_ids[@]} == 1)) || { echo "Registry $REGISTRY_NAME did not resolve to one exact container" >&2; exit 1; }
  role=$("$DOCKER_BIN" inspect --format '{{ index .Config.Labels "k3d.role" }}' "${registry_ids[0]}")
  [[ $role == registry ]] || { echo "Registry container has unexpected role: $role" >&2; exit 1; }
fi

owned_ids=("${cluster_ids[@]}" "${registry_ids[@]}")
is_owned_id() {
  local candidate=$1 id
  for id in "${owned_ids[@]}"; do [[ $candidate == "$id" ]] && return 0; done
  return 1
}

# Capture mount ownership before any container can be removed.
for id in "${owned_ids[@]}"; do
  "$DOCKER_BIN" inspect --format '{{ range .Mounts }}{{ if eq .Type "volume" }}{{ .Name }}{{ "\n" }}{{ end }}{{ end }}' "$id" >>"$manifest"
done
"$DOCKER_BIN" volume ls -q --filter "label=k3d.cluster=$CLUSTER_NAME" >>"$manifest"
sort -u -o "$manifest" "$manifest"

while IFS= read -r volume; do
  [[ -n $volume ]] || continue
  while IFS= read -r ref; do
    [[ -n $ref ]] || continue
    is_owned_id "$ref" || { echo "Volume $volume is shared with unrelated container $ref" >&2; exit 1; }
  done < <("$DOCKER_BIN" ps -aq --filter "volume=$volume")
done <"$manifest"

if "$DOCKER_BIN" network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  network_label=$("$DOCKER_BIN" network inspect --format '{{ index .Labels "app" }}' "$NETWORK_NAME")
  [[ $network_label == k3d ]] || { echo "Network $NETWORK_NAME has unexpected ownership label" >&2; exit 1; }
  while IFS= read -r endpoint; do
    [[ -n $endpoint ]] || continue
    is_owned_id "$endpoint" || { echo "Network $NETWORK_NAME is shared with unrelated endpoint $endpoint" >&2; exit 1; }
  done < <("$DOCKER_BIN" network inspect --format "$DOCKER_ENDPOINT_FORMAT" "$NETWORK_NAME")
fi

api_live=false
if $cluster_exists && [[ -f $KUBECONFIG ]] && KUBECONFIG="$KUBECONFIG" "$KUBECTL_BIN" cluster-info --request-timeout=5s >/dev/null 2>&1; then
  api_live=true
fi

if $api_live; then
  KUBECONFIG="$KUBECONFIG" "$MAKE_BIN" --no-print-directory helm-uninstall-all
  if $state_exists && ((${#state_addresses[@]} > 0)); then
    "$TERRAFORM_BIN" -chdir="$TF_DIR" init -input=false
    set +e
    "$TERRAFORM_BIN" -chdir="$TF_DIR" destroy -auto-approve
    status=$?
    set -e
    if ((status != 0)); then
      echo "WARNING: Terraform destroy failed with status $status; continuing with owned fallback" >&2
    fi
  fi
fi

# Exact k3d fallback works for stopped clusters and partial Terraform failures.
if "$K3D_BIN" cluster list 2>/dev/null | awk -v expected="$CLUSTER_NAME" '$1 == expected {found=1} END {exit !found}'; then
  "$K3D_BIN" cluster delete "$CLUSTER_NAME"
fi
if "$K3D_BIN" registry list 2>/dev/null | awk -v expected="$REGISTRY_NAME" '$1 == expected {found=1} END {exit !found}'; then
  "$K3D_BIN" registry delete "$REGISTRY_NAME"
fi

# Refuse volume cleanup unless all owned containers are physically absent.
if [[ -n $("$DOCKER_BIN" ps -aq --filter "label=k3d.cluster=$CLUSTER_NAME") ]]; then
  echo "Cluster containers remain after k3d deletion" >&2
  exit 1
fi
if [[ -n $("$DOCKER_BIN" ps -aq --filter "name=^/${REGISTRY_NAME}$") ]]; then
  echo "Registry container remains after k3d deletion" >&2
  exit 1
fi
while IFS= read -r volume; do
  [[ -n $volume ]] || continue
  if "$DOCKER_BIN" volume inspect "$volume" >/dev/null 2>&1; then
    [[ -z $("$DOCKER_BIN" ps -aq --filter "volume=$volume") ]] || { echo "Volume $volume remains referenced" >&2; exit 1; }
    "$DOCKER_BIN" volume rm "$volume"
  fi
done <"$manifest"

if "$DOCKER_BIN" network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  network_label=$("$DOCKER_BIN" network inspect --format '{{ index .Labels "app" }}' "$NETWORK_NAME")
  [[ $network_label == k3d ]] || { echo "Network ownership changed during cleanup" >&2; exit 1; }
  endpoints=$("$DOCKER_BIN" network inspect --format "$DOCKER_ENDPOINT_FORMAT" "$NETWORK_NAME")
  [[ -z $endpoints ]] || { echo "Network $NETWORK_NAME still has endpoints" >&2; exit 1; }
  "$DOCKER_BIN" network rm "$NETWORK_NAME"
fi

if $state_exists; then
  mapfile -t state_addresses < <("$TERRAFORM_BIN" -chdir="$TF_DIR" state list)
  for address in "${state_addresses[@]}"; do
    is_allowed_address "$address" || { echo "Unexpected Terraform state address after cleanup: $address" >&2; exit 1; }
    "$TERRAFORM_BIN" -chdir="$TF_DIR" state rm "$address"
  done
  [[ -z $("$TERRAFORM_BIN" -chdir="$TF_DIR" state list) ]] || { echo "Terraform state remains after reconciliation" >&2; exit 1; }
fi

rm -f -- "$KUBECONFIG" "$SECONDARY_KUBECONFIG"

"$K3D_BIN" cluster list 2>/dev/null | awk -v expected="$CLUSTER_NAME" '$1 == expected {exit 1}'
"$K3D_BIN" registry list 2>/dev/null | awk -v expected="$REGISTRY_NAME" '$1 == expected {exit 1}'
[[ -z $("$DOCKER_BIN" ps -aq --filter "label=k3d.cluster=$CLUSTER_NAME") ]]
[[ -z $("$DOCKER_BIN" ps -aq --filter "name=^/${REGISTRY_NAME}$") ]]
if "$DOCKER_BIN" network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  echo "Network $NETWORK_NAME remains after cleanup" >&2
  exit 1
fi
while IFS= read -r volume; do [[ -z $volume ]] || ! "$DOCKER_BIN" volume inspect "$volume" >/dev/null 2>&1; done <"$manifest"
printf 'Runtime ownership cleanup verified.\n'
