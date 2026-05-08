#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT_DIR/tmp/server.pid"
NGROK_PID_FILE="$ROOT_DIR/tmp/ngrok.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "Aucun serveur enregistré."
else
  SERVER_PID="$(cat "$PID_FILE")"
  if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID"
    echo "Serveur arrêté."
  else
    echo "Le processus n'était plus actif."
  fi
  rm -f "$PID_FILE"
fi

if [ -f "$NGROK_PID_FILE" ]; then
  NGROK_PID="$(cat "$NGROK_PID_FILE")"
  if kill -0 "$NGROK_PID" >/dev/null 2>&1; then
    kill "$NGROK_PID" >/dev/null 2>&1 || true
    echo "Ngrok arrêté."
  fi
  rm -f "$NGROK_PID_FILE"
fi
