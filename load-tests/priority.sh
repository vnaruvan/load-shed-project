#!/usr/bin/env bash
set -euo pipefail
BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"; tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
request(){ curl -sS -o /dev/null -w '%{http_code}\n' "$1" || true; }; export -f request
# shellcheck disable=SC2016
seq 1 100 | xargs -P40 -I{} bash -c 'request "$0/client?ms=500&timeout_ms=2000&priority=low"' "$BASE_URL" >"$tmp/low" &
# shellcheck disable=SC2016
seq 1 40 | xargs -P15 -I{} bash -c 'request "$0/client?ms=50&timeout_ms=2000&priority=high"' "$BASE_URL" >"$tmp/high" &
wait
echo "low priority status counts:"; sort "$tmp/low" | uniq -c
echo "high priority status counts:"; sort "$tmp/high" | uniq -c
