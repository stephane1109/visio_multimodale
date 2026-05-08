#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT_DIR/tmp/server.pid"
LOG_FILE="$ROOT_DIR/tmp/server.log"

mkdir -p "$ROOT_DIR/tmp"

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

if [ -f "$PID_FILE" ]; then
  EXISTING_PID="$(cat "$PID_FILE")"
  if kill -0 "$EXISTING_PID" >/dev/null 2>&1; then
    kill "$EXISTING_PID" >/dev/null 2>&1 || true
    sleep 1
  fi
  rm -f "$PID_FILE"
fi

PORT_PID="$(lsof -ti tcp:8000 -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$PORT_PID" ]; then
  echo "Le port 8000 est déjà utilisé par un autre processus (PID $PORT_PID)."
  echo "Fermez ce processus puis relancez Lancer.command."
  exit 1
fi

cd "$ROOT_DIR"
nohup "$PYTHON_BIN" app/server.py serve --host 127.0.0.1 --port 8000 >"$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" >"$PID_FILE"

sleep 2
open "http://127.0.0.1:8000/admin.html"
echo "Serveur démarré. Tableau de bord ouvert dans le navigateur."
