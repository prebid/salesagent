#!/bin/bash
# setup_hooks.sh - Install Git hooks for this workspace
#
# This script copies Git hooks to the appropriate location based on whether
# we're in a worktree or the main repository.

set -e

echo "Installing Git hooks..."

# Determine where to install hooks
if [ -f .git ]; then
    # We're in a worktree - .git is a file pointing to worktree git dir
    WORKTREE_GIT_DIR=$(cat .git | sed 's/gitdir: //')
    HOOKS_DIR="$WORKTREE_GIT_DIR/hooks"
    echo "Detected worktree setup"
    echo "Installing hooks to: $HOOKS_DIR"

    # For worktrees, get the main .git directory (follow commondir)
    if [ -f "$WORKTREE_GIT_DIR/commondir" ]; then
        COMMON_DIR=$(cat "$WORKTREE_GIT_DIR/commondir")
        # commondir is relative to worktree git dir
        SOURCE_HOOKS_DIR="$WORKTREE_GIT_DIR/$COMMON_DIR/hooks"
    else
        SOURCE_HOOKS_DIR="$WORKTREE_GIT_DIR/hooks"
    fi
else
    # We're in the main repository
    HOOKS_DIR=".git/hooks"
    SOURCE_HOOKS_DIR=".git/hooks"
    echo "Detected main repository"
    echo "Installing hooks to: $HOOKS_DIR"
fi

# Ensure hooks directory exists
mkdir -p "$HOOKS_DIR"

echo "Copying hooks from: $SOURCE_HOOKS_DIR"

# List of hooks to install
HOOKS_TO_INSTALL=("pre-commit" "pre-push")

INSTALLED_COUNT=0
for hook in "${HOOKS_TO_INSTALL[@]}"; do
    SOURCE_HOOK="$SOURCE_HOOKS_DIR/$hook"
    TARGET_HOOK="$HOOKS_DIR/$hook"

    if [ -f "$SOURCE_HOOK" ]; then
        if [ "$SOURCE_HOOK" != "$TARGET_HOOK" ]; then
            cp "$SOURCE_HOOK" "$TARGET_HOOK"
            chmod +x "$TARGET_HOOK"
            echo "✓ Installed $hook hook"
            INSTALLED_COUNT=$((INSTALLED_COUNT + 1))
        else
            echo "✓ $hook hook already in place"
            INSTALLED_COUNT=$((INSTALLED_COUNT + 1))
        fi
    else
        echo "⚠️  Warning: $hook hook not found in $SOURCE_HOOKS_DIR"
    fi
done

echo ""
if [ $INSTALLED_COUNT -eq 0 ]; then
    echo "❌ No hooks were installed"
    exit 1
else
    echo "✅ Successfully installed $INSTALLED_COUNT hook(s)"
    echo ""
    echo "Installed hooks:"
    for hook in "${HOOKS_TO_INSTALL[@]}"; do
        if [ -f "$HOOKS_DIR/$hook" ]; then
            echo "  - $hook"
        fi
    done
fi

echo ""
echo "Note: Hooks will run automatically on git commit and git push"
echo "To bypass hooks temporarily, use --no-verify flag"
