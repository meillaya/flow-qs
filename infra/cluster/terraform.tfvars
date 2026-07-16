cluster_name  = "flow-qs"
k3s_image     = "rancher/k3s:v1.35.6-k3s1"
servers       = 1
agents        = 2
server_memory = "6g"
agent_memory  = "4g"

registry_name = "observability-registry"
registry_port = 5000

namespaces = [
  "observability",
  "ecom",
  "chaos-testing",
  "load",
  "incident",
]

kubeconfig_path = "~/.kube/config-observability"
