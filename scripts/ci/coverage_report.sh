#!/usr/bin/env bash
# Coverage report gate — SINGLE SOURCE for --fail-under from .coverage-baseline.
# Used by tox -e coverage and CI Coverage job (same threshold as local full runs).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

fail_under="$(tr -d '[:space:]' < .coverage-baseline)"
args=(uv run coverage report --fail-under="${fail_under}")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-file)
      args+=(--data-file="$2")
      shift 2
      ;;
    *)
      echo "Usage: $0 [--data-file PATH]" >&2
      exit 1
      ;;
  esac
done

exec "${args[@]}"
