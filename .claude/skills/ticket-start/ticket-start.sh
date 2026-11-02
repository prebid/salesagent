#!/bin/bash
# Create an isolated worktree for one or more GitHub tickets.
#
#   .claude/skills/ticket-start/ticket-start.sh 1776 1781
#
# Branches off freshly-fetched origin/main (NOT local main, which is stale in
# every worktree here), creates a sibling worktree, and gives it its own beads
# database so parallel agents cannot corrupt the shared one.
#
# Prints a `cd` line on stdout; everything else goes to stderr, so this is
# safe to wrap:  cd "$(ticket-start.sh 1776)"

set -eo pipefail

say() { printf '%s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ $# -ge 1 ] || die "usage: ticket-start.sh <issue-number> [issue-number ...]"
command -v gh >/dev/null || die "gh CLI not found"
command -v sqlite3 >/dev/null || die "sqlite3 not found (needed for a WAL-safe beads seed)"

# This script lives at <worktree>/.claude/skills/ticket-start/ — three levels up.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$SRC_ROOT"

# The CANONICAL checkout — the parent of the shared .git dir — not whichever
# worktree this happens to be invoked from. Seeding beads from a sibling
# worktree would copy that worktree's isolated (or empty) DB instead of the
# shared one, and the mirror-back command would point at the wrong place.
MAIN_ROOT="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"

REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || echo prebid/salesagent)"

# ── Validate every ticket BEFORE creating anything ────────────────────────────
TITLES=()
for n in "$@"; do
  [[ "$n" =~ ^[0-9]+$ ]] || die "'$n' is not an issue number"
  t="$(gh issue view "$n" --repo "$REPO" --json title --jq .title 2>/dev/null)" \
    || die "issue #$n not found in $REPO"
  TITLES+=("#$n $t")
  say "  #$n  $t"
done

# ── Branch name: slug of the FIRST ticket, so it reads in a PR list ───────────
PRIMARY="$1"
SLUG="$(gh issue view "$PRIMARY" --repo "$REPO" --json title --jq .title \
        | tr '[:upper:]' '[:lower:]' \
        | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//' \
        | cut -c1-48 | sed -E 's/-+$//')"
BRANCH="fix/${PRIMARY}-${SLUG}"
WT="$(dirname "$SRC_ROOT")/salesagent-${PRIMARY}"

git show-ref --verify --quiet "refs/heads/$BRANCH" && die "branch $BRANCH already exists"
[ -e "$WT" ] && die "$WT already exists — pick another ticket or remove it yourself (never scripted)"

say ""
say "Fetching origin/main…"
git fetch origin main >/dev/null 2>&1 || die "git fetch failed"
BASE="$(git rev-parse --short origin/main)"

git worktree add -b "$BRANCH" "$WT" origin/main >/dev/null 2>&1 \
  || die "git worktree add failed"
say "Worktree  $WT"
say "Branch    $BRANCH  (off origin/main @ $BASE)"

# ── Beads isolation ───────────────────────────────────────────────────────────
# bd resolves its DB via git-common-dir, so WITHOUT this every worktree writes
# the shared main database and parallel agents clobber each other.
SHARED_DB="$MAIN_ROOT/.beads/beads.db"
mkdir -p "$WT/.beads-local"
if [ -f "$SHARED_DB" ]; then
  # .backup, not cp: the shared DB is usually live with a WAL sidecar, and a
  # plain copy can capture a torn page.
  sqlite3 "$SHARED_DB" ".backup '$WT/.beads-local/beads.db'" \
    || die "failed to seed the local beads DB"
  say "Beads     seeded from $SHARED_DB (sqlite .backup)"
else
  say "Beads     no shared DB found — starting empty"
fi

MIRROR="cd $MAIN_ROOT && env -u BEADS_DB bd import -i /tmp/beads-mirror-${PRIMARY}.jsonl"

cat > "$WT/.envrc" <<ENVRC
# Per-worktree beads DB. bd otherwise resolves via git-common-dir and would
# mutate the SHARED database that every other worktree is also using.
export BEADS_DB="\$PWD/.beads-local/beads.db"

# ⚠ ISOLATED, NOT DISPOSABLE. Tickets created or closed here exist ONLY in this
# file until you mirror them back. This has already cost real work: a full
# deferral record survived only because it was noticed by hand before teardown.
#
# Mirror before you finish (ids: whatever you created/closed here):
#   bd export --id "<ids>" -o /tmp/beads-mirror-${PRIMARY}.jsonl
#   $MIRROR
#
# 'env -u BEADS_DB' is load-bearing — cd alone does NOT escape this export.
ENVRC

printf '%s\n' "${TITLES[@]}" > "$WT/TICKETS"
say "Tickets   $WT/TICKETS"

if command -v direnv >/dev/null; then
  (cd "$WT" && direnv allow) >/dev/null 2>&1 || say "  (run 'direnv allow' in the worktree)"
else
  say "  direnv not installed — export BEADS_DB manually, or source .envrc"
fi

say ""
say "Next:  cd $WT  &&  /prime"
printf '%s\n' "$WT"
