/**
 * k6 load test: realistic checkout flow under sustained load.
 *
 * Profile: ramp from 50 to 100 VUs over 5 minutes, then ramp down.
 * Flow: POST /cart/{user_id}/items -> POST /checkout
 * Success criteria: p95 latency < 1s, error rate < 1%, checkout success rate high.
 */

const http = require("k6/http");
const { check, sleep } = require("k6");
const { Rate } = require("k6/metrics");

const NS = __ENV.SERVICE_NAMESPACE || "ecom";
const CART_URL = __ENV.CART_URL || `http://cart-service.${NS}:8002`;
const CHECKOUT_URL =
  __ENV.CHECKOUT_URL || `http://checkout-service-checkout-service.${NS}:8003`;

const checkoutSuccessRate = new Rate("checkout_success_rate");

exports.options = {
  stages: [
    { duration: "2m", target: 50 },
    { duration: "3m", target: 100 },
    { duration: "30s", target: 0 },
  ],
  thresholds: {
    http_req_duration: ["p(95)<1000"],
    http_req_failed: ["rate<0.01"],
    checkout_success_rate: ["rate>0.95"],
  },
};

function makeUserId() {
  return `load-${__VU}-${Date.now()}-${Math.floor(Math.random() * 100000)}`;
}

exports.default = function () {
  const userId = makeUserId();

  // Step 1: add an item to the cart
  const cartRes = http.post(
    `${CART_URL}/cart/${userId}/items`,
    JSON.stringify({ sku: "SKU-LOAD-001", qty: 1 }),
    {
      headers: { "Content-Type": "application/json" },
      tags: { step: "add_to_cart" },
    }
  );

  const cartOk = check(cartRes, {
    "cart add status is 200": (r) => r.status === 200,
  });

  if (!cartOk) {
    checkoutSuccessRate.add(0);
    sleep(1);
    return;
  }

  // Step 2: checkout
  const checkoutRes = http.post(
    `${CHECKOUT_URL}/checkout`,
    JSON.stringify({ user_id: userId, payment_method: "card" }),
    {
      headers: { "Content-Type": "application/json" },
      tags: { step: "checkout" },
    }
  );

  const checkoutOk = check(checkoutRes, {
    "checkout status is 200": (r) => r.status === 200,
    "checkout returned paid status": (r) =>
      r.status === 200 && r.json("status") === "paid",
  });

  checkoutSuccessRate.add(checkoutOk ? 1 : 0);

  sleep(1);
};
