# Rebase Protocol for Shared Files

**Purpose:** This rollout has 3 files edited by multiple PRs. Rebasing in the wrong order produces semantically-corrupt merges that GitHub auto-resolves textually but break downstream PRs.

---

## Mandatory rebase orders

### `.pre-commit-config.yaml`

**PR 1** SHA-pins existing hook revs.
**PR 2** deletes `mirrors-mypy` + `psf/black` blocks; inserts local hooks (`language: system`).
**PR 4** prepends `default_install_hook_types: [pre-commit, pre-push]` + `minimum_pre_commit_version: 3.2.0` directives; deletes 13 hooks; reorders.

**Required order:** PR 1 → PR 2 → PR 4 (sequential merge to main).

**If PR 2 lands BEFORE PR 1:**
- PR 1 must rebase to PR 2's `mirrors-mypy`-deleted state. Re-run `pre-commit autoupdate --freeze` against the resulting config.
- Risk: PR 1's planned diff may no longer apply cleanly.

**Pre-rebase contract test (recommended for executor):**
`scripts/verify-pre-commit-config-shape.sh` (NEW — author as part of PR 4):
```bash
#!/bin/bash
# Asserts post-rebase that .pre-commit-config.yaml has all three components:
# 1. SHA-pinned revs (PR 1)
# 2. Local-hook block with language: system (PR 2)
# 3. default_install_hook_types directive (PR 4)
set -euo pipefail
grep -qE '^\s+rev:\s+[a-f0-9]{40}' .pre-commit-config.yaml || { echo "FAIL: no SHA-pinned revs (PR 1 lost)"; exit 1; }
grep -qE 'language:\s+system' .pre-commit-config.yaml || { echo "FAIL: no language: system hook (PR 2 lost)"; exit 1; }
grep -qE 'default_install_hook_types:\s+\[pre-commit,\s*pre-push\]' .pre-commit-config.yaml || { echo "FAIL: directive missing (PR 4 lost)"; exit 1; }
echo "OK: shape contract satisfied"
```

### `pyproject.toml`

**PR 1** adds `[project.urls]` block.
**PR 2** deletes `[project.optional-dependencies].dev`; adds `pytest-xdist + pytest-randomly` to `[dependency-groups].dev`; cleans up factory-boy duplicates.
**PR 6** (potentially) adds pytest-benchmark.

**Required order:** PR 1 → PR 2 → PR 6.

### `.github/workflows/release-please.yml`

**PR 1** SHA-pins + adds `permissions:` + `persist-credentials: false`.
**PR 6** extends `publish-docker` (R29 split into `build-and-push` + `sign-and-attest`); adds D47 gate; adds `outputs.sha` to `release-please` job.

**Required order:** PR 1 → PR 6.

**v2.0 collision warning:** PR #1221 (Flask→FastAPI) may also touch `release-please.yml` for FastAPI-related release engineering. Per D20 (Path 1), #1234 lands first; v2.0 phase PRs rebase. If a v2.0 phase PR lands mid-rollout, re-verify shape post-rebase.

---

## Conflict resolution flowchart

1. **Textual conflict on rebase?** → resolve in favor of LATER PR's intent (per dependency order above)
2. **No textual conflict but shape-test fails?** → rerun the lost PR's diff manually
3. **Both PRs untouched the same line but semantic meaning changed?** (e.g., PR 1 SHA-pinned a hook PR 2 deleted) → consult the PR with the bigger structural change; smaller PR re-derives its diff
4. **Cannot resolve?** → escalate via FAILURE-BROADCAST-PROTOCOL.md

---

## v2.0 (PR #1221) collision list

Per `00-MASTER-INDEX.md` Round 10 sweep:
- `.github/workflows/release-please.yml` (3-way)
- `.github/CODEOWNERS` (PR 1 creates with both globs per Stream A Round 13)
- `pytest.ini` (PR 2 registers `arch_guard` marker)
- `tests/conftest_db.py` (PR 3 + v2.0 — take v2.0 baseline + apply PR 3's template-clone diff)

For each, document the chosen rebase strategy in this file as v2.0 phase PRs land.
