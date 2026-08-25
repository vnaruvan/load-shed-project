#!/usr/bin/env bash
set -euo pipefail

NS="${NS:-load-shed}"
echo "== PromQL to validate =="
cat <<'Q'
1) Breaker state:
   max(load_shed_circuit_breaker_state)
2) Upstream outcomes (10m totals):
   sum by (outcome) (increase(load_shed_upstream_requests_total[10m]))
3) Upstream p95:
   histogram_quantile(0.95, sum by (le) (rate(load_shed_upstream_request_duration_seconds_bucket[2m])))
4) /client rate by pod:
   sum by (pod) (rate(load_shed_http_requests_total{route="/client"}[2m]))
Q
echo

# The single-quoted program expands variables inside the demo pod.
# shellcheck disable=SC2016
kubectl -n "$NS" run demo --rm -it --restart=Never --image=curlimages/curl -- sh -lc '
set -eu
SVC="http://load-shed-api/client"
echo "A) warmup"
seq 1 20 | xargs -P5 -I{} sh -c "curl -sS -o /dev/null \"$SVC\" || true"
echo "B) five sequential qualifying failures"
seq 1 5 | xargs -P1 -I{} sh -c "curl -sS -o /dev/null \"$SVC?fail_rate=1.0\" || true"
echo "C) verify short circuit, wait for cooldown, then healthy half-open probe"
curl -sS -o /dev/null -w "%{http_code}\n" "$SVC" || true
sleep 11
curl -sS -o /dev/null -w "%{http_code}\n" "$SVC" || true
'
