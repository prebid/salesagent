# ADR-008 — Defer black/ruff target-version bump out of #1234

## Status

Accepted 2026-04-25 (Round 5+6 P0 sweep).

## Context

PR 5 of issue #1234 (cross-surface version consolidation) originally scheduled a
black/ruff target-version bump from py311 → py312 alongside the version-anchor
consolidation. The proposed step used:

```bash
ruff check --target-version py312 --fix --select UP
```

This is the exact pattern that triggered the 2026-04-14 unsafe-autofix incident
(UP040 production-schema breakage). Per `feedback_no_unsafe_autofix.md`:

> If a lint rule would rewrite 3+ files in source, STOP and ask.

The target-version bump unlocks no value chain in #1234 — it is a separable,
hand-reviewable concern.

## Decision

Defer the target-version bump to a separate PR after #1234 closes.

**PR 5 retains** (the load-bearing piece — anchor consolidation):
- cross-surface uv version consolidation (`UV_VERSION` anchor across Dockerfile, setup-env action, workflows)
- cross-surface Python version consolidation (`.python-version`, mypy.ini, tox.ini, Dockerfile, pyproject.toml `requires-python`)
- cross-surface Postgres version consolidation (compose files already at 17-alpine; only Dockerfile + workflows need anchoring)
- structural guard `test_architecture_uv_version_anchor.py` (extended to cover all named anchors)

**PR 5 drops** (deferred per this ADR):
- `[tool.black].target-version` py311 → py312
- `[tool.ruff].target-version` py311 → py312
- `ruff check --target-version py312 --fix --select UP` mass-fix
- `--no-verify` carve-out (was needed because the UP040 fix-cycle could mismatch hooks)

## Consequences

- The follow-up PR is sized appropriately for hand-applied per-site UP040 fixes.
- Reviewers can see UP040 changes per-file rather than as a single mass-edit commit.
- Risk of replaying the 2026-04-14 incident is eliminated.
- PR 5's scope shrinks slightly but remains load-bearing.
- Future contributors who run `ruff check` locally with target-version py312 may see
  warnings; document in CONTRIBUTING.md that the bump is forthcoming.

## Tripwire

Revisit after both:
1. PR 5 ships and the cross-anchor guard is verified stable.
2. PR 3's `_pytest.yml` → `_pytest/action.yml` (composite) migration is verified stable.

File the follow-up issue when authoring this ADR:
**'Post-#1234: bump black/ruff py311 → py312 with hand-applied UP040 fixes.'**

## References

- D28 (decision log entry — `03-decision-log.md`)
- `feedback_no_unsafe_autofix.md` (user memory)
- 2026-04-14 incident retrospective (UP040 production-schema break)
- ADR-001 — uv.lock as single source for pre-commit deps (defines the anchor pattern PR 5 enforces)
