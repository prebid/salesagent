# Architecture Decision Records (ADRs)

This directory contains **Architecture Decision Records** — short documents that
capture *why* a significant technical decision was made, not just *what* was decided.

## What is an ADR?

An ADR answers three questions:

1. **Context** — What problem were we solving? What constraints existed?
2. **Decision** — What did we choose to do?
3. **Consequences** — What are the trade-offs? When should this be revisited?

ADRs are written *at decision time*, kept short (~1 page), and never deleted —
even if the decision is later reversed. A superseded ADR is marked as such and
a new one is written explaining the reversal.

## Why bother?

Without ADRs, institutional knowledge lives in people's heads or gets lost in
PR comment threads. Six months later, nobody remembers why a particular approach
was chosen, and the team risks repeating the same analysis — or worse, undoing
a decision without understanding its rationale.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [ADR-001](adr-001-single-source-pre-commit-deps.md) | uv.lock as single source of truth for pre-commit deps | Accepted |
| [ADR-002](adr-002-solo-maintainer-bypass.md) | Solo-maintainer branch protection with bypass | Accepted |
| [ADR-003](adr-003-pull-request-target-trust.md) | pull_request_target trust boundary for CLA and PR-title workflows | Accepted |

## How to add a new ADR

1. Copy the structure from an existing ADR (Context / Decision / Consequences / Alternatives / Review trigger).
2. Name it `adr-NNN-short-description.md` with the next sequential number.
3. Add it to the index table above.
4. Reference it from the PR or commit that implements the decision.
