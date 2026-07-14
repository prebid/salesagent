# ADR-009 — Honest policy for #1228 F1 / F2 / G2 (ratchet what bites; accept low-signal)

## Status

Proposed 2026-07-09; **revised 2026-07-13** per [@ChrisHuie](https://github.com/ChrisHuie)
review on [#1579](https://github.com/prebid/salesagent/pull/1579) (aligns with
[@KonstantinMirin](https://github.com/KonstantinMirin) preference for ratchet over permanent amnesty);
**honesty fix 2026-07-14** (C901 table → 186; drop inline stale counts from ratchet comments).
Awaiting re-Accept on this PR.

## Context

Issue [#1228](https://github.com/prebid/salesagent/issues/1228) Cluster F/G called out
**permanent amnesty disguised as incremental cleanup**:

| ID | Surface | What #1228 found |
|----|---------|------------------|
| **F1** | `pyproject.toml` `[tool.ruff.lint].ignore` | Rules ignored with stale violation counts and "fix incrementally" comments — no ceiling, so debt can grow forever while CI stays green |
| **F2** | `mypy.ini` TODO block | `check_untyped_defs` / related flags left off with "enable incrementally" since 2025-12 — no progress mechanism |
| **G2** | `obligation_coverage_allowlist.json` | Framed as "no decrement mechanism" / largest stagnation risk |

Mechanical #1228 residuals (A4, A5, B1, C5, C7, D2, E1, E2, H2) are already closed on
`main`. These three need an **explicit quality decision**.

Measured cost of enabling F1/F2 as **hard gates** today (`src/`, locked tooling):

| Switch | Failures |
|--------|----------|
| Ruff `C901` alone | owned by `.ruff-complexity-baseline` after #1613 (do not restale inline counts) |
| Ruff `PLR091*` + `PLR2004` + `B904` + `F403` | ~614 combined (2026-07-09 manual snapshot) |
| Mypy `--check-untyped-defs` | ~226 errors / 33 files (2026-07-09 manual snapshot) |

A **count ratchet** is not the same cost: freeze today's number, fail only on an
increase, auto-lower on a decrease — zero forced remediation of existing code. We
already run that pattern for `.type-ignore-baseline` and `.duplication-baseline`.

## Decision

### F1 — Ruff: ratchet pure-complexity; permanently accept low-signal style rules

**Ratchet targets** (follow-up: `.ruff-complexity-baseline`, near-copy of
`check_type_ignore_count.py` — implemented in [#1613](https://github.com/prebid/salesagent/pull/1613) / [#1610](https://github.com/prebid/salesagent/issues/1610)):

- `C901` — complexity
- `PLR0912` — too-many-branches
- `PLR0915` — too-many-statements

These are where new code most needs a mechanical stop. Comments say **ratchet
target (ADR-009)** — not "fix incrementally" and not "permanently accepted".
Mechanical ceiling: [#1613](https://github.com/prebid/salesagent/pull/1613).

**Permanently accepted** (linter is low-signal at current thresholds):

- `PLR0911` — too-many-return-statements (guard-clause returns)
- `PLR0913` — too-many-arguments (wide spec-mirroring signatures)
- `PLR2004` — magic-value comparisons (inline constants often readable)

Comments must say **permanently accepted (ADR-009)**.

**Justified keep / boy-scout debt** (not a ratchet; clean up when touching):

- `E501`, `E402`, `E741`, `B027`, `F841`, `F821`, `F405`
- `B904`, `F403`, `E722`

**Ratchet guardrail:** a baseline going **up** requires review justification. The
`--update-baseline` path rewrites a tracked file — increases must be contested in
review, or the ratchet decays into the same false "looks-enforced" #1228 named.

### F2 — Mypy lenient flags: follow-up ratchet, not permanently deferred

Keep current defaults **for now** (`check_untyped_defs` / `disallow_incomplete_defs` /
`warn_return_any` / `disallow_untyped_defs` = `False`).

**Do not** label this permanently deferred. Policy:

- Primary target: a **`check_untyped_defs` error-count ratchet** (same move as
  `.type-ignore-baseline`), tracked as [#1611](https://github.com/prebid/salesagent/issues/1611).
- Caveat: mypy counts drift with tool/plugin versions — scope carefully; not this PR.
- Until that lands, day-to-day type-safety progress remains
  `.type-ignore-baseline` + current Quality Gate mypy config.

Comments in `mypy.ini` must say **follow-up ratchet (ADR-009)**, not "permanently
deferred" or "enable incrementally" without a ceiling.

### G2 — Obligation allowlist: document the existing exact-match ratchet

**Companion PR:** [#1612](https://github.com/prebid/salesagent/pull/1612) / [#1609](https://github.com/prebid/salesagent/issues/1609) lands the guard module docstring (this ADR PR stays policy/config only).

**Correction to #1228:** G2 already has a decrement mechanism.
`tests/unit/test_architecture_obligation_coverage.py` enforces covered-or-allowlisted,
stale-entry removal, and exact size match (`behavioral - covered`).

**Policy:**

- Allowlist **may grow** when new behavioral obligations are documented without tests
  (intentional backlog; reviewable in that PR).
- Allowlist **must shrink** when a `Covers:` test lands (already CI-enforced).
- No hard numeric ceiling in this PR.

**Reconcile with "allowlists can only shrink"** (CLAUDE.md / structural-guard rule):
that rule applies to **violation** allowlists (code that breaks an invariant — new
rows are new debt). The obligation-coverage file is a **coverage backlog**: growth
means "we documented an obligation we have not tested yet," which is reviewable
progress, not silent amnesty. Covered IDs must still leave the list. Same intent
(no fake incremental cleanup); different object.

## Consequences

**Good:**

- F1/F2 stop promising progress CI does not enforce; ratchet intent is explicit.
- Complexity growth gets a machine ceiling once the follow-up baseline lands.
- G2 docstring matches the exact-match test and no longer contradicts the general
  shrink-only guard rule without explanation.
- Reviewers can reject `--update-baseline` increases without justification.

**Tradeoffs:**

- This PR is **policy + comment honesty only**.
- Mechanical F1 ceiling ships in [#1613](https://github.com/prebid/salesagent/pull/1613);
  F2 mypy untyped-defs counter remains [#1611](https://github.com/prebid/salesagent/issues/1611);
  G2 docstring ships in [#1612](https://github.com/prebid/salesagent/pull/1612).

## Alternatives considered

**Permanently accept all F1 complexity rules** — rejected on review. A count ratchet is
cheap; permanent accept removes the mechanical stop where new code hurts most.

**Enable the rules / mypy flags as hard gates now** — rejected; ~800+ ruff hits and
~226 mypy errors would brick CI with no remediation plan.

**Leave "fix incrementally" / "permanently deferred" comments** — rejected; that is
the defect #1228 named (or its inverse overclaim).

**Implement F1 + F2 ratchets in this PR** — deferred to follow-ups so the decision
record can land without coupling policy Accept to a new hook.

## Review trigger

Revisit when:

1. `.ruff-complexity-baseline` (C901 / PLR0912 / PLR0915) merges, or
2. `check_untyped_defs` error-count ratchet merges, or
3. Obligation allowlist size becomes a release-blocking concern.

## References

- [#1228](https://github.com/prebid/salesagent/issues/1228) Cluster F / G
- [#1579](https://github.com/prebid/salesagent/pull/1579) decision PR
- Existing ratchets: `.type-ignore-baseline`, `.duplication-baseline`, `.coverage-baseline`
- Guard: `tests/unit/test_architecture_obligation_coverage.py`
