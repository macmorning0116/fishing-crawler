#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-/var/log/fishing-crawler}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/crawler.log}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python3}"

mkdir -p "$LOG_DIR"

run_board() {
  local board_key="$1"

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] incremental 시작 board=${board_key}" >> "$LOG_FILE"

  "$PYTHON_BIN" -m crawler.cli \
    --mode incremental \
    --board-key "$board_key" \
    --max-pages 5 \
    --stop-after-existing-streak 10 \
    --stop-after-existing-ratio 0.8 \
    --store-postgres \
    --store-opensearch \
    --send-email-report \
    --browser-channel chrome \
    --headless \
    --log-level INFO >> "$LOG_FILE" 2>&1

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] incremental 종료 board=${board_key}" >> "$LOG_FILE"
}

cd "$ROOT_DIR"

run_board "bass_walking"
sleep 30
run_board "bass_boating"
sleep 30
run_board "freshwater_guest"
