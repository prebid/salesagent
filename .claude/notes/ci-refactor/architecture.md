# Salesagent CI/Pre-commit Architecture: Current vs Target

> **⚠️ STALE — pre-Round-5+6-P0-sweep audit trail.** This file documents the architecture
> as understood before the 2026-04-25 P0 sweep. Several details were superseded by
> Decision-4 (composite migration), D11 (hard-gate from day 1), D18 (~81 final guards
> post-v2.0-rebase, revised again in Round 8), D27 (real hook math 36−13−10−1=12, revised
> Round 8), D28 (target-version bump deferred), the harden-runner v2.16+ pin, and **D30
> (Round 10: 11 frozen check names → 14 — added Smoke Tests, Security Audit, Quickstart),
> D31 (`default_install_hook_types` directive), D32 (creative-agent bootstrap), D33 (xdist
> +randomly + `--dist=loadscope`), D34 (Dockerfile `@sha256:` pin + `USER` non-root + Trivy
> scan + `SOURCE_DATE_EPOCH`), D35 (gitleaks adoption), D36 (ADR file lifecycle), D37
> (`workflow_dispatch` preserved), D38 (Schema Contract under integration env)**. Specific
> stale items in this file:
>
> - **All "11 frozen check names" references** → 14 names per D30. The `architecture.md`
>   text below has not been mass-rewritten; per-PR specs are authoritative for the canonical
>   list.
>
> - References to `.github/workflows/_pytest.yml` as a reusable workflow → **superseded** by
>   `.github/actions/_pytest/action.yml` composite (Decision-4). Per-job `services: postgres:`
>   blocks now live in `ci.yml`'s integration/e2e/admin/bdd/migration-roundtrip jobs.
> - References to `.github/workflows/release.yml` as a separate PR 6 workflow → **superseded**;
>   PR 6 EXTENDS the existing `release-please.yml` `publish-docker` job. No `release.yml` exists.
> - `.coverage-baseline = 53.5` "advisory 4 weeks" → **superseded** by hard-gate from PR 3 day 1.
> - PR 1 commit 10 (Gemini fallback) → **moved** to PR 3 commit 11 in the P0 sweep.
> - Black/ruff `target-version = py312` → **deferred** out of PR 5 per D28 / ADR-008.
> - "32 rows post-PR-4" / "52 total guards" → **superseded** by D18 revised "~81 final post-v2.0-rebase" (was ~73; corrected in Round 8 after v2.0 architecture/ count was re-verified at 27, not 31).
> - `harden-runner v2.12.0+` → **superseded** by v2.16.0+ floor → **further superseded by v2.19.0+ floor (Round 13 boss-level review)**.
> - Structural-guard marker references throughout this doc (`@pytest.mark.architecture`, `-m architecture`) → **renamed to `arch_guard`** to disambiguate from the entity-marker `architecture` (the latter is auto-applied by filename pattern via `tests/conftest.py` and stays unchanged). `tox -e unit -m architecture` (4 sites) also corrected to `tox -e unit -- -m arch_guard` (the `--` separator is required for tox to forward `-m` to pytest).
> - **Round 14 corrections:** D40 per-branch defaults; D44 rationale (stage-name rename, not directive); R11-R15 RESERVED never-defined.
>
> **Authoritative current state lives in:** `RESUME-HERE.md`, `EXECUTIVE-SUMMARY.md`,
> `00-MASTER-INDEX.md`, `03-decision-log.md`, and the 6 per-PR specs. This architecture doc
> is preserved for the audit trail; do not rely on it for executor handoff.

Definitive architecture documentation for the CI/pre-commit refactor (audit trail; see warning above). Documents both the present state on `main` and the post-rollout state after PRs 1-5 (issue #1234) plus PR 6 (Fortune-50 supply-chain follow-up) plus the v2.0 phase PRs.

Cross-references: `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/ci-refactor/00-MASTER-INDEX.md`, `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/ci-refactor/03-decision-log.md`, the 6 per-PR specs, and the source files cited inline.

---

# Section 1 — CURRENT ARCHITECTURE (on main, 2026-04)

## 1.1 — High-level system diagram (current)

```
┌────────────────────────────────────────────────────────────────────────────┐
│ DEVELOPER MACHINE                                                           │
│                                                                              │
│   git commit ───▶ pre-commit (40 hooks, ~18-30s warm)                       │
│                   ├─ 36 commit-stage hooks (formatters + grep + AST + tests)│
│                   ├─ 4 manual-stage hooks (smoke/test-migrations/...)       │
│                   └─ 0 pre-push hooks                                       │
│                                                                              │
│   make quality ─▶ ruff format/check + mypy + check_code_duplication + unit  │
│                                                                              │
│                          │ git push                                          │
└──────────────────────────┼──────────────────────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ GITHUB                                                                        │
│                                                                                │
│   ┌────────────────────────────┐    ┌─────────────────────────────────┐     │
│   │ Branch: main (protected?)  │    │ Issue tracker / PR review UI    │     │
│   │   state: unknown to agents │    │   no CODEOWNERS routing          │     │
│   │   (admin-only API access)  │    │   no SECURITY.md link            │     │
│   └────────────────────────────┘    └─────────────────────────────────┘     │
│                                                                                │
│   Actions runners (ubuntu-latest):                                            │
│   ┌──────────────────────────────────────────────────────────────────────┐  │
│   │ test.yml (8 logical jobs, 12 with matrix shards):                    │  │
│   │  - security-audit  (uvx uv-secure)                                    │  │
│   │  - smoke-tests                                                        │  │
│   │  - unit-tests                                                         │  │
│   │  - integration-tests (5-way matrix on creative/product/media-buy/...) │  │
│   │  - quickstart-test (docker compose up + curl)                         │  │
│   │  - e2e-tests       (full Docker stack)                                │  │
│   │  - lint            (ruff + mypy; ruff lint is `|| true`)              │  │
│   │  - test-summary                                                       │  │
│   ├──────────────────────────────────────────────────────────────────────┤  │
│   │ pr-title-check.yml   (pull_request_target → semantic-pull-request)    │  │
│   │ ipr-agreement.yml    (pull_request_target → CLA assistant)            │  │
│   │ release-please.yml   (push:main → release-please + docker publish)    │  │
│   └──────────────────────────────────────────────────────────────────────┘  │
│                                                                                │
│   No Dependabot. No CodeQL. No zizmor. No Scorecard. No SBOM.                │
└────────────────────────────────────────────────────────────────────────────┘
                           │
                           ▼  (release-please tag → docker publish)
┌────────────────────────────────────────────────────────────────────────────┐
│ EXTERNAL                                                                     │
│   PyPI (uv), GHCR + Docker Hub (image), no signing/provenance/SBOM           │
│   GitHub Actions Marketplace (all `uses:` are tag-pinned, never SHA-pinned)  │
└────────────────────────────────────────────────────────────────────────────┘
```

## 1.2 — Pre-commit architecture (current)

40 hook entries in `/Users/quantum/Documents/ComputedChaos/salesagent/.pre-commit-config.yaml` (verified by counting `- id:` lines). 36 run on commit stage, 0 on pre-push, 4 on manual stage (lines 142, 158, 166, 222). No `default_stages:` directive, so default = `[pre-commit, commit]` for any hook lacking explicit `stages:`.

**Local hooks (29 entries, all `language: system`):**

| Hook ID | Source script / inline | Stage | Enforces |
|---|---|---|---|
| check-docs-links | `.pre-commit-hooks/check_docs_links.py` | commit | Markdown docs link integrity |
| no-tenant-config | inline `sh -c 'grep ...'` (line 16) | commit | Forbid `tenant.config[...]` access |
| enforce-jsontype | inline `sh -c 'grep ...'` (line 23) | commit | `Column(JSONType)`, not `Column(JSON)` |
| no-hardcoded-urls | `.pre-commit-hooks/check_hardcoded_urls.py` | commit | JS uses `scriptRoot`, not `/api/...` |
| check-gam-auth-support | `.pre-commit-hooks/check-gam-auth-support.py` | commit | GAM clients support both auth methods |
| enforce-sqlalchemy-2-0 | `.pre-commit-hooks/check_sqlalchemy_2_0.py` | commit | No `session.query()` |
| check-rootmodel-access | `.pre-commit-hooks/check_rootmodel_access.py` | commit | No defensive `hasattr(x, "root")` |
| check-route-conflicts | `.pre-commit-hooks/check_route_conflicts.py` | commit | No duplicate Flask routes |
| check-tenant-context-order | `.pre-commit-hooks/check_tenant_context_order.py` | commit | Auth-before-`get_current_tenant()` order |
| no-skip-tests | inline `sh -c 'grep ...'` (line 81) | commit | Forbid `@pytest.mark.skip` w/o `skip_ci` |
| no-skip-integration-v2 | inline `sh -c` (line 89) | commit | DEAD: greps non-existent `tests/integration_v2/` |
| check-migration-completeness | `.pre-commit-hooks/check_migration_completeness.py` | commit | Migrations have upgrade+downgrade |
| check-import-usage | `.pre-commit-hooks/check_import_usage.py` (243 LOC) | commit | All used names are imported |
| check-response-attribute-access | `scripts/hooks/check_response_attribute_access.py` | commit | Safe response attribute access |
| check-roundtrip-tests | `.pre-commit-hooks/check_roundtrip_tests.py` | commit | `apply_testing_hooks()` has roundtrip test |
| check-parameter-alignment | `sh -c '... \|\| echo "⚠️"'` (advisory) | commit | MCP/A2A param alignment (non-blocking) |
| smoke-tests | `pytest tests/smoke/ -v -m smoke` | manual | Critical-path imports |
| check-migration-heads | `scripts/ops/check_migration_heads.py` | commit | Single Alembic head |
| test-migrations | inline `sh -c` (sqlite-based) | manual | Migrations executable |
| pytest-unit | inline `sh -c '... \|\| echo "⚠️"'` (advisory) | manual | Unit tests (advisory) |
| adcp-contract-tests | `pytest tests/unit/test_adcp_contract.py` | commit | AdCP schema compliance |
| mcp-contract-validation | `pytest tests/integration/test_mcp_contract_validation.py` | commit | MCP integration schema |
| no-fn-calls | inline `sh -c 'grep "\.fn("'` (line 198) | commit | No `.fn()` indirection |
| mcp-schema-alignment | `scripts/hooks/validate_mcp_schemas.py` | commit | MCP tool/schema alignment |
| mcp-endpoint-tests | `entry: echo Run MCP tests with ...` | manual | DEAD: literal echo string |
| type-ignore-no-regression | `.pre-commit-hooks/check_type_ignore_count.py` | commit | Ratchet `# type: ignore` count |
| check-code-duplication | `.pre-commit-hooks/check_code_duplication.py` | commit | DRY ratchet (`.duplication-baseline`) |
| suggest-test-factories | `.pre-commit-hooks/check_test_factories.py` (advisory) | commit | Factory usage (non-blocking) |
| ast-grep-bdd-guards | `ast-grep scan --rule .ast-grep/rules/` | commit | Structural BDD step patterns |

**External hooks (4 repos, tag-pinned, NEVER SHA-pinned):**

| Repo | `rev:` (line) | Hook(s) | Stage |
|---|---|---|---|
| `pre-commit/pre-commit-hooks` | `v6.0.0` (line 263) | trailing-whitespace, end-of-file-fixer, check-yaml, check-added-large-files, check-json, check-merge-conflict, check-ast, debug-statements | commit |
| `psf/black` | `25.1.0` (line 276) | black | commit |
| `astral-sh/ruff-pre-commit` | `v0.14.10` (line 282) | ruff (--fix --exit-non-zero-on-fix) | commit |
| `pre-commit/mirrors-mypy` | `v1.18.2` (line 290) | mypy with 9 `additional_dependencies` (line 295: `sqlalchemy[mypy]==2.0.36`, `adcp==3.2.0`, etc.) | commit |

**Drift evidence:**
- mypy hook's `additional_dependencies: adcp==3.2.0` (line 301) is 7 minor versions stale vs `pyproject.toml:10` `adcp>=3.10.0`. Pre-commit's isolated venv `adcp` doesn't match the project venv's — mypy disagrees between local pre-commit and CI.
- `mypy.ini:3` declares `plugins = sqlalchemy.ext.mypy.plugin, pydantic.mypy` BUT pydantic isn't in `additional_dependencies` → plugin loads silently disabled.
- Zero SHA-frozen `rev:` lines.

**Latency:** issue #1234 claims warm `time pre-commit run --all-files` = 18-30s; pre-flight A8 captures the actual baseline. Most cost is in inline pytest invocations (`adcp-contract-tests`, `mcp-contract-validation`) plus the 243-LOC `check_import_usage.py` walking `src/`.

## 1.3 — CI architecture (current)

Four workflow files in `/Users/quantum/Documents/ComputedChaos/salesagent/.github/workflows/`. Total `uses:` references: 33 (test.yml=22, release-please.yml=8, ipr-agreement.yml=2, pr-title-check.yml=1). **All tag-pinned (`@v4`, `@v5`), zero SHA-pinned.** Permissions audit: 1/4 workflows has top-level `permissions:` (release-please.yml at line 8, ipr-agreement.yml at line 9 has per-event-only); test.yml and pr-title-check.yml have none. Concurrency: 0/4. `persist-credentials: false`: 0/8 checkouts.

### `test.yml` (8 logical jobs, 12 with shards) — `/Users/quantum/Documents/ComputedChaos/salesagent/.github/workflows/test.yml`

```yaml
on: { push: [main, develop], pull_request: [main, develop], workflow_dispatch }
env:
  PYTHON_VERSION: '3.12'   # line 11 (drift: separate from mypy.ini)
  UV_VERSION: '0.11.6'     # line 12 (drift: not anchored to Dockerfile)
permissions: missing       # zero top-level permissions block
concurrency: missing       # no cancel-in-progress
```

| Job (key) | Branch-protection name | `permissions:` | `timeout-minutes:` | Services | Notes |
|---|---|---|---|---|---|
| `security-audit` | "Security Audit" | (none) | default 6h | none | `uvx uv-secure --ignore-vulns ...` |
| `smoke-tests` | "Smoke Tests (Fast Import Checks)" | (none) | default | none | inline grep for `@pytest.mark.skip` |
| `unit-tests` | "Unit Tests" | (none) | default | none | `uv sync --extra dev` then pytest |
| `integration-tests (creative)` | "Integration (creative)" | (none) | 15 (line 117) | postgres:15 (line 135) | matrix shard |
| `integration-tests (product)` | "Integration (product)" | (none) | 15 | postgres:15 | matrix shard |
| `integration-tests (media-buy)` | "Integration (media-buy)" | (none) | 15 | postgres:15 | matrix shard |
| `integration-tests (infra)` | "Integration (infra)" | (none) | 15 | postgres:15 | matrix shard |
| `integration-tests (other)` | "Integration (other)" | (none) | 15 | postgres:15 | matrix shard |
| `quickstart-test` | "Quickstart Docker Compose Test" | (none) | 10 | docker compose | inline `docker compose up` |
| `e2e-tests` | "E2E Tests" | (none) | 20 | manages own | hardcoded `ADCP_SALES_PORT: 8080` (line 347), `GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY \|\| 'test_key_for_mocking' }}` fallback (line 342) |
| `lint` | "Lint & Type Check" | (none) | default | none | ruff `\|\| true` + `continue-on-error: true` (lines 382-387) — silently advisory |
| `test-summary` | "Test Summary" | (none) | default | none | `needs:` aggregator |

**Internal Postgres drift:** the creative-agent shard at line 196 starts a `postgres:16` container, separate from the `postgres:15` test DB at line 135 — two Postgres majors in one workflow.

### `pr-title-check.yml` (1 job)

`on: pull_request_target` — safe-trigger (no checkout, only metadata). No top-level permissions. Uses `amannn/action-semantic-pull-request@v5`.

### `ipr-agreement.yml` (1 job)

`on: { issue_comment: [created], pull_request_target: ... }`. Top-level permissions are broad: `actions: write, contents: write, pull-requests: write, statuses: write`. Uses `contributor-assistant/github-action@v2.6.1`.

### `release-please.yml` (2 jobs)

`on: push: [main]`. Permissions: `contents: write, pull-requests: write, packages: write`. The `publish-docker` job builds + pushes to GHCR + Docker Hub via `docker/login-action@v3`, `docker/setup-{qemu,buildx}-action@v3`, `docker/metadata-action@v5`, `docker/build-push-action@v5`. **No cosign signing, no SBOM, no provenance attestation.**

**Required-status-checks contributions today:** unknown to agents — only admin can read `gh api repos/.../branches/main/protection/required_status_checks`. Pre-flight A2 captures the actual list. None of the 11 frozen target names (D17) exist yet.

## 1.4 — Structural guard architecture (current)

26 guard files on disk under `/Users/quantum/Documents/ComputedChaos/salesagent/tests/unit/` (verified). The CLAUDE.md guards table at `/Users/quantum/Documents/ComputedChaos/salesagent/CLAUDE.md:117-141` lists 24 rows; D18 audit found 3 reference files that don't exist on disk and 5 disk files are not in the table.

**On-disk guards (verified by `ls`):** 23 `test_architecture_*.py` files plus 3 transport-boundary files (`test_no_toolerror_in_impl.py`, `test_transport_agnostic_impl.py`, `test_impl_resolved_identity.py`). Plus standalone `.pre-commit-hooks/check_code_duplication.py` (pylint R0801 ratchet, not pytest-marked).

**Shared infrastructure: NONE.** No `tests/unit/_architecture_helpers.py` exists. Each guard duplicates `ast.parse(path.read_text())` per run — no AST cache. Some guards have JSON allowlists (`tests/unit/.allowlist-*.json`); others are zero-tolerance.

**Marker:** no `@pytest.mark.arch_guard` marker registered or applied. Guards run as ordinary unit tests within `tox -e unit`. There is no `-m arch_guard` selective execution.

**Integration with `make quality`** (`/Users/quantum/Documents/ComputedChaos/salesagent/Makefile:8-13`):
```makefile
quality:
    uv run ruff format --check .
    uv run ruff check .
    uv run mypy src/ --config-file=mypy.ini
    uv run python .pre-commit-hooks/check_code_duplication.py
    uv run pytest tests/unit/ -x      # guards run here, mixed with regular unit tests
```

## 1.5 — Dependency / supply-chain architecture (current)

| Surface | Pinning | Notes |
|---|---|---|
| Python deps (runtime+dev) | `uv.lock` (single source IN PRINCIPLE) | But pre-commit's mirrors-mypy `additional_dependencies` doesn't read uv.lock — drift between project venv and pre-commit venv |
| `[project.optional-dependencies].dev` | declared in pyproject.toml | Coexists with `[dependency-groups].dev`; v2.0 branch already deleted this |
| `[project.optional-dependencies].ui-tests` | declared at lines 79-84 of pyproject.toml | Used by `tox.ini:77 extras = ui-tests` and `scripts/setup/setup_conductor_workspace.sh:212 --extra ui-tests` |
| GitHub Actions | tag-pinned (`@v4`, `@v5`, `@v2.6.1`) | 33 references, **0 SHA-pinned** |
| Pre-commit external repos | tag-pinned (`v6.0.0`, `25.1.0`, `v0.14.10`, `v1.18.2`) | **0 SHA-frozen** |
| Docker base image | hardcoded `python:3.12-slim` (Dockerfile:4 and 43) | No `ARG PYTHON_VERSION`; not anchored to `.python-version` |
| `uv` install in Docker | `RUN pip install --no-cache-dir uv` (Dockerfile:24) | Latest at build time, no version lock |
| Docker image (releases) | published to ghcr.io + Docker Hub | **No cosign signing, no SBOM, no provenance attestation, no Sigstore** |

**Postgres version drift (3 different majors in active use):** `postgres:15` (test.yml:135), `postgres:16` (test.yml:196 creative agent), `postgres:17-alpine` (`docker-compose.yml`).

**No tooling configured:** No `.github/dependabot.yml`. No `.github/workflows/codeql.yml`. No `.github/workflows/security.yml`. No OpenSSF Scorecard. No `harden-runner`.

## 1.6 — Governance architecture (current)

| File | Status | Size |
|---|---|---|
| `.github/CODEOWNERS` | **MISSING** | — |
| `.github/dependabot.yml` | **MISSING** | — |
| `SECURITY.md` | **MISSING** | — |
| `CONTRIBUTING.md` | exists, IPR pointer only | 20 lines |
| `docs/decisions/` | **does not exist** | 0 ADRs |
| Branch protection on `main` | unknown to agents | — |
| Solo-maintainer `@chrishuie` bypass | unconfigured | — |

`docs/development/contributing.md` exists (D21 plans demote/delete); `docs/development/ci-pipeline.md` exists (~70 lines, PR 4 plans expansion).

## 1.7 — Decision boundaries (current)

| Layer | What's enforced | What's missing |
|---|---|---|
| pre-commit (local) | 36 commit-stage hooks, ~18-30s warm; some advisories (`\|\| echo`); 4 manual hooks | No layered design; no pre-push stage; no SHA-pinning on external hooks |
| `make quality` | ruff, mypy, dup-check, unit tests | Mixed concerns; structural guards run unmarked alongside regular unit tests |
| `tox -e unit` | runs guards as ordinary unit tests | No `-m arch_guard` selection |
| CI required checks | mostly green-or-red boolean; ruff lint is `\|\| true` (advisory in CI too); zero SHA pinning; zero per-job permissions; zero concurrency | No top-level `permissions: {}`; no SBOM/signing; no zizmor/CodeQL/Scorecard |
| Branch protection | unknown — agents cannot inspect | No CODEOWNERS routing; no required-checks contract |

**Drift items (PD1-PD25 from issue #1234):** PD1 (mypy adcp==3.2.0 vs pyproject 3.10), PD2 (psf/black 25.1.0 doesn't match uv.lock), PD3 (zero SHA-frozen), PD4-7 (CODEOWNERS/Dependabot/SECURITY/CONTRIBUTING missing or stale), PD8 (optional-deps.dev coexists with dependency-groups), PD9-12 (target-version py311 vs requires-python>=3.12; Postgres 15/16/17; uv unpinned), PD13-15 (zizmor, CodeQL, SHA-pinning, top-level permissions), PD16-22 (no pre-push, dead/advisory hooks, drift between hook and CI enforcement), PD23-25 (placeholder description, Gemini key fallback, CLAUDE.md table inaccuracies).

---

# Section 2 — TARGET ARCHITECTURE (post-PR-5 + PR-6 + v2.0)

## 2.1 — High-level system diagram (target)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ DEVELOPER MACHINE                                                              │
│                                                                                │
│   git commit ───▶ pre-commit (≤12 hooks, ~1.2-1.8s warm)                      │
│                   ├─ Layer 1: formatters (ruff/black via local uv run)        │
│                   ├─ Layer 1: hygiene (trailing-whitespace, check-yaml, ast)  │
│                   └─ Layer 1: fast AST (no-hardcoded-urls, route-conflicts)   │
│                                                                                │
│   git push ─────▶ pre-commit pre-push stage (~10-20s)                         │
│                   ├─ Layer 2: contract tests (adcp + mcp validation)          │
│                   ├─ Layer 2: docs links, type-ignore ratchet                 │
│                   └─ Layer 2: architecture-guards (calls tox -- -m arch_guard)│
│                                                                                │
│   make quality ─▶ Layer 3: ruff + mypy + dup-check + unit + 52 guards         │
│   pre-commit run --hook-stage manual ─▶ smoke, test-migrations                │
│                                                                                │
│                          │ git push                                            │
└──────────────────────────┼────────────────────────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ GITHUB                                                                         │
│                                                                                 │
│   ┌────────────────────────────────────────┐                                   │
│   │ Branch: main (protected, ADR-002)       │                                   │
│   │   required: 11 frozen check names (D17) │                                   │
│   │   CODEOWNERS review required             │                                   │
│   │   @chrishuie bypass                      │                                   │
│   │   no force-push, no deletions            │                                   │
│   │   squash-only, dismiss stale approvals   │                                   │
│   └────────────────────────────────────────┘                                   │
│                                                                                 │
│   Actions runners with harden-runner egress filter:                            │
│   ┌──────────────────────────────────────────────────────────────────────┐   │
│   │ ci.yml (orchestrator, 11 jobs):                                       │   │
│   │  CI / Quality Gate     CI / Type Check       CI / Schema Contract     │   │
│   │  CI / Unit Tests       CI / Integration Tests  CI / E2E Tests         │   │
│   │  CI / Admin UI Tests   CI / BDD Tests        CI / Migration Roundtrip │   │
│   │  CI / Coverage         CI / Summary                                   │   │
│   │   └─▶ uses _pytest.yml reusable workflow  (per-suite Postgres opt-in) │   │
│   │   └─▶ uses .github/actions/setup-env composite                        │   │
│   ├──────────────────────────────────────────────────────────────────────┤   │
│   │ security.yml: zizmor (gating) + pip-audit + dependency-review         │   │
│   │ codeql.yml:   security-extended (advisory→gating Week 5)              │   │
│   │ scorecard.yml: weekly OpenSSF Scorecard, badge in README              │   │
│   │ release.yml (PR 6): cosign keyless sign + SBOM embed + SLSA L2        │   │
│   │ pr-title-check.yml + ipr-agreement.yml (hardened, ADR-003)            │   │
│   │ release-please.yml (hardened: SHA pins, persist-credentials: false)   │   │
│   └──────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│   Dependabot (4 ecosystems, weekly grouped, no auto-merge per D5):             │
│     pip · pre-commit · github-actions · docker                                 │
│                                                                                 │
│   GitHub Code Scanning: receives SARIF from zizmor + CodeQL                    │
│   GitHub Security Advisories: SECURITY.md links to /security/advisories/new   │
└────────────────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ EXTERNAL                                                                     │
│   PyPI (uv) · GHCR + Docker Hub (image with cosign sig + SBOM + SLSA prov.)  │
│   Sigstore Rekor (transparency log) · OpenSSF Scorecard ≥7.5/10              │
│   GitHub Actions Marketplace (every `uses:` SHA-pinned with `# v<tag>`)      │
└────────────────────────────────────────────────────────────────────────────┘
```

## 2.2 — Pre-commit architecture (target)

≤12 commit-stage hooks (enforced by `test_architecture_pre_commit_hook_count` per PR 4 §Acceptance Criteria), ~5 pre-push hooks, ~5 manual hooks. All external `rev:` SHA-pinned with `# frozen: v<tag>` per D12.

**Layer 1: commit stage (≤12 hooks, ~1.2-1.8s warm):**

| Hook ID | Source | Enforces |
|---|---|---|
| trailing-whitespace | `pre-commit/pre-commit-hooks@<SHA>  # frozen: v6.0.0` | Hygiene |
| end-of-file-fixer | (same external) | Hygiene |
| check-yaml | (same external) | YAML parse |
| check-added-large-files | (same external) | `--maxkb=1000` |
| check-merge-conflict | (same external) | Merge marker hygiene |
| check-ast | (same external) | Python parse |
| ruff (--fix) | local `uv run ruff` (PR 2) | Lint + autofix |
| black | local `uv run black` (PR 2 commit 7) | Format |
| no-hardcoded-urls | `.pre-commit-hooks/check_hardcoded_urls.py` | JS scriptRoot pattern |
| check-route-conflicts | `.pre-commit-hooks/check_route_conflicts.py` | Flask route uniqueness |
| repo-invariants | `.pre-commit-hooks/check_repo_invariants.py` (PR 4 commit 6) | Consolidates `no-skip-tests`, `no-fn-calls`, ad-hoc grep one-liners |
| ast-grep-bdd-guards | `ast-grep scan --rule .ast-grep/rules/` | BDD step structural patterns |

**Layer 2: pre-push stage (~5-6 hooks, ~10-20s):**

| Hook ID | Stage | Source |
|---|---|---|
| adcp-contract-tests | `[pre-push]` | `pytest tests/unit/test_adcp_contract.py` |
| mcp-contract-validation | `[pre-push]` | `pytest tests/integration/test_mcp_contract_validation.py` |
| check-docs-links | `[pre-push]` | `.pre-commit-hooks/check_docs_links.py` |
| type-ignore-no-regression | `[pre-push]` | `.pre-commit-hooks/check_type_ignore_count.py` |
| architecture-guards | `[pre-push]` | `pytest tests/unit/ -m arch_guard -x` |
| mypy | `[pre-push]` | local `uv run mypy --config-file=mypy.ini` (D3) |

**Layer 5: manual stage (~5 hooks):** smoke-tests, test-migrations, pytest-unit, mcp-endpoint-tests, plus any future ad-hoc.

**Deleted hooks (PR 4 commit 7, 15 deletions):** `no-tenant-config`, `enforce-jsontype`, `check-rootmodel-access`, `enforce-sqlalchemy-2-0`, `check-import-usage` (5 → AST guards) + `check-gam-auth-support`, `check-response-attribute-access`, `check-roundtrip-tests`, `check-code-duplication` (4 → CI / Quality Gate) + `check-parameter-alignment`, `pytest-unit (advisory)`, `mcp-endpoint-tests` (echo string), `suggest-test-factories` (advisory), `no-skip-integration-v2` (dead), `check-migration-heads` (covered by `test_architecture_single_migration_head.py`).

**External hook pinning convention** (PR 1 commit 8 via `pre-commit autoupdate --freeze`):
```yaml
- repo: https://github.com/pre-commit/pre-commit-hooks
  rev: 2c9f875913ee60ca25ce70243dc24d5b6415598c  # frozen: v6.0.0
  hooks: ...
```

## 2.3 — CI architecture (target)

**6 workflows + 1 reusable workflow + 1 composite action.** Every workflow has top-level `permissions: {}`, `concurrency: { group: ..., cancel-in-progress: ${{ event_name == 'pull_request' }} }`, and `timeout-minutes:` per job. Every `actions/checkout` has `persist-credentials: false`. Every `uses:` SHA-pinned with `# v<tag>`.

### `ci.yml` (PR 3 — orchestrator, 11 jobs matching D17)

```yaml
on: { pull_request, push: [main] }
permissions: {}
concurrency: { group: ${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: ${{ github.event_name == 'pull_request' }} }
```

| Job key | Branch-protection name (D17) | `permissions:` | `timeout-minutes:` | Internals |
|---|---|---|---|---|
| `quality-gate` | `CI / Quality Gate` | `contents: read` | (default, bounded) | `setup-env` + `pre-commit run --all-files --show-diff-on-failure` + 4 absorbed hooks (`check-gam-auth-support`, `check_response_attribute_access`, `check_roundtrip_tests`, `check_code_duplication`) |
| `type-check` | `CI / Type Check` | `contents: read` | default | `mypy src/ --config-file=mypy.ini` |
| `schema-contract` | `CI / Schema Contract` | `contents: read` | 10 | calls `_pytest.yml` with `tests/unit/test_adcp_contract.py tests/integration/test_mcp_contract_validation.py` |
| `unit-tests` | `CI / Unit Tests` | `contents: read` | 15 | calls `_pytest.yml` (tox-env `unit`) |
| `integration-tests` | `CI / Integration Tests` | `contents: read` | 30 | calls `_pytest.yml` with `needs-postgres: true`, `pytest-args: '-n auto'` (xdist; replaces 5-way matrix) |
| `e2e-tests` | `CI / E2E Tests` | `contents: read` | 25 | calls `_pytest.yml` with `needs-postgres: true` |
| `admin-tests` | `CI / Admin UI Tests` | `contents: read` | 20 | calls `_pytest.yml` (tox-env `admin`) |
| `bdd-tests` | `CI / BDD Tests` | `contents: read` | 20 | calls `_pytest.yml` (tox-env `bdd`) |
| `migration-roundtrip` | `CI / Migration Roundtrip` | `contents: read` | (default) | runs `.github/scripts/migration_roundtrip.sh`: drop schema → upgrade head → downgrade base → assert no leaked tables → re-upgrade → assert schema hash identical → assert single head |
| `coverage` | `CI / Coverage` | `contents: read, pull-requests: write` | (default) | downloads `coverage-*` artifacts, `coverage combine`, `coverage report --fail-under=$(cat .coverage-baseline)` (53.5 advisory 4 weeks per D11), uploads HTML, comments PR |
| `summary` | `CI / Summary` | `{}` | (default) | `needs:` aggregator |

### `_pytest.yml` (PR 3 — reusable workflow)

```yaml
on: workflow_call:
  inputs: { tox-env, needs-postgres, timeout-minutes, pytest-args, xdist-workers }
permissions: {}
jobs.pytest:
  permissions: { contents: read }
  services: { postgres: { image: postgres:17-alpine, ... } }
  steps:
    - uses: ./.github/actions/setup-env
    - if: ${{ inputs.needs-postgres }} run: uv run python scripts/ops/migrate.py
    - run: uv run tox -e ${{ inputs.tox-env }} -- ${{ inputs.pytest-args }}
    - upload-artifact: coverage-${{ inputs.tox-env }}
```

### `.github/actions/setup-env/action.yml` (PR 3 — composite action)

`actions/checkout@<SHA>  # v4` (with `persist-credentials: false`) → `astral-sh/setup-uv@<SHA>  # v4` with `python-version-file: .python-version` (PR 5) → `for g in ${{ inputs.groups }}; do uv sync --group "$g"; done`.

### `security.yml` (PR 1)

```yaml
on: { pull_request, push: [main], schedule: [cron: '0 13 * * 1'] }
permissions: {}
jobs.pip-audit: { permissions: { contents: read }, runs uvx pip-audit }
jobs.zizmor:    { permissions: { contents: read, security-events: write }, runs uvx zizmor --format sarif → upload-sarif → uvx zizmor --min-severity medium (gates) }
```

### `codeql.yml` (PR 1)

```yaml
on: { pull_request, push: [main], schedule: [cron: '0 13 * * 1'] }
permissions: {}
jobs.analyze: { permissions: { contents: read, security-events: write }, language: python, queries: security-extended, continue-on-error: true (advisory per D10 Path C until end of Week 4) }
```

### `release.yml` (PR 6 — supersedes release-please.yml's docker job)

```yaml
on: workflow_call (called from release-please.yml on tag)
permissions: {}
jobs.publish:
  permissions: { contents: read, packages: write, id-token: write, attestations: write }
  steps:
    - docker/build-push-action@<SHA>
    - sigstore/cosign-installer@<SHA>; cosign sign ghcr.io/...@<digest>  # keyless OIDC
    - actions/attest-build-provenance@<SHA>  # SLSA L2 provenance
    - anchore/sbom-action@<SHA>; embed SBOM via labels
```

### `pr-title-check.yml` and `ipr-agreement.yml` (PR 1 hardening)

Both keep `pull_request_target:` per ADR-003. Both gain `# zizmor: ignore[dangerous-triggers]` allowlist comment + ADR-003 justification. Both gain top-level `permissions: {}` + per-job restrictive permissions.

### `release-please.yml` (PR 1 hardening)

All `uses:` SHA-pinned. `actions/checkout` gains `persist-credentials: false`. `publish-docker` job is replaced by a call into `release.yml` (PR 6).

**Required-status-checks contract (D17, set by PR 3 Phase B atomic flip):** the 11 names above, frozen. Branch protection `gh api -X PATCH` body documented in PR 3 spec §Phase B Step 2.

## 2.4 — Structural guard architecture (target)

**52 guards total** — 26 existing + 1 (PR 2: `test_architecture_pre_commit_no_additional_deps`) + 4 (PR 4: `no_tenant_config`, `jsontype_columns`, `no_defensive_rootmodel`, `import_usage`) + 2 new test functions on `test_architecture_query_type_safety` (PR 4 extension) + 1 (PR 5: `uv_version_anchor`) + 9 (v2.0's `.guard-baselines/*.json` migration) + ~10 (issue #1234 reserves space for additional guards in PR 6 follow-ups including `test_architecture_pre_commit_hook_count`).

**Shared infrastructure** (`tests/unit/_architecture_helpers.py`, PR 2 commit 8 / PR 4 commit 1):
- `parse_module(path)` — `@functools.lru_cache(maxsize=2048)` mtime-keyed AST parse cache shared across guards
- `iter_function_defs(tree)`, `iter_call_expressions(tree, name)` — common AST walks
- `src_python_files(repo)`, `repo_root()` — common path enumeration

**Marker registration** (PR 4 commit 1):
```toml
[tool.pytest.ini_options]
markers = [
    "arch_guard: structural guards (run with -m arch_guard)",
    # ... existing markers
]
```

Every existing guard gets `@pytest.mark.arch_guard` backfilled (PR 4 commit 2).

**Local enforcement:**
```bash
make quality                         # runs pytest tests/unit/ -x  (incl. guards)
pre-commit run architecture-guards   # pre-push hook → tox -e unit -- -m arch_guard
tox -e unit -- -m arch_guard         # selective execution
```

**CLAUDE.md guards table reorganization (PR 4 commit 9):** sectioned into Schema Patterns, Transport Boundary, DB Access, BDD, Test Integrity, Governance/CI, Cross-file Anchor Consistency. 32 rows post-PR-4 (existing 23 + PR 2's 1 + PR 4's 4 + extensions/corrections per D18); 41 rows after v2.0 lands.

## 2.5 — Dependency / supply-chain architecture (target)

| Surface | Pinning | Tool |
|---|---|---|
| Python deps (runtime+dev) | `uv.lock` is SOLE source of truth | local mypy/black hooks `uv run` directly; ADR-001 |
| `[project.optional-dependencies].dev` | DELETED (PR 2 commit 5) | replaced by `[dependency-groups].dev` (PEP 735) |
| `[project.optional-dependencies].ui-tests` | migrated to `[dependency-groups].ui-tests` (PR 2 commit 6, D14) | `tox.ini`, `setup_conductor_workspace.sh` callsites updated |
| GitHub Actions | every `uses:` SHA-pinned with `# v<tag>` (PR 1 commit 9) | enforced by zizmor `unpinned-uses` rule |
| Pre-commit external `rev:` | every `rev:` is 40-char SHA + `# frozen: v<tag>` (PR 1 commit 8 via `pre-commit autoupdate --freeze`) | weekly Dependabot updates |
| Docker base | `ARG PYTHON_VERSION=3.12` reading from `.python-version` (PR 5 commit 1) | structural guard `test_architecture_uv_version_anchor` |
| `uv` install in Docker | `COPY --from=ghcr.io/astral-sh/uv:0.11.6` (PR 5 commit 3) | optionally pinned by `@sha256:<digest>` |
| Postgres | `postgres:17-alpine` everywhere (PR 5 commit 2) | single anchor in `_pytest.yml` and `docker-compose*.yml` |
| Docker image (releases) | cosign keyless signed + SBOM embedded + SLSA L2 provenance attested (PR 6) | Sigstore Rekor transparency log |
| OpenSSF Scorecard | weekly run, badge in README, target ≥7.5/10 (PR 6) | `scorecard.yml` |

**Dependabot config (`.github/dependabot.yml`, PR 1 commit 4):**
- 4 ecosystems: `pip`, `pre-commit`, `github-actions`, `docker`
- Weekly cron Monday 06:00 PT
- `open-pull-requests-limit: 5` (3 for pre-commit, 2 for docker)
- Aggressive grouping: `python-runtime`, `python-dev`, `types-*`, `gcp-stack`, `security-patches`
- Per D5: NO auto-merge, ever
- Per D16: `ignore: dependency-name: "adcp"` until #1217 merges; `ignore: dependency-name: "googleads"` permanently

**Active scanning (PR 1):** zizmor (gating from day 1), pip-audit (gating), CodeQL `security-extended` (advisory 2 weeks → gating Week 5 per D10), `dependency-review-action` (fails PR if dep update introduces high+ severity advisory), `harden-runner` audit→block (PR 6).

## 2.6 — Governance architecture (target)

| File | Status | Spec |
|---|---|---|
| `.github/CODEOWNERS` | NEW (PR 1 commit 3) | ~30 lines, `* @chrishuie` default, scoped sections for `/.github/`, `/.pre-commit-config.yaml`, `/pyproject.toml`, `/uv.lock`, `/Dockerfile`, `/alembic/`, auth surface, `/SECURITY.md`, `/tests/unit/test_architecture_*.py` |
| `.github/dependabot.yml` | NEW (PR 1 commit 4) | 4 ecosystems, weekly grouped, no auto-merge per D5 |
| `SECURITY.md` | NEW (PR 1 commit 1) | ~80 lines: supported versions, private vuln reporting URL `/security/advisories/new`, triage SLA (5/10 business days), scope, CI/hook modification policy (CODEOWNERS-protected), 90-day disclosure |
| `CONTRIBUTING.md` | REWRITTEN (PR 1 commit 2) | ~120 lines: setup (uv, Python 3.12, Docker), local workflow (make quality, tox), PR process (Conventional Commits, 11 frozen check names), layered hook diagram, dep policy (no auto-merge), test integrity (zero tolerance), security, optional tooling (prek per D7) |
| `docs/decisions/` | NEW directory with 7 ADRs | |

**ADR inventory:**

| # | Title | PR | Triggers revisit |
|---|---|---|---|
| ADR-001 | Single-source pre-commit deps (uv.lock) | PR 1/2 | When pre-commit gains a CI-grade execution mode |
| ADR-002 | Solo-maintainer branch protection with bypass | PR 1 | When a second maintainer joins |
| ADR-003 | `pull_request_target` trust boundary | PR 1 | When CLA/title workflows need to checkout PR head |
| ADR-004 | Structural guard deprecation policy (allowlist shrink-only) | PR 4 | When a guard becomes redundant with a typechecker |
| ADR-005 | Fitness functions vs static linters | PR 4 | When AST-walking guard cost > 5s in unit suite |
| ADR-006 | Allowlist pattern (`# arch-ignore:`) | PR 4 | When false-positive rate > 5% |
| ADR-007 | Build provenance (cosign + SBOM + SLSA L2) | PR 6 | When SLSA L3 becomes feasible |

**Branch protection (D2, set by PR 3 Phase B + maintainer follow-up):**
- `required_approving_review_count: 1`
- `require_code_owner_reviews: true`
- 11 frozen required-checks per D17
- `@chrishuie` on bypass list
- `allow_force_pushes: false`, `allow_deletions: false`
- `required_pull_request_reviews.dismiss_stale_reviews: true`
- Squash-only merging

## 2.7 — Decision boundaries (target)

The five-layer model:

| Layer | Stage | Cost | Lives here |
|---|---|---|---|
| **Layer 1** | pre-commit (commit) | ~1.2-1.8s warm | Formatters (black, ruff), hygiene hooks, fast AST checks (no-hardcoded-urls, route-conflicts, repo-invariants), structural-token validators (check-yaml, check-ast). Anything < 100ms per file. |
| **Layer 2** | pre-commit (pre-push) | ~10-20s | Medium-cost checks tied to commit graph: contract tests (adcp + mcp), docs link checks, type-ignore ratchet, architecture-guards marker invocation, mypy. Needs project venv. |
| **Layer 3** | `tox -e unit` (incl. guards) | ~5-10s for guards alone | All AST-scanning structural guards (52 total). Run via `make quality` and `tox -e unit -- -m arch_guard`. Each guard caches AST in `_architecture_helpers.parse_module`. |
| **Layer 4** | CI required checks | 5-15min wall-clock | Authoritative enforcement. The 11 frozen names per D17. `CI / Quality Gate` runs everything Layer 1+2+3 plus the absorbed grep one-liners. Coverage combined into one number. |
| **Layer 5** | Manual / scheduled | varies | Smoke tests (`pre-commit run --hook-stage manual`), full e2e (`./run_all_tests.sh`), weekly Scorecard, weekly Dependabot, weekly CodeQL, weekly security advisories. |

**Decision tree for "which layer should this new check live in?":**
1. Runs on every committed file? → Layer 1 if <100ms; otherwise Layer 2.
2. Architecture invariant (AST-scannable, repo-wide)? → Layer 3 (write a guard).
3. Slow but authoritative? → Layer 4 (CI step in `quality-gate` or new `_pytest.yml` env).
4. One-off security audits or release-time tasks? → Layer 5.

**Escape hatches:**
- `# arch-ignore: <reason>` line comment — guards may honor for inline allowlist (ADR-006).
- `.allowlist-<guard-name>.json` — file-scoped allowlists. Can only **shrink** per ADR-004.
- `# FIXME(salesagent-xxxx)` — required at every allowlisted violation site.
- `--fail-under=$(cat .coverage-baseline)` — coverage advisory 4 weeks (D11).
- `continue-on-error: true` on CodeQL workflow — Path C advisory window (D10), removed Week 5.

---

# Section 3 — DELTAS (current → target)

| Dimension | Current | Target | Closed by |
|---|---|---|---|
| Pre-commit total hooks | 40 | ~22 (12 commit + 5 pre-push + 5 manual) | PR 4 |
| Pre-commit commit-stage hooks | 36 | ≤12 | PR 4 (acceptance: `HOOKS_COMMIT ≤ 12`) |
| Pre-commit pre-push hooks | 0 | ~5-6 | PR 4 commit 5 |
| Pre-commit external `rev:` SHA-pinned | 0/4 | 4/4 + `# frozen: v<tag>` | PR 1 commit 8 |
| Workflows with top-level `permissions:` | 1/4 (release-please only) | 6/6 (incl. ci, security, codeql, scorecard, release) | PR 1 + PR 3 |
| `actions/checkout` with `persist-credentials: false` | 0/8 | 8+/8+ | PR 1 commit 9 |
| Workflows with `concurrency:` | 0/4 | 6/6 | PR 1 + PR 3 commit 3 |
| Jobs with `timeout-minutes:` | 3/12 (15, 10, 20) | every job | PR 1 + PR 3 |
| `uses:` SHA-pinned | 0/33 | 33/33 + new | PR 1 commit 9 + PR 3 commit 5 |
| Structural guards (test_architecture_*.py + transport boundary) | 26 | 52 (incl. v2.0's 9) | All + v2.0 |
| Shared `_architecture_helpers.py` | absent | present (mtime-cached AST parse) | PR 2 commit 8 + PR 4 commit 1 |
| `@pytest.mark.arch_guard` marker | unregistered | registered + backfilled to all 26 + applied to new | PR 4 commits 1-2 |
| ADRs | 0 | 7 (`adr-001` … `adr-007`) | PR 1 + PR 4 + PR 6 |
| Governance files (CODEOWNERS, dependabot, SECURITY, CONTRIBUTING) | 1/4 (CONTRIBUTING 20 lines) | 4/4 (CODEOWNERS 30, dependabot 80, SECURITY 80, CONTRIBUTING 120 lines) | PR 1 commits 1-4 |
| Image signing | none | cosign keyless OIDC + Sigstore Rekor | PR 6 |
| SBOM | none | embedded via anchore/sbom-action | PR 6 |
| Provenance attestation | none | SLSA L2 via actions/attest-build-provenance | PR 6 |
| Dependabot ecosystems | 0 | 4 (pip, pre-commit, github-actions, docker) | PR 1 commit 4 |
| zizmor scanning | none | gating, SARIF upload to Code Scanning | PR 1 commit 5 |
| pip-audit | `uvx uv-secure` advisory in test.yml security-audit | dedicated job in security.yml | PR 1 commit 5 |
| CodeQL | none | advisory 2 weeks → gating Week 5 (D10 Path C) | PR 1 commit 6 |
| OpenSSF Scorecard | unknown | ≥7.5/10 (target 8.0) weekly | PR 6 + Week 5 verification |
| harden-runner | none | audit → block mode after soak | PR 6 |
| Pre-commit warm latency | 18-30s | 1.2-1.8s (10-20× improvement) | PR 4 commits 5-7 |
| Required CI check names | unknown (admin-only) | 11 frozen names per D17 atomically set via PR 3 Phase B `gh api -X PATCH` | PR 3 Phase B |
| Postgres anchors | 3 (postgres:15, postgres:16, postgres:17-alpine) | 1 (postgres:17-alpine everywhere) | PR 5 commit 2 |
| Python anchors | 2 (`PYTHON_VERSION: '3.12'` env + hardcoded `python:3.12-slim`) | 1 (`.python-version` canonical, `ARG PYTHON_VERSION` reads it) | PR 5 commit 1 |
| uv version anchors | 2 (env `UV_VERSION: '0.11.6'` + Dockerfile `pip install uv` unpinned) | 1 (Dockerfile `ARG UV_VERSION` matches setup-env composite default; `test_architecture_uv_version_anchor` enforces) | PR 5 commits 3-4 |
| `target-version` (black, ruff) | py311 (lines 117, 138 of pyproject.toml) | py312 (matches `requires-python>=3.12`) | PR 5 commits 5-7 |
| `[project.optional-dependencies].dev` | declared (lines ~60-78) | DELETED; PEP 735 `[dependency-groups].dev` canonical | PR 2 commit 5 |
| `--extra dev` callsites | 5 (test.yml lines ~60, 103, 171, 316, 379) | 0 (all migrated to `--group dev`) | PR 2 commit 4 |
| `additional_dependencies:` count | 9 entries (line 295 of .pre-commit-config.yaml) | 0 (mypy + black via local `uv run`); enforced by `test_architecture_pre_commit_no_additional_deps` | PR 2 commits 2, 7, 8 |
| pydantic.mypy plugin | declared in mypy.ini:3 but silently disabled | live + clean (errors fixed in PR 2 commit 3 per D13) | PR 2 commit 3 |
| Gemini key fallback | `${{ secrets.GEMINI_API_KEY \|\| 'test_key_for_mocking' }}` (line 342) | unconditional `test_key_for_mocking` (D15) | PR 1 commit 10 |
| `\|\| true` / `continue-on-error: true` on lint | yes (lines 382-387) | none | PR 3 commit 7 |
| ADCP_SALES_PORT hardcode | `ADCP_SALES_PORT: 8080` (line 347) | dynamic via conftest | PR 3 commit 8 |
| `pytest.skip` on network errors | present in some integration test | hard fail | PR 3 commit 10 |
| Coverage gating | none | `.coverage-baseline=53.5` advisory 4 weeks (D11), then gating | PR 3 commit 6 |
| Migration roundtrip test | none | `.github/scripts/migration_roundtrip.sh` runs in CI | PR 3 commit 4 |
| CLAUDE.md guards table accuracy | 24 rows, 3 phantom + 5 missing (D18) | 32 rows post-PR-4, accurate against disk | PR 4 commit 9 |
| Integration test sharding | 5-way matrix (`creative/product/media-buy/infra/other`) | single job with `pytest -n auto` (xdist) | PR 3 commit 3 |
| `make quality` content | mixed (regular + guards) | layered (guards via `-m arch_guard`) | PR 4 commit 1 |

---

# Section 4 — RATIONALE FOR THE TARGET ARCHITECTURE

**Layer 1 — Commit-stage hooks (≤12, ~1.2-1.8s warm).** Layered hook design follows the canonical pattern from CPython, ruff, FastAPI, and Apache Airflow: only sub-2-second hygiene and formatting belongs in the commit stage. The Pandas pattern (heavy mypy in pre-commit) is a documented anti-pattern — D3's "mypy lives in CI" decision matches every Tier-1 OSS reference. Cutting from 36 to ≤12 hooks is not aesthetic; it removes the friction that pushes contributors toward `--no-verify`, which silently bypasses every invariant. Per Ford/Parsons/Kua *Building Evolutionary Architectures*, a fitness function the team routinely circumvents is no fitness function at all.

**Layer 2 — Pre-push (~5-6 hooks, ~10-20s).** Pre-push captures the cost-quality midpoint that pre-commit doesn't fit. Contract tests (adcp, mcp), docs links, and the architecture-guards marker invocation each have CI duplicates, but local pre-push gives sub-minute feedback before the network round-trip. Following the Django and SQLAlchemy patterns: contract tests run BOTH locally (pre-push) AND in CI (`CI / Schema Contract`), with CI as the authoritative source of truth.

**Layer 3 — Structural guards (52 total, AST-based).** Fitness functions per Ford/Parsons/Kua are the right tool for repo-wide invariants that can't be expressed as a typechecker rule. The shared `_architecture_helpers.py` with mtime-cached `parse_module()` keeps the 52-guard suite under 5 seconds even at scale — without it, each guard would re-parse `src/`, multiplying linearly. The `@pytest.mark.arch_guard` marker enables both targeted runs (`tox -e unit -- -m arch_guard`) and pre-push hook invocation (`architecture-guards`) without rebuilding test discovery. The allowlist-shrink-only contract per ADR-004 turns each guard into a ratchet — the system cannot regress, only improve.

**Layer 4 — CI authoritative (11 frozen check names).** "CI is the source of truth" is the explicit position of every Tier-1 OSS project surveyed. The 11 D17 names are a contract: branch protection, CODEOWNERS, ADR-002 (bypass), and the rollback `gh api -X PATCH` body all reference these exact names. PR 3's three-phase merge (overlap → atomic flip → cleanup) is the playbook used by Kubernetes and CPython for branch-protection rename — it eliminates the "main is unmergeable for 5 minutes" risk that a naive flip introduces. Per-job `permissions: { contents: read }` follows GitHub Actions Security Hardening guidance and zizmor's `excessive-permissions` rule. Top-level `permissions: {}` is the most-restrictive default. `concurrency: cancel-in-progress` on PRs (not main) is the FastAPI / Pydantic pattern.

**Layer 5 — Manual / scheduled.** Heavy tasks that don't need to run on every PR live here. Smoke tests are useful as `pre-commit run --hook-stage manual smoke-tests` invocation but would dominate latency if commit-staged. Weekly Scorecard, weekly Dependabot, weekly CodeQL match Apache Airflow's cadence — frequent enough to catch advisories within 7 days, infrequent enough not to be noise. The 2-week CodeQL advisory → gating transition (D10 Path C) is the standard pattern for adopting a new gating check on a repo with pre-existing findings.

**Supply-chain hardening (PR 1 + PR 6).** Following Fortune-50 OSS posture: every external reference (action `uses:`, pre-commit `rev:`, Docker base) is SHA-pinned, with the `# frozen: v<tag>` / `# v<tag>` comment as the human-readable label. zizmor catches workflow-level RCE patterns (the tj-actions/changed-files class). CodeQL catches application-level patterns (the 99 missing-CSRF findings, addressed by v2.0's CSRF middleware, then flipped to gating). Cosign keyless signing + SBOM embedding + SLSA L2 provenance attestation match OpenSSF SLSA Level 2 requirements; the published image's authenticity is verifiable from its Sigstore Rekor entry. The "no auto-merge" decision per D5 is the explicit response to the tj-actions, Axios, and event-stream supply-chain attacks: pinning + reviewing only matters if review actually happens.

**Governance (CODEOWNERS, ADRs, branch protection).** D2's "1 review + bypass" is the only configuration that satisfies (a) GitHub forbids self-approval, (b) CODEOWNERS requires `required_approving_review_count: 1`, (c) solo maintainers must be able to ship. ADR-002 documents the partial-defeat tradeoff explicitly — the bypass is a transitional posture, with a tripwire when a second maintainer joins. ADRs are the institutional memory: every decision has a date, rationale, alternatives considered, and tripwire for revisiting, following Michael Nygard's original ADR template. Seven ADRs covers the load-bearing decisions; future PRs add ADRs for new structural choices but not for routine implementation.
