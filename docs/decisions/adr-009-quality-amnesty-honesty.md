# ADR-009 — Honest policy for #1228 F1 / F2 / G2 (no fake "fix incrementally")

## Status

Proposed 2026-07-09 (awaiting @chrishuie / @ChrisHuie sign-off on this PR).

## Context

Issue [#1228](https://github.com/prebid/salesagent/issues/1228) Cluster F/G called out
**permanent amnesty disguised as incremental cleanup**:

| ID | Surface | What #1228 found |
|----|---------|------------------|
| **F1** | `pyproject.toml` `[tool.ruff.lint].ignore` | Rules ignored with stale violation counts and "fix incrementally" comments — no ratchet, so debt can grow forever while CI stays green |
| **F2** | `mypy.ini` TODO block | `check_untyped_defs` / `disallow_incomplete_defs` / `warn_return_any` left off with "enable incrementally" since 2025-12 — no progress mechanism |
| **G2** | `obligation_coverage_allowlist.json` (~301 IDs) | Framed as "no decrement mechanism" / largest stagnation risk |

Mechanical #1228 residuals (A4, A5, B1, C5, C7, D2, E1, E2, H2) are already closed on
`main` (see [status comment](https://github.com/prebid/salesagent/issues/1228#issuecomment-4930137012)).
These three need an **explicit product/quality decision**, not another silent config tweak.

Measured cost of flipping F1/F2 on today (2026-07-09, `src/`):

| Switch | Approx. failures |
|--------|------------------|
| Ruff `C901` alone | ~186 |
| Ruff `PLR091*` + `PLR2004` + `B904` + `F403` | ~614 combined |
| Mypy `--check-untyped-defs` | ~226 errors / 33 files |

Enabling any of these as hard gates without a plan would red-main the repo. Building
per-rule ratchets (like `.type-ignore-baseline`) is valuable **follow-up work**, but only
after we stop lying in comments about incremental progress that is not happening.

## Decision

### F1 — Ruff ignores: split "style noise" vs "small debt"

**Permanently accepted** (complexity / magic-number style — not bug finders at current
thresholds; enabling them is a multi-sprint refactor, not a CI flip):

- `C901`, `PLR0911`, `PLR0912`, `PLR0913`, `PLR0915`, `PLR2004`

Comments in `pyproject.toml` must say **permanently accepted (ADR-009)**, not
"fix incrementally".

**Justified keep** (already had honest reasons; counts may drift — cleanup is welcome
but not gated):

- `E501`, `E402`, `E741`, `B027`, `F841`, `F821`, `F405`
- `B904`, `F403`, `E722` — still desirable to clean up; tracked as ordinary tech debt,
  **not** as an implied ratchet. Prefer fixing in the file you touch (boy-scout) over a
  mass campaign.

**Out of scope for this ADR:** implementing `.ruff-complexity-baseline` / per-rule
violation counters. If we want ratchets later, file a follow-up issue after this ADR is
Accepted — do not reintroduce "fix incrementally" without a machine-enforced ceiling.

### F2 — Mypy lenient flags: permanently deferred for current pin

Keep current defaults:

- `check_untyped_defs = False`
- `disallow_incomplete_defs = False`
- `warn_return_any = False`
- `disallow_untyped_defs = False`

Replace the stale "TODO: Enable these incrementally" block with an explicit
**permanently deferred (ADR-009)** note. Enabling any of these is a dedicated initiative
(hundreds of errors); it is not implied by day-to-day PRs.

Existing ratchets that **do** enforce type-safety progress remain:

- `.type-ignore-baseline` + `check_type_ignore_count.py` (no new `# type: ignore`)
- `mypy` in Quality Gate / pre-commit on the current config

### G2 — Obligation allowlist: current shrink ratchet is the policy

**Correction to #1228:** G2 is **not** "no decrement mechanism".
`tests/unit/test_architecture_obligation_coverage.py` already enforces:

1. Every behavioral obligation is covered **or** allowlisted.
2. Allowlist entries that gain a `Covers:` test must be removed (stale-entry fail).
3. Allowlist size must equal `behavioral - covered` (exact match).

**Accepted policy:**

- Allowlist **may grow** when new behavioral obligations are documented without tests
  (intentional backlog; reviewable in the PR that adds the obligation + allowlist row).
- Allowlist **must shrink** when coverage lands (already CI-enforced).
- We do **not** add a hard numeric ceiling in this PR (would block legitimate new
  obligation docs). Optional follow-up: a soft dashboard / issue template, not a gate.

Document this in the guard module docstring so the next reader does not re-open G2 as
"unratcheted amnesty".

## Consequences

**Good:**

- #1228 F1/F2/G2 become closable with an honest paper trail.
- New contributors stop reading "fix incrementally" as a promise CI does not keep.
- Reviewers can reject PRs that re-add amnesty language without an ADR update.

**Tradeoffs:**

- Complexity / untyped-defs debt will not auto-shrink via CI.
- Progress on B904/F403/E722 and mypy strictness depends on deliberate follow-up work.
- Chris (or maintainer) must explicitly Accept this ADR (or request ratchets instead)
  before we mark #1228 F/G closed.

## Alternatives considered

**Ratchet everything now** — rejected for this PR. Per-rule ruff baselines + mypy
strictness campaigns are large, easy to get wrong, and need buy-in on *which* rules
deserve ceilings. This ADR unblocks that conversation instead of shipping a half-baked
counter.

**Delete the ignores / enable mypy flags** — rejected; ~800+ ruff hits and ~226 mypy
errors would brick CI with no remediation plan.

**Leave "fix incrementally" comments** — rejected; that is the defect #1228 named.

## Review trigger

Revisit when:

1. A follow-up issue implements machine-enforced ceilings for any F1 rule or F2 flag, or
2. Obligation allowlist size becomes a release-blocking concern (then consider a soft
   budget + exception process).

## References

- [#1228](https://github.com/prebid/salesagent/issues/1228) Cluster F / G
- Existing ratchets: `.type-ignore-baseline`, `.duplication-baseline`, `.coverage-baseline`
- Guard: `tests/unit/test_architecture_obligation_coverage.py`
