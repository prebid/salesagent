# PR 5 — Cross-surface version consolidation

## Checklist

```
[ ] Pre-flight TTL guard
[ ] git checkout -b chore/ci-refactor-pr5-version-consolidation

Commits:

[ ] 1. chore(python): consolidate Python 3.12 anchors via .python-version
       Files: Dockerfile (ARG PYTHON_VERSION=3.12; FROM python:${PYTHON_VERSION}-slim);
              .github/workflows/* (any setup-python: drop hardcoded python-version, use python-version-file: .python-version)
       Verify: PY=$(cat .python-version | cut -d. -f1-2)
               grep -qE "^ARG PYTHON_VERSION=${PY}" Dockerfile
               [[ $(grep -RE 'python-version:\s' .github/workflows/ | grep -v 'python-version-file' | wc -l) == "0" ]]

[ ] 2. chore(postgres): unify CI Postgres images to 17-alpine
       Files: .github/workflows/*.yml + docker-compose*.yml — every reference postgres:17-alpine
       (Most done; this commit verifies and fixes any remaining)
       Verify: PG=$(grep -hE 'postgres:[0-9][^"]*' .github/workflows/*.yml docker-compose*.yml 2>/dev/null \
                  | grep -oE 'postgres:[0-9a-z.-]+' | sort -u)
               [[ $(echo "$PG" | wc -l) == "1" ]] && echo "$PG" | grep -qx 'postgres:17-alpine'

[ ] 3. chore(uv): pin uv via COPY --from in Dockerfile
       File: Dockerfile (ARG UV_VERSION=0.11.7; COPY --from=ghcr.io/astral-sh/uv:${UV_VERSION} /uv /uvx /usr/local/bin/)
       Optionally: ARG UV_DIGEST=sha256:… for digest-pinning.
       Verify: grep -qE 'COPY --from=ghcr\.io/astral-sh/uv:[0-9.]+' Dockerfile
               ! grep -qE '^RUN pip install.*uv' Dockerfile

[ ] 4. chore(uv): align UV_VERSION across Dockerfile and workflows + add structural guard
       Files: .github/workflows/ci.yml, .github/actions/_pytest/action.yml (composite — Decision-4),
              .github/actions/setup-env/action.yml (default '0.11.7');
              tests/unit/test_architecture_uv_version_anchor.py (new; spec §Commit 4 verbatim)
       Verify: uv run pytest tests/unit/test_architecture_uv_version_anchor.py -v

[ ] 5. VACANT per D28 — black target-version bump (py311 → py312) deferred to post-#1234 follow-up PR
       Rationale: 2026-04-14 unsafe-autofix incident pattern (UP040 broke prod schemas).
       File a follow-up issue: 'Post-#1234: bump black/ruff py311 → py312 with hand-applied UP040 fixes.'

[ ] 6. VACANT per D28 — ruff target-version bump deferred (same reason as commit 5)

[ ] 7. chore(docker): add USER non-root + structural guard for digest pin (D34)
       NOT a reformat — Dockerfile USER + structural guard. The reformat is deferred per D28.
       Files: Dockerfile (USER stanza); tests/unit/test_architecture_dockerfile_digest_pinned.py

[ ] 8. chore: regression checks against PG17
       (No code change — local verification, document in PR description.)
       docker run --rm -d --name pg17-test -e POSTGRES_USER=adcp_user -e POSTGRES_PASSWORD=test_password \
         -e POSTGRES_DB=adcp_test -p 5432:5432 postgres:17-alpine
       sleep 5
       DATABASE_URL=postgresql://adcp_user:test_password@localhost:5432/adcp_test uv run tox -e integration
       docker stop pg17-test

After all commits:
[ ] bash .claude/notes/ci-refactor/scripts/verify-pr5.sh
[ ] make quality + tox -e integration

Post-merge actions (operator):
- Close issue #1234 with summary comment listing closed PDs (PD1-PD24) + final OpenSSF Scorecard
- Final OpenSSF Scorecard re-run; verify ≥7.5 target
- File follow-up issues for any deferred items
```
