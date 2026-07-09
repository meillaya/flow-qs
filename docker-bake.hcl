variable "REGISTRY" {
  default = "ghcr.io"
}

variable "REPOSITORY" {
  default = "example/enten-v2"
}

variable "SHA" {
  default = "latest"
}

variable "SERVICES" {
  default = [
    "cart-service",
    "catalog-service",
    "checkout-service",
    "inventory-service",
    "order-service",
    "payment-gateway",
    "synthetic-monitor",
  ]
}

group "default" {
  targets = SERVICES
}

target "docker-metadata-action" {
}

target "cart-service" {
  inherits = ["docker-metadata-action"]
  context = "./services/cart-service"
  dockerfile = "Dockerfile"
  tags = ["${REGISTRY}/${REPOSITORY}/cart-service:${SHA}"]
  cache-from = ["type=gha,scope=cart-service"]
  cache-to = ["type=gha,mode=max,scope=cart-service"]
}

target "catalog-service" {
  inherits = ["docker-metadata-action"]
  context = "./services/catalog-service"
  dockerfile = "Dockerfile"
  tags = ["${REGISTRY}/${REPOSITORY}/catalog-service:${SHA}"]
  cache-from = ["type=gha,scope=catalog-service"]
  cache-to = ["type=gha,mode=max,scope=catalog-service"]
}

target "checkout-service" {
  inherits = ["docker-metadata-action"]
  context = "./services/checkout-service"
  dockerfile = "Dockerfile"
  tags = ["${REGISTRY}/${REPOSITORY}/checkout-service:${SHA}"]
  cache-from = ["type=gha,scope=checkout-service"]
  cache-to = ["type=gha,mode=max,scope=checkout-service"]
}

target "inventory-service" {
  inherits = ["docker-metadata-action"]
  context = "./services/inventory-service"
  dockerfile = "Dockerfile"
  tags = ["${REGISTRY}/${REPOSITORY}/inventory-service:${SHA}"]
  cache-from = ["type=gha,scope=inventory-service"]
  cache-to = ["type=gha,mode=max,scope=inventory-service"]
}

target "order-service" {
  inherits = ["docker-metadata-action"]
  context = "./services/order-service"
  dockerfile = "Dockerfile"
  tags = ["${REGISTRY}/${REPOSITORY}/order-service:${SHA}"]
  cache-from = ["type=gha,scope=order-service"]
  cache-to = ["type=gha,mode=max,scope=order-service"]
}

target "payment-gateway" {
  inherits = ["docker-metadata-action"]
  context = "./services/payment-gateway"
  dockerfile = "Dockerfile"
  tags = ["${REGISTRY}/${REPOSITORY}/payment-gateway:${SHA}"]
  cache-from = ["type=gha,scope=payment-gateway"]
  cache-to = ["type=gha,mode=max,scope=payment-gateway"]
}

target "synthetic-monitor" {
  inherits = ["docker-metadata-action"]
  context = "./services/synthetic-monitor"
  dockerfile = "Dockerfile"
  tags = ["${REGISTRY}/${REPOSITORY}/synthetic-monitor:${SHA}"]
  cache-from = ["type=gha,scope=synthetic-monitor"]
  cache-to = ["type=gha,mode=max,scope=synthetic-monitor"]
}
