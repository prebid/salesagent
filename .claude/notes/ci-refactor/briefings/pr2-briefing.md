# PR 2 — uv.lock single-source for pre-commit deps

## Briefing
**Where we are.** Week 2. PR 1 has merged: SHA-pinning convention is now established; CODEOWNERS routes review; ADR-001 placeholder exists in `docs/decisions/`. Calendar position: mid-rollout.

**What this PR does.** Eliminates `additional_dependencies:` drift. Replaces the external `mirrors-mypy` and `psf/black` repo blocks with `local` hooks that run `uv run mypy` and `uv run black` (`language: system`). Side effect: `pydantic.mypy` plugin (declared in `mypy.ini:3` but silently dead since project inception) becomes live — D13 says fix the resulting errors in this PR. Also migrates `[project.optional-dependencies].ui-tests` → `[dependency-groups].ui-tests` per PEP 735 (D14). Drift closed: PD1, PD2, PD8 (PD8 may already be done on v2.0 — coordinate).

**You can rely on (already done).** PR 1 shipped. ADR-001 placeholder lives at `docs/decisions/adr-001-single-source-pre-commit-deps.md`; you flesh it out in commit 1. SHA-pinning convention is in place. `.pre-commit-hooks/check_code_duplication.py` exists.

**You CANNOT do.** No deletion of `[project.optional-dependencies].dev` BEFORE migrating callsites (commit 4 must precede commit 5 — order is load-bearing). No CI workflow restructure (PR 3). No deletion of pre-commit hooks (PR 4). No re-enabling `pre-commit-uv` (zero `language: python` hooks remain after this PR; pre-commit-uv has no benefit).

**Concurrent activity.** PR #1217 may have merged — your local mypy hook validates against current `uv.lock` regardless. v2.0 may have already deleted `[project.optional-dependencies].dev` from main. **VERIFY** during commit 5: if it's already gone, commit 5 is a no-op. Do NOT re-introduce the block.

**Files (heat map).**
- Heavy: `.pre-commit-config.yaml` (delete mirrors-mypy lines 289-305 + psf/black lines 275-279; add 2 local hooks under `repos[0]`).
- Medium: `pyproject.toml` (delete `[project.optional-dependencies].dev`, migrate `ui-tests`); `.github/workflows/test.yml` (5 callsites: lines ~60, 103, 171, 316, 379 — `--extra dev` → `--group dev`); `tox.ini:77` (`extras = ui-tests` → `dependency_groups = ui-tests`); `scripts/setup/setup_conductor_workspace.sh:212`.
- Variable: wherever `pydantic.mypy` plugin surfaces errors (`src/core/schemas.py`, `src/core/schemas_*.py`, `src/core/tools/*/`).
- New: `tests/unit/test_architecture_pre_commit_no_additional_deps.py`, `tests/unit/_architecture_helpers.py`.
- DO NOT touch: `src/admin/` (v2.0 territory), CI workflows beyond `--extra dev` callsites.

**Verification environment.** `.mypy-baseline.txt` MUST exist (pre-flight P2) — verify via TTL guard. Note pydantic.mypy plugin error count BEFORE you start.

**Escalation triggers.**
- After commit 2, mypy error delta exceeds 200 (D13 tripwire) → STOP. Comment out `pydantic.mypy` from `mypy.ini:3` temporarily. File a follow-up issue. Document deferral in PR description. Continue with mypy-without-plugin.
- After commit 4, CI red on `--group dev` callsites → check if a callsite was missed. The `Makefile`, `Dockerfile`, `scripts/`, `docs/` may have additional refs.
- Commit 7 black version drift → `uv run black --version` doesn't match `uv.lock`'s resolved black; rerun `uv sync --group dev`.

**Key facts from prior rounds.**
1. **`[project.optional-dependencies].dev` is already deleted on the v2.0 branch.** Critical: do NOT re-introduce it during rebase. Verify diff against main during commit 5.
2. The pydantic.mypy plugin is in `mypy.ini:3` but has been silently dead since project inception because pydantic was never in the old hook's `additional_dependencies`. Re-enabling = surfacing dormant typing debt.
3. The structural guard pattern uses `_architecture_helpers.py` (introduced here) — PR 4 expands it. Keep helpers minimal (~30 lines).
4. `tox -e ui --notest` is the smoke check after ui-tests migration; full ui run requires `playwright install`.
5. `@pytest.mark.architecture` marker registration in `pyproject.toml [tool.pytest.ini_options].markers` happens HERE (commit 8) — PR 4 backfills the marker on existing 27 guards.
