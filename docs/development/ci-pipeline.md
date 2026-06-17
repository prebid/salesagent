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

The workflow sets `ADCP_TESTING: true` at the top level for integration/BDD/smoke
jobs. The **E2E job overrides this to empty** so `tests/e2e/conftest.py` starts
`docker-compose.e2e.yml` itself (same as legacy `test.yml`). Do not set
`ADCP_TESTING=true` on the E2E pytest step — that path assumes an already-running
stack on `ADCP_SALES_PORT` and fails with "Server not ready after 60s".

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

## Layered pre-commit model (PR 4 of #1234)

| Layer | Trigger | Enforcement |
|-------|---------|-------------|
| Commit (~12 hooks) | `git commit` | ruff, hygiene, gitleaks, repo-invariants — warm target <2s |
| Pre-push (~11 hooks) | `git push` | docs, routes, contracts, mypy — installed via `default_install_hook_types` |
| pytest `arch_guard` | `make quality` | AST guards in `tests/unit/test_architecture_*.py` |
| CI | PR to main | `make quality-ci` in Quality Gate + dedicated jobs (**20** frozen checks after PR #1379) |

Requires pre-commit ≥3.2.0. Run `pre-commit install` once per clone (installs both commit and pre-push hooks).

CI-only checks absorbed into `make quality-ci` (not re-run via `pre-commit run --all-files` in Quality Gate):
`check_code_duplication`, `check-gam-auth-support`, `check_response_attribute_access`, `check_roundtrip_tests`,
`check_route_conflicts`, `check_type_ignore_count`, `check_docs_links`, `check_hardcoded_urls`.

See [`.pre-commit-coverage-map.yml`](../../.pre-commit-coverage-map.yml) for hook migration mapping.

## Alembic migrations

`alembic/versions/` is excluded from `ruff format` in `pyproject.toml`. Never
reformat or edit committed migration files in CI PRs — it violates project policy
and creates review noise.
