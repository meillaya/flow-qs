terraform {
  required_version = ">= 1.5"
  required_providers {
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.4"
    }
  }
}

locals {
  k3d_bin       = pathexpand("~/.local/bin/k3d")
  k3d_dir       = dirname(local.k3d_bin)
  kubeconfig    = pathexpand(var.kubeconfig_path)
  port_args     = [for p in var.host_ports : "--port ${p.host}:${p.container}@loadbalancer"]
  registry_host = "k3d-${var.registry_name}"
}

# ------------------------------------------------------------------------------
# Local Docker registry attached to the k3d cluster network.
# The registry is created without a fixed host port to avoid conflicts with
# other containers already using 5000/5432/etc. on this host. The cluster
# accesses it by DNS name on port 5000.
# ------------------------------------------------------------------------------
resource "null_resource" "k3d_registry" {
  triggers = {
    registry_name = var.registry_name
    registry_host = local.registry_host
    k3d_bin       = local.k3d_bin
    k3d_dir       = local.k3d_dir
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      export PATH="${local.k3d_dir}:$PATH"
      if ! ${local.k3d_bin} registry list | grep -q "^${local.registry_host}\\b"; then
        ${local.k3d_bin} registry create ${var.registry_name}
      else
        echo "Registry ${local.registry_host} already exists"
      fi
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      set -e
      export PATH="${self.triggers.k3d_dir}:$PATH"
      if ${self.triggers.k3d_bin} registry list | grep -q "^${self.triggers.registry_host}\\b"; then
        ${self.triggers.k3d_bin} registry delete ${self.triggers.registry_host}
      fi
    EOT
  }
}

# ------------------------------------------------------------------------------
# k3d cluster: 1 server (6GiB) + 2 agents (4GiB each), pinned k3s v1.29.x,
# load-balancer port mappings for observability services, and attached registry.
# ------------------------------------------------------------------------------
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
    ports_hash      = join(",", [for p in var.host_ports : "${p.host}:${p.container}"])
    k3d_bin         = local.k3d_bin
    k3d_dir         = local.k3d_dir
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      export PATH="${local.k3d_dir}:$PATH"
      export KUBECONFIG="${local.kubeconfig}"
      mkdir -p "$(dirname "$KUBECONFIG")"

      if ${local.k3d_bin} cluster list | grep -q "^${var.cluster_name}\\b"; then
        echo "Cluster ${var.cluster_name} already exists"
      else
        ${local.k3d_bin} cluster create ${var.cluster_name} \
          --image ${var.k3s_image} \
          --servers ${var.servers} \
          --agents ${var.agents} \
          --servers-memory ${var.server_memory} \
          --agents-memory ${var.agent_memory} \
          ${join(" ", local.port_args)} \
          --registry-use ${local.registry_host}:${var.registry_port} \
          --kubeconfig-update-default=false \
          --kubeconfig-switch-context=false \
          --wait

        ${local.k3d_bin} kubeconfig get ${var.cluster_name} > "$KUBECONFIG"
      fi
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      set -e
      export PATH="${self.triggers.k3d_dir}:$PATH"
      if ${self.triggers.k3d_bin} cluster list | grep -q "^${self.triggers.cluster_name}\\b"; then
        ${self.triggers.k3d_bin} cluster delete ${self.triggers.cluster_name}
      fi
    EOT
  }
}

# ------------------------------------------------------------------------------
# Namespaces required by the platform.
# ------------------------------------------------------------------------------
resource "null_resource" "namespaces" {
  depends_on = [null_resource.k3d_cluster]

  triggers = {
    namespaces      = join(",", var.namespaces)
    kubeconfig_path = var.kubeconfig_path
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      export KUBECONFIG="${local.kubeconfig}"
      for ns in ${join(" ", var.namespaces)}; do
        kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
      done
    EOT
  }


  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      set -e
      export KUBECONFIG="${pathexpand(self.triggers.kubeconfig_path)}"
      for ns in ${join(" ", [for n in split(",", self.triggers.namespaces) : n])}; do
        kubectl delete namespace "$ns" --ignore-not-found=true
      done
    EOT
  }
}

# ------------------------------------------------------------------------------
# CoreDNS rewrite so that the registry name resolves with default search
# domains (e.g. from busybox nslookup) as well as with a trailing dot.
# ------------------------------------------------------------------------------
resource "null_resource" "coredns_registry_rewrite" {
  depends_on = [null_resource.namespaces]

  triggers = {
    registry_host   = local.registry_host
    kubeconfig_path = var.kubeconfig_path
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
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
      set -e
      export KUBECONFIG="${pathexpand(self.triggers.kubeconfig_path)}"
      kubectl delete configmap coredns-custom -n kube-system --ignore-not-found=true
      kubectl rollout restart deployment coredns -n kube-system || true
    EOT
  }
}
