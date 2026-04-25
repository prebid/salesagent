# ADR-005: Architectural fitness functions vs external tools

## Status

Accepted (2026-04-25). Implemented across PR 1 (external tools: zizmor, CodeQL,
pip-audit) and PR 4 (pytest fitness functions: ~16 new structural guards) of
the CI/pre-commit refactor (issue #1234). Tripwire: revisit when ruff/mypy
strict-mode subsumes ≥3 existing guards; consolidate via ADR-004.

## Context

The CI/pre-commit refactor introduces both **pytest structural guards** (~16 new
tests under `tests/unit/test_architecture_*.py`) AND **external tools** (zizmor,
pinact, OpenSSF Scorecard, harden-runner, dependency-review, CodeQL,
attest-build-provenance). Both are mechanisms for enforcing repo invariants.
Choosing one over the other has been ad-hoc to date.

Without an explicit principle, future contributors will re-litigate the choice
in every PR review: should this new check be a pytest test or a CI-only
linter? The cost of inconsistency is real — invariants drift between layers,
and contributors are unsure where to add a new check.

We have prior art. Ford, Parsons, and Kua's *Building Evolutionary
Architectures* coined "architectural fitness functions" — automated checks
that enforce architectural characteristics. The Python ecosystem has
implementations: `pytest-archon`, `import-linter`. The supply-chain-security
ecosystem has its own family of tools (zizmor, OpenSSF Scorecard, pinact). The
question is not "which tool is best" but "which mechanism fits which
invariant."

## Decision

A three-layer choice rule:

**Pytest fitness function** (`tests/unit/test_architecture_*.py`) when:
- The invariant is statically checkable from local files (AST traversal,
  regex, file existence, YAML/TOML parse).
- The invariant is project-specific — no off-the-shelf tool encodes it.
- We want feedback at `make quality` time (fast local iteration).
- Examples: CLAUDE.md guards-table sync, ADR existence with `## Status`
  section, hook-coverage map validity, anchor-version consistency, code-shape
  patterns (no `session.query`, no `ToolError` in `_impl`, no raw `select()`
  outside repositories).

**External tool** (CI workflow) when:
- The invariant is workflow- or supply-chain-security flavored: zizmor for
  workflow permissions and dangerous triggers; pinact for action SHA-pinning;
  OpenSSF Scorecard for composite signal; harden-runner for runtime egress
  control; CodeQL for static security analysis.
- The tool encodes industry-consensus rules we do not want to re-derive.
- We benefit from the tool's published rule library, signature/Sigstore
  integration, or SARIF interoperability.

**Verify-script** (`.claude/notes/ci-refactor/scripts/verify-pr*.sh`,
maintainer-run) when:
- The invariant requires admin-scope `gh api` calls (branch protection
  configuration, repo settings, secret scanning state).
- The check is a one-shot pre-flight or rollback-validation step, not a
  per-PR gate.
- Runtime measurement is required (timing benchmarks, latency assertions).
- The check asserts on external state (CodeQL findings count for ADR-NNN
  tripwire, Dependabot PR backlog, SARIF inventory).

## Options considered

**Option A — All-pytest.** Rejected. Reinvents zizmor's workflow-security
rules, CodeQL's SAST queries, and Sigstore's signing logic in pytest. Slow
local feedback for security checks (CI is the right layer). Doesn't leverage
industry consensus.

**Option B — All-external-tool.** Rejected. Project-specific invariants like
CLAUDE.md table sync, ADR existence, and hook-coverage-map validity have no
off-the-shelf tool. Pytest is the right mechanism — it gives a `pytest -k`
debug experience that an external linter can't.

**Option C — All-verify-script.** Rejected. Humans don't run scripts
reliably; CI/auto-enforcement is the load-bearing control. Verify-scripts
are a complement to, not a replacement for, automated mechanisms.

**Option D — Three-tier (chosen).** Best fit for the actual invariant nature.
Each layer has a clear purpose; ambiguity at the boundary is documented and
resolved by ADR.

## Consequences

**Positive.**
- Right tool for the job: fast feedback for local code-shape invariants;
  industry-standard tools for security; targeted scripts for admin checks.
- Each layer has a recognizable failure mode; debugging is faster.
- New invariants have a documented decision tree for placement.

**Negative.**
- Three mechanisms to maintain, document, and onboard contributors to.
- Boundary classification is occasionally ambiguous (is "workflow uses SHA
  pins" a project rule or a security rule?). Resolution: defer to where the
  upstream tool already implements it (zizmor + pinact own this).
- Tooling drift over time: ruff/mypy strict-mode may eventually subsume some
  guards. ADR-004 governs that retirement.

## Tripwire

Revisit when ruff or mypy strict-mode subsumes ≥3 existing pytest fitness
functions (a tipping point indicating "the platform layer caught up"). At
that point:

1. Run an audit using the criteria in ADR-004.
2. File retirement PRs for the subsumed guards.
3. If the trend continues for >12 months, consider revising this ADR's
   default toward "external tool first" for new invariants.
