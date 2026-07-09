/**
 * k6 smoke test: minimal health-check validation for every service.
 *
 * Profile: 1 VU for 30 seconds, one iteration per service health endpoint.
 * Success criteria: every /healthz returns HTTP 200.
 */

const http = require("k6/http");
const { check, sleep } = require("k6");

const NS = __ENV.SERVICE_NAMESPACE || "ecom";

const SERVICES = [
  { name: "catalog-service", url: `http://catalog-service.${NS}:8000/healthz` },
  { name: "inventory-service", url: `http://inventory-service.${NS}:8001/healthz` },
  { name: "cart-service", url: `http://cart-service.${NS}:8002/healthz` },
  { name: "checkout-service", url: `http://checkout-service-checkout-service.${NS}:8003/healthz` },
  { name: "order-service", url: `http://order-service.${NS}:8004/healthz` },
  { name: "payment-gateway", url: `http://payment-gateway.${NS}:8005/healthz` },
];

exports.options = {
  vus: 1,
  duration: "30s",
  thresholds: {
    http_req_duration: ["p(95)<100"],
    http_req_failed: ["rate<0.01"],
    checks: ["rate==1"],
  },
};

exports.default = function () {
  for (const svc of SERVICES) {
    const res = http.get(svc.url, { tags: { service: svc.name } });
    check(res, {
      [`${svc.name} status is 200`]: (r) => r.status === 200,
      [`${svc.name} response is healthy`]: (r) =>
        r.status === 200 && /healthy|ok/.test(r.body),
    });
  }
  sleep(1);
};
