# drafts/ — staging content for canonical paths

These files are pre-execution drafts the executor agent lifts to canonical
locations during PR execution. Treat as staging area; once lifted, the
canonical file in the repo is authoritative.

| Draft | Lifted to | By |
|---|---|---|
| `adr-004-guard-deprecation-criteria.md` | `docs/decisions/adr-004-guard-deprecation-criteria.md` | PR 4 |
| `adr-005-fitness-functions.md` | `docs/decisions/adr-005-fitness-functions.md` | PR 4 |
| `adr-006-allowlist-shrink-only.md` | `docs/decisions/adr-006-allowlist-shrink-only.md` | PR 4 |
| `adr-007-build-provenance.md` | `docs/decisions/adr-007-build-provenance.md` | PR 6 (reconciles cosign + attest-build-provenance overlap; tripwire for Rekor downtime) |
| `_architecture_helpers.py` | `tests/unit/_architecture_helpers.py` | PR 2 commit 8 (baseline) + PR 4 commit 1 (extend to ~221 lines) per D27 + Blocker #3 |
| `guards/test_architecture_*.py` (8 files) | `tests/unit/test_architecture_*.py` | **Per D19, all 8 are owned by PR 4** (the structural-guards PR). PR 1 and PR 3 ship workflow content the guards inspect, but the guards themselves land in PR 4 once `_architecture_helpers.py::iter_workflow_files` and friends exist. Earlier drafts attributed 3 to PR 1; that allocation was reverted to keep PR 1 strictly governance/supply-chain. |
| `claudemd-guards-table-final.md` | `CLAUDE.md` (replace existing 24-row table) | PR 4 commit 9 |
| `precommit-prepush-hook.md` | `.pre-commit-config.yaml` (architecture-guards pre-push hook) | PR 4 |

**Do not delete this directory** until ALL PRs (1-6) have lifted their
content. The reconciled `_architecture_helpers.py` (221 lines) in
particular is the only place where the PR-2-baseline + PR-4-extension is
unified — without it, the executor would have to re-derive the helper
shape from two specs at once.

After PR 6 merges, this directory becomes audit-trail (rename to
`drafts.archived/` or move under `research/`).
