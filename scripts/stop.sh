#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PF_DIR="$ROOT/.pf"

kill_pidfile() {
  local file="$1"
  local pid
  [[ -f "$file" ]] || return 0
  pid="$(<"$file")"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
  fi
  rm -f "$file"
}

kill_pidfile "$PF_DIR/api.pid"
kill_pidfile "$PF_DIR/grafana.pid"
kill_pidfile "$PF_DIR/prom.pid"
echo "stopped recorded port-forwards"
