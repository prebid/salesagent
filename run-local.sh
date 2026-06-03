#!/usr/bin/env bash
# Run the salesagent locally against the production DB (via SSH tunnel on port 5432).
# Usage: ./run-local.sh
# Requires: SSH tunnel to be active on localhost:5432

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Kill any existing instance
pkill -f "scripts/run_server.py" 2>/dev/null && echo "Stopped previous instance" || true
sleep 1

exec uv run --env-file .env python scripts/run_server.py