const http = require("k6/http");
const { sleep } = require("k6");
const { Rate } = require("k6/metrics");

const NS = __ENV.SERVICE_NAMESPACE || "ecom";
const CART_URL = __ENV.CART_URL || `http://cart-service.${NS}:8002`;
const CHECKOUT_URL = __ENV.CHECKOUT_URL ||
  `http://checkout-service.${NS}:8003`;

const checkoutSuccessRate = new Rate("checkout_success_rate");

exports.options = {
  stages: [
    { duration: "5m", target: 100 },
    { duration: "5m", target: 200 },
  ],
  thresholds: {
    http_req_duration: ["p(95)<2000"],
    http_req_failed: ["rate<0.10"],
    checkout_success_rate: ["rate>0.80"],
  },
};

const HEALTH_ENDPOINTS = [
  `http://catalog-service.${NS}:8000/healthz`,
  `http://inventory-service.${NS}:8001/healthz`,
  `http://cart-service.${NS}:8002/healthz`,
  `http://checkout-service.${NS}:8003/healthz`,
  `http://order-service.${NS}:8004/healthz`,
  `http://payment-gateway.${NS}:8005/healthz`,
];

function makeUserId() {
  return `stress-${__VU}-${Date.now()}-${
    Math.floor(Math.random() * 100000)
  }`;
}

exports.default = function () {
  const healthUrl =
    HEALTH_ENDPOINTS[Math.floor(Math.random() * HEALTH_ENDPOINTS.length)];
  http.get(healthUrl, { tags: { step: "health" } });

  if (Math.random() >= 0.01) {
    sleep(0.5);
    return;
  }

  const userId = makeUserId();
  const cartRes = http.post(
    `${CART_URL}/cart/${userId}/items`,
    JSON.stringify({
      sku: `SKU-${String((__ITER + __VU) % 20 + 1).padStart(3, "0")}`,
      qty: 1,
    }),
    {
      headers: { "Content-Type": "application/json" },
      tags: { step: "add_to_cart" },
    },
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
    },
  );

  const checkoutOk = checkoutRes.status === 200 &&
    checkoutRes.json("status") === "paid";
  checkoutSuccessRate.add(checkoutOk ? 1 : 0);

  sleep(0.5);
};
