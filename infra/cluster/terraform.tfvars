cluster_name  = "observability-platform"
k3s_image     = "rancher/k3s:v1.29.10-k3s1"
servers       = 1
agents        = 2
server_memory = "6g"
agent_memory  = "4g"

registry_name = "observability-registry"
registry_port = 5000

host_ports = [
  { host = 8080, container = 80 },   # Grafana
  { host = 9090, container = 9090 }, # Prometheus
  { host = 9093, container = 9093 }, # Alertmanager
  { host = 3100, container = 3100 }, # Loki
  { host = 3200, container = 3200 }, # Tempo
  { host = 4317, container = 4317 }, # OTLP gRPC
  { host = 4318, container = 4318 }, # OTLP HTTP
]

namespaces = [
  "observability",
  "ecom",
  "chaos-testing",
  "load",
  "incident",
]

kubeconfig_path = "~/.kube/config-observability"
