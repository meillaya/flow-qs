variable "cluster_name" {
  description = "Name of the k3d cluster"
  type        = string
  default     = "observability-platform"
}

variable "k3s_image" {
  description = "Pinned k3s image tag"
  type        = string
  default     = "rancher/k3s:v1.35.6-k3s1"
}

variable "k3d_bin" {
  description = "k3d executable resolved from PATH by default"
  type        = string
  default     = "k3d"
}

variable "k3d_version" {
  description = "Exact k3d CLI version required by this stack"
  type        = string
  default     = "v5.9.0"
}

variable "servers" {
  description = "Number of server nodes"
  type        = number
  default     = 1
}

variable "agents" {
  description = "Number of agent nodes"
  type        = number
  default     = 2
}

variable "server_memory" {
  description = "Memory limit for server nodes (e.g. 6g)"
  type        = string
  default     = "6g"
}

variable "agent_memory" {
  description = "Memory limit for agent nodes (e.g. 4g)"
  type        = string
  default     = "4g"
}

variable "registry_name" {
  description = "Base name for the k3d-managed Docker registry (will be prefixed with 'k3d-')"
  type        = string
  default     = "observability-registry"
}

variable "registry_port" {
  description = "Port on which the registry listens inside the cluster network"
  type        = number
  default     = 5000
}

variable "namespaces" {
  description = "Kubernetes namespaces to create"
  type        = list(string)
  default = [
    "observability",
    "ecom",
    "chaos-testing",
    "load",
    "incident",
  ]
}

variable "kubeconfig_path" {
  description = "Path to the generated kubeconfig file"
  type        = string
  default     = "~/.kube/config-observability"
}
