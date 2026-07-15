terraform {
  required_version = ">= 1.5"
  required_providers {
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}

locals {
  kubeconfig    = pathexpand(var.kubeconfig_path)
  registry_host = "k3d-${var.registry_name}"
}

resource "null_resource" "k3d_registry" {
  triggers = {
    registry_name = var.registry_name
    registry_host = local.registry_host
    k3d_bin       = var.k3d_bin
    k3d_version   = var.k3d_version
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -eu
      actual="$(${var.k3d_bin} version | awk '/^k3d version/{print $3}')"
      test "$actual" = "${var.k3d_version}" || {
        echo "k3d ${var.k3d_version} is required; found $actual" >&2
        exit 1
      }
      if ! ${var.k3d_bin} registry list | grep -q "^${local.registry_host}\\b"; then
        ${var.k3d_bin} registry create ${var.registry_name}
      else
        echo "Registry ${local.registry_host} already exists"
      fi
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      set -eu
      if ${self.triggers.k3d_bin} registry list | grep -q "^${self.triggers.registry_host}\\b"; then
        ${self.triggers.k3d_bin} registry delete ${self.triggers.registry_host}
      fi
    EOT
  }
}

# k3d 5.9.0 runs the pinned K3s v1.35 image. Host access is deliberately
# port-forward-only: there are no k3d load-balancer host mappings here.
resource "null_resource" "k3d_cluster" {
  depends_on = [null_resource.k3d_registry]

  triggers = {
    cluster_name    = var.cluster_name
    k3s_image       = var.k3s_image
    servers         = var.servers
    agents          = var.agents
    server_memory   = var.server_memory
    agent_memory    = var.agent_memory
    registry_name   = var.registry_name
    registry_port   = var.registry_port
    kubeconfig_path = var.kubeconfig_path
    k3d_bin         = var.k3d_bin
    k3d_version     = var.k3d_version
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -eu
      umask 077
      export KUBECONFIG="${local.kubeconfig}"
      mkdir -p "$(dirname "$KUBECONFIG")"

      if ${var.k3d_bin} cluster list | grep -q "^${var.cluster_name}\\b"; then
        server_count="$(${var.k3d_bin} node list --no-headers | awk '$2 == "server" && $3 == "${var.cluster_name}" {count++} END {print count+0}')"
        agent_count="$(${var.k3d_bin} node list --no-headers | awk '$2 == "agent" && $3 == "${var.cluster_name}" {count++} END {print count+0}')"
        test "$server_count" = "${var.servers}" && test "$agent_count" = "${var.agents}" || {
          echo "Existing cluster ${var.cluster_name} has unexpected topology" >&2
          exit 1
        }
      else
        ${var.k3d_bin} cluster create ${var.cluster_name} \
          --image ${var.k3s_image} \
          --servers ${var.servers} \
          --agents ${var.agents} \
          --k3s-arg "--disable=traefik@server:*" \
          --servers-memory ${var.server_memory} \
          --agents-memory ${var.agent_memory} \
          --registry-use ${local.registry_host}:${var.registry_port} \
          --kubeconfig-update-default=false \
          --kubeconfig-switch-context=false \
          --wait
      fi

      ${var.k3d_bin} kubeconfig get ${var.cluster_name} > "$KUBECONFIG"
      chmod 600 "$KUBECONFIG"
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      set -eu
      if ${self.triggers.k3d_bin} cluster list | grep -q "^${self.triggers.cluster_name}\\b"; then
        ${self.triggers.k3d_bin} cluster delete ${self.triggers.cluster_name}
      fi
    EOT
  }
}

resource "null_resource" "namespaces" {
  depends_on = [null_resource.k3d_cluster]

  triggers = {
    namespaces      = join(",", var.namespaces)
    kubeconfig_path = var.kubeconfig_path
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -eu
      export KUBECONFIG="${local.kubeconfig}"
      for ns in ${join(" ", var.namespaces)}; do
        kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
      done
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      set -eu
      export KUBECONFIG="${pathexpand(self.triggers.kubeconfig_path)}"
      for ns in ${join(" ", [for n in split(",", self.triggers.namespaces) : n])}; do
        kubectl delete namespace "$ns" --ignore-not-found=true
      done
    EOT
  }
}

resource "null_resource" "coredns_registry_rewrite" {
  depends_on = [null_resource.namespaces]

  triggers = {
    registry_host   = local.registry_host
    kubeconfig_path = var.kubeconfig_path
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -eu
      export KUBECONFIG="${local.kubeconfig}"
      cat <<'MANIFEST' | kubectl apply -f -
      apiVersion: v1
      kind: ConfigMap
      metadata:
        name: coredns-custom
        namespace: kube-system
      data:
        registry.override: |
          rewrite name regex (.*\.)?${local.registry_host}(\..*)? ${local.registry_host}
      MANIFEST
      kubectl rollout restart deployment coredns -n kube-system
      kubectl rollout status deployment coredns -n kube-system --timeout=120s
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      set -eu
      export KUBECONFIG="${pathexpand(self.triggers.kubeconfig_path)}"
      kubectl delete configmap coredns-custom -n kube-system --ignore-not-found=true
      kubectl rollout restart deployment coredns -n kube-system || true
    EOT
  }
}
