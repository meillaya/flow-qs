output "kubeconfig_path" {
  description = "Path to the kubeconfig file for the k3d cluster"
  value       = var.kubeconfig_path
}

output "cluster_name" {
  description = "Name of the provisioned k3d cluster"
  value       = var.cluster_name
}

output "registry_url" {
  description = "URL of the local Docker registry reachable from the cluster"
  value       = "${local.registry_host}:${var.registry_port}"
}
