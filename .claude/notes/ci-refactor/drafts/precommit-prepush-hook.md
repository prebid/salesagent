# Artifact 8 — Pre-push hook configuration block (R13 mitigation)

### `.pre-commit-config.yaml` block (append to the `repos:` list)

```yaml
  - repo: local
    hooks:
      - id: architecture-guards
        name: Architectural fitness functions
        entry: uv run pytest tests/unit/ -m architecture -x --tb=short -q
        language: system
        pass_filenames: false
        stages: [pre-push]
        always_run: true
```

Notes on each line:
- `pass_filenames: false` — guards scan a fixed set of files (full repo); they don't react to which files staged
- `stages: [pre-push]` — pre-commit (commit) stage stays under 12 hooks per the `test_architecture_pre_commit_hook_count` guard; this runs only on `git push`
- `always_run: true` — the guards must execute even when the changeset is small/empty, since they read repo-wide state

### `CONTRIBUTING.md` snippet (under "Development setup")

```markdown
### Hook installation (one-time, after cloning)

```bash
# Commit-stage hooks (formatters, hygiene) — run on every `git commit`
pre-commit install

# Pre-push hooks (architectural fitness functions) — run on every `git push`
pre-commit install --hook-type pre-push
```

The pre-push hook runs `uv run pytest tests/unit/ -m architecture` (~5-10s) before
your push reaches the remote. If a structural guard fails, fix the violation
locally — do not bypass with `git push --no-verify`. CI will fail the same
guards on the PR.

If you need to push without running pre-push (e.g., a docs-only branch where
you want CI to be the gate), set `SKIP=architecture-guards git push`. Use
sparingly — the hook exists because pre-commit alone can't catch
architecture-level regressions.
```

---

## Summary of artifacts produced

All 8 artifacts are production-ready content delivered above for the executor agent to lift into the indicated locations.

1. **ADR-004** (~75 lines) — Guard deprecation criteria (4 retirement criteria, retirement-PR process, 75-guard tripwire). For embedding in `pr4-hook-relocation.md` and creation at `docs/decisions/adr-004-guard-deprecation.md`.
2. **ADR-005** (~95 lines) — Pytest fitness functions vs external tools split. Decision rule with applied-examples table; cites Ford/Parsons/Kua. For `docs/decisions/adr-005-fitness-vs-tools.md`.
3. **ADR-006** (~75 lines) — Three-tier allowlist pattern (in-module set / inline `# arch-ignore:` / forbidden central YAML); stale-detection mandate; 200-entry tripwire. For `docs/decisions/adr-006-allowlist-pattern.md`.
4. **PR 6 spec** (~280 lines) — 8 commits: harden-runner audit→block, cosign keyless signing, dependency-review gating, CodeQL gating flip, provenance mode=max, repo settings hygiene, optional pytest-benchmark suite. Each commit has files/verification/acceptance. To save as `.claude/notes/ci-refactor/pr6-image-supply-chain.md`.
5. **8 guard skeletons** (~25 lines each):
   - `test_architecture_explicit_nested_serialization.py` (PR 4)
   - `test_architecture_no_advisory_ci.py` (PR 3)
   - `test_architecture_pre_commit_hook_count.py` (PR 4)
   - `test_architecture_required_ci_checks_frozen.py` (PR 3)
   - `test_architecture_workflow_concurrency.py` (PR 1)
   - `test_architecture_persist_credentials_false.py` (PR 1)
   - `test_architecture_workflow_timeout_minutes.py` (PR 1)
   - `test_architecture_helpers.py` meta-guard (PR 2)
6. **Reconciled `_architecture_helpers.py`** (~140 lines) — final canonical version with mtime-keyed cache, file-iteration helpers (workflows + compose), action-uses regex, `assert_violations_match_allowlist` (D23), `assert_anchor_consistency` (D25), `format_failure` (D26).
7. **Full CLAUDE.md guards table** — 52 rows organized by section: Schema patterns (2), Transport boundary (5), Database access (11), BDD (7), Test integrity (7), Governance/CI (14), Cross-file anchor consistency (4). Alphabetical within each section. PR 4 commit 9 lifts this into `CLAUDE.md`.
8. **Pre-push hook config** — `.pre-commit-config.yaml` `architecture-guards` hook entry with `stages: [pre-push]`, `always_run: true`, `pass_filenames: false`; companion `CONTRIBUTING.md` "Hook installation" snippet documenting both `pre-commit install` and `pre-commit install --hook-type pre-push`.

**Note on Write tool denial:** I attempted to write artifact 4 (`pr6-image-supply-chain.md`) directly but was blocked by tool permissions, then re-emitted all content inline per the system reminder that the harness reads my final assistant message rather than written files.
