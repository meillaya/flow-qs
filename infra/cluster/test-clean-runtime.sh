#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
helper="$root/infra/cluster/clean-runtime.sh"
failures=0

fail() { printf 'FAIL: %s\n' "$*" >&2; failures=$((failures + 1)); }

make_fakes() {
  local case_dir=$1
  mkdir -p "$case_dir"/{bin,world,tf,home/.kube,home/.config}
  : >"$case_dir/log"
  : >"$case_dir/tf/state-addresses"
  : >"$case_dir/world/cluster-containers"
  : >"$case_dir/world/volumes"
  : >"$case_dir/world/network-endpoints"

  cat >"$case_dir/bin/k3d" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf 'k3d %s\n' "$*" >>"$MOCK_LOG"
case "$1 $2" in
  'cluster list')
    printf 'NAME SERVERS AGENTS LOADBALANCER\n'
    [[ -e "$MOCK_WORLD/cluster-present" ]] && printf '%s 1/1 2/2 true\n' "$CLUSTER_NAME"
    [[ -e "$MOCK_WORLD/lookalike-cluster" ]] && printf '%s-old 1/1 0/0 true\n' "$CLUSTER_NAME"
    true
    ;;
  'registry list')
    printf 'NAME ROLE\n'
    [[ -e "$MOCK_WORLD/registry-present" ]] && printf '%s registry\n' "$REGISTRY_NAME"
    [[ -e "$MOCK_WORLD/lookalike-registry" ]] && printf '%s-copy registry\n' "$REGISTRY_NAME"
    true
    ;;
  'cluster delete')
    rm -f "$MOCK_WORLD/cluster-present" "$MOCK_WORLD/cluster-containers"
    sed -i 's/|c1|/||/' "$MOCK_WORLD/volumes"
    grep -Fxv c1 "$MOCK_WORLD/network-endpoints" >"$MOCK_WORLD/network-endpoints.next" || true
    mv "$MOCK_WORLD/network-endpoints.next" "$MOCK_WORLD/network-endpoints"
    ;;
  'registry delete')
    rm -f "$MOCK_WORLD/registry-present" "$MOCK_WORLD/registry-container"
    sed -i 's/|r1|/||/' "$MOCK_WORLD/volumes"
    grep -Fxv r1 "$MOCK_WORLD/network-endpoints" >"$MOCK_WORLD/network-endpoints.next" || true
    mv "$MOCK_WORLD/network-endpoints.next" "$MOCK_WORLD/network-endpoints"
    ;;
  *) exit 2 ;;
esac
FAKE

  cat >"$case_dir/bin/kubectl" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf 'kubectl %s\n' "$*" >>"$MOCK_LOG"
[[ "$1" == cluster-info && -e "$MOCK_WORLD/api-live" ]]
FAKE

  cat >"$case_dir/bin/make" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf 'make %s\n' "$*" >>"$MOCK_LOG"
[[ "$*" == '--no-print-directory helm-uninstall-all' ]]
FAKE

  cat >"$case_dir/bin/terraform" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf 'terraform %s\n' "$*" >>"$MOCK_LOG"
args=("$@")
cmd=''
for value in "${args[@]}"; do
  case "$value" in init|destroy|state) cmd=$value; break ;; esac
done
case "$cmd" in
  init) exit 0 ;;
  destroy)
    status=${MOCK_DESTROY_STATUS:-0}
    if [[ $status == 0 ]]; then
      rm -f "$MOCK_WORLD/cluster-present" "$MOCK_WORLD/registry-present" \
        "$MOCK_WORLD/cluster-containers" "$MOCK_WORLD/registry-container"
      sed -i 's/|c1|/||/; s/|r1|/||/' "$MOCK_WORLD/volumes"
      : >"$MOCK_WORLD/network-endpoints"
      : >"$TF_DIR/state-addresses"
    fi
    exit "$status"
    ;;
  state)
    sub=''
    for value in "${args[@]}"; do
      case "$value" in list|rm) sub=$value; break ;; esac
    done
    if [[ $sub == list ]]; then cat "$TF_DIR/state-addresses"; exit 0; fi
    if [[ $sub == rm ]]; then
      address=${args[-1]}
      grep -Fxq "$address" "$TF_DIR/state-addresses"
      grep -Fxv "$address" "$TF_DIR/state-addresses" >"$TF_DIR/state-addresses.next" || true
      mv "$TF_DIR/state-addresses.next" "$TF_DIR/state-addresses"
      exit 0
    fi
    exit 2
    ;;
  *) exit 2 ;;
esac
FAKE

  cat >"$case_dir/bin/docker" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf 'docker %s\n' "$*" >>"$MOCK_LOG"
cmd=$1; shift
case "$cmd" in
  ps)
    filter=${*: -1}
    case "$filter" in
      label=k3d.cluster=*) [[ ! -s "$MOCK_WORLD/cluster-containers" ]] || cut -d'|' -f1 "$MOCK_WORLD/cluster-containers" ;;
      name=*) [[ ! -s "$MOCK_WORLD/registry-container" ]] || cut -d'|' -f1 "$MOCK_WORLD/registry-container" ;;
      volume=*)
        volume=${filter#volume=}
        awk -F'|' -v v="$volume" '$1==v && $2!="" {print $2}' "$MOCK_WORLD/volumes"
        ;;
      *) exit 2 ;;
    esac
    ;;
  inspect)
    format=$2; id=$3
    if grep -q "k3d.cluster" <<<"$format"; then awk -F'|' -v id="$id" '$1==id {print $3}' "$MOCK_WORLD/cluster-containers"; exit; fi
    if grep -q "k3d.role" <<<"$format"; then awk -F'|' -v id="$id" '$1==id {print $3}' "$MOCK_WORLD/registry-container"; exit; fi
    if grep -q '.Mounts' <<<"$format"; then awk -F'|' -v id="$id" '$2==id {print $1}' "$MOCK_WORLD/volumes"; exit; fi
    exit 2
    ;;
  volume)
    sub=$1; shift
    case "$sub" in
      ls) awk -F'|' '$3!="" {print $1}' "$MOCK_WORLD/volumes" | sort -u ;;
      inspect) grep -q "^$1|" "$MOCK_WORLD/volumes" ;;
      rm) volume=$1; awk -F'|' -v v="$volume" '$1!=v' "$MOCK_WORLD/volumes" >"$MOCK_WORLD/volumes.next"; mv "$MOCK_WORLD/volumes.next" "$MOCK_WORLD/volumes" ;;
      *) exit 2 ;;
    esac
    ;;
  network)
    sub=$1; shift
    case "$sub" in
      inspect)
        [[ -e "$MOCK_WORLD/network-present" ]] || exit 1
        [[ $1 != --format ]] && exit 0
        format=$2
        if grep -q 'Labels' <<<"$format"; then cat "$MOCK_WORLD/network-label"; else cat "$MOCK_WORLD/network-endpoints"; fi
        ;;
      rm) rm -f "$MOCK_WORLD/network-present" "$MOCK_WORLD/network-label"; : >"$MOCK_WORLD/network-endpoints" ;;
      *) exit 2 ;;
    esac
    ;;
  *) exit 2 ;;
esac
FAKE
  chmod +x "$case_dir/bin/"*
}

seed_owned_runtime() {
  local case_dir=$1
  touch "$case_dir/world/cluster-present" "$case_dir/world/registry-present" "$case_dir/world/network-present"
  printf 'k3d\n' >"$case_dir/world/network-label"
  printf 'c1|k3d-observability-platform-server-0|observability-platform\n' >"$case_dir/world/cluster-containers"
  printf 'r1|k3d-observability-registry|registry\n' >"$case_dir/world/registry-container"
  printf 'vol-cluster|c1|observability-platform\nvol-registry|r1|\n' >"$case_dir/world/volumes"
  printf 'c1\nr1\n' >"$case_dir/world/network-endpoints"
}

run_case() {
  local case_dir=$1 expected=$2 destroy_status=${3:-0}
  set +e
  HOME="$case_dir/home" TF_DIR="$case_dir/tf" \
    CLUSTER_NAME=observability-platform REGISTRY_NAME=k3d-observability-registry \
    KUBECONFIG="$case_dir/home/.kube/config-observability" \
    K3D_BIN="$case_dir/bin/k3d" DOCKER_BIN="$case_dir/bin/docker" \
    KUBECTL_BIN="$case_dir/bin/kubectl" TERRAFORM_BIN="$case_dir/bin/terraform" \
    MAKE_BIN="$case_dir/bin/make" MOCK_WORLD="$case_dir/world" \
    MOCK_LOG="$case_dir/log" MOCK_DESTROY_STATUS="$destroy_status" \
    bash "$helper" >"$case_dir/stdout" 2>"$case_dir/stderr"
  local status=$?
  set -e
  [[ $status == "$expected" ]] || fail "$(basename "$case_dir") status=$status expected=$expected: $(cat "$case_dir/stderr")"
}

assert_no_mutator() {
  local log=$1
  if grep -Eq 'helm-uninstall-all| destroy | state rm |cluster delete|registry delete|volume rm|network rm' "$log"; then
    fail "unexpected mutator in $log"
  fi
}

main() {
  if [[ ! -x $helper ]]; then
    printf 'FAIL: cleanup helper is missing: %s\n' "$helper" >&2
    return 1
  fi
  suite=$(mktemp -d "${TMPDIR:-/tmp}/enten-clean-runtime-test.XXXXXX")
  trap 'rm -rf -- "${suite:-}"' EXIT

  local case_dir
  case_dir="$suite/absent"; make_fakes "$case_dir"; run_case "$case_dir" 0; assert_no_mutator "$case_dir/log"
  run_case "$case_dir" 0; assert_no_mutator "$case_dir/log"

  case_dir="$suite/stale-state"; make_fakes "$case_dir"; printf '{}\n' >"$case_dir/tf/terraform.tfstate"; touch "$case_dir/home/.kube/config-observability"; printf 'null_resource.k3d_cluster\n' >"$case_dir/tf/state-addresses"; run_case "$case_dir" 0; grep -q 'state rm null_resource.k3d_cluster' "$case_dir/log" || fail 'stale state was not reconciled'

  case_dir="$suite/stopped"; make_fakes "$case_dir"; seed_owned_runtime "$case_dir"; printf '{}\n' >"$case_dir/tf/terraform.tfstate"; touch "$case_dir/home/.kube/config-observability"; printf 'null_resource.k3d_cluster\nnull_resource.k3d_registry\n' >"$case_dir/tf/state-addresses"; run_case "$case_dir" 0; ! grep -q 'helm-uninstall-all\| destroy ' "$case_dir/log" || fail 'stopped path used graceful mutators'; grep -q 'cluster delete observability-platform' "$case_dir/log" || fail 'stopped cluster not deleted'; grep -q 'registry delete k3d-observability-registry' "$case_dir/log" || fail 'stopped registry not deleted'

  case_dir="$suite/live-success"; make_fakes "$case_dir"; seed_owned_runtime "$case_dir"; touch "$case_dir/world/api-live" "$case_dir/home/.kube/config-observability"; printf '{}\n' >"$case_dir/tf/terraform.tfstate"; printf 'null_resource.k3d_cluster\n' >"$case_dir/tf/state-addresses"; run_case "$case_dir" 0; grep -q 'make --no-print-directory helm-uninstall-all' "$case_dir/log" || fail 'live path skipped Helm'; grep -q 'terraform .* destroy -auto-approve' "$case_dir/log" || fail 'live path skipped destroy'

  case_dir="$suite/live-destroy-fails"; make_fakes "$case_dir"; seed_owned_runtime "$case_dir"; touch "$case_dir/world/api-live" "$case_dir/home/.kube/config-observability"; printf '{}\n' >"$case_dir/tf/terraform.tfstate"; printf 'null_resource.k3d_cluster\n' >"$case_dir/tf/state-addresses"; run_case "$case_dir" 0 42; grep -q 'cluster delete observability-platform' "$case_dir/log" || fail 'destroy-failure fallback skipped cluster'; grep -q 'state rm null_resource.k3d_cluster' "$case_dir/log" || fail 'destroy-failure state not reconciled'

  case_dir="$suite/no-state"; make_fakes "$case_dir"; seed_owned_runtime "$case_dir"; touch "$case_dir/world/api-live" "$case_dir/home/.kube/config-observability"; run_case "$case_dir" 0; ! grep -q '^terraform ' "$case_dir/log" || fail 'no-state path invoked Terraform'

  case_dir="$suite/unknown-state"; make_fakes "$case_dir"; printf '{}\n' >"$case_dir/tf/terraform.tfstate"; printf 'random_resource.foreign\n' >"$case_dir/tf/state-addresses"; run_case "$case_dir" 1; assert_no_mutator "$case_dir/log"

  case_dir="$suite/wrong-registry"; make_fakes "$case_dir"; touch "$case_dir/world/registry-present"; printf 'r1|k3d-observability-registry|database\n' >"$case_dir/world/registry-container"; run_case "$case_dir" 1; assert_no_mutator "$case_dir/log"

  case_dir="$suite/shared-network"; make_fakes "$case_dir"; seed_owned_runtime "$case_dir"; printf 'c1\nr1\nforeign\n' >"$case_dir/world/network-endpoints"; run_case "$case_dir" 1; assert_no_mutator "$case_dir/log"

  case_dir="$suite/shared-volume"; make_fakes "$case_dir"; seed_owned_runtime "$case_dir"; printf 'vol-cluster|c1|observability-platform\nvol-cluster|foreign|\n' >"$case_dir/world/volumes"; run_case "$case_dir" 1; assert_no_mutator "$case_dir/log"

  case_dir="$suite/lookalikes"; make_fakes "$case_dir"; touch "$case_dir/world/lookalike-cluster" "$case_dir/world/lookalike-registry"; run_case "$case_dir" 0; assert_no_mutator "$case_dir/log"

  if ((failures > 0)); then printf 'cleanup matrix failures: %d\n' "$failures" >&2; return 1; fi
  printf 'cleanup fake matrix passed: 11 scenarios plus idempotent repeat\n'
}

main "$@"
