#!/bin/bash
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$ROOT_DIR/stop_mac.sh"
"$ROOT_DIR/stop_mac.sh"
