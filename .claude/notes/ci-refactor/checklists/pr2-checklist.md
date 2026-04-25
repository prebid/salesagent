# PR 2 — uv.lock single-source for pre-commit deps

## Checklist

```
[ ] Pre-flight TTL guard (PR-2 line uncommented: .mypy-baseline.txt freshness)
[ ] git checkout -b refactor/ci-refactor-pr2-uvlock-single-source

Commits in order:

[ ] 1. docs: flesh out ADR-001
       File: docs/decisions/adr-001-single-source-pre-commit-deps.md (was placeholder in PR 1)
       Content: full ADR text — context, decision (local hooks language:system), options considered, consequences
       Verify: grep -q '## Status' docs/decisions/adr-001-single-source-pre-commit-deps.md && \
               grep -q 'language: system' docs/decisions/adr-001-single-source-pre-commit-deps.md

[ ] 2. refactor(pre-commit): replace mirrors-mypy with local uv run mypy
       Files: .pre-commit-config.yaml (delete lines 289-305; add new local hook in repos[0].hooks per spec)
       Verify: yq '.repos[0].hooks[] | select(.id == "mypy") | .language' .pre-commit-config.yaml | grep -qx system && \
               yq '.repos[0].hooks[] | select(.id == "mypy") | .entry' .pre-commit-config.yaml | grep -q 'uv run mypy' && \
               ! grep -q 'mirrors-mypy' .pre-commit-config.yaml
       STOP HERE — capture pydantic.mypy error delta:
         uv run mypy src/ --config-file=mypy.ini > .mypy-current.txt 2>&1 || true
         echo "before: $(grep -c 'error:' .mypy-baseline.txt) | after: $(grep -c 'error:' .mypy-current.txt)"
       If delta > 200 → ESCALATE per D13 tripwire (write escalation file; STOP).

[ ] 3. fix(types): address pydantic.mypy plugin errors surfaced in PR 2
       Files: variable; typically src/core/schemas.py, src/core/schemas_*.py, src/core/tools/*/
       Approach: real type bugs → fix; Pydantic-internal cases (model_dump exclude=True etc.) → inline # type: ignore[code]
       Verify: uv run mypy src/ --config-file=mypy.ini  # exit 0
               uv run pre-commit run mypy --all-files

[ ] 4. chore(ci): migrate uv sync --extra dev → --group dev
       Files: .github/workflows/test.yml (5 callsites lines ~60, 103, 171, 316, 379); Makefile; scripts/; Dockerfile (if applicable)
       Verify: [[ $(grep -c 'uv sync --extra dev' .github/workflows/test.yml) == "0" ]] && \
               [[ $(grep -c 'uv sync --group dev' .github/workflows/test.yml) -ge "5" ]] && \
               [[ $(grep -rE 'pip install -e \.\[dev\]|--extra dev' Makefile scripts/ docs/ 2>/dev/null | wc -l) == "0" ]]
       LOAD-BEARING: this MUST land before commit 5. CI will be red between commits 4 and 5 if you reorder.

[ ] 5. refactor(deps): delete [project.optional-dependencies].dev (PEP 735 dependency-groups is canonical)
       Files: pyproject.toml (delete the [project.optional-dependencies].dev block, ~lines 60-78)
       FIRST verify v2.0 hasn't already done this on main:
         awk '/\[project\.optional-dependencies\]/,/^\[/' pyproject.toml | grep -E '^dev\s*='
       If empty → commit is a no-op (good — coordination worked); skip and continue to commit 6.
       If non-empty → delete the block.
       Verify: ! awk '/\[project\.optional-dependencies\]/,/^\[/' pyproject.toml | grep -qE '^dev\s*='

[ ] 6. refactor(deps): migrate ui-tests extras to PEP 735 dependency-group
       Files: pyproject.toml (move [project.optional-dependencies].ui-tests → [dependency-groups].ui-tests);
              tox.ini:77 (extras = ui-tests → dependency_groups = ui-tests);
              scripts/setup/setup_conductor_workspace.sh:212 (--extra ui-tests → --group ui-tests)
       Verify: ! awk '/\[project\.optional-dependencies\]/,/^\[/' pyproject.toml | grep -qE '^ui-tests\s*=' && \
               awk '/\[dependency-groups\]/,/^\[/' pyproject.toml | grep -qE '^ui-tests\s*=' && \
               grep -q 'dependency_groups = ui-tests' tox.ini && \
               grep -q 'uv sync --group ui-tests' scripts/setup/setup_conductor_workspace.sh && \
               uv run tox -e ui --notest

[ ] 7. refactor(pre-commit): replace psf/black with local uv run black
       Files: .pre-commit-config.yaml (delete lines 275-279; add new local black hook per spec)
       Verify: yq '.repos[0].hooks[] | select(.id == "black") | .language' .pre-commit-config.yaml | grep -qx system && \
               ! grep -q 'psf/black' .pre-commit-config.yaml && \
               uv run pre-commit run black --all-files && \
               [[ "$(uv run black --version | awk '{print $2}')" == \
                  "$(grep -A1 '^name = .black.$' uv.lock | grep version | head -1 | awk -F'\"' '{print $2}')" ]]

[ ] 8. test: add structural guard for additional_dependencies drift
       Files: tests/unit/test_architecture_pre_commit_no_additional_deps.py (new, ~40 lines per spec);
              tests/unit/_architecture_helpers.py (new, ~30 lines per spec);
              pyproject.toml [tool.pytest.ini_options].markers (register "architecture")
       Verify: uv run pytest tests/unit/test_architecture_pre_commit_no_additional_deps.py -v -x
       Red-team:
         git stash
         sed -i.bak 's/      - id: black/      - id: black\n        additional_dependencies:\n          - factory-boy>=3.3.0/' .pre-commit-config.yaml
         uv run pytest tests/unit/test_architecture_pre_commit_no_additional_deps.py -v 2>&1 | grep -q "factory-boy"  # guard fires
         git stash pop && rm .pre-commit-config.yaml.bak
       Document red-team result in PR description.

[ ] 9. docs: update CLAUDE.md guards table to include pre-commit drift guard
       File: CLAUDE.md
       Audit per D18: every row's test file exists; every architecture test on disk has a row.
       Add 5 missing rows; remove 3 phantom rows; add new pre_commit_no_additional_deps row.
       Target post-PR-2: 28 rows.
       Verify: ls tests/unit/test_architecture_*.py | xargs -n1 basename | while read f; do
                 grep -qF "$f" CLAUDE.md || echo "NOT IN TABLE: $f"
               done | head  # output empty

After all commits:
[ ] bash .claude/notes/ci-refactor/scripts/verify-pr2.sh  (all 9 sections; spec §Verification)
[ ] make quality + ./run_all_tests.sh

Stop conditions / escalation:
- Commit 2: pydantic.mypy delta > 200 errors (D13) → escalate
- Commit 4 → 5 ordering violated → CI red on main
- Commit 7: black version drift between uv.lock and `uv run black --version`
- Commit 8: red-team test doesn't fire (guard is broken)
File: .claude/notes/ci-refactor/escalations/pr2-<topic>.md

Post-merge:
- File follow-up issue: track inline `# type: ignore[...]` added in commit 3 (`git grep '# type: ignore' src/`)
- Re-run OpenSSF Scorecard
```
