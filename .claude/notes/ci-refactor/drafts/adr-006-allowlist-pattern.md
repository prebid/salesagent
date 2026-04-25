# ADR-006: Allowlist pattern for structural guards

## Status

Accepted (2026-04-25). Implemented in PR 4 of the CI/pre-commit refactor
(issue #1234). Tripwire: schedule a fix-and-shrink sprint when total
allowlist entries across all guards exceeds 200.

## Context

Some structural guards have known pre-existing violations that cannot be fixed
at guard-introduction time. The `nested_serialization` guard, for instance,
introduces ~20 violations on day one — fixing them all in the introducing PR
would balloon scope and delay the protection from new violations.

We need a documented mechanism for tracking "known violations to be fixed
later" without abandoning the invariant. Without this:

1. Contributors disable the guard entirely (defeats its purpose).
2. Or block all work until violations are zero (impractical for guards with
   20+ pre-existing violations).
3. Or use ad-hoc inline `# noqa`-style comments without provenance.

Allowlist drift is a known failure mode: an entry is added because the
violation can't be fixed today, but six months later the violation has been
removed and nobody updates the allowlist. The list grows stale, then
contributors lose trust in it.

## Decision

A three-tier allowlist pattern:

**1. In-module set literal (PRIMARY).** The guard test file declares
`_KNOWN_VIOLATIONS: set[tuple[...]]` at the top. Each entry has a
`# FIXME(salesagent-XXXX)` trailing comment with a tracking-issue reference.

```python
_KNOWN_VIOLATIONS: set[tuple[str, str]] = {
    ("src/admin/blueprints/legacy.py", "model_dump"),  # FIXME(salesagent-1245)
    ("src/core/policy/legacy.py", "session.query"),    # FIXME(salesagent-1246)
}
```

Easy review, type-checked tuples, lives next to the test logic.

**2. Inline `# arch-ignore: <guard-id> -- <reason>` (SECONDARY).** Used when
the violation is intentional and unlikely to recur. Mirrors `# noqa: <code>`
and `# zizmor: ignore[...]` precedent, so contributors recognize it.

```python
session.query(Tenant)  # arch-ignore: no-session-query -- bulk import path; ADR-006
```

**3. Central `architecture_allowlist.yml` (FORBIDDEN).** Splits logic across
files; no type checking; harder to audit. Do not introduce.

**Stale-detection mandate.** Every allowlist must pair with an
`assert_violations_match_allowlist` helper call. Entries that reference files
or finding-tuples no longer present in the codebase cause the test to fail.
This prevents allowlist rot — the moment a violation is fixed, the
allowlist must shrink to match.

**Allowlist rules.**

- New violations cannot be added without explicit ADR-level escalation.
- Allowlists may only shrink; PRs cannot grow them.
- Each entry has a FIXME comment with a tracking ref (issue, PR, or `salesagent-XXXX`).
- The stale-entry test removes the temptation to leave dead entries in place.

## Options considered

**Option A — In-module + inline (chosen).** Leverages existing patterns
(`# noqa`, `# zizmor: ignore[...]`); auditable; type-checked.

**Option B — Central YAML file.** Rejected. Splits the invariant across two
files; no type checking; allowlist drift is harder to detect; review surface
is larger.

**Option C — No allowlist (zero-tolerance everywhere).** Rejected.
Impractical for guards introducing 20+ pre-existing violations like
`nested_serialization`. Forces guards to be deferred until full remediation,
which means new violations slip in during the deferral window.

**Option D — Database-backed allowlist (separate service).** Rejected.
Over-engineering. The set of allowlist entries is small (low hundreds at
most), the read/write pattern is "edit alongside the code change," and
adding a service dependency adds CI complexity for no benefit.

## Consequences

**Positive.**
- Gradualism allowed: a guard can ship while pre-existing violations are
  triaged separately.
- Ratchet mechanism: every fix removes an entry; allowlist count strictly
  decreases over time.
- FIXME comments give every entry traceable provenance.
- Stale-detection test prevents drift.

**Negative.**
- Allowlist drift if FIXME tracking lapses (mitigated by stale-detection).
- Two patterns (in-module set, inline comment) means contributors must learn
  both — but each maps to existing precedent (noqa/zizmor).
- Type-checking the in-module sets requires the guard test file to use
  consistent tuple shapes; minor maintenance cost.

## Tripwire

When the **total allowlist entries across all structural guards exceeds 200**,
schedule a fix-and-shrink sprint:

1. Inventory entries by guard and by FIXME issue.
2. Group by file/owner; remediate the largest clusters first.
3. Drop the count below 100 before the sprint closes.

Independent of count, every quarter the maintainer audits the FIXME refs to
confirm tracking issues are still active. Closed issues should already have
removed their allowlist entries; lingering refs are a process bug.
