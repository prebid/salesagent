# CI Pipeline

Authoritative workflow: [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)

Legacy `.github/workflows/test.yml` is removed by PR #1372. Branch
protection must reference the new rendered check names — see
[Branch protection migration](#branch-protection-migration) below.

## Prerequisites

PR #1372 (**PR3**) depends on PR #1370 (**PR2**: uv.lock, pre-commit, dependency
groups). Merge PR2 first, then rebase PR3 onto `upstream/main`:

```bash
git fetch upstream
git checkout ci/pr3-ci-authoritative-pipeline
git rebase upstream/main
git diff upstream/main --stat   # target: ~20-30 files, no alembic/versions/ changes
```

## Required checks (20 rendered names after PR #1379)

Run `./scripts/capture-rendered-names.sh` after any job rename. The frozen guard
[`tests/unit/test_architecture_required_ci_checks_frozen.py`](../../tests/unit/test_architecture_required_ci_checks_frozen.py)
must stay in sync.

PR #1372 (PR3) establishes **18** checks. PR #1379 adds **2 BDD shard** checks
plus the aggregate `CI / BDD Tests` status proxy (replacing PR3's single BDD job).

| Rendered check name | Job |
|---------------------|-----|
| `CI / Quality Gate` | Static analysis (ruff, mypy, duplication) — **no pytest** |
| `CI / Type Check` | mypy |
| `CI / Schema Contract` | AdCP contract tests |
| `CI / Security Audit` | uv-secure against `uv.lock` |
| `CI / Quickstart` | docker compose health |
| `CI / Smoke Tests` | import smoke + skip-decorator guard |
| `CI / Unit Tests` | unit + harness with coverage artifact |
| `CI / Integration (creative)` | integration shard |
| `CI / Integration (product)` | integration shard |
| `CI / Integration (media-buy)` | integration shard |
| `CI / Integration (infra)` | integration shard |
| `CI / Integration (other)` | integration shard |
| `CI / E2E Tests` | full stack |
| `CI / Admin UI Tests` | admin suite |
| `CI / BDD Tests (Shard 1/2)` | BDD greedy scenario shard |
| `CI / BDD Tests (Shard 2/2)` | BDD greedy scenario shard |
| `CI / BDD Tests` | aggregate status proxy (shard pass/fail) |
| `CI / Migration Roundtrip` | alembic upgrade |
| `CI / Coverage` | `--fail-under` from `.coverage-baseline` using unit + BDD artifacts |
| `CI / Summary` | aggregation gate |

## BDD Test Shards

BDD tests (`tests/bdd/test_*.py`, 13 files) run in **2 parallel shards** via
greedy min-load assignment by Gherkin scenario count (`scripts/ci/shard_split.py`).
Each shard uploads coverage; the **Coverage** job combines unit + BDD shard
artifacts and enforces `.coverage-baseline`. The aggregate `BDD Tests` job is
status-only. Shard jobs route pytest through the shared `_pytest` composite.

| Shard | Files | Scenarios (approx.) |
|-------|------:|--------------------:|
| 1/2 | 5 | 479 |
| 2/2 | 8 | 441 |

Reproduce a CI shard locally:

```bash
uv run pytest $(uv run python scripts/ci/shard_paths.py bdd 1)
```

`make test-bdd` (full `tests/bdd/`) runs the same scenarios as both shards combined.

**Load metric note:** greedy assignment balances Gherkin `Scenario` line counts (~920 total). Collected pytest items are much higher (~9×) because each scenario expands by `Examples:` rows and transport parametrization. Wall-clock is dominated by slow DB-heavy scenarios, not test volume — the reason 2 shards was chosen over 3–4.

Structural guard: `tests/unit/test_architecture_ci_bdd_shard_manifest.py`.

## Integration Test Shards

Integration tests are split into 5 parallel shards by entity marker. Shards use a
**strict partition** (priority: creative → product → media-buy → infra → other) so
multi-marker files run in exactly one shard — measured overlap on the pre-fix
markers was 14 duplicate file assignments across shards 1–4.

| Shard | Markers |
|-------|---------|
| creative | `creative` |
| product | `product and not creative` |
| media-buy | `(media_buy or delivery) and not creative and not product` |
| infra | `(transport or auth or … or agent) and not creative and not product and not media_buy and not delivery` |
| other | negation of all 15 markers above |

Each shard runs against a GitHub Actions service container (Postgres 15).

**Admin UI** and **BDD** jobs also require the Postgres service container plus
`_postgres` (wait + migrate) — admin blueprint tests use the `integration_db` fixture.

## E2E Tests

The workflow sets `ADCP_TESTING: true` at the top level. The **E2E job**
pre-starts the stack (pinned `creative-agent` build + `compose up -d --wait`
with healthchecks and the host-ports overlay), then runs pytest with
`ADCP_TESTING: "true"` set explicitly on the pytest step so
`docker_services_e2e` is verify-only. Do **not** clear or omit it on that
step — an empty/false value forces the fixture into the standalone cold-build
path under `pytest --timeout=300` (setup timeouts / ENOSPC on cold runners).
Local standalone runs (no pre-started stack) still clear or omit
`ADCP_TESTING` so conftest owns build+up.

## Reference Creative Agent

The `creative` shard starts a reference creative agent from the upstream
[adcontextprotocol/adcp](https://github.com/adcontextprotocol/adcp) server.
This is the full adcp monolith — the creative agent is one route within it.

### Pinning to a known-good commit

The upstream repo is actively developed and its migrations can break without
warning. We pin to a specific commit SHA via the GitHub archive API:

```yaml
curl -sL https://github.com/adcontextprotocol/adcp/archive/<SHA>.tar.gz \
  | tar xz -C /tmp/adcp-server --strip-components=1
```

**Why archive API instead of `git clone`?** GitHub's smart HTTP protocol does
not allow `git fetch` of arbitrary SHAs on repositories you don't own. A shallow
clone (`--depth 1`) only gets HEAD. The archive endpoint works for any public
commit without authentication.

**When to update the pin:** After verifying that upstream HEAD's migrations run
cleanly. Check the `community_points` / `users` table FK ordering — this was the
failure that prompted pinning (April 2026).

The pin lives only in [`scripts/creative-agent-stack.sh`](../../scripts/creative-agent-stack.sh)
— both CI and `run_all_tests.sh` call that script so local and CI cannot diverge.

### CI image cache (ghcr.io) and retries

On ephemeral GitHub Actions runners, local `/tmp` tarball reuse and `docker image
inspect` guards do not survive between runs. Pin-keyed images in GHCR avoid a
full monolith compile on warm CI runs.

**Publish (trusted `main` context only).** The
[`publish-creative-agent.yml`](../../.github/workflows/publish-creative-agent.yml)
workflow runs on `push` to `main` and `workflow_dispatch`. It logs in to
`ghcr.io` and calls `creative-agent-stack.sh publish`, which builds from the
pinned tarball and pushes `ghcr.io/<repo>/adcp-creative-agent:${ADCP_PIN}`.
If the tag already exists, publish is a no-op. Publish failures fail the
workflow loudly (unlike the test shard, which degrades gracefully).

**CI test shard (pull-only).** The creative matrix leg logs in to `ghcr.io`
(authenticated pulls dodge anonymous rate limits and work for public images
from fork PRs), sets `CREATIVE_AGENT_GHCR_IMAGE`, and calls
`creative-agent-stack.sh up`:

- **Warm run** (public image tag `${ADCP_PIN}` in ghcr.io): `docker pull` +
  retag to local `adcp-creative-agent` — no compile.
- **Cold / degraded** (package absent, private, or pull blip): fetch tarball and
  `docker build` locally. The shard never pushes.

**Local.** `run_all_tests.sh` calls `up` without `CREATIVE_AGENT_GHCR_IMAGE` —
plain `docker build`, same pin source.

Tarball fetch (`curl -f`), `docker pull`, and `docker build` each retry up to
3 times with exponential backoff to absorb transient `ECONNRESET` flakes.
Publish also retries `docker push`.

The `integration-tests` job grants `packages: read` for GHCR pulls. Publishing
uses `packages: write` only in `publish-creative-agent.yml` on `main`.

**Maintainer prerequisites (org/repo — one-time):**

1. After merge, trigger `Publish creative agent image` via `workflow_dispatch`
   and confirm it creates `adcp-creative-agent` (not `denied: Create
   organization package`). If denied, enable Actions package creation in Org →
   Packages or have an admin pre-create the empty package, then re-run.
2. Set the GHCR package **Public** so fork PRs can pull. Until then, the
   creative shard builds locally everywhere (green, no speedup).

**Fork PR caveat:** warm-cache pull is not verifiable on a fork PR until the
public package exists. Measure the real pull time on `main` before claiming
#1189's <30s warm step — a large image may still exceed 30s; slimming the
image is a sensible follow-up if needed.

### Creative agent infrastructure

The agent runs in its own Docker network (`creative-net`) with a separate
Postgres 16 instance (`adcp-postgres`). It is not connected to the test
database used by our integration tests.

```
creative-net:
  adcp-postgres (Postgres 16, user=adcp, db=adcp_registry)
  creative-agent (port 9999 → 8080)
```

## Security Audit

The `Security Audit` job runs `scripts/security-audit.sh`, which invokes
`uv-secure` against `uv.lock`. Any known vulnerability in a pinned dependency
fails the build unless explicitly ignored with documented rationale in the
script. Fix by bumping the affected package in `pyproject.toml` and running
`uv lock`.

## Postgres Health Check

The service container uses `pg_isready -U adcp_user` for health checks. The
`-U` flag is required — without it, `pg_isready` defaults to the OS user
(`root` on GitHub Actions runners), which produces noisy "role root does not
exist" log entries.

## Branch protection migration

**Do this before merging PR #1372**, or PRs will show green checks that are not
required (or stay blocked on stale `Test Suite / …` names).

### Maintainer checklist

1. Merge PR #1370 (PR2) and rebase PR #1372 (PR3) onto `main`.
2. Run `./scripts/capture-rendered-names.sh` on the rebased PR branch and copy the output.
3. In GitHub → Settings → Rules → `main` branch protection:
   - Remove legacy required checks prefixed with `Test Suite /`.
   - Add all 18 checks prefixed with `CI /` from the script output.
4. Keep non-CI required checks unchanged (`check-pr-title`, CodeQL, `security.yml` jobs, etc.).
5. Merge PR #1372 only after a test PR shows all 18 `CI / …` checks as required and green.
6. After PR #1379 (BDD sharding): add the 2 `CI / BDD Tests (Shard …/2)` required checks and keep `CI / BDD Tests` as the aggregate proxy — **separate** branch-protection update from PR3.

### Local vs CI quality targets

| Target | Runs pytest? | Used by |
|--------|--------------|---------|
| `make quality-ci` | No | CI Quality Gate job |
| `make quality` | Yes (`tests/unit/ -x`) | Local pre-commit habit |
| `make quality-full` | Full suites via `run_all_tests.sh` | Pre-PR local gate |

### Local vs CI Postgres isolation (D9, #1233)

Local `tox -e integration` and `make test-int` reuse a **persistent** Postgres
instance (agent-db or Docker stack on the host). CI integration/admin/BDD jobs
start a **fresh** `postgres:17-alpine` service container per job run.

Cross-test isolation bugs that depend on process-wide factory binding (see
`tests/admin/conftest.py`) can reproduce locally but not in CI, or vice versa.

**Diagnostic:** run `tox -p` or `./run_all_tests.sh quick` locally against a
shared Postgres, then compare with the same slice in CI. If a failure is
isolation-specific, inspect factory session binding and tenant scoping — not
Postgres version drift (guarded by `test_architecture_postgres_image_anchor`).

### Legacy `requires_server` tests removed (D11, #1233)

Integration/admin tests marked ``@pytest.mark.requires_server`` were deselected
by ``-m "not requires_server"`` in tox and CI and never executed (many used the
in-process ``mcp_server`` fixture rather than an external server). Equivalent
coverage lives in ``tests/e2e/`` via ``live_server`` / ``docker_services_e2e``.

**Post-#1234 follow-up — still #1233 scope:**

| Item | Tracking | When |
|------|----------|------|
| `test_sell_readiness_browser.py` admin browser flows | #1233 D11 follow-up | Dedicated admin+e2e-stack CI job |

### Nightly GAM regression (D13, #1477)

~20 tests in `tests/e2e/test_gam_*.py` carry `@pytest.mark.requires_gam`. They
call the live GAM sandbox network and are **excluded from per-PR CI** (quota,
credential blast radius on fork PRs).

| Property | Value |
|----------|-------|
| Workflow | [`.github/workflows/gam-nightly.yml`](../../.github/workflows/gam-nightly.yml) |
| Trigger | Daily cron (`07:00 UTC`) + `workflow_dispatch` |
| Branch | `main` only (`if: github.ref == 'refs/heads/main'`) |
| Environment | `gam-nightly` — create under **Settings → Environments**, restrict deployment branches to `main` |
| Secret | `GAM_SERVICE_ACCOUNT_JSON` on the `gam-nightly` environment (not repo-level; unavailable to fork PRs) |
| Command | `pytest tests/e2e/ -m requires_gam` (GHA Postgres service on :5435 for `gam_lifecycle_db`; tests that request `docker_services_e2e` still start the e2e stack via conftest) |
| `DATABASE_URL` | `postgresql://adcp_user:secure_password_change_me@127.0.0.1:5435/postgres` — GHA `services.postgres` on host port 5435; `gam_lifecycle_db` creates ephemeral databases there |

Failures notify repository watchers via GitHub's default workflow notifications.

**Local equivalent** (requires credentials + Docker):

```bash
export GAM_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'
export DATABASE_URL=postgresql://adcp_user:secure_password_change_me@127.0.0.1:5435/postgres
uv run pytest tests/e2e/ -m requires_gam -v --timeout=300
```

## Layered pre-commit model (PR 4 of #1234)

| Layer | Trigger | Enforcement |
|-------|---------|-------------|
| Commit (~12 hooks) | `git commit` | ruff, hygiene, gitleaks, repo-invariants — warm target <2s |
| Pre-push (~11 hooks) | `git push` | docs, routes, contracts, mypy — installed via `default_install_hook_types` |
| pytest `arch_guard` | `make quality` | AST guards in `tests/unit/test_architecture_*.py` |
| CI | PR to main | `make quality-ci` in Quality Gate + dedicated jobs (**20** frozen checks after PR #1379) |

Requires pre-commit ≥3.2.0. Run `pre-commit install` once per clone (installs both commit and pre-push hooks).

Checks invoked directly by `make quality-ci` (not via `pre-commit run --all-files` in the Quality Gate):

- **CI-only** (no commit/pre-push stage): `check_code_duplication`, `check-gam-auth-support`,
  `check_response_attribute_access`, `check_roundtrip_tests`.
- **Dual-stage** (`pre-push + ci-step` in the coverage map): `check-route-conflicts`,
  `type-ignore-no-regression`, `check-docs-links`, `no-hardcoded-urls`.

See [`.pre-commit-coverage-map.yml`](../../.pre-commit-coverage-map.yml) for hook migration mapping.

## Alembic migrations

`alembic/versions/` is excluded from `ruff format` in `pyproject.toml`. Never
reformat or edit committed migration files in CI PRs — it violates project policy
and creates review noise.

## GitHub Code Quality vs CodeQL scoping (#1422)

Two separate analyzers can comment on Python PRs:

| Analyzer | Configured by | Alembic exclusions |
|----------|---------------|-------------------|
| **CodeQL** (security workflow) | In-repo [`.github/codeql/codeql-config.yml`](../../.github/codeql/codeql-config.yml) | `paths-ignore: alembic/versions/` — honored |
| **Code Quality** (`github-code-quality[bot]`) | GitHub org/repo **Advanced Security → Code Quality** settings | **Not** driven by `codeql-config.yml`; **path exclusions not available yet** (public preview) |

### Problem

Code Quality flags alembic module globals (`revision`, `down_revision`, `branch_labels`, `depends_on`) as "Unused global variable". Those globals are Alembic's public migration API — false positives on every migration touch.

### Current disposition (2026-07)

GitHub Code Quality **does not support path/directory exclusions** today. Org admins cannot mirror `.github/codeql/codeql-config.yml` `paths-ignore` in Code Quality settings — only language selection and runner choice are exposed. GitHub staff have path customization on the roadmap ([community discussion #186446](https://github.com/orgs/community/discussions/186446)).

**Impact:** bot comments are non-gating (COMMENTED, never a failing check) — comment noise only.

**Options until path exclusion ships:**

1. **Wait** (default) — apply `alembic/versions/` exclusion the moment GitHub ships directory customization. Intended scope is already documented here and cross-linked from [`.github/codeql/codeql-config.yml`](../../.github/codeql/codeql-config.yml) via #1491.
2. **Disable Code Quality for Python** — removes alembic false positives but also drops genuine `src/` findings. All-or-nothing; use only if noise becomes intolerable.

Do **not** hunt org settings for a path-exclusion checkbox that does not exist yet.

Track: [#1422](https://github.com/prebid/salesagent/issues/1422).
