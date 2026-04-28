# Fix Plan — Issue #1232

> `./run_all_tests.sh quick` exits 1 even when tests pass, breaking the worktree pre-push hook.

**Status:** AUDITED — ready for implementation pending user approval.

**Revision history:**
- v1: initial plan
- v2: revised after independent audit + empirical prototype verification

---

## 1. Verified root cause

`run_all_tests.sh:48` inside `collect_reports()`:

```bash
for name in unit integration e2e admin bdd ui; do
    [ -f ".tox/${name}.json" ] && cp ".tox/${name}.json" "$RESULTS_DIR/"
done
```

When the last loop iteration's `[ -f ]` returns 1 (file missing), the `&&` short-circuits, the compound returns 1, the `for` loop inherits that as its exit status, and the function returns 1. With `set -eo pipefail` active, the standalone call at line 62 trips `set -e` and the script aborts before reaching the `[ "$TOX_RC" -ne 0 ]` check, the security audit, or the summary.

In quick mode only `unit` and `integration` JSONs are produced, so iterations 3-6 always fail the `[ -f ]` test, and the LAST iteration's failure is what counts.

**Bash-version coverage:** reproduced on bash 3.2 (macOS `/bin/bash`) and bash 5 (Linux/CI). Behavior is consistent across both.

**Trigger frequency:** 100% of pushes from a Conductor worktree, because `scripts/setup/setup_conductor_workspace.sh:157-199` installs a pre-push hook that runs `./run_all_tests.sh quick` and aborts on non-zero exit. Worktree pre-push hooks invoke the script from the working tree (not a baked copy), so fixing `run_all_tests.sh` fixes ALL existing AND future worktrees.

**Vintage:** bug pre-dates PR #1176 (which added `ui` to the loop). Has existed since the function was introduced (PR #1107, March 2026).

---

## 2. Scope decision

| Site | Severity | In scope? | Reasoning |
|------|----------|-----------|-----------|
| Line 48 (`collect_reports`) | **Active** — fires every quick-mode run | **YES** | The reported bug. |
| Line 124 (summary block) | **Latent** — fires only when tox crashes before writing any reports | **YES — refactor into `print_summary()` function** | Same anti-pattern, same code path. Refactoring to a function (a) eliminates the latent bug, (b) makes the line testable, (c) tightens the boundary between "produce summary" and "decide exit status". Net change: extract 5 inline lines into a 12-line function. Small, justified by testability. |
| Line 26 (`ls test-results/*/ \| tail \| xargs rm -rf`) | **Latent** — masked by line 23's `mkdir -p` | **NO** | Different code path (cleanup of stale result dirs at script start). Currently safe in practice. Fixing it is "cleanup beyond what the task requires" per CLAUDE.md scope discipline. |
| Line 15 (`[ -f .env ] && { ...; }`) | **REVIEWED-SAFE** | **NO** | Same syntactic pattern but the compound `{ set -a; source .env; set +a; }` always returns 0 when entered (last command is `set +a`). When the LHS condition fails, the `&&` short-circuit puts this in the "command in `&&` list except final" category — set -e is documented to ignore this. Verified empirically. No fix needed. |
| Architectural rewrite (drop `set -e`, lean on `FAILURES`) | Design fragility | **NO** | Out of scope for a bug fix. Big blast radius. |

---

## 3. Fix design

### 3.1 Line 48 — convert `&&` chain to `if/fi`

```diff
@@ run_all_tests.sh:44-50 @@
 collect_reports() {
     # Copy JSON reports from .tox/ to results dir
     mkdir -p "$RESULTS_DIR"
     for name in unit integration e2e admin bdd ui; do
-        [ -f ".tox/${name}.json" ] && cp ".tox/${name}.json" "$RESULTS_DIR/"
+        if [ -f ".tox/${name}.json" ]; then
+            cp ".tox/${name}.json" "$RESULTS_DIR/"
+        fi
     done
 }
```

**Why this form:** `if [ -f X ]; then cp; fi` returns 0 in both branches (cp succeeded, or skipped because file missing). The function's last command's exit status is then 0, so the function returns 0, no set -e. Idiomatic, explicit, no `|| true` smell.

**Behavioral parity (verified empirically):** under `set -eo pipefail`, both `[ -f X ] && cp X Y` and `if [ -f X ]; then cp X Y; fi` exit the script with status 1 if `cp` fails (permission denied, disk full). `cp` is the FINAL command in the original `&&` chain, so set -e fires equally there. No observable behavioral change.

### 3.2 Extract `print_summary()` from inline lines 122-126

```diff
@@ run_all_tests.sh:50-52 @@
 collect_reports() {
     ...
 }
+
+print_summary() {
+    echo "================================================================"
+    echo "Reports: $RESULTS_DIR/"
+    for f in "$RESULTS_DIR"/*.json; do
+        [ -e "$f" ] || continue
+        echo "  $(basename "$f")"
+    done
+    if [ -z "$FAILURES" ]; then
+        echo -e "${GREEN}ALL PASSED${NC}"
+        return 0
+    fi
+    echo -e "${RED}FAILED:$FAILURES${NC}"
+    return 1
+}

@@ run_all_tests.sh:120-126 @@
 # --- Summary ---
 FAILURES="${FAILURES:-}"
-echo "================================================================"
-echo "Reports: $RESULTS_DIR/"
-ls "$RESULTS_DIR"/*.json 2>/dev/null | while read f; do echo "  $(basename $f)"; done
-[ -z "$FAILURES" ] && echo -e "${GREEN}ALL PASSED${NC}" && exit 0
-echo -e "${RED}FAILED:$FAILURES${NC}" && exit 1
+print_summary
```

**Why a function (not just an inline `for` loop replacement):** the line-124 fix in isolation is testable only via full-script subprocess invocation, which requires stubbing tox via PATH and copying repo skeleton. Extracting `print_summary()` makes the unit testable in isolation with the same `sed`-extraction pattern as `collect_reports`. Net: +12 lines (function), -5 lines (inline). Same exit semantics (under set -e, `print_summary` returning 1 triggers script exit 1 — verified empirically).

**Why the `for f in glob` form (instead of `ls glob | while`):** survives empty-glob (literal pattern → `[ -e ]` skip), avoids the pipefail interaction the inline `ls | while` had, no parse-ls issues, no subshell scoping pitfalls. Quoted `"$f"` in `basename` improves robustness on paths with spaces.

**Why `nullglob`-free:** the script also has line 26 (`ls -dt $(pwd)/test-results/*/`) which under `shopt -s nullglob` would change from "ls of literal pattern (returns 2)" to "ls with no args (lists cwd)" — different bug. Targeted per-site fix is safer than a global option change.

### 3.3 Net diff

```
run_all_tests.sh
  Line 48:    1 line removed, 3 lines added
  Lines 122-126: 5 lines removed (inline summary)
  After collect_reports (~line 51): 14 lines added (print_summary function)
  Line 124 → replaced: 1 line added (print_summary call)

Total: -6 lines old, +18 lines new → net +12 lines.
```

---

## 4. Regression tests

### 4.1 Location

`tests/unit/test_run_all_tests_script.py`

- Picked up by `tox -e unit` (which runs `pytest tests/unit/ tests/harness/`).
- Filename does NOT match any `_ENTITY_PATTERNS` substring in `tests/conftest.py:50-225`, so no entity marker auto-applied. Confirmed.
- `@pytest.mark.smoke` registered in `pytest.ini:17`. Tag for criticality, no suite-membership effect.

### 4.2 Empirical verification of test mechanism

Audit raised concern that `eval "$(sed ...)"` + Python `.format()` would collide on `${...}` braces in the function body. **This concern is incorrect** — verified empirically:

- Python `.format()` operates on the **template string only** (which has `{{`/`}}` escapes for the literal `{` and `}` in the sed pattern). The function body is generated at **bash runtime** by `sed` inside `$(...)`, AFTER `.format()` has run. The two layers do not interact.
- Prototype run against unfixed script: `rc=1` (RED — bug reproduced), partial files copied before set -e fired.
- Prototype run against fixed script: `rc=0` (GREEN — fix works), all expected files copied, function returns cleanly.

### 4.3 Test cases (4 total)

| # | Test name | Setup | Asserts | Catches |
|---|-----------|-------|---------|---------|
| 1 | `test_collect_reports_returns_zero_when_only_unit_integration_jsons_exist` | `.tox/{unit,integration}.json` only | rc=0; both jsons copied to results dir | The active #1232 regression |
| 2 | `test_collect_reports_returns_zero_when_no_jsons_exist` | empty `.tox/` | rc=0; results dir empty | Catastrophic case (tox crashes pre-report) |
| 3 | `test_print_summary_returns_zero_when_failures_empty` | results dir with one json; `FAILURES=""` | rc=0; "ALL PASSED" in stdout | Success-path regression for line-124 refactor |
| 4 | `test_print_summary_returns_one_when_failures_set` | empty results dir; `FAILURES="tox"` | rc=1; "FAILED:tox" in stdout | **Failure-path regression** (audit finding #5) — protects against future "fix" silently swallowing real failures |

### 4.4 Test file structure (illustrative — actual implementation in step 1)

```python
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "run_all_tests.sh"


def _setup_tox_dir(workdir: Path, names: list[str]) -> Path:
    """Create a fake .tox/ with empty JSON files for the named tox envs."""
    tox_dir = workdir / ".tox"
    tox_dir.mkdir(exist_ok=True)
    for name in names:
        (tox_dir / f"{name}.json").write_text("{}")
    return tox_dir


def _run_extracted_function(func_name: str, workdir: Path, env: dict) -> subprocess.CompletedProcess:
    """Extract a function from run_all_tests.sh via sed and invoke it under set -eo pipefail."""
    bash = textwrap.dedent("""
        set -eo pipefail
        eval "$(sed -n '/^{name}() {{/,/^}}/p' {script})"
        {name}
    """).format(name=func_name, script=SCRIPT)
    return subprocess.run(
        ["bash", "-c", bash],
        cwd=workdir,
        env={**dict(__import__("os").environ), **env},
        capture_output=True,
        text=True,
    )


@pytest.mark.smoke
def test_collect_reports_returns_zero_when_only_unit_integration_jsons_exist(tmp_path):
    """Regression for the active bug: quick mode produces only unit/integration JSONs.

    Before the fix, the last loop iteration's `[ -f .tox/X.json ]` test returned 1,
    making the function return 1, tripping set -e at the call site, and aborting
    the script with exit 1 before the summary phase. The pre-push hook reads $?
    and blocks every push from a worktree.
    """
    _setup_tox_dir(tmp_path, ["unit", "integration"])
    results = tmp_path / "results"

    rc = _run_extracted_function(
        "collect_reports", tmp_path, env={"RESULTS_DIR": str(results)}
    )

    assert rc.returncode == 0, f"rc={rc.returncode}\nstdout:{rc.stdout}\nstderr:{rc.stderr}"
    assert (results / "unit.json").exists()
    assert (results / "integration.json").exists()


@pytest.mark.smoke
def test_collect_reports_returns_zero_when_no_jsons_exist(tmp_path):
    """Edge case: tox crashed before writing any reports."""
    (tmp_path / ".tox").mkdir()
    results = tmp_path / "results"

    rc = _run_extracted_function(
        "collect_reports", tmp_path, env={"RESULTS_DIR": str(results)}
    )

    assert rc.returncode == 0


@pytest.mark.smoke
def test_print_summary_returns_zero_when_failures_empty(tmp_path):
    """When all suites passed, print_summary must signal success."""
    results = tmp_path / "results"
    results.mkdir()
    (results / "unit.json").write_text("{}")

    rc = _run_extracted_function(
        "print_summary",
        tmp_path,
        env={
            "RESULTS_DIR": str(results),
            "FAILURES": "",
            "GREEN": "",
            "RED": "",
            "NC": "",
        },
    )

    assert rc.returncode == 0
    assert "ALL PASSED" in rc.stdout


@pytest.mark.smoke
def test_print_summary_returns_one_when_failures_set(tmp_path):
    """When any suite failed, print_summary must propagate the failure to set -e."""
    results = tmp_path / "results"
    results.mkdir()

    rc = _run_extracted_function(
        "print_summary",
        tmp_path,
        env={
            "RESULTS_DIR": str(results),
            "FAILURES": "tox",
            "GREEN": "",
            "RED": "",
            "NC": "",
        },
    )

    assert rc.returncode == 1
    assert "FAILED:tox" in rc.stdout
```

### 4.5 What these tests do NOT cover (acceptable gaps)

- Full top-to-bottom script flow (covered by manual verification step + the live `make quality` invocation in step 4).
- The CI mode path (`./run_all_tests.sh ci`) — same `collect_reports` function, same fix, no separate test needed.
- The conductor pre-push hook — generated dynamically by `setup_conductor_workspace.sh`, hard to test in CI without spinning up a worktree. Out of scope.

---

## 5. Implementation steps (TDD)

Per `.claude/rules/workflows/tdd-workflow.md` — Red, Green, Refactor.

### Step 1 — RED
1. Create `tests/unit/test_run_all_tests_script.py` with all 4 test cases + helpers.
2. Run `tox -e unit -- tests/unit/test_run_all_tests_script.py -v`.
3. **Confirm test 1 (collect_reports with partial JSONs) FAILS** — rc=1, no stdout. The bug.
4. **Confirm test 4 (print_summary with FAILURES=tox) FAILS** — function `print_summary` doesn't exist yet, sed extraction returns empty, eval is no-op, calling `print_summary` errors out as "command not found". This is acceptable RED — proves the function isn't there.
5. Tests 2 and 3 may pass or fail depending on partial state — acceptable.

If RED conditions don't match, stop and reconsider.

### Step 2 — GREEN
1. Edit `run_all_tests.sh:48` (if/fi form).
2. Add `print_summary()` function definition after `collect_reports`.
3. Replace inline summary block (lines 122-126) with single `print_summary` call.
4. Re-run `tox -e unit -- tests/unit/test_run_all_tests_script.py -v`.
5. Confirm all 4 tests pass.

### Step 3 — Refactor (none needed)
Both edits land in canonical form on the first pass.

### Step 4 — Quality gate
1. `make quality` — full pre-commit + unit suite. Must pass.
2. `./run_all_tests.sh quick` — real end-to-end (with actual tox). Confirm:
   - exit 0
   - "ALL PASSED" printed
   - both unit and integration JSONs listed in summary
3. *(Optional manual)* introduce a deliberately failing unit test, run `./run_all_tests.sh quick`, confirm:
   - exit 1
   - "FAILED:tox" printed
   - then revert the test.

### Step 5 — Commit
- Single commit, conventional-commits format.
- Title: `fix: run_all_tests.sh quick mode exits 1 despite tests passing` (matches issue title style).
- Body: explain the `set -e` + `[ -f ] && cp` interaction in the function tail, scope (lines 48 + summary refactor), why line 26 is intentionally untouched.
- `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.
- **Do NOT push.** User owns push (durable preference).

---

## 6. Verification matrix

| Check | Command | Expected |
|-------|---------|----------|
| All 4 regression tests pass | `tox -e unit -- tests/unit/test_run_all_tests_script.py -v` | 4 passed |
| Unit suite still green | `make quality` | All pass, no new lint/format/typecheck errors |
| Quick-mode actual run, success | `./run_all_tests.sh quick` (real tox, ~2-3 min) | Exit 0, "ALL PASSED" |
| Quick-mode actual run, failure | (manual) introduce a fake test failure | Exit 1, "FAILED:tox" |
| No new pre-commit violations | `pre-commit run --all-files` | All hooks pass |

---

## 7. Adherence to project rules

| Rule source | Rule | How this plan complies |
|-------------|------|------------------------|
| `CLAUDE.md` | Conventional Commits PR title prefix | `fix:` prefix, will appear in "Bug Fixes" changelog section |
| `CLAUDE.md` | "A bug fix doesn't need surrounding cleanup" | Line 26 explicitly out of scope. `print_summary` extraction is justified by testability, not gratuitous. |
| `CLAUDE.md` | DRY | `_setup_tox_dir` and `_run_extracted_function` helpers in test file deduplicate setup across 4 tests. No duplication in `run_all_tests.sh` (the two fixes target different sites with different idioms). |
| `CLAUDE.md` | "Default to writing no comments" | Added: `print_summary()` has zero comments — its body is self-documenting (echo, list, branch on FAILURES). The if/fi line replacement adds zero comments. |
| `tdd-workflow.md` | Red, then green | Step 1 (RED) confirms tests 1 and 4 fail before fix. |
| `tdd-workflow.md` | "NEVER adjust tests to match code" | Tests assert the contract: `collect_reports` and `print_summary` must return correctly. Script is fixed to satisfy these. |
| `bug-reporting.md` | "Always write the test FIRST" | Step 1 RED. |
| `quality-gates.md` | `make quality` before commit | Step 4. |
| `session-completion.md` | No `git push` | Step 5 explicit. |
| `testing-patterns.md` | Max 10 mocks per test file | 0 mocks (subprocess + tmp_path). |
| `testing-patterns.md` | "Test YOUR code, not Python built-ins" | Tests source the actual functions from production script via `sed` extraction. No Python mocking; pure observation of shell behavior. |
| Memory: `feedback_no_beads_workflow` | Skip bd commands | No `bd create/close` in this plan. |
| Memory: `feedback_no_issue_refs_in_comments` | No PR/issue numbers in code comments or docstrings | Test docstrings describe the regression class ("the last loop iteration's failing `[ -f ]`...") without naming #1232. The git commit body may reference #1232 once. |
| Memory: `feedback_no_pointless_comments` | No explanatory comments on clear code | Zero comments added to `run_all_tests.sh` or test file. |
| Memory: `feedback_user_owns_git_push` | Don't push | Confirmed in step 5. |
| Memory: `feedback_planning_doc_location` | `.claude/notes/<slug>/` | This file lives at `.claude/notes/fix-1232-quick-mode-exit/PLAN.md`. |
| Memory: `feedback_no_code_in_planning_stage` | No code edits during planning | The diff blocks and test code in this plan are previews. No file in the project tree is modified. The script file at `/tmp/audit_proto*` was a sandbox copy, since deleted. |
| Memory: `feedback_always_improve_testing` | Add regression coverage | 4 new test cases covering both functions and both branches of `print_summary`. |
| Memory: `feedback_thorough_review` | Multiple verification rounds before cross-cutting changes | Independent audit completed; 2 BLOCKER/SHOULD-FIX findings empirically refuted (#1, #4); 2 valid findings incorporated (#3 → `print_summary` extraction; #5 → failure-path test); plan revised to v2. |

---

## 8. Audit reconciliation (v1 → v2 deltas)

| Audit finding | Severity | Resolution |
|---------------|----------|------------|
| #1 Format-string collision in `eval "$(sed ...)"` | BLOCKER | **REFUTED** — empirical prototype shows `.format()` and bash brace-expansion operate at different times; no collision. Plan keeps the sed-extraction approach. |
| #2 Test placement / entity-marker auto-tag | SHOULD-FIX | **CONFIRMED SAFE** — filename matches no `_ENTITY_PATTERNS` substring. No tox.ini change required. |
| #3 Line-124 test approach undefined | SHOULD-FIX | **RESOLVED** — extract `print_summary()` function, test it identically to `collect_reports` via sed. Section 3.2 now committed. |
| #4 `if/fi` differs from `&&` on cp failure | SHOULD-FIX | **REFUTED** — empirical test shows both forms exit with rc=1 under set -e on cp failure. cp is the FINAL command in the && chain, so set -e fires equally. No behavioral difference. |
| #5 No automated failure-path test | SHOULD-FIX | **RESOLVED** — test 4 (`test_print_summary_returns_one_when_failures_set`) covers this. |
| #6 Line 15 `[ -f .env ] && {...}` not documented | NICE-TO-HAVE | **DOCUMENTED** — section 2 row added explaining why the pattern is safe at line 15 (compound block always returns 0 when entered, set -e ignores LHS-of-&& failure). |
| #7 Code dedup helper for test setup | NICE-TO-HAVE | **RESOLVED** — `_setup_tox_dir` and `_run_extracted_function` helpers (section 4.4). |
| #8 Conductor worktree integration | CONFIRMED | Existing worktrees auto-pick up the fix (hook invokes script from working tree, not a baked copy). |
| #9 `if/fi` bulletproof under `set -e` | CONFIRMED | No edge cases. |

---

## 9. Open questions for user

1. **Refactor to extract `print_summary()`** — adds 12 lines to `run_all_tests.sh` for testability. Borderline scope; v2 plan includes it. Want to keep, or strictly minimal (line 48 only, no print_summary refactor, accept finding #5 gap)?
2. **Optional bonus test** — add a 5th test covering the happy path (all 6 JSONs present, all copied)? Tests #1 and #2 cover the regression and edge case; the happy path is implicitly covered by #1 (which copies 2 of the 6). Skip it unless you want belt-and-suspenders.

---

## 10. Out of scope (potential follow-ups, NOT this PR)

- Line 26 latent bug.
- Architectural rewrite to drop `set -e` in favor of explicit `FAILURES` accumulation.
- Adding `shellcheck` to the pre-commit suite.
- Refactoring the script into `lib/` + thin invoker.
- Replacing the bespoke `quick` / `ci` mode dispatch with an argparse-style flag set.

Each warrants its own ticket and PR.
