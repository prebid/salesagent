# ADR-002: Solo-maintainer branch protection with bypass

**Status:** Accepted (PR 1 / issue #1234)
**Date:** 2026-05
**Deciders:** @chrishuie

## Context

`main` requires passing status checks before merge. With a single maintainer
(@chrishuie), requiring a second approving review is counterproductive — it blocks
legitimate hotfixes and prevents the agent-assisted agentic workflow used by the
project.

GitHub's branch protection supports a "bypass actors" list that lets specific users
(or apps) merge without satisfying review-count requirements while still enforcing
status checks.

## Decision

Branch protection on `main` is configured as:

- **Required status checks:** all `CI / *` jobs, `Security / zizmor`, `CodeQL / analyze`
  (CodeQL becomes required after the 2-week advisory ramp — see D10).
- **Require a pull request before merging:** yes, but **dismiss stale reviews: yes**
  and **bypass actors: @chrishuie**.
- **Block force push:** yes (no bypass).
- **Require signed commits:** yes (no bypass).
- **Allow deletions:** no.

This gives @chrishuie the ability to merge PRs authored by agent accounts without
a second human reviewer, while the status-check requirement ensures CI passes.

## Consequences

**Good:**
- No blocked hotfixes on a solo project.
- Agent-authored PRs can be merged by the maintainer in a single review round.
- Force-push protection prevents rewriting shared history.

**Bad / tradeoffs:**
- If @chrishuie account is compromised, the bypass could be used to merge
  without CI. Mitigated by hardware MFA on the account (per SECURITY.md).
- No code review from a second human — accepted risk for a solo maintainer project.

## Alternatives considered

**Require two reviewers** — rejected; blocks the project entirely with a single
maintainer.

**No branch protection** — rejected; removes signed-commit, force-push, and
status-check requirements.

## Review trigger

Revisit if a second maintainer joins the project. At that point, remove the bypass
and require one approving review from a non-author maintainer.
