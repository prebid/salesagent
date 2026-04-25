# D. Self-sufficiency score per per-PR spec

Rating scale: A (cold-start executable, no questions), B (minor clarifications needed), C (major gaps; agent escalates), D (incomplete in load-bearing ways), F (unauthored).

| PR | Rating | Justification |
|---|---|---|
| **PR 1** | **A** | 11 commits, each with explicit verification one-liners. Embedded drafts for SECURITY.md, CODEOWNERS, dependabot.yml, ADR-002, ADR-003, security.yml, codeql.yml, codeql-config.yml. Commit 9's SHA-resolution loop is verbatim copy-pasteable. The only ambiguity: "fill prose for CONTRIBUTING.md from these bullets" is judgment-call but the bullets are detailed. Edge case: if `pre-commit autoupdate --freeze` breaks an unexpected hook, D12 tripwire is documented. |
| **PR 2** | **B** | 9 commits, ordering load-bearing and explicit. The pydantic.mypy fix commit (commit 3) cannot pre-specify exact code edits because the errors depend on actual mypy output. D13 tripwire (>200) is the only escape hatch. **Minor clarification needed:** what counts as "real type bug fixed" vs "Pydantic-internal `# type: ignore`" requires judgment. Spec says "inline `# type: ignore[arg-type]` is acceptable for genuinely-Pydantic-internal cases" but doesn't enumerate examples beyond CLAUDE.md Pattern #4. A fresh agent can ship but may flag-flop on a few choices. |
| **PR 3** | **B** | 10 Phase A commits + admin Phase B + 2 Phase C commits. Phase A is fully self-contained with embedded YAML for `_pytest.yml`, `ci.yml`, `setup-env/action.yml`, `migration_roundtrip.sh`. **Minor clarification needed:** the spec corrects two architectural choices vs the issue (`_setup-env` is a composite action; `_postgres.yml` collapses into `_pytest.yml`) — fresh agent must read this carefully or they'll over-design. **Phase B explicitly delegated to user**; spec is clear about USER vs AGENT actions. **Phase C is trivial** (delete `test.yml`, update one doc section). |
| **PR 4** | **B** | 10 commits with strict ordering (guards before hook deletions). Embedded code for 5 new guards + helper module. **Minor gap:** `test_architecture_import_usage.py` is described as "ports `.pre-commit-hooks/check_import_usage.py` (243 LOC) to AST" without showing the actual port. A fresh agent must read the original hook and translate; not trivial but tractable. **Red-team test list is explicit** which prevents R7. |
| **PR 5** | **B** | 8 commits. Each surface (Python, Postgres, uv, target-version) is clearly scoped with verification. **Minor clarification needed:** the pre-flight measurement for black/ruff target-version py312 may produce a large diff; spec says "commit the reformat as a separate commit immediately after" but doesn't pre-specify content. Fresh agent decides. **Embedded uv-version structural guard test** is full code. |
| **PR 6** | **D** | **Unauthored.** D-pending-4 deferred this from the 5-PR rollout. No spec exists in `.claude/notes/ci-refactor/`. The user's prompt assumes PR 6 commits 1-2 are merged and commit 3 is in progress, but the spec a fresh agent would load doesn't exist. To make PR 6 self-sufficient: author `pr6-fortune-50-hardening.md` similar in shape to the existing 5 specs, covering harden-runner audit→block flow, SBOM generation, sigstore/cosign release-tag signing, and any other Fortune-50 patterns deferred from the issue. Until that spec exists, a fresh agent at Point 5 can only reconstruct intent from D-pending-4 + StepSecurity docs + git log. |

**Overall observations:**
- **A-grade PR 1** because it's almost entirely additive with embedded drafts; the only judgment calls are well-bounded
- **B-grade PRs 2-5** because each has 1-2 "judgment moments" (mypy fix scope, CONTRIBUTING.md prose, Pydantic-internal type-ignore policy, target-version reformat scope) but each has a clear tripwire/escape hatch
- **D-grade PR 6** because no spec exists; would require authoring before Point 5's scenario can be agent-executed
- **No C or F-grades** in the existing 5; the spec quality is uniformly high

**Action item:** Author `pr6-fortune-50-hardening.md` to bring PR 6 to B-grade before the rollout reaches Week 5.

---

# Summary

The plan is largely cold-start-executable. Two gaps:

1. **No "if you only read ONE file" executive summary exists.** Recommend creating `.claude/notes/ci-refactor/EXECUTIVE-SUMMARY.md` (~180 lines, ~3k tokens) that collapses 00-MASTER-INDEX + 03-decision-log + 02-risk-register highlights. This drops cold-start budget from ~22-28k to ~14-20k tokens and gives a single canonical pointer.

2. **PR 6 spec is unauthored.** D-pending-4 deferred Fortune-50 hardening to a follow-up, but Point 5's scenario assumes PR 6 is in flight. If PR 6 is genuinely planned, author `pr6-fortune-50-hardening.md` to bring it to PR 1-5 quality. If PR 6 is genuinely out-of-scope, document that in the executive summary and Point 5 becomes hypothetical.

The continuity hygiene rules above (15 of them) and the per-PR spec ratings (A, B×4, D×1) characterize self-sufficiency precisely. PRs 1-5 are agent-executable from a fresh session given their specs + decisions + risks; PR 6 is not yet authored.
