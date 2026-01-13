#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PF_DIR="$ROOT/.pf"

kill_pidfile() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  local pid
  pid="$(cat "$f" || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
  fi
  rm -f "$f"
}

kill_pidfile "$PF_DIR/api.pid"
kill_pidfile "$PF_DIR/grafana.pid"
kill_pidfile "$PF_DIR/prom.pid"

echo "stopped port-forwards"
