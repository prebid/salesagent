# ADR-003: pull_request_target trust boundary for CLA and PR-title workflows

**Status:** Accepted (PR 1 / issue #1234)
**Date:** 2026-05
**Deciders:** @chrishuie

## Context

GitHub's `pull_request` event runs with read-only permissions for fork PRs — it
cannot write comments, labels, or statuses. Some workflows legitimately need to
write back to a PR from a fork (e.g., CLA bot comments, PR-title feedback).

`pull_request_target` solves this: it runs in the context of the base branch
(not the fork), so it has write access. The risk is that `pull_request_target` +
`actions/checkout@ref:${{ github.event.pull_request.head.sha }}` creates a
privilege-escalation path where a malicious fork PR can execute arbitrary code
with write access to the base repo.

## Decision

`pull_request_target` is permitted ONLY in workflows that:

1. Do NOT check out the PR's source code (no `actions/checkout` against the head SHA).
2. Use only `github.event.pull_request.*` metadata (title, labels, author, etc.) — not code.
3. Are explicitly labeled `# trust-boundary: pull_request_target` with a comment justification.

Current approved uses:
- `.github/workflows/pr-title-check.yml` — reads `github.event.pull_request.title`, writes a
  PR status check. No code checkout.

**IPR Agreement is no longer a `pull_request_target` consumer (#1669):**
- Path A (verify) uses `pull_request` with a read-only `gh`/Python check against
  `signatures/ipr-signatures.json` (no PR head checkout, no PR-comments API).
  Tip workflow durability requires `pull_request` — `pull_request_target` always
  loads the base-branch workflow copy, so verify/sign hardening would not apply
  until merge.
- Path B (sign) uses `issue_comment` → CLA Assistant (write) after API health wait,
  then re-runs the same read-only verify and re-triggers failed `ipr-check` jobs
  on the PR head SHA.
- Residual trust note: because verify YAML comes from the PR tip, a hostile tip
  could gut the job while keeping the check name. Accepted for now —
  `ipr-check` is **not** in the org required-checks ruleset (compliance-gate
  integrity, not secret escalation). Revisit if `ipr-check` becomes required.

Future `pull_request_target` additions require a separate ADR entry and @chrishuie review.

## Consequences

**Good:**
- CLA and PR-title checks can write statuses/comments on fork PRs.
- Explicit policy prevents accidental privilege escalation in future workflows.

**Bad / tradeoffs:**
- Workflow authors must consciously opt into the trust boundary rule.
  Enforced by zizmor's `pull-request-target` finding and the `.github/zizmor.yml` allowlist
  (currently `pr-title-check.yml` only for PRT; IPR verify moved off PRT in #1669).
- IPR tip-YAML residual (hostile gut of `ipr-check`) — see approved-uses note above.

## Alternatives considered

**Use `pull_request` for everything** — rejected; GitHub returns 403 on write
operations from fork PRs with the `pull_request` event.

**Use a separate bot token** — rejected; adds secret management complexity for
two simple metadata-only workflows.

## Review trigger

Revisit if a new workflow needs `pull_request_target` for code checkout. At that
point, consider using environment protection rules instead.
