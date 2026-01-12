#!/usr/bin/env bash
set -euo pipefail

NS="${NS:-load-shed}"
SVC="${SVC:-load-shed-api}"
PATH_CLIENT="${PATH_CLIENT:-/client}"

WARMUP_N="${WARMUP_N:-200}"
TRIP_N="${TRIP_N:-1200}"
RECOVER_N="${RECOVER_N:-600}"

FAIL_RATE_TRIP="${FAIL_RATE_TRIP:-1.0}"
FAIL_RATE_RECOVER="${FAIL_RATE_RECOVER:-0.0}"

kubectl -n "${NS}" get pods -l app=load-shed-api >/dev/null
kubectl -n "${NS}" get endpointslices -l kubernetes.io/service-name="${SVC}" >/dev/null

kubectl -n "${NS}" run demo-breaker --rm -it --restart=Never --image=curlimages/curl -- sh -lc "
SVC_URL='http://${SVC}${PATH_CLIENT}'

echo 'PROMQL breaker_state: max(circuit_breaker_state{job=\"load-shed-api\"})'
echo 'PROMQL upstream_results_total: sum by (result) (upstream_requests_total{job=\"load-shed-api\"})'
echo 'PROMQL upstream_p95: histogram_quantile(0.95, sum by (le) (rate(upstream_request_duration_seconds_bucket{job=\"load-shed-api\"}[2m])))'
echo 'PROMQL client_rate_by_pod: sum by (pod) (rate(http_requests_total{job=\"load-shed-api\",path=\"/client\"}[2m]))'

echo warmup
for i in \$(seq 1 ${WARMUP_N}); do curl -sS -o /dev/null \"\${SVC_URL}\" || true; done

echo trip
for i in \$(seq 1 ${TRIP_N}); do curl -sS -o /dev/null \"\${SVC_URL}?fail_rate=${FAIL_RATE_TRIP}\" || true; done

echo recover
for i in \$(seq 1 ${RECOVER_N}); do curl -sS -o /dev/null \"\${SVC_URL}?fail_rate=${FAIL_RATE_RECOVER}\" || true; done

echo done
"
