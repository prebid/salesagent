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
       File: Dockerfile (ARG UV_VERSION=0.11.6; COPY --from=ghcr.io/astral-sh/uv:${UV_VERSION} /uv /uvx /usr/local/bin/)
       Optionally: ARG UV_DIGEST=sha256:… for digest-pinning.
       Verify: grep -qE 'COPY --from=ghcr\.io/astral-sh/uv:[0-9.]+' Dockerfile
               ! grep -qE '^RUN pip install.*uv' Dockerfile

[ ] 4. chore(uv): align UV_VERSION across Dockerfile and workflows + add structural guard
       Files: .github/workflows/_pytest.yml, ci.yml; .github/actions/setup-env/action.yml (default '0.11.6');
              tests/unit/test_architecture_uv_version_anchor.py (new; spec §Commit 4 verbatim)
       Verify: uv run pytest tests/unit/test_architecture_uv_version_anchor.py -v

[ ] 5. refactor(format): bump black target-version to py312
       File: pyproject.toml:117 — target-version = ['py311'] → ['py312']
       Pre-flight: uvx black --check --diff --target-version py312 src/ 2>&1 | tee /tmp/black-py312.txt
       Verify: python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); \
                 assert 'py312' in d['tool']['black']['target-version']"

[ ] 6. refactor(format): bump ruff target-version to py312
       File: pyproject.toml:138 — target-version = "py311" → "py312"
       Pre-flight: uv run ruff check src/ tests/ --target-version py312 --statistics
       Apply autofix if needed: uv run ruff check src/ tests/ --target-version py312 --fix --select UP
       Verify: python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); \
                 assert d['tool']['ruff']['target-version'] == 'py312'"
               uv run ruff check src/

[ ] 7. chore: black reformat + ruff fix (target-version py312)
       (Only if commits 5+6 produced diff. Files: variable.)
       Verify: make quality

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
