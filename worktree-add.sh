#!/bin/bash
# worktree-add.sh — Create a git worktree with shared local state
#
# Usage:
#   ./worktree-add.sh <directory> <branch> [base-ref]
#
# Examples:
#   ./worktree-add.sh ../salesagent-schemas chore/schemas-split
#   ./worktree-add.sh ../salesagent-review review/temp main
#   ./worktree-add.sh ../salesagent-main main  # existing branch (no -b)
#
# What it does:
#   1. Creates the worktree (new branch from base-ref, or checks out existing)
#   2. Symlinks .beads/ for shared task tracking
#   3. Symlinks untracked .claude/ dirs (research, settings, agent-memory)
#   4. Runs uv sync if uv is available

set -euo pipefail

MAIN_REPO="$(cd "$(dirname "$0")" && pwd)"

if [ $# -lt 2 ]; then
    echo "Usage: $0 <directory> <branch> [base-ref]"
    echo ""
    echo "Examples:"
    echo "  $0 ../salesagent-schemas chore/schemas-split"
    echo "  $0 ../salesagent-main main"
    exit 1
fi

TARGET="$1"
BRANCH="$2"
BASE="${3:-main}"

# Check if branch already exists
if git rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
    echo "Branch '$BRANCH' exists, checking out..."
    git worktree add "$TARGET" "$BRANCH"
else
    echo "Creating branch '$BRANCH' from '$BASE'..."
    git worktree add "$TARGET" -b "$BRANCH" "$BASE"
fi

# Resolve to absolute path
TARGET="$(cd "$TARGET" && pwd)"

# Symlink .beads/ for shared task tracking
if [ -d "$MAIN_REPO/.beads" ]; then
    rm -rf "$TARGET/.beads"
    ln -sf "$MAIN_REPO/.beads" "$TARGET/.beads"
    echo "Linked .beads/"
fi

# Symlink untracked .claude/ local state
# (tracked .claude/ files like rules/workflows come from git automatically)
mkdir -p "$TARGET/.claude"
for item in research settings.local.json agent-memory; do
    if [ -e "$MAIN_REPO/.claude/$item" ]; then
        rm -rf "$TARGET/.claude/$item"
        ln -sf "$MAIN_REPO/.claude/$item" "$TARGET/.claude/$item"
        echo "Linked .claude/$item"
    fi
done

# Copy .env with unique CONDUCTOR_PORT for independent docker-compose up/down
if [ -f "$MAIN_REPO/.env" ]; then
    cp "$MAIN_REPO/.env" "$TARGET/.env"
    # Find a free port for this worktree's docker-compose stack
    CONDUCTOR_PORT=$(python3 -c "
import socket
for p in range(8001, 8100):
    try:
        s = socket.socket()
        s.bind(('127.0.0.1', p))
        s.close()
        print(p)
        break
    except OSError:
        s.close()
")
    if [ -n "$CONDUCTOR_PORT" ]; then
        # Append or replace CONDUCTOR_PORT in the worktree .env
        if grep -q '^CONDUCTOR_PORT=' "$TARGET/.env" 2>/dev/null; then
            sed -i '' "s/^CONDUCTOR_PORT=.*/CONDUCTOR_PORT=$CONDUCTOR_PORT/" "$TARGET/.env"
        else
            echo "CONDUCTOR_PORT=$CONDUCTOR_PORT" >> "$TARGET/.env"
        fi
        echo "Copied .env with CONDUCTOR_PORT=$CONDUCTOR_PORT"
    else
        echo "WARNING: Could not find free port in 8001-8100 for CONDUCTOR_PORT" >&2
        echo "Copied .env without CONDUCTOR_PORT (will use default 8000)"
    fi
else
    echo "No .env found in main repo — worktree will need manual .env setup"
fi

# Install dependencies if uv is available
if command -v uv >/dev/null 2>&1; then
    echo "Running uv sync..."
    (cd "$TARGET" && uv sync --quiet)
    echo "Dependencies installed."
fi

echo ""
echo "Worktree ready:"
echo "  Path:   $TARGET"
echo "  Branch: $BRANCH"
if [ -n "${CONDUCTOR_PORT:-}" ]; then
    echo "  Port:   $CONDUCTOR_PORT (docker compose → http://localhost:$CONDUCTOR_PORT)"
fi
echo ""
echo "To use:  cd $TARGET"
echo "To test: cd $TARGET && docker compose up -d"
echo "To remove: git worktree remove $TARGET"
