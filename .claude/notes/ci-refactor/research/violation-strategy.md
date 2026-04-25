# Guard Rollout Strategy Analysis

## Measured State on Main (2026-04-24)

| Metric | Value |
|--------|-------|
| Workflow files | 4 (test.yml, ipr-agreement.yml, pr-title-check.yml, release-please.yml) |
| Workflows with top-level `permissions:` | 2/4 (ipr-agreement.yml, release-please.yml) |
| Workflows missing `permissions:` | **2** (test.yml, pr-title-check.yml) |
| Action `uses:` references (total occurrences) | 32 |
| Unique action refs | 12 |
| SHA-pinned action refs | **0** |
| Workflows with `concurrency:` | **0** |
| `actions/checkout` calls with `persist-credentials: false` | **0** (out of 8 occurrences) |
| Total jobs across workflows | 12 |
| Jobs with `timeout-minutes:` | 3 (integration, quickstart, e2e) |
| Jobs missing `timeout-minutes:` | **9** |
| Pre-commit external repo blocks | 4 (pre-commit-hooks, black, ruff, mypy) |
| External `rev:` SHA-pinned | **0** |
| `\|\| true` / `continue-on-error: true` lines | **13** (mostly cleanup, but 2 in lint job suppress real errors) |
| Total pre-commit hook IDs | 40 |
| Commit-stage hooks (excluding `stages: [manual]`) | **36** (target: ≤ 12) |
| `Response*` classes in `src/core/schemas/` | 31 |
| `def model_dump` overrides (non-`_internal`) | 7 |
| Estimated Pattern-#4 violations | **~24** |
| Governance: `CODEOWNERS` | MISSING |
| Governance: `SECURITY.md` | MISSING |
| Governance: `CONTRIBUTING.md` | EXISTS |
| Governance: `.github/dependabot.yml` | MISSING |
| ADRs (any of ADR-001..003) | MISSING (no `docs/adr/` directory) |

---

## Strategy Table

| # | Guard | Today's Violations | Strategy | Effort | Notes |
|---|-------|-------------------|----------|--------|-------|
| 1 | `workflow_permissions` | 2 (test.yml, pr-title-check.yml) | **A — fix first** | 5 min | Backfill commit B1 inserts `permissions: read-all` (or `contents: read`) at top of both files |
| 2 | `actions_sha_pinned` | 32 occurrences (12 unique) | **A — fix first** | 30 min | Backfill commit B2 resolves SHAs for all 12 unique action+version pairs and substitutes `@<sha> # v<tag>` in all 32 lines |
| 3 | `precommit_sha_frozen` | 4 (all external blocks) | **A — fix first** | 10 min | Backfill commit B3 converts pre-commit-hooks v6.0.0, black 25.1.0, ruff v0.14.10, mypy v1.18.2 to SHA + `# frozen: vX.Y.Z` comment |
| 4 | `governance_files` | 3 missing (CODEOWNERS, SECURITY.md, dependabot.yml) | **N/A — order within PR** | 0 min added | PR 1 already creates these; guard added in commit 12+ runs AFTER artifact-creating commits 1-7. Verify ordering in PR 1 spec. |
| 5 | `adrs_exist` | 3 missing (ADR-001..003) | **N/A — order within PR** | 0 min added | Same as #4 — PR 1 creates ADRs in earlier commits. |
| 6 | `workflow_concurrency` | 4 (all workflows) | **A — fix first** | 10 min | Backfill commit B4 adds `concurrency:` block. Per-workflow analysis: test.yml (PR-triggered → cancel-in-progress), release-please.yml (push-only → DON'T cancel, use group-only), ipr-agreement.yml (DON'T cancel — handles signatures), pr-title-check.yml (cancel-in-progress safe). |
| 7 | `persist_credentials_false` | 8 `actions/checkout` calls | **A — fix first** | 5 min | Backfill commit B5 adds `with: { persist-credentials: false }` to all 8 checkouts. **Caveat:** publish-docker job in release-please.yml uses GITHUB_TOKEN to push images, but those are passed via `secrets.GITHUB_TOKEN` not via the cloned repo, so `persist-credentials: false` is safe. Verify nothing in test.yml uses `git push` or relies on cloned credentials. |
| 8 | `workflow_timeout_minutes` | 9 jobs | **A — fix first** | 10 min | Backfill commit B6 adds `timeout-minutes:` to 9 jobs. Recommended values: security-audit 5, smoke-tests 5, unit-tests 10, lint 5, test-summary 2, release-please 5, publish-docker 30, ipr-check 5, check-pr-title 2. |
| 9 | `precommit_no_additional_deps` | Status unclear — needs PR 2 spec | **Specced in PR 2** | (per spec) | Already documented in pr2-uvlock-single-source.md. Existing mypy hook uses `additional_dependencies` — that's the violation PR 2 fixes. |
| 10 | `required_ci_checks_frozen` | Depends on PR 3 final 11 names | **Order within PR 3** | 0 min added | The guard is added AFTER ci.yml is created with the 11 names. Self-consistent. |
| 11 | `no_advisory_ci` | 13 lines (2 hard violations in lint job, 11 cleanup `\|\| true`) | **A (split) — fix first, with allowlist for cleanup** | 15 min | Backfill commit B7 removes `continue-on-error: true` and trailing `\|\| true` from lint steps (lines 382-387, the real bugs). The 11 cleanup `\|\| true` (docker prune, find -delete) are legitimate — guard must allowlist them as "cleanup-only" patterns. Better: guard scans only `run:` steps, not within `if: always()` or `Cleanup` blocks; OR use a literal allowlist of (file, line) tuples. |
| 12 | `no_tenant_config` | 0 (existing pre-commit hook is green) | **N/A — passes today** | 0 min | Migrating an enforced hook into a structural guard. PR 4 should keep the hook running until guard is verified green. |
| 13 | `jsontype_columns` | 0 (existing pre-commit hook is green) | **N/A — passes today** | 0 min | Same as #12. |
| 14 | `no_defensive_rootmodel` | 0 (existing pre-commit hook is green) | **N/A — passes today** | 0 min | Same as #12. |
| 15 | `import_usage` | 0 (existing pre-commit hook is green) | **N/A — passes today** | 0 min | Same as #12. |
| 16 | `nested_serialization` (Pattern #4) | **~24** Response classes lacking `model_dump` override | **B — allowlist + ratchet** | 20 min for allowlist; future work for fixes | Fixing all 24 is out-of-scope for PR 4. Build allowlist from AST scan, store at `tests/structural_guards/_allowlist_nested_serialization.py`. |
| 17 | `claudemd_table_complete` | Per master-index findings: 3 phantom + 5 missing rows | **A — fix first** | 10 min | Backfill commit B8 (in PR 4) corrects the CLAUDE.md "Structural Guards" table BEFORE the guard lands. |
| 18 | `precommit_coverage_map` | File doesn't exist | **N/A — created in PR 4** | 0 min | Guard is added AFTER commit creating `.precommit-coverage-map.yaml` (or whatever name). Order within PR. |
| 19 | `hook_count_permanent` | 36 commit-stage (target ≤ 12) | **N/A — guard is the goal of PR 4** | 0 min added | The whole point of PR 4 is to delete duplicate hooks. Guard goes in the FINAL commit of PR 4 after deletions land. |
| 20 | `python_version_anchor` | TBD — needs anchor file convention | **N/A — created in PR 5** | 0 min | Guard added AFTER `.tool-versions` (or anchor) is created. |
| 21 | `postgres_version_anchor` | TBD | **N/A — created in PR 5** | 0 min | Same as #20. |
| 22 | `uv_version_anchor` | Per PR 5 spec | **Specced in PR 5** | (per spec) | Already documented. |

---

## Backfill Commit Specifications (PR 1)

### B1 — `workflow_permissions` backfill

**File: `.github/workflows/test.yml`** — insert at line 9 (before `env:`):
```yaml
permissions:
  contents: read
```

**File: `.github/workflows/pr-title-check.yml`** — insert at line 6 (before `jobs:`):
```yaml
permissions:
  pull-requests: read
```

### B2 — `actions_sha_pinned` backfill

Resolve SHAs for these 12 refs (do this via `gh api repos/<owner>/<repo>/commits/<tag>` per ref):

| Action | Tag | Notes |
|--------|-----|-------|
| `actions/checkout` | v4 | 8 occurrences |
| `actions/setup-python` | v5 | 5 occurrences |
| `actions/cache` | v4 | 3 occurrences |
| `astral-sh/setup-uv` | v4 | 5 occurrences |
| `amannn/action-semantic-pull-request` | v5 | 1 |
| `contributor-assistant/github-action` | v2.6.1 | 1 |
| `docker/build-push-action` | v5 | 1 |
| `docker/login-action` | v3 | 2 |
| `docker/metadata-action` | v5 | 1 |
| `docker/setup-buildx-action` | v3 | 1 |
| `docker/setup-qemu-action` | v3 | 1 |
| `googleapis/release-please-action` | v4 | 1 |

Substitute pattern: `uses: <repo>@<sha> # <tag>`. 32 substitutions across 4 files.

### B3 — `precommit_sha_frozen` backfill

Modify `.pre-commit-config.yaml`:
- Line 263: `rev: v6.0.0` → `rev: <sha-of-pre-commit-hooks-v6.0.0>  # frozen: v6.0.0`
- Line 276: `rev: 25.1.0` (black) → `rev: <sha>  # frozen: 25.1.0`
- Line 282: `rev: v0.14.10` (ruff) → `rev: <sha>  # frozen: v0.14.10`
- Line 290: `rev: v1.18.2` (mypy) → `rev: <sha>  # frozen: v1.18.2`

### B4 — `workflow_concurrency` backfill

Add per workflow:
```yaml
# test.yml, pr-title-check.yml (PR-triggered, cancel safe):
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

# release-please.yml (push-to-main, never cancel):
concurrency:
  group: release-please-${{ github.ref }}
  cancel-in-progress: false

# ipr-agreement.yml (signature workflow, never cancel):
concurrency:
  group: ipr-${{ github.event.pull_request.number || github.event.issue.number }}
  cancel-in-progress: false
```

### B5 — `persist_credentials_false` backfill

For all 8 occurrences of `- uses: actions/checkout@v4`, add:
```yaml
- uses: actions/checkout@<sha>  # v4
  with:
    persist-credentials: false
```

### B6 — `workflow_timeout_minutes` backfill

Add `timeout-minutes:` to 9 jobs. Specific patches in test.yml: line 17 (security-audit: 5), line 36 (smoke-tests: 5), line 79 (unit-tests: 10), line 363 (lint: 5), line 398 (test-summary: 2). In release-please.yml: line 15 (release-please: 5), line 30 (publish-docker: 30). In ipr-agreement.yml: line 17 (ipr-check: 5). In pr-title-check.yml: line 9 (check-pr-title: 2).

### B7 — `no_advisory_ci` backfill (partial)

In `test.yml`, lines 381-387, REMOVE the `|| true` and `continue-on-error: true` from lint steps (real bugs hiding ruff failures). The 11 cleanup `|| true` (docker prune, etc.) stay — guard must be designed to allow cleanup-only patterns OR allowlist them.

### B8 — `claudemd_table_complete` backfill (PR 4)

Per master-index finding "3 phantom + 5 missing rows" — this fix lands in PR 4 BEFORE the corresponding guard. Specifics depend on the audit; the ordering rule is `fix CLAUDE.md table → add guard`.

---

## Strategy-B Allowlist (Guard #16 — `nested_serialization`)

Construct the allowlist by AST-scanning `src/core/schemas/*.py` for class definitions where:
- Bases include `*Response`, `*Result`, or `AdCPBaseModel` AND
- Class has `Mapped[list[...]]` or `list[Model]` annotated field AND
- Class body does NOT define `def model_dump`

Allowlist literal (skeleton — populate via the same AST query that powers the guard):

```python
# tests/structural_guards/_allowlist_nested_serialization.py
"""Nested-serialization allowlist (Pattern #4). Shrinks only.

Each entry is (relative_path, class_name). Remove an entry only when the
class is fixed (model_dump override added). The corresponding stale-entry
test will fail if a removed class is still violating, or if a class in the
allowlist has been deleted entirely.
"""
ALLOWLIST: set[tuple[str, str]] = {
    # Populate from AST scan output. Roughly:
    # ("src/core/schemas/account.py", "ListAccountsResponse"),
    # ("src/core/schemas/_base.py", "GetSignalsResponse"),
    # ... ~24 total entries, each with FIXME(salesagent-XXXX)
}
```

The actual entries must be enumerated by running the AST scan. I attempted this via `uv run python -c` but execution was denied. **Action item for the rollout:** the PR-4 implementer must produce the allowlist by running the AST scan in their working environment and committing the populated set. Estimated 24 entries based on the (31 Response classes − 7 model_dump overrides) gap.

---

## Updated PR 1 Commit Sequence

Original PR 1 spec adds the 8 governance/workflow/Fortune-50 guards. With backfill required for 6 of them, the sequence becomes:

| Commit | Purpose |
|--------|---------|
| 1 | feat: add CODEOWNERS |
| 2 | feat: add SECURITY.md |
| 3 | docs: add ADR-001 (CI/PR philosophy) |
| 4 | docs: add ADR-002 (... per spec) |
| 5 | docs: add ADR-003 (... per spec) |
| 6 | feat: add .github/dependabot.yml |
| 7 | feat: add governance.md or similar |
| **8 (NEW)** | **fix: add `permissions:` to workflows missing it (B1)** |
| **9 (NEW)** | **fix: add `concurrency:` to all workflows (B4)** |
| **10 (NEW)** | **fix: add `persist-credentials: false` to all checkouts (B5)** |
| **11 (NEW)** | **fix: add `timeout-minutes:` to all jobs (B6)** |
| **12 (NEW)** | **fix: remove advisory `\|\| true` from lint steps (B7-partial)** |
| **13 (NEW)** | **chore: SHA-pin all GitHub Actions (B2) — 32 substitutions** |
| **14 (NEW)** | **chore: SHA-pin pre-commit external repos (B3)** |
| 15 | test: add 8 structural guards (workflow_permissions, actions_sha_pinned, precommit_sha_frozen, governance_files, adrs_exist, workflow_concurrency, persist_credentials_false, workflow_timeout_minutes) |
| 16 | docs: update CLAUDE.md guards table |

**PR 1 grew from ~9 commits to ~16.** Net diff size grows by ~250 lines (mostly mechanical SHA substitutions).

---

## Effort Delta Per PR

| PR | Original estimate | New estimate | Delta |
|----|-------------------|--------------|-------|
| PR 1 | ~3 hours | ~4.5 hours | +1.5 hr (7 backfill commits, ~75 min mechanical work) |
| PR 2 | (per spec) | (per spec) | 0 |
| PR 3 | ~2 hours | ~2.25 hours | +15 min (verify required-checks list matches before adding guard) |
| PR 4 | ~6 hours | ~7 hours | +1 hr (CLAUDE.md table backfill + AST scan to build nested_serialization allowlist) |
| PR 5 | (per spec) | (per spec) | 0 |
| **Total** | — | — | **+2.75 hours** |

---

## Top 3 Surprises Changing the Rollout Estimate

### 1. ZERO action SHA-pinning today, not "≥30" — every action ref needs replacement
Round-2 findings underestimated this: I measured **32 occurrences across 12 unique actions, all using semver tags, none SHA-pinned**. Backfill is 32 line substitutions, not "a few". Recommend automating with a script (`gh api repos/<owner>/<repo>/commits/<tag>` per unique ref → bulk sed). Estimated 30 minutes if scripted, 2+ hours if manual.

### 2. ZERO concurrency/persist-credentials/most timeouts — Fortune-50 guards reveal a fully-unhardened CI surface
The plan's "Fortune-50 must-add" guards were framed as polish, but the measurements show this is a 4-workflow greenfield. **All 4 workflows lack concurrency control. All 8 checkouts lack `persist-credentials: false`. 9 of 12 jobs lack timeouts.** Each fix is mechanical and small, but the cumulative diff in PR 1 is ~80-100 lines of YAML — large enough that the PR description must call it out clearly to reviewers. Consider: split PR 1 into "PR 1a: governance artifacts + ADRs + 5 governance guards" and "PR 1b: workflow hardening + 3 Fortune-50 guards" to keep diffs reviewable.

### 3. Pattern #4 violations are real and numerous — `nested_serialization` cannot land as Strategy A
With 31 `*Response` classes in `src/core/schemas/` and only 7 `def model_dump` overrides (and several of those are `model_dump_internal` variants), the violation count is **~24**. Fixing 24 nested-serialization bugs is at least a full day's work and needs per-class testing because each one changes the wire format. Strategy B (allowlist + ratchet, with FIXME tracking) is mandatory. The implementation must NOT attempt to fix all 24 in PR 4 — that becomes a follow-up beads epic. The guard's value comes from preventing the 25th violation, not eliminating the 24 today.
