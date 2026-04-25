# PR 1 — Supply-chain hardening

## Checklist

```
[ ] Pre-flight TTL guard passes (Deliverable 4 block, with PR-1 line uncommented)

[ ] Branch:  git checkout -b feat/ci-refactor-pr1-supply-chain

Commits in order:

[ ] 1. docs: add SECURITY.md, [project.urls], description
       Files: SECURITY.md (new, ~80 lines lifted from spec §Embedded SECURITY.md);
              pyproject.toml (line 4 description, [project.urls] block)
       Verify: test -s SECURITY.md && [[ $(wc -l < SECURITY.md) -ge 30 ]] \
               && grep -qE '\[project\.urls\]' pyproject.toml \
               && ! grep -qE 'description = "Add your description here"' pyproject.toml
       If fails: SECURITY.md missing private-vuln link → see spec §Embedded SECURITY.md verbatim.

[ ] 2. docs: rewrite CONTRIBUTING.md
       Files: CONTRIBUTING.md (rewrite using spec §"Embedded CONTRIBUTING.md outline" — fill ~120 lines from the 10 bullet sections);
              docs/development/contributing.md (delete OR ≤5-line pointer)
       Verify: [[ $(wc -l < CONTRIBUTING.md) -ge 80 ]] && \
               grep -q 'uv sync --group dev' CONTRIBUTING.md && \
               grep -q 'pre-commit install' CONTRIBUTING.md && \
               grep -qE 'CI / Quality Gate|CI / Type Check' CONTRIBUTING.md
       If fails: missing the 11 frozen check-name list → spec §Embedded outline §4.

[ ] 3. chore: add CODEOWNERS
       Files: .github/CODEOWNERS (new, lift from spec §Embedded CODEOWNERS verbatim)
       Verify: grep -qE '^\*\s+@chrishuie' .github/CODEOWNERS && \
               grep -qE '^/\.pre-commit-config\.yaml\s+@chrishuie' .github/CODEOWNERS

[ ] 4. ci: add dependabot.yml (no auto-merge)
       Files: .github/dependabot.yml (new, ~80 lines from spec §Embedded dependabot.yml)
       Verify: yamllint -d relaxed .github/dependabot.yml && \
               for eco in pip pre-commit github-actions docker; do
                 grep -qE "package-ecosystem: \"?${eco}\"?" .github/dependabot.yml || { echo MISSING $eco; exit 1; }
               done && \
               grep -qE 'dependency-name: "?adcp"?' .github/dependabot.yml && \
               ! grep -qE 'auto-?merge' .github/dependabot.yml
       If fails: pre-commit ecosystem may not be GA in this org's plan — see spec §"Fallback for the pre-commit ecosystem" (peter-evans/create-pull-request workflow).

[ ] 5. ci: add security.yml (zizmor + pip-audit)
       Files: .github/workflows/security.yml (new, lift from spec §Embedded security.yml)
       Verify: yamllint -d relaxed .github/workflows/security.yml && \
               grep -qE '^permissions:\s*\{?\s*\}?' .github/workflows/security.yml && \
               grep -q 'zizmor' .github/workflows/security.yml && \
               grep -q 'pip-audit' .github/workflows/security.yml

[ ] 6. ci: add codeql.yml (advisory per D10)
       Files: .github/workflows/codeql.yml + .github/codeql/codeql-config.yml (both new, lift from spec)
       Verify: grep -qE 'security-extended' .github/workflows/codeql.yml && \
               grep -q 'continue-on-error: true' .github/workflows/codeql.yml
       (continue-on-error is REQUIRED at this stage — D10 Path C. Removing it is a Week 5 admin action.)

[ ] 7. docs: add ADR-001 + ADR-002
       Files: docs/decisions/adr-001-single-source-pre-commit-deps.md (placeholder text describing PR 2; full content lands in PR 2);
              docs/decisions/adr-002-solo-maintainer-bypass.md (lift from spec §Embedded ADR-002 verbatim)
       Verify: test -f docs/decisions/adr-001-single-source-pre-commit-deps.md && \
               test -f docs/decisions/adr-002-solo-maintainer-bypass.md && \
               grep -q '## Status' docs/decisions/adr-002-solo-maintainer-bypass.md

[ ] 8. chore: pre-commit autoupdate --freeze (SHA-pin all external hooks)
       Procedure: on a SCRATCH branch first:
                  git checkout -b chore/sha-freeze-preview
                  uv run pre-commit autoupdate --freeze
                  git diff .pre-commit-config.yaml > /tmp/sha-freeze.diff
                  cat /tmp/sha-freeze.diff   # review the 4 hook bumps
                  uv run pre-commit run --all-files
                  # If clean: cherry-pick or replay the diff onto the PR 1 branch and commit there
       Verify: [[ $(grep -E '^\s+rev:' .pre-commit-config.yaml | grep -vcE 'rev: [a-f0-9]{40}\s+# frozen: v') == "0" ]] && \
               uv run pre-commit run --all-files
       If fails: a bumped hook breaks pre-commit run --all-files → hold that hook at previous SHA via:
                 uv run pre-commit autoupdate --freeze --repo <url-of-other-hook>  # for each non-broken
                 then manually pin the broken one.

[ ] 9. ci: pin GitHub Actions to SHAs and add top-level permissions
       Files: .github/workflows/{test,pr-title-check,release-please,ipr-agreement,security,codeql}.yml
       Procedure: use the spec §Commit 9 batch SHA-resolution loop:
                  for ref in $(grep -RhoE 'uses: [^ ]+' .github/workflows/ | sort -u | sed 's/uses: //'); do
                    case "$ref" in *@v*)
                      tool=${ref%@*}; tag=${ref#*@}
                      sha=$(gh api repos/$tool/git/refs/tags/$tag --jq '.object.sha')
                      [[ $(gh api repos/$tool/git/tags/$sha --jq '.object.type' 2>/dev/null) == commit ]] && \
                        sha=$(gh api repos/$tool/git/tags/$sha --jq '.object.sha')
                      echo "$ref -> $sha  # $tag"
                    ;; esac
                  done
                  Apply via sed or manual edit. Add # v<tag> trailing comment.
       Verify: total=$(grep -RhoE 'uses: [^ ]+@[^ ]+' .github/workflows/ | grep -vcE 'uses: \./'); \
               sha=$(grep -RhoE 'uses: [^ ]+@[a-f0-9]{40}' .github/workflows/ | wc -l); \
               [[ "$total" == "$sha" ]] && \
               for f in .github/workflows/*.yml; do grep -qE '^permissions:' "$f"; done

[ ] 10. ci: gate Gemini key behind unconditional mock
        Files: .github/workflows/test.yml (line 342 area)
        Edit: GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY || 'test_key_for_mocking' }}
              → GEMINI_API_KEY: test_key_for_mocking
        Verify: ! grep -q 'secrets.GEMINI_API_KEY' .github/workflows/test.yml && \
                grep -q "GEMINI_API_KEY: test_key_for_mocking" .github/workflows/test.yml

[ ] 11. ci: zizmor pre-flight findings — fix or allowlist
        Files: .github/workflows/pr-title-check.yml + ipr-agreement.yml (allowlist comments at top:
               "# zizmor: ignore[dangerous-triggers]" + "# Justification: ADR-003");
               docs/decisions/adr-003-pull-request-target-trust.md (lift from spec verbatim);
               .github/zizmor.yml (new — see spec).
        Verify: uvx zizmor .github/workflows/ --min-severity medium  # exit 0 OR exit 1 with only allowlisted findings
                test -f docs/decisions/adr-003-pull-request-target-trust.md

After all commits — full verification:
[ ] bash .claude/notes/ci-refactor/scripts/verify-pr1.sh  (all 8 sections green; spec §Verification)
[ ] make quality  (green)
[ ] uv run pre-commit run --all-files  (green)

Stop conditions / escalation:
- Any verification step exits non-zero and the fix is non-mechanical
- A bumped pre-commit hook breaks main (commit 8)
- zizmor finds a high-severity issue not in the pre-flight list (commit 11)
- gh api SHA resolution fails for a real action ref (commit 9)
Write to: .claude/notes/ci-refactor/escalations/pr1-<topic>.md and STOP.

Final artifacts to produce:
[ ] PR description from templates/pr-description.md filled with PR 1 specifics
[ ] Update 00-MASTER-INDEX.md status row: PR 1 → "open" (or "merged" once it lands)
[ ] Drafted issue #1234 progress comment listing PD3, PD4, PD5, PD6, PD7, PD13, PD14, PD15, PD23, PD24

Post-merge actions (operator only — `@chrishuie`):
- Configure branch-protection bypass for @chrishuie via UI (Settings → Branches → main → "Allow specified actors to bypass required pull requests" → add chrishuie)
- T+1h: confirm dependabot.yml validates (Settings → Code security → Dependabot → "Last run")
- Friday landing → expect 5-13 Dependabot PRs Saturday morning UTC
- Re-run OpenSSF Scorecard at end of Week 1; capture delta vs A9 baseline
```
