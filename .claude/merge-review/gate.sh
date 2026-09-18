#!/usr/bin/env bash
# Coherence gate for the staged RFC 9421 semantic merge.
#
# Layers run cheapest-to-slowest and STOP at the first failure, per the semantic-merge
# skill. The layer CEILING rises as the tree becomes runnable:
#
#   stage 1 : markers, parse, no-retired-contract           (the tree does not import yet)
#   stage 2+: + ruff, + egress/boundary configs
#   stage 3+: + mypy, + full collection
#   stage 4+: + unit suite
#   stage 5+: + integration/bdd, + node-id comparison vs BOTH parents
#
# Usage: gate.sh <max-layer> [path-scope-file]
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2
MAX=${1:-9}
SCOPE=${2:-}
fail() { echo "GATE FAIL (layer $1): $2"; exit 1; }

files() {
  if [ -n "$SCOPE" ]; then cat "$SCOPE"; else git diff --name-only HEAD; fi
}

# ---- layer 0: no conflict markers anywhere in the scope -----------------------
echo "== layer 0: conflict markers =="
# A git marker is EXACTLY seven characters: '<<<<<<< ' and '>>>>>>> ' are always followed by
# a label, and '=======' is alone on its line. The looser '^=======' matched an RST table
# border (a run of '=' longer than seven) in a docstring and reported a resolved file as
# conflicted. Both controls below are mandatory: the first proves the pattern still catches a
# real marker, the second proves it no longer catches a table border. A checker that silently
# matches nothing, or matches everything, is the failure this gate exists to catch.
MARKER='^(<<<<<<< |>>>>>>> |=======$)'
printf '<<<<<<< HEAD\n=======\n>>>>>>> branch\n' > /tmp/gate_ctl_marker.txt
printf 'col  col\n===========================\nx    y\n' > /tmp/gate_ctl_rst.txt
grep -qE "$MARKER" /tmp/gate_ctl_marker.txt || fail 0 "marker pattern misses a real marker — the check is broken"
grep -qE "$MARKER" /tmp/gate_ctl_rst.txt && fail 0 "marker pattern matches an RST table border — false positives"
found=$(files | while read -r f; do [ -f "$f" ] && grep -lE "$MARKER" "$f" 2>/dev/null; done)
[ -n "$found" ] && fail 0 "conflict markers remain:\n$found"
echo "   ok (controls: catches a marker, ignores a table border)"
[ "$MAX" -lt 1 ] && exit 0

# ---- layer 1: every changed python file parses -------------------------------
echo "== layer 1: parse =="
bad=$(files | grep '\.py$' | while read -r f; do [ -f "$f" ] && { .venv/bin/python -m py_compile "$f" 2>/dev/null || echo "$f"; }; done)
[ -n "$bad" ] && fail 1 "files do not parse:\n$bad"
echo "   ok"
[ "$MAX" -lt 2 ] && exit 0

# ---- layer 2: the retired error contract is not resurrected -------------------
echo "== layer 2: retired contract =="
# Delegated to a Python scanner (.claude/merge-review/retired_scan.py) that distinguishes USE
# from MENTION with an AST docstring pass and runs a two-directional control on every
# invocation. Two shell versions of this check were silently broken -- one anchored its
# comment filter at ^ while grep emits `path:lineno:content`, the other tripped over backtick
# quoting -- and a check whose failure mode is "passes by doing nothing" is the one thing this
# gate exists to prevent.
if [ -n "$SCOPE" ]; then
  .venv/bin/python .claude/merge-review/retired_scan.py "$SCOPE" || fail 2 "retired contract in scope"
  # ALL of src/ + scripts/, not just the changed files: a file restored to HEAD is absent
  # from the diff, and HEAD is exactly where a stage-0 latent bug would be hiding.
  git ls-files 'src/*.py' 'scripts/*.py' > /tmp/gate_src_scope.txt
  debt=$(.venv/bin/python .claude/merge-review/retired_scan.py /tmp/gate_src_scope.txt | grep -c '^src/\|^scripts/' || true)
  echo "   whole-tree debt still owed by later stages: $debt lines"
else
  git ls-files 'src/*.py' 'scripts/*.py' > /tmp/gate_src_scope.txt
  .venv/bin/python .claude/merge-review/retired_scan.py /tmp/gate_src_scope.txt || fail 2 "retired contract in src/"
fi
[ "$MAX" -lt 3 ] && exit 0

# ---- layer 3: lint + format ---------------------------------------------------
# Scoped like layer 2: a stage cannot be held to the whole tree while later stages are
# still unresolved. With no scope this is the whole-tree check, which is the final run.
echo "== layer 3: ruff =="
if [ -n "$SCOPE" ]; then
  TARGETS=$(while read -r f; do [ -f "$f" ] && [[ "$f" == *.py ]] && echo "$f"; done < "$SCOPE")
  [ -z "$TARGETS" ] && { echo "   (no python files in scope)"; [ "$MAX" -lt 4 ] && exit 0; }
else
  TARGETS="src/ tests/ scripts/"
fi
# shellcheck disable=SC2086
.venv/bin/python -m ruff check $TARGETS || fail 3 "ruff check"
# shellcheck disable=SC2086
.venv/bin/python -m ruff format --check $TARGETS >/dev/null || fail 3 "ruff format"
for cfg in ruff-egress ruff-boundary ruff-ownership ruff-serialization ruff-environment; do
  if [ -f "$cfg.toml" ]; then
    SRCT=$(echo "$TARGETS" | tr ' ' '\n' | grep -E '^(src/|scripts/)' | tr '\n' ' ')
    [ -n "$SRCT" ] || continue
    # shellcheck disable=SC2086
    .venv/bin/python -m ruff check --config "$cfg.toml" --ignore-noqa --no-respect-gitignore $SRCT || fail 3 "$cfg"
  fi
done
echo "   ok"
[ "$MAX" -lt 4 ] && exit 0

# ---- layer 4: types -----------------------------------------------------------
echo "== layer 4: mypy =="
# THE THIRD SILENTLY-BROKEN LAYER IN THIS GATE, and the subtlest. It was
#   mypy ... 2>&1 | grep -E '^[^ ].*error:' && fail 4 "mypy"
# which reads correctly and is wrong under `set -o pipefail`: when mypy finds errors it
# EXITS 1, pipefail gives the whole pipeline that status regardless of grep's success, the
# `&&` therefore never fires, and the layer prints "ok" having just printed the errors. It
# passed vacuously for exactly the input it exists to catch. Capture first, test the capture.
mypy_out=$(.venv/bin/python -m mypy src/ --config-file=mypy.ini 2>&1 || true)
mypy_errs=$(printf '%s\n' "$mypy_out" | grep -cE '^[^ ].*error:' || true)
# Control, mandatory: prove the matcher still recognizes a real mypy error line. A counter
# that can only ever report 0 is the failure mode this whole gate exists to prevent.
printf '%s\n' 'src/x.py:1:1: error: Name "y" is not defined  [name-defined]' \
  | grep -qE '^[^ ].*error:' || fail 4 "mypy matcher no longer recognizes an error line — the check is vacuous"
[ "$mypy_errs" -gt 0 ] && { printf '%s\n' "$mypy_out" | grep -E '^[^ ].*error:'; fail 4 "mypy: $mypy_errs errors"; }
echo "   ok (control: matcher recognizes an error line; 0 errors)"
[ "$MAX" -lt 5 ] && exit 0

# ---- layer 4b: function-local imports resolve ---------------------------------
# WHOLE TREE, always, regardless of scope. This is the one layer that cannot be scoped to
# the diff: it catches a file REFERENCING a symbol the merge removed, and such a file may be
# one NEITHER side edited -- so no surface computed from changed files can contain it. Three
# shipped during this merge (AdCPError in tests/harness/_base.py, list_tasks in
# tests/harness/task_management.py and tests/bdd/steps/domain/uc002_task_query.py), each a
# function-local import that fails at CALL time rather than at import, and each invisible to
# every other layer here including collection.
echo "== layer 4b: function-local imports resolve =="
.venv/bin/python -m pytest -c pytest.ini tests/unit/test_architecture_function_local_imports_resolve.py \
  -q -p no:randomly -o addopts="" >/tmp/gate_fli.txt 2>&1 || { cat /tmp/gate_fli.txt; fail 4 "function-local imports"; }
echo "   ok ($(grep -oE '[0-9]+ passed' /tmp/gate_fli.txt | head -1))"
[ "$MAX" -lt 5 ] && exit 0

# ---- layer 5: import-all / collection -----------------------------------------
echo "== layer 5: collection =="
for suite in unit integration bdd e2e admin; do
  n=$(.venv/bin/python -m pytest "tests/$suite" --collect-only -q -o addopts="" -p no:randomly 2>/dev/null | grep -c '::')
  echo "   $suite: $n"
  # A suite reporting 0 collected did NOT run — it is a failure, never a pass.
  [ "$n" -eq 0 ] && fail 5 "$suite collected 0 — collection error, not an empty suite"
done
echo "   ok"
[ "$MAX" -lt 6 ] && exit 0

# ---- layer 6: unit behaviour --------------------------------------------------
echo "== layer 6: unit =="
.venv/bin/python -m pytest tests/unit -q -p no:randomly || fail 6 "unit suite"
echo "   ok"
