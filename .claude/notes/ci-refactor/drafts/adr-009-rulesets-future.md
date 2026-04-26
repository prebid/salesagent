# ADR-009 — GitHub Rulesets as future replacement for classic Branch Protection (deferred)

## Status

Deferred. Tracked but not adopted within #1234.

## Context

GitHub Rulesets (GA 2024) supersede classic Branch Protection for new repositories created
on github.com. Rulesets:

- Are layerable (multiple rulesets can apply to a single ref)
- Are exportable as code (REST API + Terraform support)
- Support file-path scoping (e.g., changes under `.github/workflows/` require CODEOWNERS review)
- Coexist with classic Branch Protection during migration (the more restrictive rule wins)
- Can be created/edited via `gh api repos/.../rulesets` (different endpoint than classic protection)

Plan #1234 uses classic Branch Protection (`gh api -X PATCH .../branches/main/protection`).
The `flip-branch-protection.sh` script and the D17/D26 framing both assume classic Branch
Protection contexts, not Ruleset rules.

## Decision

**Don't migrate to Rulesets within #1234.** The migration is a Q3 2026 follow-up after
#1234 closes.

Reasoning:
- #1234's scope is supply-chain hardening (governance + SHA-pinning + workflow architecture +
  hook discipline + image supply chain). Adding a protection-mechanism migration would dilute
  scope and increase risk during the 6-week rollout.
- Classic Branch Protection is not deprecated as of 2026-04-25 (no announced sunset date).
- The 14 frozen check names (D17) work identically under Rulesets, so migration is mechanical
  whenever it happens.

## Consequences

- Plan stays focused on supply-chain hardening, not protection-mechanism migration.
- Future migration adds layered protection (path-scoped CODEOWNERS rules) atop existing
  classic protection — this is additive, not destructive.
- Maintainers continue to use the existing `flip-branch-protection.sh` admin script.

## When to revisit

Revisit if any of:
- GitHub announces a sunset date for classic Branch Protection.
- The repo onboards a second maintainer and benefits from path-scoped review rules
  (e.g., "any change under `src/admin/csrf.py` requires Security Reviewer").
- Compliance requirement forces a Ruleset-only feature (e.g., signed commits enforced
  per-path, which is Ruleset-exclusive).

## References

- GitHub Rulesets documentation (docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets)
- D17 (frozen check names — work identically under Rulesets)
- D26 (workflow naming convention — works identically under Rulesets)
- `scripts/flip-branch-protection.sh` (classic Branch Protection PATCH script)
