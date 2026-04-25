# ADR-004: Structural-guard deprecation criteria

## Status

Accepted (2026-04-25). Implemented in PR 4 of the CI/pre-commit refactor (issue
#1234). Tripwire: schedule a quarterly retirement review when the live guard
count exceeds 75.

## Context

The repository enforces architecture invariants via "structural guards" —
AST-scanning pytest tests under `tests/unit/test_architecture_*.py` that run on
every `make quality` invocation. The current guard count is 27 today and
projected to ~52 once the v2.0 rollout (Flask-to-FastAPI) and the CI/pre-commit
refactor (issue #1234) finish landing their additions. At the current cadence
of new-guard creation we project 75-100 guards within five years.

Two cost curves matter:

1. **Wall-clock budget.** `tox -e unit` is held to a 2-minute budget. Per-guard
   cost averages ~1 second; at ~75 guards we exceed budget.
2. **Cognitive overhead.** Every guard is a rule a contributor must understand
   when reading a failure. A long-lived guard whose underlying invariant is now
   enforced upstream (by ruff, mypy strict mode, an LSP, or a semgrep rule)
   costs review time without paying for itself.

Without an explicit retirement process, guards accumulate indefinitely. Path
dependence sets in: nobody knows whether a 3-year-old guard is still earning
its keep, so it stays. We need documented criteria so retirement is a routine
operation rather than a contentious one.

## Decision

A guard MAY be retired when ANY of the following criteria are met:

1. **Subsumed by upstream tooling.** The invariant is now provably enforced by
   a tool present in CI — a ruff custom rule, a mypy strict-mode check, a
   zizmor audit, etc. *Provable* means: introducing a fake violation triggers
   the upstream tool's failure (demonstrated in the retirement PR).
2. **Six months of zero violations.** The invariant has held with zero
   violations for ≥6 months across all PRs. The pattern it forbade no longer
   exists in the codebase, and no contributor has tried to introduce it. The
   guard is dead code that pays nothing.
3. **No longer relevant.** The pattern the guard forbade has been removed
   from the codebase entirely. Example: the `tenant.config` access guard
   becomes irrelevant once all `tenant.config` access is removed and the
   column is dropped.
4. **Strict subset of another guard.** The guard's checks are wholly contained
   in another guard's checks. Consolidate to reduce surface area.

**Process.** Retirement requires a PR that:

- Deletes the guard test file (`tests/unit/test_architecture_<name>.py`).
- Removes the row from the CLAUDE.md guards table.
- Cites the criterion met in the PR description.
- Attaches proof: a link to the ruff rule that subsumes it (criterion 1), a
  6-month audit log of green runs (criterion 2), a grep showing the pattern
  is gone (criterion 3), or a diff showing the consolidation (criterion 4).

CODEOWNERS review applies (the architecture-guards path is in CODEOWNERS).

## Options considered

**Option A — Never retire.** Rejected. Long-term maintainability collapse;
runtime budget exhausted by year 5; cognitive overhead grows unboundedly.

**Option B — Auto-retire by date (e.g., guards expire after 2 years).**
Rejected. Architecture invariants don't have a shelf-life — a 10-year-old
invariant about transport layering is just as valid as a freshly-added one.
Auto-retirement risks losing genuinely-needed guards.

**Option C — Manual retire with documented criteria.** Chosen. Retirement is a
deliberate act with evidence; the four criteria cover the realistic reasons a
guard might no longer earn its keep.

**Option D — Convert all guards to ruff custom rules at the outset.**
Rejected. Ruff custom rules don't yet cover the full range of invariants
(e.g., CLAUDE.md table sync, ADR existence, allowlist stale-detection). ADR-005
explains the boundary between pytest fitness functions and external tools.

## Consequences

**Positive.**
- Guard count growth is sustainable, not path-dependent.
- Contributors have a clear playbook for proposing retirement.
- Retirement evidence becomes part of the PR record, audit-trail-quality.
- Quarterly review keeps the inventory honest.

**Negative.**
- Process overhead per retirement (PR with evidence, CODEOWNERS review).
- Criterion 2's "6 months of zero violations" requires audit infrastructure
  (CI history search). Tooling for this is straightforward (`gh api` query
  over the last 6 months of workflow runs) but not yet automated.
- Some genuine-but-low-frequency invariants might be retired prematurely
  under criterion 2 — mitigated by the requirement that the pattern *also*
  be absent from the current codebase.

## Tripwire

When the live guard count exceeds **75**, schedule a quarterly retirement
review. The review:

1. Audits all guards against the four criteria.
2. Files retirement PRs for guards that meet a criterion.
3. Reports the new total in the CLAUDE.md guards table preamble.

Independent of count, this ADR is reviewed annually (every April) to confirm
the criteria are still appropriate. If ruff/mypy/LSP capabilities expand
substantially, criterion 1 may become the dominant retirement path.
