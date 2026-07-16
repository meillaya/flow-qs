#!/usr/bin/env bash
set -euo pipefail

measurement="${1:?usage: verify-network-delay-measurement.sh MEASUREMENT.json}"

jq -e '
  def valid_samples:
    type == "array" and length == 3 and all(.[ ];
      .http_status == 200 and
      .status == "paid" and
      (.trace_id | type == "string" and length == 32) and
      (.time_total_seconds | type == "number" and . > 0)
    );
  def median: map(.time_total_seconds) | sort | .[1];
  .schema == "flow-qs.networkchaos.measurement/v1" and
  .experiment == "network-delay-payment" and
  (.samples.baseline | valid_samples) and
  (.samples.injected | valid_samples) and
  ((.samples.injected | median) - (.samples.baseline | median) >= 1.5)
' "${measurement}" >/dev/null

jq -c '
  def median: map(.time_total_seconds) | sort | .[1];
  (.samples.baseline | median) as $baseline |
  (.samples.injected | median) as $injected |
  {
    schema: "flow-qs.networkchaos.measurement-result/v1",
    baseline_median_seconds: $baseline,
    injected_median_seconds: $injected,
    delta_seconds: ($injected - $baseline),
    passed: (($injected - $baseline) >= 1.5)
  }
' "${measurement}"
