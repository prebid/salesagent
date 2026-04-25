# research/ — audit trail (do not edit)

These files are the round-3 / round-4 measurement and audit outputs that
produced the corrections now baked into the base plan files. Treat as
read-only audit trail; the base plan files (`00-MASTER-INDEX.md`,
`02-risk-register.md`, `03-decision-log.md`, `pr<N>-*.md`) supersede them.

| File | Purpose | Status |
|---|---|---|
| `empirical-baseline.md` | Measured numbers (40 hooks, 36 commit-stage, 26 guards, 55.56% coverage, 99 CSRF gap, 0/24 SHA-pinned, 0/8 persist-credentials). **Drift note:** updated 2026-04-25 — actual today is 41 hooks / 37 commit-stage / 23 SHA refs (one fewer site than baseline). | Live reference |
| `external-tool-yaml.md` | Production-ready YAML for zizmor, pinact, OSSF Scorecard, harden-runner, attest-build-provenance, dependency-review. **Updated 2026-04-25** — harden-runner block uses `disable-sudo-and-containers` (CVE-2025-32955 fix). | Live reference |
| `violation-strategy.md` | Strategy A/B/C per new guard + 7 backfill commits B1-B7 for PR 1 | Live reference |
| `integrity-audit.md` | 3 critical blockers + 7 dirty dimensions + refactor application order | Resolved (blockers fixed in specs; D26+D27 added) |
| `handoff-readiness-audit.md` | 14 blockers, 4 implicit assumptions, 13 missing artifacts | Superseded by `integrity-audit.md` |
| `edge-case-stress-test.md` | 30 failure modes, R11-R25 risks, Bayesian risk math | R11-R25 to be appended to `02-risk-register.md` (pending) |

**Do not delete this directory.** The audit trail demonstrates the basis
for D-numbered decisions and lets a reviewer trace any spec assertion back
to its measurement. If the corpus ever gets re-generated, restart from
this trail.
