/**
 * k6 stress test: ramp to high VUs to find the breaking point.
 *
 * Profile: 0 -> 100 VUs over 5 minutes, then 100 -> 200 VUs over 5 minutes.
 * Flow: mixed health checks + full checkout flow.
 * Success criteria: HTTP error rate < 10%; no OOM kills observed in cluster.
 */

const http = require("k6/http");
const { sleep } = require("k6");
const { Rate } = require("k6/metrics");

const NS = __ENV.SERVICE_NAMESPACE || "ecom";
const CART_URL = __ENV.CART_URL || `http://cart-service.${NS}:8002`;
const CHECKOUT_URL =
  __ENV.CHECKOUT_URL || `http://checkout-service-checkout-service.${NS}:8003`;

const checkoutSuccessRate = new Rate("checkout_success_rate");

exports.options = {
  stages: [
    { duration: "5m", target: 100 },
    { duration: "5m", target: 200 },
  ],
  thresholds: {
    http_req_failed: ["rate<0.10"],
    checkout_success_rate: ["rate>0.80"],
  },
};

const HEALTH_ENDPOINTS = [
  `http://catalog-service.${NS}:8000/healthz`,
  `http://inventory-service.${NS}:8001/healthz`,
  `http://cart-service.${NS}:8002/healthz`,
  `http://checkout-service-checkout-service.${NS}:8003/healthz`,
  `http://order-service.${NS}:8004/healthz`,
  `http://payment-gateway.${NS}:8005/healthz`,
];

function makeUserId() {
  return `stress-${__VU}-${Date.now()}-${Math.floor(Math.random() * 100000)}`;
}

exports.default = function () {
  // Light health-check probe to ensure all services stay reachable under load.
  const healthUrl =
    HEALTH_ENDPOINTS[Math.floor(Math.random() * HEALTH_ENDPOINTS.length)];
  http.get(healthUrl, { tags: { step: "health" } });

  // Full checkout flow.
  const userId = makeUserId();
  const cartRes = http.post(
    `${CART_URL}/cart/${userId}/items`,
    JSON.stringify({ sku: "SKU-STRESS-001", qty: 1 }),
    {
      headers: { "Content-Type": "application/json" },
      tags: { step: "add_to_cart" },
    }
  );

  const cartOk = cartRes.status === 200;
  if (!cartOk) {
    checkoutSuccessRate.add(0);
    sleep(0.5);
    return;
  }

  const checkoutRes = http.post(
    `${CHECKOUT_URL}/checkout`,
    JSON.stringify({ user_id: userId, payment_method: "card" }),
    {
      headers: { "Content-Type": "application/json" },
      tags: { step: "checkout" },
    }
  );

  const checkoutOk =
    checkoutRes.status === 200 && checkoutRes.json("status") === "paid";
  checkoutSuccessRate.add(checkoutOk ? 1 : 0);

  sleep(0.5);
};
