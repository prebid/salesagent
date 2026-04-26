# `.claude/notes/ci-refactor/drafts/`

Staging directory for content authored at planning time and lifted into the codebase by the appropriate PR's executor.

## ADR location split

ADR-001, ADR-002, and ADR-003 are EMBEDDED in `pr1-supply-chain-hardening.md` (commits 1, 2, 3 author them inline). They are NOT staged here. The text in the PR 1 spec is the canonical source until extraction at commit time.

ADR-004 onward exist as standalone files in this directory because they were authored as separate planning artifacts in earlier rounds. Both forms produce equivalent files in `docs/decisions/` after their respective PRs land.

ADR drafts to extract during executor runs:

| ADR | Topic | Authored in | Extracts to |
|---|---|---|---|
| ADR-001 | Single-source pre-commit deps | `pr1-supply-chain-hardening.md` (inline) | `docs/decisions/adr-001-single-source-pre-commit-deps.md` |
| ADR-002 | Solo-maintainer branch protection | `pr1-supply-chain-hardening.md` (inline) | `docs/decisions/adr-002-solo-maintainer-bypass.md` |
| ADR-003 | `pull_request_target` trust | `pr1-supply-chain-hardening.md` (inline) | `docs/decisions/adr-003-pull-request-target-trust.md` |
| ADR-004 | Allowlist shrink-only | `drafts/adr-004-*.md` | `docs/decisions/adr-004-*.md` |
| ADR-005 | Fitness functions vs static linters | `drafts/adr-005-*.md` | `docs/decisions/adr-005-*.md` |
| ADR-006 | Inline allowlist pattern | `drafts/adr-006-*.md` | `docs/decisions/adr-006-*.md` |
| ADR-007 | Build provenance | `drafts/adr-007-build-provenance.md` | `docs/decisions/adr-007-build-provenance.md` |
| ADR-008 | Defer target-version bump | `drafts/adr-008-*.md` | `docs/decisions/adr-008-*.md` |
| ADR-009 | Rulesets future | `drafts/adr-009-*.md` | `docs/decisions/adr-009-*.md` |

## Conformance to ADR template

All ADRs (embedded or staged) should follow `templates/adr-template.md`. Existing drafts may need refresh during their PR commit time to conform.

## Other staged content

- `_architecture_helpers.py` — reconciled draft for the helpers module created by PR 2 (~30 lines) and extended by PR 4 (~221 lines).
- `guards/` — structural guard skeletons referenced by PR 4. Filenames match `test_architecture_*.py` pattern; in-file marker uses `arch_guard` (the new structural-guard marker; entity-marker `architecture` continues to apply via filename auto-tagging in `tests/conftest.py`).
- `claudemd-guards-table-final.md` — planned authoritative ~81-row guards table for CLAUDE.md (D18 revised Round 8; lifted in PR 4 commit 9).
