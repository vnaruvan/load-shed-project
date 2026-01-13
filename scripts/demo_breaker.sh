#!/usr/bin/env bash
set -euo pipefail

NS="${NS:-load-shed}"

echo "== PromQL to validate =="
cat <<'Q'
1) Breaker state:
   max(circuit_breaker_state{job="load-shed-api"})
2) Upstream results (10m totals):
   sum by (result) (increase(upstream_requests_total{job="load-shed-api"}[10m]))
3) Upstream p95:
   histogram_quantile(0.95, sum by (le) (rate(upstream_request_duration_seconds_bucket{job="load-shed-api"}[2m])))
4) /client rate by pod:
   sum by (pod) (rate(http_requests_total{job="load-shed-api",path="/client"}[2m]))
Q
echo

kubectl -n "$NS" run demo --rm -it --restart=Never --image=curlimages/curl -- sh -lc '
set -eu
SVC="http://load-shed-api/client"

echo "A) warmup (healthy)"
seq 1 200 | xargs -n1 -P10 -I{} sh -c "curl -sS -o /dev/null \"$SVC\" || true"

echo "B) trip breaker (fail_rate=1.0)"
seq 1 800 | xargs -n1 -P20 -I{} sh -c "curl -sS -o /dev/null \"$SVC?fail_rate=1.0\" || true"

echo "C) recover (fail_rate=0.0)"
seq 1 400 | xargs -n1 -P10 -I{} sh -c "curl -sS -o /dev/null \"$SVC?fail_rate=0.0\" || true"

echo "done"
'
