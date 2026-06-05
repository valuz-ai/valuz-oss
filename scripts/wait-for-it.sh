#!/usr/bin/env bash
# wait-for-it.sh - Wait for a host:port to become available
# Simplified version. Usage: ./wait-for-it.sh host:port [-t timeout] [-q]

TIMEOUT=15
QUIET=0
HOST=""
PORT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    *:* )
      HOST=$(echo "$1" | cut -d: -f1)
      PORT=$(echo "$1" | cut -d: -f2)
      shift
      ;;
    -t)
      TIMEOUT="$2"
      shift 2
      ;;
    -q)
      QUIET=1
      shift
      ;;
    *)
      shift
      ;;
  esac
done

if [[ -z "$HOST" || -z "$PORT" ]]; then
  echo "Usage: wait-for-it.sh host:port [-t timeout] [-q]"
  exit 1
fi

START_TS=$(date +%s)
while :; do
  (echo >/dev/tcp/$HOST/$PORT) 2>/dev/null && break
  ELAPSED=$(( $(date +%s) - START_TS ))
  if [[ $ELAPSED -ge $TIMEOUT ]]; then
    echo "Timeout waiting for $HOST:$PORT after ${TIMEOUT}s"
    exit 1
  fi
  sleep 1
done

[[ $QUIET -eq 0 ]] && echo "$HOST:$PORT is available"
exit 0
