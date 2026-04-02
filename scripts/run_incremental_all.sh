#!/usr/bin/env bash

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${LOG_DIR:-/var/log/fishing-crawler}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/crawler.log}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python3}"
TMP_DIR="$(mktemp -d)"
HOSTNAME_VALUE="$(hostname)"
FAILED=0
BOARD_RESULTS=()

mkdir -p "$LOG_DIR"

cleanup() {
  rm -rf "$TMP_DIR"
}

trap cleanup EXIT

board_name_for_key() {
  case "$1" in
    bass_walking) echo "배스 조행기(워킹조행)" ;;
    bass_boating) echo "배스 조행기(보팅조행)" ;;
    freshwater_guest) echo "민물 조행기(손님고기)" ;;
    *) echo "$1" ;;
  esac
}

append_result() {
  BOARD_RESULTS+=("$1")
}

run_board() {
  local board_key="$1"
  local board_name
  local board_output
  local status
  local summary
  local error_tail

  board_name="$(board_name_for_key "$board_key")"
  board_output="$TMP_DIR/${board_key}.log"

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] incremental 시작 board=${board_key}" >> "$LOG_FILE"

  "$PYTHON_BIN" -m crawler.cli \
    --mode incremental \
    --board-key "$board_key" \
    --max-pages 5 \
    --stop-after-existing-streak 10 \
    --stop-after-existing-ratio 0.8 \
    --store-postgres \
    --store-opensearch \
    --browser-channel chrome \
    --headless \
    --log-level INFO 2>&1 | tee -a "$LOG_FILE" > "$board_output"

  status=${PIPESTATUS[0]}

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] incremental 종료 board=${board_key} status=${status}" >> "$LOG_FILE"

  if [ "$status" -eq 0 ]; then
    summary="$(awk '
      /^실행 결과:/ {capture=1}
      capture {print}
    ' "$board_output")"

    append_result "$(printf '[성공] %s (%s)\n%s' "$board_name" "$board_key" "${summary:-실행 결과 요약 없음}")"
  else
    FAILED=1
    error_tail="$(tail -n 20 "$board_output")"
    append_result "$(printf '[실패] %s (%s)\n종료 코드: %s\n최근 로그:\n%s' "$board_name" "$board_key" "$status" "$error_tail")"
  fi
}

send_batch_report() {
  local subject
  local body
  local separator
  local item
  local status_label="성공"

  if [ "$FAILED" -ne 0 ]; then
    status_label="부분 실패"
  fi

  subject="[크롤러 배치 ${status_label}] incremental 전체 게시판"
  body="$(printf '호스트: %s\n실행 시각: %s\n배치 유형: incremental 전체 게시판\n\n' \
    "$HOSTNAME_VALUE" \
    "$(date '+%Y-%m-%d %H:%M:%S %Z')")"

  separator=""
  for item in "${BOARD_RESULTS[@]}"; do
    body+="${separator}${item}"
    separator=$'\n\n----------------------------------------\n\n'
  done

  SUBJECT="$subject" BODY="$body" "$PYTHON_BIN" - <<'PY'
import os
from crawler.notifications import send_email_report

send_email_report(os.environ["SUBJECT"], os.environ["BODY"])
PY
}

cd "$ROOT_DIR"

run_board "bass_walking"
sleep 30
run_board "bass_boating"
sleep 30
run_board "freshwater_guest"

send_batch_report

if [ "$FAILED" -ne 0 ]; then
  exit 1
fi
