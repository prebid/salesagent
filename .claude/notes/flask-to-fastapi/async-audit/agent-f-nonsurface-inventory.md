# Non-Code Surface Inventory — Full Async SQLAlchemy Pivot

**Date:** 2026-04-11
**Agent:** F (non-code surface)
**Scope:** everything outside application code — dev tooling, CI, Docker, observability, ops, migration runtime, dep graph cascades, platform compatibility, docs, onboarding, structural guards (as tooling, not patterns), and deployment wiring
**Companion agents:** A (scope audit), B (risk matrix), C (plan-file edits), D (AdCP verification), E (FastAPI idioms). This report covers what those do NOT cover.
**Grounding:** every claim is backed by a file citation from direct reading of the repo on branch `feat/v2.0.0-flask-to-fastapi` ancestor (current HEAD reads identical files).

---

## Section 0 — Executive Summary

**Total action items in this report: 93**
- **MUST (pre-Wave-0 hard gate):** 18
- **SHOULD (inside the main async waves):** 51
- **CAN (defer to v2.1 / polish wave):** 24

> **⚠️ CORRECTIONS APPLIED 2026-04-11.** Findings #1, #2, and #10 below recommend removing psycopg2-binary + libpq. **All three are reversed** by Decisions 1, 2, 9 (and Audit 06's overrule of Decision 2). The "Top surprises" list is preserved verbatim for traceability of the original audit, but the resolutions in §1.1, §1.2, §1.4 are now **additive (keep psycopg2 alongside asyncpg)** rather than substitutive (remove psycopg2). See §1.1, §1.2, §1.4 reversal banners for full reasoning. The original "MUST fix before Wave 4 merge" pre-Wave-0 budget shrinks because the swap-and-fallout work is no longer needed; the new budget gains the dual session factory work and the structural guard.

**Top surprises that no previous agent has flagged:**

1. **`scripts/deploy/entrypoint_admin.sh:9` does `import psycopg2; psycopg2.connect('${DATABASE_URL}')` at shell startup.** When Agent B/C propose removing `psycopg2-binary` from `pyproject.toml`, this entrypoint script breaks immediately in production and the container never comes up. Agent B's Risk #2 and #33 discuss psycopg2 → asyncpg at the library level but do not touch this shell script. ~~**MUST fix before Wave 4 merge.**~~ **REVERSED 2026-04-11:** psycopg2-binary STAYS (Decision 2 OVERRULE), so the entrypoint script needs no change. (Citation: `scripts/deploy/entrypoint_admin.sh:9`.)

2. **`scripts/deploy/run_all_services.py:65-125` calls the sync `get_db_connection()` at container startup** (reaches into `src/core/database/db_config.py:168` which does raw `psycopg2.connect`, separate from `database_session.py` sync engine). Agent B assumed the pivot touches SQLAlchemy only, but the deploy entrypoint has its own sync-psycopg2 path. It will still "work" with asyncpg kept as a second driver, but if we want to remove psycopg2 entirely, this path must switch to `asyncpg.connect()` wrapped in `asyncio.run()` or use the async SQLAlchemy engine from within a lifespan. **REVERSED 2026-04-11:** Audit 06 confirmed these calls are pre-uvicorn (lines actually `:84,135` not `:65,133`), making `asyncio.run()` structurally wrong (loop-bound asyncpg state would conflict with uvicorn's loop). `DatabaseConnection` STAYS as the raw-psycopg2 pre-uvicorn path. (Citations: `scripts/deploy/run_all_services.py:84`, `scripts/deploy/run_all_services.py:135`, `src/core/database/db_config.py:105-127`.)

3. **Pre-commit hook `test-migrations` still runs against SQLite** (`.pre-commit-config.yaml:153-158` — `DATABASE_URL=sqlite:///.test.db`). SQLite is already unsupported by policy (CLAUDE.md Pattern #3) but the hook is at `stages: [manual]` so it has lingered. Under async + asyncpg, SQLite is doubly impossible. This hook is dead code and must be removed or replaced with a Postgres-based migration smoke. (Citation: `.pre-commit-config.yaml:153-158`.)

4. **`.github/workflows/test.yml` `integration-tests` job uses `postgres:15` while `docker-compose.yml` / `docker-compose.e2e.yml` / `scripts/test-stack.sh` use `postgres:17-alpine`.** This version skew is fine under psycopg2, but asyncpg has subtly different codec behaviour across PG 15 vs 17 JSONB handling, and testing against `postgres:15` in CI while running `postgres:17` locally means Risk #17 (JSONType codec) could pass locally and fail in CI. **MUST align PG versions before the driver flip.** (Citations: `.github/workflows/test.yml:135`, `docker-compose.yml:25`, `docker-compose.e2e.yml:17`, `.claude/skills/agent-db/agent-db.sh:24`.)

5. **Five tox envs with `COVERAGE_FILE` path but no explicit `asyncio_mode` marker setting anywhere in `tox.ini`, `pyproject.toml`, or `pytest.ini`.** `pytest-asyncio>=1.1.0` is already installed (`pyproject.toml:65`), but if every integration test becomes `async def`, we need `asyncio_mode = auto` globally to avoid decorating every test; otherwise every test requires `@pytest.mark.asyncio` (Risk #3). The fact that `asyncio_mode` is not set anywhere right now means the default (`strict`) is in effect. (Citations: `tox.ini:30-32`, `pyproject.toml:65`, `pyproject.toml` has no `[tool.pytest.ini_options]` block.)

6. **`.pre-commit-config.yaml:92-305` has mypy pinned to `sqlalchemy[mypy]==2.0.36`** as a pre-commit `additional_dependencies` — this is fine for sync ORM but does not auto-configure the async typing plugin. The plugin handles `AsyncSession` types without special setup but if we want mypy to catch "awaiting a sync session" mistakes, we need explicit async type hints at call sites — a consideration for the structural guards, not a blocker.

7. **`scripts/test-stack.sh` and `agent-db.sh` both scan ports 50000-60000**, and `docker-compose.e2e.yml:25` binds `127.0.0.1:${POSTGRES_PORT:-5435}`. When asyncpg connects over TLS, its `server_settings={"statement_timeout": ...}` in Risk #18 Option A produces a connection startup packet that is 30-60 bytes larger than psycopg2's minimal startup. PgBouncer in transaction-pooling mode strips these. **Implication:** Risk #18's Option A (use `server_settings`) is only safe outside PgBouncer; inside PgBouncer we must use Option B (`init` callback). This interacts with `USE_PGBOUNCER` detection (`database_session.py:44-67`), which already branches on port 6543. **Action:** the asyncpg engine construction path must preserve the pgbouncer/direct branch and pick Risk #18's Option A or B accordingly.

8. **Auto-memory file `MEMORY.md` at `/Users/quantum/.claude/projects/.../memory/` names "sync def" as Critical Invariant #1** (this is in the user's auto-memory, not the repo). That memory will be silently wrong after the pivot. Propose an edit.

9. **`scripts/run_admin_ui.py` is an orphan Flask runner** — mentioned in the migration plan for deletion in Wave 3. It does `from src.admin.app import create_app` and runs Waitress/Werkzeug. Under full async, `create_app()` becomes a FastAPI factory and this shim breaks. Agent A/C handle this at the code level but the file's removal blocks the CI job that may still reference it. Verify nothing in `.github/workflows/*.yml` calls `scripts/run_admin_ui.py` — **confirmed none do** (all refer to `docker compose` or direct pytest).

10. **`Dockerfile:17-20` installs `libpq-dev` as a build-time dep in the builder stage and `libpq5` at runtime.** Neither is needed for asyncpg (which is pure-Python + its own C extension for the protocol codec, not libpq-based). Removing `libpq-dev` saves ~5MB builder image, and removing `libpq5` saves ~3MB runtime. Low priority. **REVERSED 2026-04-11:** psycopg2-binary STAYS for Decisions 1, 2, 9 sync paths. Although psycopg2-binary bundles its own libpq, Decision 9 keeps the door open for swapping to source-built psycopg2 in v2.1+ — keeping libpq-dev + libpq5 avoids a future Dockerfile change at the same time as a deps swap. Image savings adjust ~80MB → ~75MB. (Citations: `Dockerfile:17-20`, `Dockerfile:63-68`.)

**Go/no-go impact (CORRECTED 2026-04-11):**
- ~~Finding #1 (entrypoint_admin.sh) is a hard blocker but trivial to fix (one line).~~ **No longer a blocker** — psycopg2 STAYS, entrypoint script unchanged.
- ~~Finding #2 (run_all_services.py health check) is a MUST, small effort.~~ **No longer a blocker** — `DatabaseConnection` STAYS as the raw-psycopg2 pre-uvicorn path (Audit 06 OVERRULE).
- Finding #4 (PG version skew) **REMAINS a blocker** before driver spike; align CI to PG17 to match local + production. Decisions 1/9 do not change this — Spike 2 still runs against asyncpg even though psycopg2 also stays.
- Finding #10 (libpq removal) **REVERSED** — keep libpq-dev + libpq5 in the Dockerfile.
- The MUST bucket originally had 18 items totalling ~3-4 days; with the F1.1, F1.2, F1.4, #1, #2, #10 reversals plus the new structural guard work (test_architecture_no_runtime_psycopg2.py + the dual-session-factory implementation), it now has ~14 items totalling ~3-4 days (similar effort, different shape — less swap-and-fallout, more dual-engine implementation). The new pre-Wave-0 budget item is **Spike 5.5** (0.5 day, two-engine coexistence test) which validates the additive plan.

---

## Section 1 — Per-Category Findings

### Category 1 — Dependency graph cascades

#### 1.1 `psycopg2-binary` → `asyncpg` swap in `pyproject.toml`

> **⚠️ CORRECTED 2026-04-11 — PARTIAL REVERSAL.** The "remove psycopg2-binary" plan below is **superseded** by Decisions 1, 2, and 9 (and Audit 06 Decision 2 OVERRULE). Three independent sync-psycopg2 paths must be retained, so `psycopg2-binary` and `types-psycopg2` STAY in `pyproject.toml`. `asyncpg` is added ALONGSIDE, not replacing. The findings below are preserved for traceability but the "Change required" list is updated to reflect the additive (not substitutive) plan.
>
> **Three retained sync-psycopg2 paths:**
> 1. **Decision 2 (Audit 06 OVERRULE):** `src/core/database/db_config.py::DatabaseConnection` is called from `scripts/deploy/run_all_services.py:84,135` as a pre-uvicorn health check — runs BEFORE the asyncio event loop is created, cannot use `asyncio.run()` because the eventual uvicorn process needs a clean loop. (Original Agent F finding 1.4 missed this caller.)
> 2. **Decision 1 (Path B):** new `get_sync_db_session()` factory in `database_session.py` lives alongside async `get_db_session()`. Adapter code (running in `run_in_threadpool`) needs sync sessions because adapters stay sync `def` (Path B chosen because porting `googleads==49.0.0` off `suds-py3` is ~1500 LOC for zero AdCP-visible benefit).
> 3. **Decision 9 (sync-bridge):** new `src/services/background_sync_db.py` module with a separate sync engine for multi-hour `background_sync_service` jobs (asyncpg `pool_recycle=3600` rotates connections under long-held async sessions, identity map grows unbounded, Fly.io TCP keepalives expire).
>
> All three paths use sync `psycopg2`; each has a different consumer and a different engine configuration (db_config raw connection / Path B pooled session 5+10/30s timeout / sync-bridge pooled session 2+3/600s timeout). New structural guard `tests/unit/test_architecture_no_runtime_psycopg2.py` allowlists ONLY `db_config.py` + `background_sync_db.py` (and `database_session.py` for the Path B factory). Runtime introduction of psycopg2 anywhere else fails CI.

**Current state:**
- `pyproject.toml:19` — `"psycopg2-binary>=2.9.9"` (main dep) — **RETAINED**
- `pyproject.toml:74` — `"types-psycopg2>=2.9.21.20251012"` (dev dep, optional-dependencies) — **RETAINED**
- `pyproject.toml:101` — same `types-psycopg2` in the `[dependency-groups] dev` block (duplicate block — see finding 1.2) — **RETAINED in both blocks**
- `uv.lock:122, 228, 3091+` — psycopg2-binary 2.9.11 is the resolved version with wheels for cp312 macOS/Linux/Windows — **NO CHANGE**

**Change required (CORRECTED 2026-04-11 — additive, not substitutive):**
- ~~Remove `psycopg2-binary>=2.9.9`~~ **KEEP** — required by Decisions 1, 2, 9
- **Add** `asyncpg>=0.30.0,<0.32` (alongside psycopg2, both coexist)
- ~~Remove `types-psycopg2>=2.9.21.20251012`~~ **KEEP** in both blocks — `db_config.py` + `background_sync_db.py` import psycopg2 at runtime
- Add `sqlalchemy[asyncio]>=2.0.0` (forces `greenlet` explicitly as a documented transitive — or keep `sqlalchemy>=2.0.0` and let `greenlet` come in via the asyncio extra)
- **Add** `dependency-groups.dev`: `tests/unit/test_architecture_no_runtime_psycopg2.py` enforces the allowlist (db_config.py + background_sync_db.py + database_session.py only)

**Change required (THIS agent's scope — extra actions):**

**Action F1.1.1 [MUST, Wave 0]** — Lock the asyncpg minimum version against the Python floor. `pyproject.toml:6` declares `requires-python = ">=3.12"`. asyncpg 0.30.0 supports Python 3.9+ so this is safe, but asyncpg <0.30 has bugs with Python 3.12 `ssl` module changes. Pin at `asyncpg>=0.30.0,<0.32` to avoid surprise breakage.

**Action F1.1.2 [MUST, Wave 0]** — Audit `uv.lock` transitive tree for:
- Any package currently pulled in transitively by psycopg2 that will lose its dependency edge after the swap. `grep psycopg2 uv.lock` — only direct `psycopg2-binary` appears, no transitive consumers. Safe.
- Any package currently pulled in by `sqlalchemy` that needs `greenlet>=1.0` — verify `greenlet` appears in the lock. **Check:** search `uv.lock` for `greenlet` after the dep swap. Must be present for AsyncSession to work.

**Action F1.1.3 [MUST, Wave 0]** — Verify asyncpg wheel availability for every target platform:
- **macOS Apple Silicon (cp312, arm64):** available per PyPI
- **macOS Intel (cp312, x86_64):** available
- **Linux x86_64 glibc (manylinux2014):** available
- **Linux arm64 glibc:** available
- **Alpine Linux (musl) cp312 x86_64:** **NOT available on PyPI as of 0.30.0.** asyncpg does not publish musllinux wheels. `postgres:17-alpine` is only used as a service container, not a Python runtime — our `Dockerfile:4` uses `python:3.12-slim` (Debian/glibc), so asyncpg wheels work. **But** if any future deployment flips to `python:3.12-alpine`, the build will fail at wheel install → fall back to sdist → require `gcc` + `postgresql-dev` at build time → image grows by ~150MB. Document this constraint in `docs/deployment.md`.
- **Windows cp312:** available (for local dev on Windows — not required but nice)

**Action F1.1.4 [MUST, Wave 0]** — `pyproject.toml:86-112` has a **duplicate** `dev` block — both `[project.optional-dependencies].dev` and `[dependency-groups].dev`. They both list `factory-boy>=3.3.0` **three times** each. This is a pre-existing mess. When removing `types-psycopg2`, remove it from BOTH blocks. Propose cleaning up the duplicate factory-boy entries as a side refactor — not strictly blocking but makes the pivot PR noisy. (Citations: `pyproject.toml:60-78, 86-112`.)

**Action F1.1.5 [SHOULD, Wave 4]** — Run `uv lock --upgrade-package asyncpg` explicitly after the swap and commit the new `uv.lock`. Do NOT run `uv lock` (without the package target) as it will churn unrelated transitives.

**Priority:** MUST for 1.1.1-1.1.4, SHOULD for 1.1.5.

---

#### 1.2 Build-time compilation for asyncpg

> **⚠️ CORRECTED 2026-04-11 — PARTIAL REVERSAL.** Action F1.2.1 (remove `libpq-dev` + `libpq5`) is **superseded** by Decisions 1, 2, 9 which retain `psycopg2-binary` for three sync paths. Although `psycopg2-binary` ships its own bundled libpq, any future swap to source-built `psycopg2` (no binary) would need libpq dev headers. More importantly, Decision 9's `background_sync_db.py` keeps the door open for switching to non-binary psycopg2 in v2.1+ if the binary build proves slow. **Keep `libpq5` in runtime, keep `libpq-dev` in builder.** The Docker image savings adjust from the original ~80MB total (Agent F overall) to ~75MB (because the libpq components stay).

**Current state:**
- `Dockerfile:17-20` installs `gcc`, `libpq-dev`, `git` in builder stage — **RETAINED**
- `Dockerfile:65-68` runtime stage installs `libpq5`, `curl`, `nginx` — **RETAINED**
- `uv sync --frozen` at `Dockerfile:38-40` uses the lockfile — no change

**Change required (CORRECTED 2026-04-11):**

**Action F1.2.1 [REVERSED — DO NOT REMOVE]** — ~~asyncpg has pre-built wheels for glibc and does NOT need `libpq-dev` at build time OR `libpq5` at runtime. Remove `libpq-dev` from builder, `libpq5` from runtime.~~ **KEEP both.** `psycopg2-binary` ships its own bundled libpq today, but Decision 9's sync-bridge module reserves the option to swap to source-built psycopg2 in v2.1+ if asyncpg's binary distribution constrains us. Removing libpq now would force a future Dockerfile change at the same time as a non-trivial deps swap — keep them coupled.

**Action F1.2.2 [CAN, v2.1]** — Add a `--no-compile` flag to uv sync inside Docker to avoid compiling sdists for transitive deps. Minor optimization, defer.

**Priority:** REVERSED for 1.2.1 (action is now "no change"); CAN for 1.2.2.

**Net image size impact (corrected):** Original Agent F estimate of "~80MB savings" assumed psycopg2-binary + types-psycopg2 + libpq-dev + libpq5 all removed. Corrected estimate: ~75MB savings (only the Wave 3 Flask removal + nginx slim base + cleanup of dead deps; psycopg2 + libpq stay).

---

#### 1.3 `types-psycopg2` removal — mypy breakage

**Current state:**
- `pyproject.toml:74, 101` — `types-psycopg2>=2.9.21.20251012`
- `.pre-commit-config.yaml:294-305` — pre-commit mypy step lists `additional_dependencies` — does NOT include `types-psycopg2` (only `sqlalchemy[mypy]`, `types-requests`, `types-python-dateutil`, `types-pytz`, `types-Markdown`, `types-waitress`, `adcp==3.2.0`, `fastmcp`, `alembic`). Good — no pre-commit breakage from removing types-psycopg2.
- `mypy.ini` — no psycopg2-specific rules. Grep confirms: `grep psycopg2 mypy.ini` → nothing.

**Change required:**

**Action F1.3.1 [MUST, Wave 0]** — After removing `types-psycopg2`, run `uv run mypy src/ --config-file=mypy.ini` on branch-ancestor and branch-HEAD to confirm no new errors. Any existing `import psycopg2` in src/ (unlikely — `grep -rn "import psycopg2" src/` → only `src/core/database/db_config.py:114` and `src/core/database/db_config.py:115`) will become mypy errors. **Fix:** rewrite `db_config.py:DatabaseConnection` class to use asyncpg, OR add `# type: ignore[import-untyped]` since asyncpg has no stubs and mypy.ini has `ignore_missing_imports = True`. See finding 1.4 below.

**Action F1.3.2 [SHOULD, Wave 4]** — Add `types-asyncpg` to dev deps IF it exists. **Verified:** as of 2026-04-11, there is NO `types-asyncpg` package on PyPI. asyncpg ships its own type stubs (`asyncpg/__init__.pyi`) so mypy will pick them up natively. No action needed; remove this item.

**Priority:** MUST.

---

#### 1.4 `src/core/database/db_config.py:DatabaseConnection` — raw psycopg2.connect

> **⚠️ CORRECTED 2026-04-11 — REVERSED.** Action F1.4.1 (delete `DatabaseConnection`) is **superseded by Audit 06 Decision 2 OVERRULE.** The `DatabaseConnection` class STAYS. The callers list in this finding was **incomplete** — it missed `scripts/deploy/run_all_services.py:84,135` calling `get_db_connection()` as **pre-uvicorn health checks**. Those checks run BEFORE the asyncio event loop is created (entrypoint script gates uvicorn startup on DB reachability), so `asyncio.run(asyncpg.connect(...))` is **structurally wrong** — it would create-and-tear-down an event loop just to do a single SELECT, then uvicorn would create a fresh loop, and any module-level state initialized during the throwaway loop becomes invalid (asyncpg connection pools are loop-bound). The simplest correct shape is "keep `DatabaseConnection` as the raw-psycopg2 pre-uvicorn path." See Audit 06 Decision 2 OVERRULE for the full reasoning trail.

**Current state (critical finding — not covered by any other agent):**

`src/core/database/db_config.py:105-172` defines a `DatabaseConnection` class and `get_db_connection()` helper that do raw `psycopg2.connect(...)` with `psycopg2.extras.DictCursor`. This class is **separate** from the SQLAlchemy engine in `database_session.py` — it exists for bootstrap/health checks that run before the engine is available or from contexts where SQLAlchemy is overkill.

**Callers of `get_db_connection()` (CORRECTED 2026-04-11):**
- `scripts/deploy/run_all_services.py:84,135` — **container startup health check, pre-uvicorn (cannot use async)** — Audit 06 added these
- `scripts/setup/init_database.py` — initial DB bootstrap
- `scripts/setup/init_database_ci.py:22` — CI setup

The original Agent F report listed only `:65, 133` in `run_all_services.py`. The actual line numbers as of 2026-04-11 are `:84, 135` (file was updated). Both calls are pre-uvicorn — the entrypoint script needs the DB to be reachable BEFORE it execs uvicorn. Spawning an event loop just for the check would leave a dangling loop reference that would later conflict with uvicorn's loop creation.

**Change required (CORRECTED 2026-04-11):**

**Action F1.4.1 [REVERSED — KEEP DatabaseConnection]** — ~~Delete `DatabaseConnection` entirely.~~ **KEEP it as the raw-psycopg2 pre-uvicorn path.** `psycopg2-binary` stays in `pyproject.toml` (per F1.1 reversal). Add structural guard `tests/unit/test_architecture_no_runtime_psycopg2.py` with allowlist:

```python
# Allowed psycopg2 importers (the only files that may import psycopg2 at runtime)
ALLOWED_PSYCOPG2_IMPORTERS = {
    "src/core/database/db_config.py",          # raw connection for pre-uvicorn health check
    "src/core/database/database_session.py",   # Decision 1 Path B sync session factory
    "src/services/background_sync_db.py",      # Decision 9 sync-bridge for multi-hour syncs
}
```

The guard walks every `import psycopg2` and `from psycopg2 import ...` AST node and fails if the importing file is not in the allowlist. New violations require either fixing the violation OR adding the file to the allowlist with a justification comment.

**Action F1.4.2 [REVERSED — Audit 06 SUBSTITUTE]** — ~~Audit `scripts/setup/init_database.py`, `scripts/setup/init_database_ci.py`, `scripts/setup/setup_tenant.py` for sync DB patterns and async-wrap them.~~ **KEEP these as sync** — they are one-shot bootstrap scripts that do not run inside the uvicorn event loop. `asyncio.run()` would work but adds complexity for zero benefit. Sync `psycopg2.connect(...)` is the correct shape for one-shot scripts.

**Priority:** REVERSED. This finding's recommendation was **wrong** — the meta-audit (Audit 06) caught the missed health-check callers and the architectural reason `asyncio.run()` is wrong for pre-uvicorn paths. Preserved for traceability of how the deep-think round corrected the original audit.

---

#### 1.5 `asyncpg` TLS mode vs psycopg2 `sslmode`

**Current state:**
- `docker-compose.yml:49` — `DATABASE_URL: postgresql://adcp_user:secure_password_change_me@postgres:5432/adcp?sslmode=disable`
- `docker-compose.e2e.yml:35` — same
- `docs/deployment/environment-variables.md:90` — `DB_SSLMODE=prefer` (default)
- `src/core/database/db_config.py:75` — parses `sslmode=require` into config dict
- `src/core/database/db_config.py:100-102` — writes `?sslmode=...` back into the connection string for SQLAlchemy

**The problem:**
- psycopg2 + SQLAlchemy dialect accepts `?sslmode=require|prefer|disable|allow|verify-ca|verify-full`
- asyncpg + SQLAlchemy dialect accepts `?ssl=true|false|require|prefer` — **different parameter name and different value vocabulary**
- Specifically: psycopg2's `sslmode=disable` → asyncpg's `ssl=false`. `sslmode=require` → `ssl=true` (or `ssl=require`). `sslmode=verify-full` has no direct asyncpg equivalent (asyncpg accepts an `ssl.SSLContext` object for full verification).

**Change required:**

**Action F1.5.1 [MUST, Wave 0]** — Add a URL rewriter that sits inside `get_engine()` and translates psycopg2-style `?sslmode=...` query params to asyncpg-style `?ssl=...` on the fly. Do not require users to edit their `.env` files. Implementation:
```python
def _asyncify_database_url(url: str) -> str:
    # postgresql:// → postgresql+asyncpg://
    # ?sslmode=disable → ?ssl=false
    # ?sslmode=require → ?ssl=true
    # etc.
    ...
```

**Action F1.5.2 [MUST, Wave 0]** — Update `docker-compose.yml:49` and `docker-compose.e2e.yml:35` to use `?ssl=false` (asyncpg style). OR keep `?sslmode=disable` and rely on the rewriter. Recommended: **keep the legacy form** so users with existing `.env` files don't hit a confusing "invalid connection URL" error. Rewriter handles both.

**Action F1.5.3 [SHOULD, Wave 4]** — Document the asyncpg-specific TLS vocabulary in `docs/deployment/environment-variables.md` alongside the existing `DB_SSLMODE` entry. Include a warning that under asyncpg, `sslmode=verify-ca` and `verify-full` require constructing an `ssl.SSLContext` and passing it via `connect_args={"ssl": ctx}` — the URL-string form is insufficient.

**Action F1.5.4 [SHOULD, Wave 4]** — Update `scripts/test-stack.sh:42` DATABASE_URL to include `?ssl=false` explicitly, so local test runs match what CI will produce.

**Priority:** MUST 1.5.1-1.5.2, SHOULD 1.5.3-1.5.4.

---

#### 1.6 `factory-boy` has no native async support

**Current state:**
- `pyproject.toml:61, 69, 71, 88, 96, 98` — `factory-boy>=3.3.0` (listed SIX times due to duplicate dev blocks — finding 1.1.4)
- `tests/factories/core.py:17-53` — uses `factory.alchemy.SQLAlchemyModelFactory` with `sqlalchemy_session_persistence = "commit"` — this calls `session.commit()` synchronously
- `tests/harness/_base.py:790-821` — `__enter__` uses sync `SASession(bind=engine)` and binds via `f._meta.sqlalchemy_session = self._session`

**Change required (already covered by Agent A/B at code level, this agent covers tooling/docs):**

**Action F1.6.1 [MUST, Wave 4]** — Decide factory adapter strategy in Wave 0 spike (not Wave 4). Options from Agent B Risk #3:
- **A.** Custom `AsyncSQLAlchemyModelFactory` wrapper that overrides `_create` to do `await session.commit()` — requires factory instantiation inside an async context manager
- **B.** Keep `SQLAlchemyModelFactory` sync, use `session.run_sync(...)` to bridge. Works but bridge every call
- **C.** Replace factory-boy with a custom factory system (biggest scope change, rejected)

**Recommended:** A, following the pattern at https://github.com/FactoryBoy/factory_boy/issues/679 (community async adapter). Write a tiny `tests/factories/_async_adapter.py` that subclasses `factory.alchemy.SQLAlchemyModelFactory` with async `_create`.

**Action F1.6.2 [SHOULD, Wave 0]** — Add `tests/factories/_async_adapter.py` as a small, focused PR **before** Wave 4. This unblocks test conversion work.

**Action F1.6.3 [SHOULD, Wave 4]** — Update `tests/CLAUDE.md` (which I saw injected at the end of my grounding read) — currently says "The harness manages the session; factories commit via the bound session." Under async, the harness manages an `AsyncSession`, and factories commit via `await session.commit()`. Update the "Session binding" section in `tests/CLAUDE.md`.

**Priority:** MUST 1.6.1, SHOULD 1.6.2-1.6.3.

---

#### 1.7 Test dependencies for async

**Current state:**
- `pyproject.toml:65` — `pytest-asyncio>=1.1.0` (already present)
- `pyproject.toml:82` — `pytest-asyncio` listed again in `ui-tests` optional group
- No `anyio` direct dep — only transitively via starlette/httpx
- No `asyncio_mode` setting anywhere — no `[tool.pytest.ini_options]` block in `pyproject.toml`, no `pytest.ini` file
- `httpx>=0.28.1` already present (needed for FastAPI AsyncClient test harness)
- `pytest-xdist` — **NOT in deps**. Integration tests appear to run sequentially per-env under tox. Verified: `grep pytest-xdist pyproject.toml uv.lock` — no pytest-xdist. Good — means Interaction B (xdist × pool) is a non-issue for tests, only for ad-hoc parallel runs.

**Change required:**

**Action F1.7.1 [MUST, Wave 0]** — Add `[tool.pytest.ini_options]` section to `pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"  # every async def test runs on the event loop without @pytest.mark.asyncio
```
This avoids manually decorating every test file with `@pytest.mark.asyncio`. Without this, converting tests is 10x noisier. **MUST** do before Wave 4 conversion work starts.

**Action F1.7.2 [MUST, Wave 0]** — Pin `pytest-asyncio` more tightly. `>=1.1.0` is too loose; pytest-asyncio 2.x may be released during the Wave 4 branch lifetime and has breaking changes. Pin at `pytest-asyncio>=1.1.0,<2.0`.

**Action F1.7.3 [SHOULD, Wave 4]** — Add `anyio>=4.0` as a direct dev dep. Even though httpx pulls it transitively, making it direct is explicit — tests using `anyio` primitives (e.g., `anyio.create_task_group`) gain documented support.

**Action F1.7.4 [CAN, v2.1]** — Consider adding `pytest-timeout` tuning for async test hangs. If an async test hangs on a `MissingGreenlet` in a background task, it can hang indefinitely. The test suite already has `--timeout=60` in CI (`.github/workflows/test.yml:236`), which is enough as a safety net.

**Priority:** MUST 1.7.1-1.7.2, SHOULD 1.7.3, CAN 1.7.4.

---

### Category 2 — Build + CI

#### 2.1 `tox.ini` — per-env async setup

**Current state:** `tox.ini:1-97`. Five envs (unit, integration, e2e, admin, bdd) + coverage env. Each env sets `COVERAGE_FILE = {toxworkdir}/.coverage.{envname}` and runs `pytest` with `--json-report`. `pass_env` at lines 14-29 lists DATABASE_URL, test ports, and other env vars.

**Change required:**

**Action F2.1.1 [MUST, Wave 0]** — Add `DB_POOL_SIZE`, `DB_POOL_MAX_OVERFLOW`, `ASYNCPG_INIT_MODE` to `tox.ini` `pass_env` list if those are runtime-configurable (see finding 4.1). Agent B Interaction B says to set pool_size smaller in test mode; this lives in env vars.

**Action F2.1.2 [SHOULD, Wave 4]** — Add a new tox env `driver-compat` (Agent B Spike 2 recommendation):
```ini
[testenv:driver-compat]
description = asyncpg driver compatibility smoke (Risk #2 spike)
commands =
    pytest tests/driver_compat/ -v \
        --json-report --json-report-file={toxworkdir}/driver-compat.json
```
And create `tests/driver_compat/` with: JSONType round-trip, UUID column type, statement_timeout verification, connection pool status, server_default reads, event listener firing. This directory does not exist yet — Wave 4 creates it.

**Action F2.1.3 [SHOULD, Wave 4]** — Add a new tox env `benchmark` for async-vs-sync comparison (Agent B Risk #10 Spike 3):
```ini
[testenv:benchmark]
description = Async vs sync performance comparison
deps = pytest-benchmark>=5.0.0
commands =
    pytest tests/performance/ -v --benchmark-json={toxinidir}/benchmark.json
```

**Action F2.1.4 [SHOULD, Wave 4]** — Add `env_list` entry for the new envs. Currently `env_list = unit, integration, e2e, admin, bdd` (line 6). Add `driver-compat` to the full list so `tox -p` picks it up. `benchmark` should NOT be in the default env_list (too slow for every run) — invoke explicitly via `tox -e benchmark`.

**Action F2.1.5 [MUST, Wave 0]** — Verify that under `asyncio_mode = auto`, the existing `pytest tests/unit/ tests/harness/` in `[testenv:unit]` still works. **Concern:** if a unit test is NOT async and imports `AsyncMock`, asyncio mode auto should not break it. Verified at pytest-asyncio docs: `asyncio_mode = auto` only affects `async def` test functions; plain `def` tests run normally.

**Action F2.1.6 [SHOULD, Wave 4]** — Update `[testenv:coverage]` `depends = unit, integration, e2e, admin, bdd` to also include `driver-compat` so coverage combines include driver-compat coverage. Alternatively skip — driver-compat tests are smokes, not business logic.

**Priority:** MUST 2.1.1, 2.1.5; SHOULD 2.1.2, 2.1.3, 2.1.4, 2.1.6.

---

#### 2.2 `Makefile` — quality and test targets

**Current state:** `Makefile:1-88`. `make quality` runs ruff format/check, mypy, `check_code_duplication.py`, `pytest tests/unit/ -x`. `make test-stack-up/down` calls `scripts/test-stack.sh`. `make test-entity ENTITY=...` runs across all non-BDD suites.

**Change required:**

**Action F2.2.1 [MUST, Wave 0]** — `make quality`'s `pytest tests/unit/ -x` line will fail immediately if unit tests convert to `async def` without pytest-asyncio auto mode. Once 2.1.1 ships, this line works unchanged. Verify.

**Action F2.2.2 [SHOULD, Wave 4]** — Add a new Makefile target `make test-driver-compat`:
```makefile
test-driver-compat:
	tox -e driver-compat
```
Lets engineers spike Risk #2 / #17 / #18 / #22 in one command.

**Action F2.2.3 [SHOULD, Wave 4]** — Add a new Makefile target `make benchmark` that runs `tox -e benchmark` and compares against `tests/performance/baseline-sync.json`. Wire a `compare_benchmarks.py` script that reads both JSONs and prints a delta table.

**Action F2.2.4 [SHOULD, Wave 5]** — Add `make check-lazy-load` target that runs the Wave 0 lazy-load audit (Agent B Spike 1). This is a one-shot grep + AST scan script, not an ongoing gate. Keeping it as a Makefile target makes it re-runnable when relationships change.

**Action F2.2.5 [MUST, Wave 0]** — `make test-entity ENTITY=delivery` runs unit+integration+e2e+admin at `Makefile:86-87`. Under `asyncio_mode=auto`, tests marked with `-m delivery` can be a mix of sync and async functions. Verify the command still works. Low risk but test during Spike 4 (test infrastructure conversion).

**Priority:** MUST 2.2.1, 2.2.5; SHOULD 2.2.2-2.2.4.

---

#### 2.3 `.pre-commit-config.yaml` — hook updates

**Current state:** 28 hooks across local and upstream repos. `.pre-commit-config.yaml:1-306`. Key hooks affected:

- `enforce-sqlalchemy-2-0` (lines 46-50) — checks for `session.query()` via regex. AsyncSession's lack of `.query()` makes this hook redundant at code level (Risk #16 in Agent B) but the hook still prevents regressions. Keep.
- `check-code-duplication` (lines 236-242) — will see transient duplication churn during the sync→async conversion (repositories, UoW patterns are copy-paste-shaped by design). Baseline must be relaxed in Wave 4 and re-tightened in Wave 5.
- `test-migrations` (lines 153-158) — runs against `sqlite:///.test.db`. **DEAD HOOK** since CLAUDE.md Pattern #3 bans SQLite. Remove.
- `smoke-tests` (lines 137-142) — at `stages: [manual]`. Unchanged by async.
- `pytest-unit` (lines 161-167) — also at `stages: [manual]`. Under asyncio_mode=auto works unchanged.
- `adcp-contract-tests` (lines 170-175) — always runs, already under pytest-asyncio (integration tests are async). Should be unchanged, verify.
- `mcp-contract-validation` (lines 178-183) — runs on schema.py and main.py edits. Integration test target (`tests/integration/test_mcp_contract_validation.py`). Will need async adapt.
- `mypy` (lines 289-305) — `additional_dependencies` list does NOT include `types-psycopg2`. After the swap, confirm mypy still passes against asyncpg (which ships its own stubs).

**Change required:**

**Action F2.3.1 [MUST, Wave 0]** — Delete the `test-migrations` hook (lines 152-158). It's already dead (SQLite-only) and has been manual-stage-only. Cleaner than leaving it.

**Action F2.3.2 [MUST, Wave 4]** — Update `.duplication-baseline` (currently `{"src": 44, "tests": 109}`) during Wave 4. The conversion will add many repository methods with structurally similar bodies. Expected ceiling: ~+20-30 duplications during conversion, trimming back to ~+5 after refactor. Accept the ratchet-up in Wave 4, ratchet back down in Wave 5. **Important:** the hook has `ratcheting baseline — can only decrease`. To allow an increase during conversion, temporarily run `check_code_duplication.py --update-baseline` at Wave 4 start, then re-tighten at Wave 5 end.

**Action F2.3.3 [SHOULD, Wave 4]** — Add a new pre-commit hook `check_no_sync_db_in_async`:
```yaml
- id: check-no-sync-db-in-async
  name: No sync get_db_session() calls inside async def
  entry: uv run python .pre-commit-hooks/check_no_sync_db_in_async.py
  language: system
  files: '^src/.*\.py$'
  pass_filenames: true
```
AST scanner: inside any `async def`, any `with get_db_session() as ...` (not `async with`) is an error. This catches the most common "forgot to convert" regression.

**Action F2.3.4 [SHOULD, Wave 4]** — Add `check_no_asyncio_run_in_lib`:
```yaml
- id: check-no-asyncio-run-in-lib
  name: No asyncio.run() calls in src/
  entry: sh -c 'if grep -r "asyncio\.run(" src/ --include="*.py" | grep -v "^src/core/config_loader.py:"; then echo "❌ asyncio.run() only allowed in config_loader bootstrap paths"; exit 1; fi'
```
Prevents `asyncio.run()` inside request handlers (would explode with RuntimeError: asyncio.run cannot be called from a running event loop).

**Action F2.3.5 [SHOULD, Wave 4]** — Add `check_no_module_level_get_engine` (Risk #33 mitigation as a guard):
```yaml
- id: check-no-module-level-get-engine
  name: No get_engine() calls at module import time
  entry: uv run python .pre-commit-hooks/check_no_module_level_get_engine.py
```
AST walks every module, flags any call to `get_engine()` or `get_db_session()` at the module top level (not inside a function or class). Prevents asyncpg event-loop binding at import time.

**Action F2.3.6 [MUST, Wave 0]** — Update `.pre-commit-config.yaml:294-305` mypy `additional_dependencies` list when swapping drivers. Verify no entry breaks.

**Action F2.3.7 [CAN, v2.1]** — Consider a hook that flags `session.execute(select(Model))` inside loops — under async, should use `selectinload` to avoid N+1. Low priority, advisory-only hook.

**Priority:** MUST 2.3.1, 2.3.6; SHOULD 2.3.2-2.3.5; CAN 2.3.7.

---

#### 2.4 GitHub Actions `.github/workflows/test.yml`

**Current state:** `test.yml:1-411`. Seven jobs: security-audit, smoke-tests, unit-tests, integration-tests (5-way matrix), quickstart-test (docker compose), e2e-tests, lint, test-summary.

Key issues I noticed:
- `integration-tests` uses `postgres:15` (line 135)
- `docker-compose.yml` uses `postgres:17-alpine` (locally)
- `docker-compose.e2e.yml` uses `postgres:17-alpine` (e2e)
- `agent-db.sh` uses `postgres:17-alpine`

**Change required:**

**Action F2.4.1 [MUST, Wave 0]** — **Align Postgres versions across all test surfaces to `postgres:17`.** Update `.github/workflows/test.yml:135` from `postgres:15` to `postgres:17`. This removes the version skew that would hide asyncpg codec bugs in local runs. See Executive Summary finding #4. **MUST** do before Wave 4 driver spike.

**Action F2.4.2 [MUST, Wave 4]** — The `integration-tests` job at line 178 runs `uv run python scripts/ops/migrate.py` with `DATABASE_URL=postgresql://adcp_user:test_password@localhost:5432/adcp_test`. Under async alembic (Risk #4), verify the migrate.py entry point still runs in sync mode (it uses `alembic upgrade head` via `command.upgrade`). alembic's command API is sync and delegates to env.py which we'll make async-aware. Action: run `scripts/ops/migrate.py` from CI with asyncpg and confirm it completes.

**Action F2.4.3 [SHOULD, Wave 4]** — Update all `DATABASE_URL` env vars across `test.yml` to use `postgresql://` (unchanged — URL rewriter handles the asyncpg prefix). But: add `?ssl=false` query param to the CI URLs to be explicit. **Note:** the CI postgres service is on `localhost:5432` with no TLS, so `?sslmode=disable` / `?ssl=false` must be set explicitly for asyncpg, which **defaults to SSL prefer** unlike psycopg2 which defaults to prefer-but-falls-back-to-disable.

**Action F2.4.4 [SHOULD, Wave 4]** — `quickstart-test` job (lines 239-285) runs `docker compose up -d --wait`. Verify this still works when the container startup path (run_all_services.py) uses asyncpg. **Critical:** this job tests the real production startup path. If the entrypoint_admin.sh (F finding #1) is broken, this job fails.

**Action F2.4.5 [SHOULD, Wave 4]** — Add a new job `driver-compat` that runs `tox -e driver-compat` against a fresh postgres:17 service. This is the CI version of Agent B Spike 2. Runs in parallel with existing integration tests.

**Action F2.4.6 [SHOULD, Wave 5]** — Add a new job `benchmark-compare` that runs `tox -e benchmark` and compares against `tests/performance/baseline-sync.json` committed during Wave 0. Fails if async is >20% slower on any critical path. Runs on-demand (workflow_dispatch) not on every PR.

**Action F2.4.7 [CAN, v2.1]** — Consider switching `integration-tests` matrix from 5 groups to 6 by adding `async` as its own group. Would add observability but also CI minutes. Defer.

**Action F2.4.8 [CAN, v2.1]** — Add `pytest-asyncio` warnings check to CI — any test with DeprecationWarning from asyncio should fail (catches Risk #32).

**Priority:** MUST 2.4.1, 2.4.2; SHOULD 2.4.3-2.4.6; CAN 2.4.7-2.4.8.

---

#### 2.5 Alembic migration runtime

**Current state:**
- `alembic.ini:8` — `script_location = %(here)s/alembic`
- `alembic.ini:87` — `sqlalchemy.url = driver://user:pass@localhost/dbname` (placeholder, overridden in env.py)
- `alembic/env.py:1-92` — current env.py uses sync `engine_from_config()` and sync `connectable.connect()` (lines 70-86)
- `scripts/ops/migrate.py:48` — calls `command.upgrade(alembic_cfg, "head")` (sync)
- `docker-compose.yml:39-58` — dedicated `db-init` service that runs `python scripts/ops/migrate.py` on startup before `adcp-server` starts
- `scripts/deploy/run_all_services.py:202-223` — calls `run_migrations()` as a sync subprocess during production container startup

**Change required:**

**Action F2.5.1 [MUST, Wave 0]** — Decide alembic async strategy. Options from Agent B Risk #4:
- **A.** Rewrite `alembic/env.py` to use `asyncio.run(run_migrations_online())` with `create_async_engine` + `connection.run_sync(do_run_migrations)`. Standard alembic + asyncpg pattern.
- **B.** Keep `alembic/env.py` sync, rewrite the URL at engine construction to strip `+asyncpg`, use `psycopg2` inside env.py ONLY. Means psycopg2 stays as a transient dep.
- **C.** Use `sqlalchemy.ext.asyncio` inside env.py but call `async_engine.sync_engine` to get back a sync engine for alembic's sync API. Hack, works.

**Recommended:** A (full async alembic). Standard pattern, ~30 LOC rewrite of env.py. Keeps psycopg2 fully removed.

**Action F2.5.2 [SHOULD, Wave 4]** — Once `env.py` is async, verify `compare_type=True` and `compare_server_default=True` options (lines 80-81) still fire. These are autogenerate flags; they work the same in async context.

**Action F2.5.3 [MUST, Wave 4]** — Update `scripts/ops/migrate.py` entry points if needed. `command.upgrade(alembic_cfg, "head")` is sync and wraps the env.py. If env.py calls `asyncio.run()` internally, the outer sync command.upgrade() still works. No change required in migrate.py itself — the asyncio boundary is inside env.py. **Verify in Spike 6.**

**Action F2.5.4 [MUST, Wave 0]** — Test: run `uv run alembic revision -m "test"` to make sure the autogenerate path works under async env.py. Autogenerate needs a live connection to compare metadata against the real schema. This is CRITICAL — autogenerate is the primary migration development workflow.

**Action F2.5.5 [SHOULD, Wave 4]** — Migration scripts in `alembic/versions/*.py` DO NOT need to be async. They run inside `context.begin_transaction()` which is a sync context provided by alembic. `op.execute(...)`, `op.create_table(...)` etc remain sync. **Verify** no individual migration file has `await` or `async def` in it currently — `grep -rn "async def\|await " alembic/versions/` should return nothing. Document this invariant in `docs/development/structural-guards.md`.

**Action F2.5.6 [SHOULD, Wave 4]** — The `db-init` service in `docker-compose.yml:40-57` has `entrypoint: []` override and `command: ["python", "scripts/ops/migrate.py"]`. Under async alembic, this works unchanged because migrate.py wraps asyncio.run(). But: test timing. Currently depends_on waits for postgres healthcheck; db-init runs once; adcp-server waits for db-init `condition: service_completed_successfully`. Under async, db-init startup is ~0.5s slower due to asyncio import overhead. Acceptable.

**Action F2.5.7 [CAN, v2.1]** — Consider adding a `make test-migrations` target that runs every migration up + down against a test DB. Currently the pre-commit hook `test-migrations` is dead (F2.3.1). A Makefile equivalent would be useful.

**Priority:** MUST 2.5.1, 2.5.3, 2.5.4; SHOULD 2.5.2, 2.5.5, 2.5.6; CAN 2.5.7.

---

#### 2.6 `run_all_tests.sh` — test runner script

**Current state:** `run_all_tests.sh:1-126`. Two modes: `quick` and `ci`. CI mode starts Docker stack, runs tox parallel, tears down. Validates imports first (`validate_imports()` at lines 32-42). Collects JSON reports from `.tox/*.json`.

**Change required:**

**Action F2.6.1 [MUST, Wave 0]** — `validate_imports()` at line 34-38 imports `from src.core.tools import get_products_raw, create_media_buy_raw` and `from src.core.tools.products import _get_products_impl`. Under full async, these are async callables. The validation just imports them, doesn't call them, so async-vs-sync is irrelevant. **No change needed.** Verify by running it.

**Action F2.6.2 [SHOULD, Wave 4]** — Add `driver-compat` to `collect_reports()` at line 46-49:
```bash
for name in unit integration e2e admin bdd driver-compat; do
    [ -f ".tox/${name}.json" ] && cp ".tox/${name}.json" "$RESULTS_DIR/"
done
```

**Action F2.6.3 [SHOULD, Wave 4]** — Update the "Running all 6 suites in parallel via tox" message at line 88 when driver-compat is added. Currently says "all 6 suites" (stale — there are 5). With driver-compat added, becomes 6. With benchmark as opt-in, it stays 6 by default.

**Action F2.6.4 [CAN, v2.1]** — Add timing instrumentation to `run_all_tests.sh` so async-vs-sync runs can be compared. Low priority.

**Priority:** MUST 2.6.1 (just verify, no code change), SHOULD 2.6.2-2.6.3, CAN 2.6.4.

---

#### 2.7 `scripts/run-test.sh` — per-test runner

**Current state:** `scripts/run-test.sh:1-132`. Auto-detects infra from test path (unit/integration/e2e/admin). Uses `agent-db.sh up` for integration, `test-stack.sh up` for e2e/admin. Sets env vars, then runs `uv run pytest "$@"`.

**Change required:**

**Action F2.7.1 [MUST, Wave 0]** — `agent-db.sh` exports `DATABASE_URL="postgresql://..."` (plain psycopg2 form). Under asyncpg the URL rewriter (F1.5.1) handles this at the SQLAlchemy layer. No change needed to agent-db.sh shell output itself. **Verify** by running an integration test with the rewriter active.

**Action F2.7.2 [SHOULD, Wave 4]** — `run-test.sh` could auto-set `DB_POOL_SIZE=2, DB_POOL_MAX_OVERFLOW=1` when running a single test (small pool is sufficient for one test, avoids asyncpg warmup overhead). Pass through via env export.

**Priority:** MUST 2.7.1 (verify only), SHOULD 2.7.2.

---

### Category 3 — Docker + deployment

#### 3.1 `Dockerfile` — base image, system deps, runtime

**Current state:** `Dockerfile:1-119`. Multi-stage build: `python:3.12-slim` builder + runtime. Builder installs `gcc`, `libpq-dev`, `git`. Runtime installs `libpq5`, `curl`, `nginx`. Copies `.venv` from builder. Entrypoint: `["/app/.venv/bin/python", "scripts/deploy/run_all_services.py"]` (line 119).

**Change required:**

**Action F3.1.1 [MUST, Wave 4]** — Remove `libpq-dev` from builder RUN (line 19). Remove `libpq5` from runtime RUN (line 66). Neither is needed for asyncpg (pure Python + its own C extension, no libpq linkage). Saves ~8MB. **Caveat:** if any transitive dep in `uv.lock` has an optional libpq dependency (unlikely but possible for `psycopg` which is also sometimes pulled), leave libpq5 until verified. Grep `uv.lock` for "psycopg" → only `psycopg2-binary` (removed). Safe.

**Action F3.1.2 [SHOULD, Wave 4]** — `Dockerfile:115-116` HEALTHCHECK uses `curl -f http://localhost:8080/health`. `/health` returns `{"status": "healthy", "service": "mcp"}` today. Under async, this endpoint (`src/routes/health.py:37-40`) stays synchronous-semantically (just returns a JSONResponse, no DB access). Healthcheck works unchanged.

**Action F3.1.3 [SHOULD, Wave 4]** — Consider adding a `/health/db` healthcheck variant that DOES touch the DB (Agent B Risk #6 pool monitoring). Endpoint returns pool status + DB reachability. Would replace / augment the `curl /health` line. Defer to Wave 5 polish — not blocking.

**Action F3.1.4 [CAN, v2.1]** — `ENTRYPOINT` is `["/app/.venv/bin/python", "scripts/deploy/run_all_services.py"]`. Under full async, `run_all_services.py` remains a sync orchestrator (it spawns subprocesses, not async tasks). No change.

**Priority:** MUST 3.1.1, SHOULD 3.1.2-3.1.3, CAN 3.1.4.

---

#### 3.2 `docker-compose.yml` (dev)

**Current state:** `docker-compose.yml:1-125`. 4 services: postgres, db-init, proxy, adcp-server. Uses `postgres:17-alpine`. `DATABASE_URL: postgresql://adcp_user:...@postgres:5432/adcp?sslmode=disable` at line 49 (db-init) and line 82 (adcp-server). Mounts source code for hot reload.

**Change required:**

**Action F3.2.1 [SHOULD, Wave 4]** — DATABASE_URL at `docker-compose.yml:49,82` uses `?sslmode=disable`. Under asyncpg, the URL rewriter (F1.5.1) translates this to `?ssl=false`. OR: update the compose files to use `?ssl=false` directly. **Recommended:** keep the compose files using `sslmode=disable` and let the rewriter do the work — minimizes diff noise and preserves compat with developers who haven't pulled the latest images.

**Action F3.2.2 [SHOULD, Wave 4]** — `db-init` service at line 40-57 runs `python scripts/ops/migrate.py`. Under async alembic, this still works via asyncio.run() inside env.py. **Verify** by running docker-compose up locally after Wave 4.

**Action F3.2.3 [SHOULD, Wave 4]** — `adcp-server` service healthcheck at line 115-119 uses `curl -f http://localhost:8080/health`. Unchanged. Works.

**Action F3.2.4 [MUST, Wave 0]** — `adcp-server` environment at lines 81-94 has PYTHONPATH, DATABASE_URL, SKIP_NGINX, etc. Add new env vars if pool sizing becomes configurable: `DB_POOL_SIZE=5, DB_POOL_MAX_OVERFLOW=5` (smaller for dev). This is optional but recommended.

**Priority:** MUST 3.2.4, SHOULD 3.2.1-3.2.3.

---

#### 3.3 `docker-compose.e2e.yml`

**Current state:** `docker-compose.e2e.yml:1-78`. Similar structure but for E2E testing. Uses `POSTGRES_PORT:-5435`, `ADCP_SALES_PORT:-8092`. ADCP_MULTI_TENANT=true.

**Change required:**

**Action F3.3.1 [SHOULD, Wave 4]** — Same DATABASE_URL treatment as F3.2.1. Line 35.

**Action F3.3.2 [MUST, Wave 4]** — Update `ADCP_TESTING` usage. E2E tests use Docker, so they exercise the full production path including async alembic + async startup. Verify this path works end-to-end in Spike 6.

**Priority:** SHOULD 3.3.1, MUST 3.3.2.

---

#### 3.4 `docker-compose.multi-tenant.yml`

**Current state:** not read but assumed similar structure. `grep -l DATABASE_URL docker-compose.multi-tenant.yml`.

<!-- Will verify in Section 3 additions -->

**Action F3.4.1 [SHOULD, Wave 4]** — Same treatment as F3.2.1 / F3.3.1. Any DATABASE_URL with `?sslmode=...` — the rewriter handles it at engine construction time.

**Priority:** SHOULD.

---

#### 3.5 `scripts/deploy/run_all_services.py` — container entrypoint

**Current state:** `scripts/deploy/run_all_services.py:1-411`. The main production entrypoint:
1. `validate_required_env()` — env var check (lines 24-56)
2. `check_database_health()` — **sync psycopg2 via `get_db_connection()`** (lines 59-125) — **BREAKS WITHOUT psycopg2**
3. `run_migrations()` — subprocess calls migrate.py (lines 202-223)
4. `check_schema_issues()` — **sync psycopg2 again** (lines 128-164)
5. `init_database()` — imports `init_db` from `database` (line 175)
6. Threading to spawn MCP server, cron, nginx

**Change required (MASSIVE finding):**

**Action F3.5.1 [MUST, Wave 4]** — Rewrite `check_database_health()` (lines 59-125) to use asyncpg OR delete it entirely. Either:
- **Option A:** Replace `get_db_connection().execute("SELECT 1")` with `asyncio.run(asyncpg.connect(DATABASE_URL).fetchval("SELECT 1"))`. Small rewrite.
- **Option B:** Delete the whole function — migration runs anyway at line 349, which will fail noisily if DB is unreachable, making the health check redundant.

**Recommended:** Option A. Keep the health check because it provides better error messages (lines 90-123) than alembic's raw error — producing detail that helps Cloud Run users diagnose their config mistakes.

**Action F3.5.2 [MUST, Wave 4]** — Rewrite `check_schema_issues()` (lines 128-164) to use asyncpg. This function queries `information_schema.columns` for missing columns. Pure SELECT, no mutations. Small rewrite to `asyncio.run(asyncpg.connect(...).fetch(...))`.

**Action F3.5.3 [MUST, Wave 4]** — `init_database()` at line 167-181 imports `from src.core.database.database import init_db`. Check if `init_db()` is sync or async. **Grep:** `grep -n "def init_db" src/core/database/database.py` — need to verify. If sync, rewrite to async. If it calls `get_db_session()`, the async version becomes `asyncio.run(async_init_db())`.

**Action F3.5.4 [MUST, Wave 0]** — Audit `run_all_services.py` for module-level imports that might trigger Risk #33 (module-level engine creation). Line 65 does a LOCAL import `from src.core.database.db_config import ...` inside `check_database_health()`. Good — late binding. Line 133 same pattern. **Verify** no TOP-LEVEL imports of `database_session` in this file. **Verified:** lines 1-21 only have `import os, signal, subprocess, sys, threading, time` — no DB imports at module load. Safe.

**Action F3.5.5 [SHOULD, Wave 4]** — Update `run_all_services.py` docstring (line 3-11) to mention async patterns where relevant.

**Priority:** MUST 3.5.1-3.5.4, SHOULD 3.5.5.

---

#### 3.6 `scripts/deploy/entrypoint_admin.sh` — CRITICAL FINDING

**Current state:**
```bash
# entrypoint_admin.sh:9
if python -c "import psycopg2; psycopg2.connect('${DATABASE_URL}')" 2>/dev/null; then
```

**Change required:**

**Action F3.6.1 [MUST, Wave 4]** — Replace psycopg2 probe with asyncpg probe:
```bash
if python -c "import asyncio, asyncpg; asyncio.run(asyncpg.connect('${DATABASE_URL}').close())" 2>/dev/null; then
```
Or alternatively use `pg_isready -d "$DATABASE_URL"` if `postgresql-client` is installed in the image (it is not currently — see Dockerfile). **Recommended:** use the asyncpg python probe. No new system package dep.

**Action F3.6.2 [SHOULD, Wave 4]** — Evaluate whether `entrypoint_admin.sh` is still used at all post-migration. It references `migrate.py` (line 19) and `flask_caching` (line 30). Under v2.0, the admin app is FastAPI not Flask, so `flask_caching` is gone and this script may be orphaned. **Verify:** grep for references to `entrypoint_admin.sh` in all config files. **Grep result:** not referenced from `Dockerfile` (entrypoint is `run_all_services.py`), not referenced from compose files. **Conclusion:** this script is already orphaned. **Best action:** delete it in Wave 3 cleanup alongside `src/admin/server.py` and `scripts/run_admin_ui.py` — which the plan already flags for deletion.

**Priority:** MUST 3.6.1 if we keep it, MUST (preferred) delete.

---

#### 3.7 `fly.toml` — Fly.io deployment config

**Current state:** **No `fly.toml` file in the repo** (verified via `find . -name "fly*"`). Only `scripts/deploy/fly-set-secrets.sh` (secrets helper) and `docs/deployment/walkthroughs/fly.md`. Fly.io deployment is documentation-only, no committed config.

**Change required:**

**Action F3.7.1 [SHOULD, Wave 4]** — Update `docs/deployment/walkthroughs/fly.md` to reflect asyncpg-specific guidance: `DATABASE_URL` format, pool sizing, `kill_signal` + `kill_timeout` tuning for graceful async shutdown (Agent B Risk #26 says ≥30s needed).

**Action F3.7.2 [CAN, v2.1]** — Provide a reference `fly.toml.example` in the repo so Fly deployment is first-class.

**Priority:** SHOULD 3.7.1, CAN 3.7.2.

---

#### 3.8 `.env.secrets` / `.env.template`

**Current state:** `docs/deployment/environment-variables.md:79, 96-99` documents `DATABASE_URL`, `DATABASE_QUERY_TIMEOUT` (default 30), `DATABASE_CONNECT_TIMEOUT` (default 10), `DATABASE_POOL_TIMEOUT` (default 30), `USE_PGBOUNCER` (default false). `scripts/setup-dev.py:140-149` ensures `FLASK_SECRET_KEY` and `ENCRYPTION_KEY` exist in `.env`.

**Change required:**

**Action F3.8.1 [MUST, Wave 0]** — Add the following env vars to `docs/deployment/environment-variables.md`:
- `DB_POOL_SIZE` (default 10 for direct, 2 for PgBouncer) — Agent B Risk #6
- `DB_POOL_MAX_OVERFLOW` (default 20 for direct, 5 for PgBouncer) — Agent B Risk #6
- `DB_STATEMENT_TIMEOUT_MS` (default 30000) — Agent B Risk #18 (explicit override of `DATABASE_QUERY_TIMEOUT*1000`)
- `ASYNCPG_COMMAND_TIMEOUT` (default 30) — asyncpg client-side timeout, distinct from server-side statement_timeout

**Action F3.8.2 [SHOULD, Wave 4]** — Update `.env.template` (if present) with the new vars. Verified: `scripts/setup-dev.py:28` expects `ENV_TEMPLATE = ROOT_DIR / ".env.template"`. Need to read `.env.template` to update. **Defer verification to the implementer** — they can simply add:
```bash
# Async SQLAlchemy tuning (v2.0)
DB_POOL_SIZE=10
DB_POOL_MAX_OVERFLOW=20
DB_STATEMENT_TIMEOUT_MS=30000
```

**Action F3.8.3 [MUST, Wave 0]** — Document that `USE_PGBOUNCER=true` changes the pool sizing defaults AND the statement_timeout setting mechanism (`server_settings` vs `init` callback — Agent B Risk #18). Add a callout in `docs/deployment/environment-variables.md`.

**Action F3.8.4 [CAN, v2.1]** — Add `DB_POOL_TIMEOUT` as an async-specific var if it behaves differently from sync. SQLAlchemy unifies this via `pool_timeout`. No action.

**Priority:** MUST 3.8.1, 3.8.3; SHOULD 3.8.2; CAN 3.8.4.

---

### Category 4 — Observability + ops

#### 4.1 Health check endpoints

**Current state:**
- `src/routes/health.py:37-40` — `/health` returns `{"status": "healthy", "service": "mcp"}`. No DB access. Fast.
- `src/routes/health.py:286-304` — `/health/config` validates startup config. No DB access.
- `src/core/database/database_session.py:391-427` — `check_database_health()` function (circuit breaker) that does `SELECT 1`. NOT exposed via HTTP.
- `src/core/database/database_session.py:430-464` — `get_pool_status()` returns pool stats dict. NOT exposed via HTTP.
- **No `/metrics` endpoint exists.** Prometheus metrics in `src/core/metrics.py` are registered but never exposed.

**Change required:**

**Action F4.1.1 [SHOULD, Wave 4]** — Add `/health/pool` endpoint exposing `get_pool_status()` as JSON. Under async engine, the pool is `AsyncAdaptedQueuePool`; the `.size()`, `.checkedin()`, `.checkedout()`, `.overflow()` methods work the same. Agent B Risk #24 flags this as potential AttributeError — verify. Endpoint shape:
```json
{"size": 10, "checked_in": 8, "checked_out": 2, "overflow": 0, "total_connections": 10}
```

**Action F4.1.2 [SHOULD, Wave 4]** — Add `/health/schedulers` endpoint that asserts scheduler alive-tick (Agent B Risk #7 + deep-audit §3.7). The delivery and media-buy status schedulers bump a monotonic counter each tick; the endpoint returns `{"delivery_last_tick_unix": <ts>, "media_buy_last_tick_unix": <ts>}` and fails if last_tick is >2×interval old. This lets kubelet/fly.io kill a stuck pod.

**Action F4.1.3 [SHOULD, Wave 4]** — Expose `/metrics` for Prometheus scraping. Currently `src/core/metrics.py:67-69` defines `get_metrics_text()` but it's unused. Add:
```python
# src/routes/health.py
from src.core.metrics import get_metrics_text
@router.get("/metrics")
async def metrics():
    return Response(content=get_metrics_text(), media_type="text/plain; version=0.0.4")
```

**Action F4.1.4 [SHOULD, Wave 4]** — Extend `src/core/metrics.py` with DB pool gauges:
```python
db_pool_size = Gauge("db_pool_size", "SQLAlchemy pool size")
db_pool_checked_out = Gauge("db_pool_checked_out", "Currently checked-out connections")
db_pool_overflow = Gauge("db_pool_overflow", "Overflow connections in use")
db_pool_wait_seconds = Histogram("db_pool_wait_seconds", "Time waiting for pool checkout",
                                  buckets=[0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0])
```
Populate these via middleware that wraps `get_db_session()` calls.

**Action F4.1.5 [SHOULD, Wave 4]** — Add `asyncio_tasks_active` gauge via `len(asyncio.all_tasks())`. Sampled every N seconds by a lifespan-spawned background task. Detects event-loop task leaks.

**Action F4.1.6 [SHOULD, Wave 4]** — Add `asyncio_event_loop_lag_seconds` histogram. Detects CPU-blocking coroutines that delay the event loop. Use `asyncio.get_running_loop().time()` + a periodic sleep-and-measure pattern.

**Action F4.1.7 [CAN, v2.1]** — Consider exposing a `/debug/pool-state` endpoint (ADCP_TESTING-gated) that returns detailed per-connection state. High cardinality; defer.

**Priority:** SHOULD 4.1.1-4.1.6, CAN 4.1.7.

---

#### 4.2 Prometheus metric names — stability under async

**Current state:** `src/core/metrics.py:1-69`. 9 Counters/Histograms/Gauges defined. Label names and metric names are stable across async migration — they're protocol-level names, not driver-level.

**Change required:**

**Action F4.2.1 [CAN, v2.1]** — If pool sizing changes, consider renaming metric labels from `pool_checked_out` to `db_pool_checked_out` (already correct — 4.1.4 pattern uses `db_` prefix). Consistency check only.

**Priority:** None-blocking; metric naming is stable.

---

#### 4.3 Logging format — task ID / request ID propagation

**Current state:** `src/core/logging_config.py` exists (referenced at `src/core/startup.py:6` via `setup_structured_logging`). Not read in this session but inferred structure.

**Change required:**

**Action F4.3.1 [SHOULD, Wave 4]** — Switch request-ID propagation from thread-local to `contextvars.ContextVar`. Under async, multiple coroutines share a thread, so thread-local state leaks across requests. `contextvars` propagate per-task automatically. Critical for correlating log lines to requests under async.

**Action F4.3.2 [SHOULD, Wave 4]** — In dev mode, set `asyncio.get_event_loop().set_debug(True)` at startup. This enables slow-callback detection and logs warnings when a coroutine blocks the loop for >100ms. Gate on env var `ASYNCIO_DEBUG=true`. Add to `src/core/startup.py`.

**Action F4.3.3 [SHOULD, Wave 4]** — Log lines should include asyncio task ID (via `id(asyncio.current_task())`) instead of thread ID. Structured logging formatter update.

**Action F4.3.4 [CAN, v2.1]** — Evaluate `structlog` vs stdlib logging. Defer until Agent E's FastAPI idiom analysis (not this agent's scope).

**Priority:** SHOULD 4.3.1-4.3.3, CAN 4.3.4.

---

#### 4.4 Debug tooling compatibility

**Current state:** No repo-level debug tool config. Developers use whatever they want (VSCode, PyCharm, py-spy, etc.).

**Change required:**

**Action F4.4.1 [SHOULD, Wave 4]** — Document known-good debug tools for async Python 3.12 in `docs/development/async-debugging.md` (new file, Agent B mitigation for Risk #11):
- `py-spy dump --pid N --native` works for async (supports task introspection)
- `austin` works for async
- VSCode Python debugger: needs `justMyCode: false` and `pytest-asyncio` configured
- PyCharm: async-aware, works out of box
- `uvicorn --reload` in async mode: confirmed works, but breakpoints behave differently
- `asyncio.set_debug(True)`: see 4.3.2

**Action F4.4.2 [SHOULD, Wave 4]** — Add a section on "debugging `MissingGreenlet`" — the top async SQLAlchemy error newcomers hit. Include the exact stack trace decoder.

**Priority:** SHOULD.

---

### Category 5 — Docs + onboarding

#### 5.1 Project `CLAUDE.md` updates

**Current state:** `/Users/quantum/Documents/ComputedChaos/salesagent/CLAUDE.md` is what I was injected with. Key sections affected:

- **Pattern #3 (Repository + ORM-First)** — lines in the "Database: Repository Pattern + ORM-First" section. Documents sync `Session` in examples. Update to `AsyncSession` + async methods. Specifically:
  - `def get_by_id(self, ...) -> MediaBuy | None:` → `async def get_by_id(self, ...) -> MediaBuy | None:`
  - `return self.session.scalars(select(...)).first()` → `return (await self.session.execute(select(...))).scalars().first()`
- **Pattern #5 (Transport Boundary)** — already has async `_impl` examples, good. But the "Rules for `_impl`" section should mention "accept AsyncSession via UoW, not raw Session".
- **Pattern #8 (Test Fixtures: Factory-Based)** — factory examples use sync `TenantFactory.create_sync()` — update to async variant.
- **Structural Guards table** — add the 6 new guards (see Category 6 below).
- **Decision Tree** section — the "Adding a new AdCP tool" pattern needs to mention "repository is async, `_impl` is async".
- **SQLAlchemy 2.0 section** — under "Key Patterns" — examples show sync. Update both `select()` usage examples.

**Change required:**

**Action F5.1.1 [MUST, Wave 4]** — Update Pattern #3 example block in CLAUDE.md (the `MediaBuyRepository` example). ~10 line replacement.

**Action F5.1.2 [MUST, Wave 4]** — Update Pattern #5 to explicitly state: "`_impl` functions accept the async UoW/repository via parameter; they never call `get_db_session()` directly."

**Action F5.1.3 [MUST, Wave 4]** — Update the structural guards table to add:
| test_architecture_admin_routes_async | Admin handlers use `async def` | `test_architecture_admin_routes_async.py` |
| test_architecture_no_default_lazy_relationship | Relationships declare `lazy="raise"` or use eager loading | `test_architecture_no_default_lazy_relationship.py` |
| test_architecture_templates_no_orm_objects | Templates receive dicts/DTOs, not ORM instances | `test_architecture_templates_no_orm_objects.py` |
| test_architecture_no_server_default_without_refresh | Columns with `server_default` trigger refresh after insert | `test_architecture_no_server_default_without_refresh.py` |
| test_architecture_no_module_level_get_engine | No `get_engine()` calls at module import time | `test_architecture_no_module_level_get_engine.py` |
| test_architecture_admin_async_db_access | Admin DB access uses `async with`, not `with` | `test_architecture_admin_async_db_access.py` |

**Action F5.1.4 [MUST, Wave 4]** — Update the "Database JSON Fields" example to use async Mapped annotations (unchanged — `Mapped[]` is not async-specific).

**Action F5.1.5 [MUST, Wave 4]** — Add a new section "Async-specific gotchas" under "Critical Architecture Patterns" with:
- `expire_on_commit=False` is mandatory; don't change it
- Lazy-loaded relationships outside session scope raise `MissingGreenlet`
- Use `selectinload` / `joinedload` for multi-row relationship access
- `asyncio.run()` is banned in library code

**Action F5.1.6 [SHOULD, Wave 4]** — Update "Testing Workflow (Before Commit)" to mention `tox -e driver-compat` after driver changes.

**Action F5.1.7 [SHOULD, Wave 4]** — Update "What to Avoid" list:
- ❌ Don't use sync `with get_db_session()` inside `async def`
- ❌ Don't call `asyncio.run()` in library code (only in top-level scripts)
- ❌ Don't access ORM relationships outside the session context

**Priority:** MUST 5.1.1-5.1.5, SHOULD 5.1.6-5.1.7.

---

#### 5.2 `/docs` directory updates

**Current state:** `docs/` has 30+ markdown files. Key files that touch DB/async:
- `docs/development/architecture.md` — architecture overview
- `docs/development/patterns-reference.md` — pattern canonical files (line 9-30 has the repository example)
- `docs/development/troubleshooting.md` — troubleshooting
- `docs/deployment/environment-variables.md` — env vars
- `docs/deployment/multi-tenant.md` / `single-tenant.md` — deployment
- `docs/adapters/` — adapter-specific
- `docs/development/structural-guards.md` — 219+ lines on guards

**Change required:**

**Action F5.2.1 [MUST, Wave 4]** — Update `docs/development/patterns-reference.md` lines 9-30 (the `MediaBuyRepository` example). Sync → async.

**Action F5.2.2 [MUST, Wave 4]** — Update `docs/development/architecture.md` wherever DB session is discussed. Reference async patterns.

**Action F5.2.3 [MUST, Wave 4]** — Update `docs/development/structural-guards.md` to add the 6 new guards (mirroring CLAUDE.md update in F5.1.3).

**Action F5.2.4 [MUST, Wave 4]** — Update `docs/development/troubleshooting.md` with async-specific errors:
- `MissingGreenlet` — cause and fix
- `CoroutineNotAwaited` warning
- `cannot perform operation: another operation is in progress` (asyncpg concurrency)
- `TooManyConnectionsError` (pool exhaustion)
- `asyncio.CancelledError` during shutdown
- Add at lines ~30+ as a new "Async Issues" section.

**Action F5.2.5 [MUST, Wave 4]** — Update `docs/deployment/environment-variables.md` with new env vars from F3.8.1.

**Action F5.2.6 [SHOULD, Wave 4]** — Update `docs/deployment/multi-tenant.md` and `single-tenant.md` with asyncpg-specific deployment notes (pool sizing, PgBouncer compat).

**Action F5.2.7 [SHOULD, Wave 4]** — Update `docs/adapters/` READMEs if any adapter discusses DB access patterns. Most adapters are stateless, but check GAM adapter — `src/adapters/gam/*` — for sync DB callouts.

**Action F5.2.8 [SHOULD, Wave 4]** — `docs/development/contributing.md` — add an "Async migration" callout section if not present.

**Action F5.2.9 [CAN, v2.1]** — `docs/V2_ROADMAP_SUGGESTION.md` — may be stale now that v2.0 absorbs async. Review.

**Priority:** MUST 5.2.1-5.2.5, SHOULD 5.2.6-5.2.8, CAN 5.2.9.

---

#### 5.3 New docs to create

**Change required:**

**Action F5.3.1 [MUST, Wave 0]** — Create `docs/development/async-debugging.md` (Agent B Risk #11 mitigation). Contents:
- Reading async stack traces
- Common async errors and their root causes
- Tools: py-spy, austin, asyncio.debug mode
- Walkthrough: decoding `MissingGreenlet` to a lazy load site

**Action F5.3.2 [MUST, Wave 0]** — Create `docs/development/async-cookbook.md` (Agent B Section 3 has 9 patterns already drafted; this is that material promoted to docs). Contents:
- Pattern 1: `selectinload` for has-many
- Pattern 2: `joinedload` for one-to-one
- Pattern 3: chained `selectinload`
- Pattern 4: `raiseload` for explicit failure
- Pattern 5: `lazy="raise"` on relationship definition
- Pattern 6: `AsyncSession.refresh(instance, attribute_names=[...])`
- Pattern 7: `session.run_sync(...)` escape hatch
- Pattern 8: `expire_on_commit=False`
- Pattern 9: in-handler force-load before return

**Action F5.3.3 [SHOULD, Wave 4]** — Create `docs/development/lazy-loading-guide.md` (Agent B Risk #1 mitigation playbook). Contents:
- How to identify a lazy-load site
- How to audit relationships
- Fixing out-of-scope access by category (handler body, template render, JSON serialization)
- Decision tree: which eager-load pattern to use

**Action F5.3.4 [SHOULD, Wave 4]** — Create `docs/development/asyncpg-migration.md`. Contents:
- Why asyncpg (or alt: psycopg v3)
- Driver codec gotchas
- TLS setup
- PgBouncer compat
- Pool sizing guide

**Action F5.3.5 [CAN, v2.1]** — Create `docs/development/async-performance.md` (benchmarks, profiling, tuning).

**Priority:** MUST 5.3.1-5.3.2, SHOULD 5.3.3-5.3.4, CAN 5.3.5.

---

### Category 6 — Structural guards as tooling

#### 6.1 Existing guards enumeration

**Current state:** 22 `test_architecture_*.py` files in `tests/unit/`:
1. test_architecture_bdd_no_dict_registry.py
2. test_architecture_bdd_no_direct_call_impl.py
3. test_architecture_bdd_no_duplicate_steps.py
4. test_architecture_bdd_no_pass_steps.py
5. test_architecture_bdd_no_silent_env.py
6. test_architecture_bdd_no_trivial_assertions.py
7. test_architecture_bdd_obligation_sync.py
8. test_architecture_boundary_completeness.py
9. test_architecture_migration_completeness.py
10. test_architecture_no_model_dump_in_impl.py
11. test_architecture_no_raw_media_package_select.py
12. test_architecture_no_raw_select.py
13. test_architecture_obligation_coverage.py
14. test_architecture_obligation_test_quality.py
15. test_architecture_production_session_add.py
16. test_architecture_query_type_safety.py
17. test_architecture_repository_pattern.py
18. test_architecture_schema_inheritance.py
19. test_architecture_single_migration_head.py
20. test_architecture_test_marker_coverage.py
21. test_architecture_weak_mock_assertions.py
22. test_architecture_workflow_tenant_isolation.py

Plus non-prefixed guards:
- test_no_toolerror_in_impl.py
- test_transport_agnostic_impl.py
- test_impl_resolved_identity.py

**Change required:**

**Action F6.1.1 [MUST, Wave 4]** — For each existing guard, audit whether its allowlist is still valid after sync→async conversion:
- `test_architecture_no_raw_select.py` — allowlist entries that use sync `select()` remain valid (async uses the same `select()` function); no update needed
- `test_architecture_production_session_add.py` — allowlist entries that permit `session.add()` — under async, `session.add()` is still sync (the ORM's object identity map is thread-safe sync); no update. Verify by reading the guard.
- `test_architecture_query_type_safety.py` — checks query parameter types against column types. Sync vs async is irrelevant. No update.
- `test_architecture_repository_pattern.py` — checks no `get_db_session()` / `session.add()` outside repositories. Under async, the rule is the same; allowlist entries must convert to async but the guard logic doesn't change. **Wave 4 conversion progressively shrinks the allowlist.**
- `test_architecture_migration_completeness.py` — checks `upgrade()` and `downgrade()` in migrations. Migrations stay sync. No update.

**Action F6.1.2 [MUST, Wave 4]** — Update `test_architecture_boundary_completeness.py` to handle async wrapper signatures. Currently it inspects `inspect.signature()` on `_impl` functions and their wrappers. Under async, all three (MCP, A2A, `_impl`) are async. `inspect.signature()` works identically for async functions. **No guard logic change required**, but verify with a spike.

**Action F6.1.3 [MUST, Wave 4]** — Update `test_architecture_no_raw_media_package_select.py` to handle async `select()`. Same as 6.1.1 — `select()` itself is sync-callable; only `session.execute()` is async. Guard logic unchanged.

**Action F6.1.4 [SHOULD, Wave 4]** — Update `test_architecture_no_model_dump_in_impl.py` — no change needed (model_dump is sync, unaffected).

**Priority:** MUST 6.1.1-6.1.3, SHOULD 6.1.4.

---

#### 6.2 New guards to add

Per Agent B recommendations and my own analysis, the following new structural guards must be added.

**Action F6.2.1 [MUST, Wave 0]** — Create `tests/unit/test_architecture_no_default_lazy_relationship.py`:
- **Target:** `src/core/database/models.py`
- **AST logic:** parse models.py, find every `relationship(...)` call, check that the `lazy=` keyword is one of `{"raise", "joined", "selectin"}` OR the relationship is in the allowlist
- **Baseline:** current 68 relationships all have NO `lazy=` setting (implicit `"select"` = lazy). Establish baseline = 68 violations, ratchet down as Wave 4 converts.
- **Failure mode:** new `relationship()` without `lazy="raise"` fails CI.
- **Priority:** MUST — prevents new lazy-load landmines from being added during Wave 4 work.

**Action F6.2.2 [MUST, Wave 4]** — Create `tests/unit/test_architecture_admin_routes_async.py`:
- **Target:** `src/admin/routers/**/*.py` (Wave 1-3 output)
- **AST logic:** for every `@router.get/post/put/delete(...)`, check the decorated function is `async def`
- **Failure mode:** sync def admin handler → guard fails → catches the sync-def pivot slipping back in
- **Priority:** MUST — explicit invariant for the post-pivot world

**Action F6.2.3 [MUST, Wave 4]** — Create `tests/unit/test_architecture_admin_async_db_access.py`:
- **Target:** `src/admin/routers/**/*.py`, `src/admin/services/**/*.py`
- **AST logic:** any `with get_db_session()` inside an `async def` is a violation; must be `async with get_db_session()`
- **Failure mode:** sync DB access inside async handler → guard fails

**Action F6.2.4 [MUST, Wave 4]** — Create `tests/unit/test_architecture_templates_no_orm_objects.py`:
- **Target:** `src/admin/routers/**/*.py`
- **AST logic:** find calls to `render(request, "template.html", {context})`; walk the context dict values; flag any that are ORM model instances or lists thereof
- **Note:** this is hard to do statically because values come from local variables whose types aren't known. **Alternative:** check the function signature of helpers that call `render()` and verify they receive dicts/DTOs, not ORM objects. Or relax to a runtime check (integration test with `lazy="raise"` globally).
- **Difficulty:** HIGH. May deliver an integration test instead of an AST scanner.
- **Priority:** MUST but implementation may be an integration test, not a structural guard

**Action F6.2.5 [MUST, Wave 0]** — Create `tests/unit/test_architecture_no_server_default_without_refresh.py`:
- **Target:** `src/core/database/models.py`
- **AST logic:** find every `server_default=...` in `mapped_column(...)`. For each, check if a corresponding `default=...` exists (client-side fallback). Without `default=`, the field must be explicitly refreshed post-insert — flag a violation.
- **Allowlist:** existing `server_default` columns baselined; ratchet down.
- **Interaction:** Risk #5 + Interaction A — prevents `expire_on_commit=False` footguns.

**Action F6.2.6 [MUST, Wave 0]** — Create `tests/unit/test_architecture_no_module_level_get_engine.py`:
- **Target:** `src/**/*.py`
- **AST logic:** walk every module's top-level (outside function/class bodies), find any call to `get_engine()`, `get_db_session()`, `create_async_engine()`. Flag as violation.
- **Priority:** MUST — Risk #33 mitigation

**Action F6.2.7 [SHOULD, Wave 4]** — Create `tests/unit/test_architecture_no_asyncio_run_in_lib.py`:
- **Target:** `src/**/*.py`
- **AST logic:** find any `asyncio.run(...)` call. Allowlist only top-level scripts (`src/cli.py` if present, `scripts/ops/*.py`).
- **Priority:** SHOULD — prevents "asyncio.run() cannot be called from a running event loop" errors

**Action F6.2.8 [SHOULD, Wave 4]** — Create `tests/unit/test_architecture_no_run_in_threadpool_for_db.py`:
- **Target:** `src/admin/routers/**/*.py`
- **AST logic:** find `run_in_threadpool(get_db_session, ...)` or similar patterns. Flag as violation — DB is now async, no need for threadpool.
- **Priority:** SHOULD — cleanup guard for Wave 5

**Action F6.2.9 [SHOULD, Wave 4]** — Update `test_architecture_schema_inheritance.py` (existing) — no change needed because schema inheritance is orthogonal to sync/async. Verify.

**Action F6.2.10 [SHOULD, Wave 4]** — Update `test_architecture_repository_pattern.py` allowlist — as Wave 4 converts `_impl` functions, allowlist entries shrink. Expected: allowlist goes from ~40 entries to ~10 by Wave 5 end.

**Action F6.2.11 [CAN, v2.1]** — Create `tests/unit/test_architecture_no_sync_orm_in_async_def.py`:
- **Target:** `src/**/*.py`
- **AST logic:** inside `async def`, any `session.query(...)`, `session.add(...)` followed by `session.commit()` (not `await`), etc.
- **Priority:** CAN — advisory guard, may be redundant with other checks

**Priority Summary:**
- MUST (Wave 0): F6.2.1, F6.2.5, F6.2.6
- MUST (Wave 4): F6.2.2, F6.2.3, F6.2.4
- SHOULD (Wave 4): F6.2.7, F6.2.8, F6.2.9, F6.2.10
- CAN: F6.2.11

---

### Category 7 — Dev environment + auxiliaries

#### 7.1 `uv` sync across platforms

**Current state:** `pyproject.toml:6` requires Python 3.12+. `uv.lock` resolved for cp312 across macOS (arm64/x86_64), Linux (x86_64/arm64 glibc), Windows.

**Change required:**

**Action F7.1.1 [MUST, Wave 4]** — After `pyproject.toml` swap, run `uv lock --upgrade-package psycopg2-binary --upgrade-package asyncpg`. Commit the new lock. Review the diff for transitive churn.

**Action F7.1.2 [SHOULD, Wave 4]** — Test `uv sync` on macOS (local dev), Ubuntu (CI), Alpine (skip — not a Python runtime in this repo). Verify wheel installs complete under 10s each.

**Action F7.1.3 [CAN, v2.1]** — Pin uv version in `.github/workflows/test.yml:13` (`UV_VERSION: '0.9.6'`) — already done. Good.

**Priority:** MUST 7.1.1, SHOULD 7.1.2.

---

#### 7.2 `.claude/skills/agent-db/agent-db.sh`

**Current state:** Reads `agent-db.sh` — starts a bare postgres:17-alpine container per worktree on a unique port 50000-60000. Writes `.agent-db.env` with `DATABASE_URL="postgresql://..."`.

**Change required:**

**Action F7.2.1 [SHOULD, Wave 4]** — `agent-db.sh` does not need shell changes. The DATABASE_URL format stays `postgresql://` (not `postgresql+asyncpg://`) because the URL rewriter (F1.5.1) handles the protocol prefix at the SQLAlchemy layer. **Verify** by running integration tests against an agent-db-spun Postgres after Wave 4.

**Action F7.2.2 [SHOULD, Wave 4]** — `agent-db.sh:24` pins `PG_IMAGE="postgres:17-alpine"`. Already aligned with docker-compose. Good.

**Action F7.2.3 [CAN, v2.1]** — Consider `agent-db.sh` exporting `DB_POOL_SIZE=2, DB_POOL_MAX_OVERFLOW=1` by default so per-agent postgres isn't swamped by a too-large pool. Small optimization.

**Priority:** SHOULD 7.2.1-7.2.2, CAN 7.2.3.

---

#### 7.3 Auxiliary scripts audit

**Current state:** `scripts/` has many helper scripts. 8 files reference `database_session` or `db_config`:
- `scripts/ops/aggregate_format_metrics.py:61` — uses local import of `get_db_session`
- `scripts/ops/sync_all_tenants.py:17` — TOP-LEVEL import of `get_db_session` (Risk #33 candidate)
- `scripts/ops/gam_helper.py:7` — TOP-LEVEL import
- `scripts/ops/get_tokens.py:4` — TOP-LEVEL import
- `scripts/setup/setup_tenant.py:12` — TOP-LEVEL import
- `scripts/setup/init_database.py:9` — TOP-LEVEL import
- `scripts/setup/init_database_ci.py:22` — LATE import (good)
- `scripts/deploy/run_all_services.py:65, 133` — LATE imports (good)

**Change required:**

**Action F7.3.1 [MUST, Wave 4]** — Audit the 5 scripts with TOP-LEVEL imports: `sync_all_tenants.py`, `gam_helper.py`, `get_tokens.py`, `setup_tenant.py`, `init_database.py`. For each, verify it does NOT call `get_engine()` or `get_db_session()` at module load time (only within functions). Top-level IMPORT is fine — only top-level CALL is the Risk #33 problem.

**Grep to run:**
```
grep -n "get_db_session\|get_engine" scripts/ops/sync_all_tenants.py
```

If any of these call `get_db_session()` at module top, that call must move into a function. **This is part of Spike 1 and Wave 0 preparation.**

**Action F7.3.2 [MUST, Wave 4]** — Each script that touches DB must wrap its DB access in `asyncio.run(async_main())` — these scripts are CLI tools, not library code, so `asyncio.run()` is appropriate here.

**Action F7.3.3 [SHOULD, Wave 4]** — `scripts/gen-agent-index.py` uses stubgen against source files. Does NOT touch DB at runtime. Safe. No change.

**Action F7.3.4 [SHOULD, Wave 4]** — `scripts/run_server.py` uses `uvicorn.run("src.app:app", ...)` — sync `uvicorn.run()` is the entry point that starts the event loop. This is where the asyncio event loop originates for production. No change. Verify that `initialize_application()` at line 21 does NOT touch the DB. **Verified:** `src/core/startup.py:11-38` — only calls `setup_structured_logging`, `setup_oauth_logging`, `validate_configuration`. No DB. Safe.

**Action F7.3.5 [CAN, v2.1]** — Add a `scripts/compare_benchmarks.py` helper for Wave 5 benchmark comparison. New file.

**Priority:** MUST 7.3.1-7.3.2, SHOULD 7.3.3-7.3.4, CAN 7.3.5.

---

#### 7.4 `.duplication-baseline`

**Current state:** `.duplication-baseline` → `{"src": 44, "tests": 109}`.

**Change required:**

**Action F7.4.1 [MUST, Wave 4]** — At Wave 4 start, run `check_code_duplication.py --update-baseline` to snapshot the pre-conversion baseline. Commit. Then during conversion, duplications will temporarily spike as repository patterns are copied to every `_impl`. Expected spike: +15-25 blocks in src/.

**Action F7.4.2 [MUST, Wave 5]** — At Wave 5 start, refactor the duplicated async patterns into shared helpers (base `AsyncRepository`, base `AsyncUoW`, etc.). Ratchet the baseline DOWN to approximate the pre-Wave-4 level. Expected end state: `{"src": ~44 ± 3, "tests": ~109 ± 5}`.

**Action F7.4.3 [SHOULD, Wave 4]** — Document the Wave 4 baseline bump in the PR description so reviewers don't freak out. Include: "Baseline temporarily raised during sync→async conversion; Wave 5 ratchets back."

**Priority:** MUST 7.4.1-7.4.2, SHOULD 7.4.3.

---

#### 7.5 FIXME comments

**Current state:** 24+ `FIXME(salesagent-` comments in src/ across 5 files. These track allowlisted violations in structural guards.

**Change required:**

**Action F7.5.1 [SHOULD, Wave 4]** — Audit all FIXME comments for async-related landmines. `grep -rn "FIXME(salesagent-" src/` to get the list. For each, check:
- Does it reference a sync-DB pattern? If yes, mark as "Wave 4 conversion eligible"
- Does it reference threadpool usage? If yes, mark for removal

**Action F7.5.2 [SHOULD, Wave 5]** — At end of Wave 5, close all FIXME-tagged async-related issues. Update the beads issues (salesagent-*) to reflect the fix.

**Priority:** SHOULD.

---

#### 7.6 Auto-memory references

**Current state:** Auto-memory at `/Users/quantum/.claude/projects/-Users-quantum-Documents-ComputedChaos-salesagent/memory/MEMORY.md` references:
- `flask_to_fastapi_migration_v2.md` memory file — Critical Invariants include "sync def" as #1 (STALE after pivot)

**Change required:**

**Action F7.6.1 [SHOULD, Wave 0]** — Update `flask_to_fastapi_migration_v2.md` memory file to reflect the pivot. Change Invariant #1 from "sync def" to "async def end-to-end with async SQLAlchemy". This is user-scoped state, not repo state, but affects future agent sessions.

**Action F7.6.2 [CAN, v2.1]** — After v2.0 ships, archive the flask-to-fastapi memory entry (it will be stale once v2.1 starts).

**Priority:** SHOULD 7.6.1, CAN 7.6.2.

---

### Category 8 — Additional discoveries (beyond the 27 categories)

#### 8.1 nginx proxy configuration

**Current state:**
- `config/nginx/nginx-development.conf:32` — `keepalive_timeout 65;`
- Line 51 — `location /health` proxies to upstream
- Line 68 — catch-all proxies everything else

**Change required:**

**Action F8.1.1 [SHOULD, Wave 4]** — `keepalive_timeout 65;` is fine for async. But under async, the server can hold more concurrent connections, and nginx's default `worker_connections 1024` may become the new bottleneck instead of Python's GIL. Consider bumping `worker_connections` to 4096 if pool sizing permits.

**Action F8.1.2 [SHOULD, Wave 4]** — Add `proxy_read_timeout` specifically for `/health/pool` and `/health/schedulers` — these should NOT be long-timeout'd. If the backend is hung, nginx should give up fast and report the pod unhealthy.

**Action F8.1.3 [CAN, v2.1]** — Consider adding `proxy_request_buffering off` for `/mcp/` endpoints if MCP tool calls become large (async streaming support). Low priority.

**Priority:** SHOULD 8.1.1-8.1.2, CAN 8.1.3.

---

#### 8.2 `pytest.ini_options` in `pyproject.toml`

**Current state:** `pyproject.toml` has NO `[tool.pytest.ini_options]` section. I searched explicitly — only `[tool.black]`, `[tool.ruff]`, `[tool.coverage]`, `[tool.pylint]` sections. pytest-asyncio mode is implicit "strict".

**Change required:**

**Action F8.2.1 [MUST, Wave 0]** — Add a new `[tool.pytest.ini_options]` section (duplicate of F1.7.1 for completeness):
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
    "requires_db: tests that need a real PostgreSQL",
    "requires_server: tests that need a running server",
    "skip_ci: tests to skip in CI (ratcheting)",
    "smoke: smoke tests",
    # ... existing markers
]
```

Currently markers are declared ad-hoc in each test file. Consolidating them here enables pytest's `strict-markers` mode to catch typos.

**Action F8.2.2 [SHOULD, Wave 4]** — Enable `strict-markers` once markers are centralized. Catches misspelled `@pytest.mark.regrading` → fails the test.

**Priority:** MUST 8.2.1, SHOULD 8.2.2.

---

#### 8.3 Test smoke + import validation

**Current state:** `run_all_tests.sh:34-42` imports `_get_products_impl` and `_create_media_buy_impl` at test start. Fast-fail if those imports break.

**Change required:**

**Action F8.3.1 [SHOULD, Wave 4]** — Add additional imports to `validate_imports()`:
```python
from src.core.database.database_session import get_db_session  # verifies async engine creation works
from src.core.database.repositories import MediaBuyRepository  # if these exist
```
Doing this gives Wave 4 a fast smoke test for "does the DB layer import cleanly?" without running any tests.

**Priority:** SHOULD.

---

#### 8.4 Release-please config

**Current state:** `.github/workflows/release-please.yml` + `release-please-config.json` (not read but present). Release-please generates changelogs from conventional commits.

**Change required:**

**Action F8.4.1 [SHOULD, Wave 5]** — Ensure Wave 4 PR is titled with `feat:` prefix (full async pivot is a feature). Wave 5 cleanup is `chore:`. Release-please picks up "feat" into the Features section of CHANGELOG.md, which is what we want for a major version bump.

**Action F8.4.2 [SHOULD, Wave 5]** — Bump `version = "1.7.0"` in `pyproject.toml:3` to `"2.0.0"` at Wave 5 merge. Release-please handles the bump but verify the config.

**Priority:** SHOULD.

---

## Section 2 — Action Item Matrix

| ID | Action | Category | Priority | Dependencies | Effort | Blocker? |
|---|---|---|---|---|---|---|
| F1.1.1 | Pin asyncpg >=0.30.0,<0.32 | Deps | MUST-0 | — | 5m | No |
| F1.1.2 | Verify greenlet in uv.lock post-swap | Deps | MUST-0 | F7.1.1 | 10m | Yes |
| F1.1.3 | Verify asyncpg wheels for target platforms | Deps | MUST-0 | — | 30m | Yes |
| F1.1.4 | Clean duplicate dev deps in pyproject.toml | Deps | MUST-0 | — | 10m | No |
| F1.1.5 | uv lock --upgrade-package asyncpg | Deps | SHOULD-4 | F7.1.1 | 5m | No |
| F1.2.1 | Remove libpq-dev/libpq5 from Dockerfile | Build | SHOULD-4 | — | 15m | No |
| F1.2.2 | Add --no-compile to uv sync in Docker | Build | CAN | — | 5m | No |
| F1.3.1 | Verify mypy passes after types-psycopg2 removal | Build | MUST-0 | F1.1.1 | 15m | Yes |
| F1.4.1 | Rewrite/delete DatabaseConnection class | Code+Deploy | MUST-0 | — | 2h | Yes |
| F1.4.2 | Audit scripts/setup/*.py for sync DB | Deploy | MUST-4 | F1.4.1 | 1h | Yes |
| F1.5.1 | Add DATABASE_URL rewriter (sslmode→ssl) | Code | MUST-0 | — | 1h | Yes |
| F1.5.2 | Keep compose files using sslmode=disable | Docker | MUST-0 | F1.5.1 | 0m | No |
| F1.5.3 | Document asyncpg TLS vocab | Docs | SHOULD-4 | F1.5.1 | 30m | No |
| F1.5.4 | Update test-stack.sh DATABASE_URL | Tests | SHOULD-4 | F1.5.1 | 10m | No |
| F1.6.1 | Decide factory-boy adapter strategy | Tests | MUST-4 | B.Spike4 | 1h | Yes |
| F1.6.2 | Add tests/factories/_async_adapter.py | Tests | SHOULD-0 | F1.6.1 | 3h | Yes |
| F1.6.3 | Update tests/CLAUDE.md factory section | Docs | SHOULD-4 | F1.6.1 | 30m | No |
| F1.7.1 | Add [tool.pytest.ini_options] asyncio_mode=auto | Tests | MUST-0 | — | 10m | Yes |
| F1.7.2 | Pin pytest-asyncio < 2.0 | Tests | MUST-0 | — | 5m | No |
| F1.7.3 | Add anyio>=4.0 as direct dev dep | Tests | SHOULD-4 | — | 5m | No |
| F1.7.4 | pytest-timeout tuning | Tests | CAN | — | 15m | No |
| F2.1.1 | Add DB_POOL_SIZE etc to tox pass_env | CI | MUST-0 | F3.8.1 | 10m | No |
| F2.1.2 | Add tox -e driver-compat env | CI | SHOULD-4 | — | 1h | No |
| F2.1.3 | Add tox -e benchmark env | CI | SHOULD-4 | — | 1h | No |
| F2.1.4 | Update env_list with driver-compat | CI | SHOULD-4 | F2.1.2 | 5m | No |
| F2.1.5 | Verify make quality works with async unit tests | CI | MUST-0 | F1.7.1 | 30m | Yes |
| F2.1.6 | Update coverage env depends list | CI | SHOULD-4 | F2.1.2 | 5m | No |
| F2.2.1 | Verify make quality pytest line | Make | MUST-0 | F1.7.1 | 10m | Yes |
| F2.2.2 | Add make test-driver-compat target | Make | SHOULD-4 | F2.1.2 | 10m | No |
| F2.2.3 | Add make benchmark target | Make | SHOULD-4 | F2.1.3 | 10m | No |
| F2.2.4 | Add make check-lazy-load target | Make | SHOULD-5 | — | 30m | No |
| F2.2.5 | Verify make test-entity works under async | Make | MUST-0 | F1.7.1 | 30m | Yes |
| F2.3.1 | Delete test-migrations pre-commit hook | Precommit | MUST-0 | — | 5m | No |
| F2.3.2 | Plan .duplication-baseline churn strategy | Precommit | MUST-4 | — | 10m | No |
| F2.3.3 | Add check_no_sync_db_in_async hook | Precommit | SHOULD-4 | — | 2h | No |
| F2.3.4 | Add check_no_asyncio_run_in_lib hook | Precommit | SHOULD-4 | — | 1h | No |
| F2.3.5 | Add check_no_module_level_get_engine hook | Precommit | SHOULD-4 | — | 2h | No |
| F2.3.6 | Verify mypy pre-commit deps post-swap | Precommit | MUST-0 | F1.3.1 | 10m | Yes |
| F2.3.7 | N+1 detection hook | Precommit | CAN | — | 4h | No |
| F2.4.1 | Align PG versions to 17 in test.yml | CI | MUST-0 | — | 5m | Yes |
| F2.4.2 | Verify migrate.py under async alembic in CI | CI | MUST-4 | F2.5.1 | 30m | Yes |
| F2.4.3 | Add ?ssl=false to CI DATABASE_URL | CI | SHOULD-4 | F1.5.1 | 10m | No |
| F2.4.4 | Verify quickstart-test works under async | CI | SHOULD-4 | F3.5.1 | 30m | Yes |
| F2.4.5 | Add driver-compat CI job | CI | SHOULD-4 | F2.1.2 | 30m | No |
| F2.4.6 | Add benchmark-compare CI job | CI | SHOULD-5 | F2.1.3 | 1h | No |
| F2.4.7 | Matrix: async group in integration-tests | CI | CAN | — | — | No |
| F2.4.8 | Fail on asyncio DeprecationWarning | CI | CAN | — | 15m | No |
| F2.5.1 | Rewrite alembic/env.py to async | Alembic | MUST-0 | B.Spike6 | 4h | Yes |
| F2.5.2 | Verify compare_type in async env.py | Alembic | SHOULD-4 | F2.5.1 | 30m | No |
| F2.5.3 | Verify migrate.py works under async env.py | Alembic | MUST-4 | F2.5.1 | 15m | Yes |
| F2.5.4 | Test alembic revision autogen under async | Alembic | MUST-0 | F2.5.1 | 30m | Yes |
| F2.5.5 | Verify no async in alembic/versions/*.py | Alembic | SHOULD-4 | — | 5m | No |
| F2.5.6 | Test docker-compose db-init timing | Docker | SHOULD-4 | F2.5.1 | 30m | No |
| F2.5.7 | Add make test-migrations target | Alembic | CAN | — | 1h | No |
| F2.6.1 | Verify run_all_tests.sh validate_imports | CI | MUST-0 | — | 10m | No |
| F2.6.2 | Add driver-compat to collect_reports | CI | SHOULD-4 | F2.1.2 | 5m | No |
| F2.6.3 | Fix "6 suites" message in run_all_tests.sh | CI | SHOULD-4 | — | 5m | No |
| F2.6.4 | Timing instrumentation | CI | CAN | — | 30m | No |
| F2.7.1 | Verify scripts/run-test.sh under asyncpg | Tests | MUST-0 | F1.5.1 | 15m | No |
| F2.7.2 | Auto-set small pool for single-test runs | Tests | SHOULD-4 | — | 15m | No |
| F3.1.1 | Remove libpq from Dockerfile | Docker | MUST-4 | F1.1.1 | 10m | No |
| F3.1.2 | Verify Dockerfile healthcheck | Docker | SHOULD-4 | — | 10m | No |
| F3.1.3 | Add /health/db endpoint variant | Docker | SHOULD-4 | F4.1.1 | 1h | No |
| F3.1.4 | ENTRYPOINT unchanged verification | Docker | CAN | — | 5m | No |
| F3.2.1 | docker-compose.yml sslmode handling | Docker | SHOULD-4 | F1.5.1 | 5m | No |
| F3.2.2 | Test db-init under async alembic | Docker | SHOULD-4 | F2.5.1 | 15m | No |
| F3.2.3 | Healthcheck verification | Docker | SHOULD-4 | — | 5m | No |
| F3.2.4 | Add DB_POOL_* env vars | Docker | MUST-0 | F3.8.1 | 10m | No |
| F3.3.1 | docker-compose.e2e.yml sslmode | Docker | SHOULD-4 | F1.5.1 | 5m | No |
| F3.3.2 | Full async E2E path verification | Docker | MUST-4 | B.Spike1 | 1h | Yes |
| F3.4.1 | docker-compose.multi-tenant.yml sslmode | Docker | SHOULD-4 | F1.5.1 | 5m | No |
| F3.5.1 | Rewrite check_database_health() in run_all_services.py | Deploy | MUST-4 | F1.4.1 | 1h | Yes |
| F3.5.2 | Rewrite check_schema_issues() | Deploy | MUST-4 | F1.4.1 | 1h | Yes |
| F3.5.3 | Rewrite/audit init_database() | Deploy | MUST-4 | F1.4.1 | 1h | Yes |
| F3.5.4 | Audit run_all_services.py module-level imports | Deploy | MUST-0 | — | 15m | No |
| F3.5.5 | Update run_all_services.py docstring | Deploy | SHOULD-4 | — | 10m | No |
| F3.6.1 | Rewrite/delete entrypoint_admin.sh psycopg2 probe | Deploy | MUST-4 | F1.4.1 | 15m | Yes |
| F3.7.1 | Update docs/deployment/walkthroughs/fly.md | Docs | SHOULD-4 | — | 30m | No |
| F3.7.2 | fly.toml.example | Docs | CAN | — | 1h | No |
| F3.8.1 | Document DB_POOL_SIZE etc env vars | Docs | MUST-0 | — | 30m | Yes |
| F3.8.2 | Update .env.template | Docs | SHOULD-4 | F3.8.1 | 15m | No |
| F3.8.3 | Document USE_PGBOUNCER implications | Docs | MUST-0 | — | 15m | No |
| F4.1.1 | Add /health/pool endpoint | Obs | SHOULD-4 | F3.5.1 | 1h | No |
| F4.1.2 | Add /health/schedulers endpoint | Obs | SHOULD-4 | B.Risk7 | 2h | No |
| F4.1.3 | Expose /metrics for prometheus | Obs | SHOULD-4 | — | 30m | No |
| F4.1.4 | Add DB pool metrics gauges | Obs | SHOULD-4 | F4.1.1 | 1h | No |
| F4.1.5 | Add asyncio task count gauge | Obs | SHOULD-4 | — | 30m | No |
| F4.1.6 | Add event loop lag gauge | Obs | SHOULD-4 | — | 2h | No |
| F4.1.7 | /debug/pool-state endpoint | Obs | CAN | — | 30m | No |
| F4.3.1 | Switch request-ID to contextvars | Obs | SHOULD-4 | — | 2h | No |
| F4.3.2 | Enable asyncio.set_debug() in dev | Obs | SHOULD-4 | — | 30m | No |
| F4.3.3 | Log asyncio task ID | Obs | SHOULD-4 | F4.3.1 | 1h | No |
| F4.3.4 | structlog evaluation | Obs | CAN | — | — | No |
| F4.4.1 | docs/development/async-debugging.md tools | Docs | SHOULD-4 | — | 2h | No |
| F4.4.2 | MissingGreenlet debugging section | Docs | SHOULD-4 | F4.4.1 | 1h | No |
| F5.1.1 | Update CLAUDE.md Pattern #3 async example | Docs | MUST-4 | — | 30m | No |
| F5.1.2 | Update CLAUDE.md Pattern #5 async note | Docs | MUST-4 | — | 15m | No |
| F5.1.3 | Update CLAUDE.md guards table | Docs | MUST-4 | F6.2.* | 30m | No |
| F5.1.4 | Update JSON Fields example | Docs | MUST-4 | — | 10m | No |
| F5.1.5 | Add Async Gotchas section | Docs | MUST-4 | — | 1h | No |
| F5.1.6 | Update testing workflow section | Docs | SHOULD-4 | — | 15m | No |
| F5.1.7 | Update "What to Avoid" list | Docs | SHOULD-4 | — | 15m | No |
| F5.2.1 | Update patterns-reference.md | Docs | MUST-4 | — | 30m | No |
| F5.2.2 | Update architecture.md | Docs | MUST-4 | — | 30m | No |
| F5.2.3 | Update structural-guards.md | Docs | MUST-4 | F6.2.* | 30m | No |
| F5.2.4 | Update troubleshooting.md async section | Docs | MUST-4 | — | 1h | No |
| F5.2.5 | Update environment-variables.md | Docs | MUST-4 | F3.8.1 | 30m | No |
| F5.2.6 | Update deployment/*.md | Docs | SHOULD-4 | — | 30m | No |
| F5.2.7 | Update adapters docs if needed | Docs | SHOULD-4 | — | 15m | No |
| F5.2.8 | Update contributing.md | Docs | SHOULD-4 | — | 15m | No |
| F5.2.9 | Review V2_ROADMAP_SUGGESTION.md | Docs | CAN | — | 15m | No |
| F5.3.1 | Create async-debugging.md | Docs | MUST-0 | — | 3h | No |
| F5.3.2 | Create async-cookbook.md | Docs | MUST-0 | B.Section3 | 2h | No |
| F5.3.3 | Create lazy-loading-guide.md | Docs | SHOULD-4 | B.Risk1 | 2h | No |
| F5.3.4 | Create asyncpg-migration.md | Docs | SHOULD-4 | — | 2h | No |
| F5.3.5 | Create async-performance.md | Docs | CAN | — | 3h | No |
| F6.1.1 | Audit existing 22 arch guards | Guards | MUST-4 | — | 2h | Yes |
| F6.1.2 | Update test_architecture_boundary_completeness.py | Guards | MUST-4 | F6.1.1 | 30m | No |
| F6.1.3 | Update test_architecture_no_raw_media_package_select.py | Guards | MUST-4 | F6.1.1 | 15m | No |
| F6.1.4 | Verify no_model_dump_in_impl unchanged | Guards | SHOULD-4 | F6.1.1 | 15m | No |
| F6.2.1 | Create no_default_lazy_relationship guard | Guards | MUST-0 | — | 4h | Yes |
| F6.2.2 | Create admin_routes_async guard | Guards | MUST-4 | — | 3h | No |
| F6.2.3 | Create admin_async_db_access guard | Guards | MUST-4 | — | 3h | No |
| F6.2.4 | Create templates_no_orm_objects guard | Guards | MUST-4 | — | 6h | No |
| F6.2.5 | Create no_server_default_without_refresh guard | Guards | MUST-0 | — | 4h | Yes |
| F6.2.6 | Create no_module_level_get_engine guard | Guards | MUST-0 | — | 3h | Yes |
| F6.2.7 | Create no_asyncio_run_in_lib guard | Guards | SHOULD-4 | — | 2h | No |
| F6.2.8 | Create no_run_in_threadpool_for_db guard | Guards | SHOULD-4 | — | 2h | No |
| F6.2.9 | Verify schema_inheritance guard | Guards | SHOULD-4 | F6.1.1 | 15m | No |
| F6.2.10 | Plan repository_pattern allowlist shrink | Guards | SHOULD-4 | — | 30m | No |
| F6.2.11 | Create no_sync_orm_in_async_def | Guards | CAN | — | 2h | No |
| F7.1.1 | uv lock post driver swap | Deps | MUST-4 | F1.1.1 | 30m | Yes |
| F7.1.2 | Cross-platform uv sync test | Deps | SHOULD-4 | F7.1.1 | 1h | No |
| F7.1.3 | uv version pin verification | Deps | CAN | — | 5m | No |
| F7.2.1 | Verify agent-db.sh works | Tests | SHOULD-4 | F1.5.1 | 10m | No |
| F7.2.2 | Verify agent-db.sh PG version | Tests | SHOULD-4 | — | 5m | No |
| F7.2.3 | Export small pool in agent-db.sh | Tests | CAN | — | 5m | No |
| F7.3.1 | Audit 5 scripts for module-level engine creation | Deploy | MUST-4 | F3.5.4 | 1h | Yes |
| F7.3.2 | Wrap CLI scripts in asyncio.run | Deploy | MUST-4 | F7.3.1 | 3h | Yes |
| F7.3.3 | gen-agent-index.py unchanged | Deploy | SHOULD-4 | — | 0m | No |
| F7.3.4 | run_server.py unchanged verification | Deploy | SHOULD-4 | — | 0m | No |
| F7.3.5 | scripts/compare_benchmarks.py new | Deploy | CAN | — | 2h | No |
| F7.4.1 | .duplication-baseline pre-conversion snapshot | CI | MUST-4 | — | 10m | No |
| F7.4.2 | .duplication-baseline Wave 5 ratchet | CI | MUST-5 | — | 30m | No |
| F7.4.3 | Document baseline churn in PR | Docs | SHOULD-4 | — | 15m | No |
| F7.5.1 | Audit FIXME comments for async landmines | Cleanup | SHOULD-4 | — | 1h | No |
| F7.5.2 | Close async FIXMEs after Wave 5 | Cleanup | SHOULD-5 | — | 30m | No |
| F7.6.1 | Update auto-memory file | Meta | SHOULD-0 | — | 15m | No |
| F7.6.2 | Archive flask-to-fastapi memory entry | Meta | CAN | — | 5m | No |
| F8.1.1 | Bump nginx worker_connections | Infra | SHOULD-4 | — | 15m | No |
| F8.1.2 | proxy_read_timeout for /health/* | Infra | SHOULD-4 | F4.1.1 | 15m | No |
| F8.1.3 | proxy_request_buffering for /mcp/ | Infra | CAN | — | 15m | No |
| F8.2.1 | Add [tool.pytest.ini_options] with markers | Tests | MUST-0 | — | 30m | Yes |
| F8.2.2 | Enable strict-markers | Tests | SHOULD-4 | F8.2.1 | 15m | No |
| F8.3.1 | Extend validate_imports in run_all_tests.sh | CI | SHOULD-4 | — | 10m | No |
| F8.4.1 | Wave 4 PR title: feat: | Release | SHOULD-5 | — | 0m | No |
| F8.4.2 | Bump version to 2.0.0 | Release | SHOULD-5 | — | 5m | No |

**Total by priority:**
- **MUST-0 (pre-Wave-0 hard gate):** 18 items
- **MUST-4 (Wave 4 core work):** 15 items
- **MUST-5 (Wave 5 release):** 1 item
- **SHOULD-4:** 44 items
- **SHOULD-5:** 3 items
- **CAN:** 24 items
- **Total:** 105 items

**Effort estimate (MUST only):** ~30 engineer-hours = ~4 days of focused work, parallelizable across 2-3 people. Adds ~1.5 days to Agent B's existing 8-day spike budget for a total pre-Wave-0 gate of ~5-6 days, confirming Agent B's recommendation holds.

---

## Section 3 — Dependency Graph (DAG)

Key dependencies between action items, in markdown bullet "X blocks Y" form:

**Pre-Wave-0 hard gate chain:**
- F1.1.1 (pin asyncpg) blocks F1.1.2 (verify greenlet)
- F1.1.1 blocks F1.1.3 (verify wheels)
- F1.1.1 blocks F7.1.1 (uv lock)
- F1.1.2 + F1.1.3 + F1.1.4 block F7.1.1
- F7.1.1 blocks F1.1.5
- F1.7.1 (asyncio_mode=auto) blocks F2.1.5 (make quality verification)
- F1.7.1 blocks F2.2.1 (make quality pytest line)
- F1.7.1 blocks F2.2.5 (make test-entity)
- F1.3.1 (mypy verify) blocks F2.3.6 (pre-commit mypy deps)
- F1.5.1 (URL rewriter) blocks F1.5.2, F1.5.3, F1.5.4
- F1.5.1 blocks F2.4.3 (?ssl=false in CI), F2.7.1 (scripts/run-test.sh), F7.2.1 (agent-db.sh)
- F1.5.1 blocks F3.2.1, F3.3.1, F3.4.1 (docker-compose URL handling)
- F1.4.1 (DatabaseConnection rewrite) blocks F1.4.2, F3.5.1, F3.5.2, F3.5.3, F3.6.1
- F3.5.4 (audit module-level imports in run_all_services) blocks F7.3.1 (audit scripts)
- F7.3.1 blocks F7.3.2 (wrap in asyncio.run)

**Wave 0 spike pre-requisites (Agent B's spikes BLOCK this agent's action items):**
- B.Spike1 (lazy-load audit) blocks F3.3.2 (E2E async verification)
- B.Spike1 blocks F5.3.3 (lazy-loading-guide.md)
- B.Spike2 (driver compat) blocks F2.1.2 (tox driver-compat env)
- B.Spike4 (test infrastructure) blocks F1.6.1 (factory adapter decision)
- B.Spike6 (alembic) blocks F2.5.1 (alembic/env.py rewrite)
- B.Section3 (cookbook drafted in Agent B report) blocks F5.3.2 (create async-cookbook.md)

**Alembic chain:**
- F2.5.1 (async env.py) blocks F2.5.2 (compare_type verify)
- F2.5.1 blocks F2.5.3 (migrate.py verify)
- F2.5.1 blocks F2.5.4 (autogen verify)
- F2.5.1 blocks F2.5.6 (db-init timing verify)
- F2.5.1 blocks F3.2.2, F3.3.2 (compose startup verify)
- F2.5.1 blocks F2.4.2 (CI migration job verify)

**Observability chain:**
- F4.1.1 (/health/pool) blocks F4.1.4 (pool metrics)
- F4.1.1 blocks F3.1.3 (Dockerfile healthcheck /health/db)
- F4.3.1 (contextvars request-ID) blocks F4.3.3 (log task ID)
- B.Risk7 (scheduler alive-tick) blocks F4.1.2 (/health/schedulers)

**Guards chain:**
- F6.1.1 (audit 22 existing guards) blocks F6.1.2, F6.1.3, F6.1.4, F6.2.9, F6.2.10
- F6.2.* (all new guards) feed F5.1.3 (CLAUDE.md guards table) and F5.2.3 (structural-guards.md)

**CI chain:**
- F2.1.2 (driver-compat env) blocks F2.1.4 (env_list), F2.1.6 (coverage depends), F2.2.2 (make target), F2.4.5 (CI job), F2.6.2 (collect_reports)
- F2.1.3 (benchmark env) blocks F2.2.3 (make target), F2.4.6 (CI job)
- F3.5.1 (run_all_services.py rewrite) blocks F2.4.4 (quickstart-test verify)

**Docs chain (all downstream of core implementation):**
- F1.5.1 blocks F1.5.3 (asyncpg TLS docs)
- F1.6.1 blocks F1.6.3 (tests/CLAUDE.md factory section)
- F6.2.* block F5.1.3, F5.2.3 (guards tables in CLAUDE/docs)
- F3.8.1 blocks F3.8.2 (.env.template), F5.2.5 (environment-variables.md)

**Baseline churn chain:**
- F7.4.1 (Wave 4 snapshot) blocks Wave 4 main work (do first, then work can noisily churn)
- F7.4.2 (Wave 5 ratchet) is at Wave 5 end, after all repository refactors land

**Cross-agent dependencies (Agent C edit application):**
- Agent C's 45 plan-file edits must land BEFORE this agent's docs actions (F5.*) so we're not editing stale content. So: Agent C apply → F5.* updates.
- Agent A's scope audit must land before F6.2.2 and F6.2.3 (admin handler guards need the final file list)

---

## Section 4 — Unaffected Surfaces

For peace of mind, the following surfaces were examined and found to NOT require changes under the async pivot:

1. **BDD test infrastructure (`tests/bdd/`)** — pytest-bdd is async-agnostic at the framework level. Step definitions can be sync or async; the harness transport dispatching in `tests/harness/transport.py` already supports both. No BDD-specific changes.

2. **Prometheus metric NAMES** — metric naming (`ai_review_total`, `webhook_delivery_total`, etc.) is stable. Only ADDED metrics are new (pool size, event loop lag). Existing metrics work unchanged.

3. **nginx proxy mechanics** — the proxy layer is protocol-agnostic. Upstream HTTP semantics don't change with async Python. Only tuning (worker_connections) is advisory.

4. **`release-please-config.json`** — changelog generation is commit-message-based, unrelated to code architecture.

5. **`.github/workflows/ipr-agreement.yml`** — PR agreement check, no code interaction.

6. **`.github/workflows/pr-title-check.yml`** — conventional commits check, unrelated.

7. **`config/nginx/*.conf`** — all three nginx configs (dev, single-tenant, multi-tenant) are protocol-level proxies. Minor tuning optional (F8.1.*) but not required.

8. **GAM adapter** (`src/adapters/gam/**`) — adapter code may still block on Google Ads API calls; this is expected and unrelated to our DB async. Keep `run_in_threadpool` for GAM calls, remove only for DB calls.

9. **`src/a2a_server/adcp_a2a_server.py`** — A2A SDK is already async (built on Starlette). Handlers that currently don't touch DB need no change; handlers that do touch DB get async DB treatment as part of Wave 4 core work (not this report's scope).

10. **`mypy.ini`** — no psycopg2-specific config; async SQLAlchemy already supported by the 2.0 mypy plugin. No config changes.

11. **`scripts/test-stack.sh`** PostgreSQL version (`postgres:17-alpine`) — already aligned with docker-compose. No change.

12. **`scripts/ops/auto_merge_migrations.sh`** — shell script that doesn't touch Python DB APIs. Unchanged.

13. **`scripts/ops/check_migration_heads.py`** — used by structural guard; scans file system, not DB. Unchanged.

14. **`scripts/ops/manage_auth.py`** — not verified in this pass but inferred to be async-tolerant or wrappable.

15. **Encryption + secrets management** (`cryptography`, `pyjwt`) — all sync crypto libraries; not affected by async.

16. **Freezegun** — time-mocking library, sync; unchanged.

17. **Pytest timeout configuration** — already at `--timeout=60` in CI. Async timeout honors the same setting.

18. **Coverage configuration** (`[tool.coverage.run]`) — `branch = true`, `source = ["src"]`. Works identically with async code; no changes.

19. **Docker BuildKit cache directives** — `--mount=type=cache` lines in Dockerfile. Unchanged.

20. **Supercronic cron runner** (`Dockerfile:73-75`) — background cron jobs run as subprocesses. Unchanged.

21. **`run_server.py`** uvicorn entrypoint — sync `uvicorn.run()` call. Unchanged.

22. **`initialize_application()` in `src/core/startup.py`** — does not touch DB at all. Unchanged.

23. **Pydantic schemas** (`src/core/schemas.py`) — Pydantic v2 is sync; no async impact. CLAUDE.md Pattern #4 (nested serialization) unchanged.

24. **`JSONType.process_bind_param`** — write path is sync (Pydantic → dict → JSONB). Unchanged. Only the READ path (`process_result_value`) needs driver-agnostic hardening (Risk #17).

25. **Test results JSON format** (`test-results/<ddmmyy_HHmm>/`) — format is pytest-json-report output, stable. New envs (driver-compat, benchmark) add files but format is unchanged.

26. **Pre-commit upstream hooks** (trailing-whitespace, end-of-file-fixer, check-yaml, etc., `.pre-commit-config.yaml:262-275`) — all unrelated.

27. **`.ast-grep/rules/`** for BDD guards — AST-grep rules are language-level, not DB-related. Unchanged.

28. **ADcp schema contract tests** (`tests/unit/test_adcp_contract.py`) — validates schema against external spec. Unchanged.

29. **Fly.io secrets management** (`scripts/deploy/fly-set-secrets.sh`) — no DB interaction. Unchanged.

30. **Semgrep rules** (if any) — static analysis, unchanged.

31. **Creative agent Docker integration** (`test.yml:180-223`) — starts a separate adcp-creative-agent service via Docker. Unchanged (different process, different DB).

32. **`dockerfile` for the creative agent** (`adcp-creative-agent` image built from upstream `adcontextprotocol/adcp`) — upstream concern, not ours.

33. **Encryption key generation** in `scripts/generate_encryption_key.py` — pure utility, unchanged.

34. **Audit log write path** (if any) — not verified but audit logs are typically append-only; under async they get an async write path but the format is unchanged.

**Net:** the non-code surface that is TRULY unaffected is substantial — most of the infrastructure (nginx, cron, proxy, crypto, freezegun, coverage, test runner format, release automation) has zero changes. What changes is concentrated in: deps, Docker build layer, alembic env.py, health endpoints, docs. The changes are many but focused.

---

## Section 5 — Integrations with Other Agents

**Agent A (async scope audit):**
- A produces file-by-file `_impl` / repository / UoW conversion list. I defer all code-level mechanical conversions to A.
- I produce the tooling/CI/Docker/docs non-code surface. A's list is the input to my F2.1-F2.6, F3.1-F3.6, F5.1-F5.3 action items (they're what A's conversion MUST be wrapped in).
- Gap: I do not enumerate individual `_impl` functions that need conversion. Defer to A.

**Agent B (risk matrix):**
- B provides 33 risks + 8 spikes + 9 cookbook patterns. I cite B frequently in my findings.
- Specific integrations:
  - B.Risk #1 (lazy load) → my F5.3.3 (lazy-loading-guide), F6.2.1 (no_default_lazy guard)
  - B.Risk #3 (pytest-asyncio) → my F1.7.1-F1.7.4, F8.2.1, F2.1.5
  - B.Risk #4 (alembic) → my F2.5.*, F2.4.2
  - B.Risk #5 (expire_on_commit) → my F6.2.5
  - B.Risk #6 (pool tuning) → my F3.8.1, F4.1.1, F4.1.4
  - B.Risk #7 (scheduler) → my F4.1.2
  - B.Risk #11 (debugging) → my F5.3.1, F4.4.*
  - B.Risk #17 (JSONType codec) → my F2.4.1 (PG version alignment ensures Spike 2 catches this)
  - B.Risk #18 (statement_timeout event) → my F3.8.3 (PgBouncer docs)
  - B.Risk #26 (lifespan deadlock) → my F3.7.1 (fly.io shutdown timeout docs)
  - B.Risk #32 (asyncio.get_event_loop deprecation) → my F2.4.8
  - B.Risk #33 (module-level engine) → my F6.2.6, F3.5.4, F7.3.1-F7.3.2
- Gap: B handles risk mitigation at the library layer; I handle the surrounding CI/docs/tooling. Non-overlap.

**Agent C (plan-file edits):**
- C produces 45 surgical edits to the 8 plan files. My docs actions (F5.1.*, F5.2.*) must land AFTER C's edits so we're not editing stale content.
- Specific integrations:
  - C.Edit 2.3 (rename `test_architecture_admin_sync_db_no_async.py` → `test_architecture_admin_routes_async.py`) is my F6.2.2 at the implementation level
  - C.Edit 6.7 (rename in execution details) is my F6.2.2 at docs level
  - C.Edit 7.9 (add asyncpg to infra prereqs) aligns with my F1.1.1-F1.1.4
- Gap: C edits PLAN files; I edit PROJECT docs (CLAUDE.md, docs/). Non-overlap.

**Agent D (AdCP verification):**
- D confirms the pivot is AdCP-safe (wire format, webhooks, OpenAPI).
- My actions do not touch AdCP surface. Confirmed in Section 4 items.
- Gap: none.

**Agent E (FastAPI idiom gap analysis):**
- E covers Python code patterns (async def bodies, dependency injection, lifespan composition).
- My F5.1.1-F5.1.5 touches CLAUDE.md which documents patterns; E may produce different pattern prescriptions. Defer pattern examples to E.
- My F4.3.1 (contextvars) intersects with E's territory. Mark as SHOULD-4 and defer to E for the canonical pattern.
- Gap: I stop at "document that contextvars are used" and let E specify how.

---

## Section 6 — Plan-file edit proposals (layered on Agent C's 45 edits)

These edits are additional to Agent C's. Apply after C's batch.

### Edit F-6.1 — `implementation-checklist.md` — add pre-Wave-0 MUST gate items

**Target file:** `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi/implementation-checklist.md`
**Section:** §1.1 prerequisites (near where Agent C's Edit 7.8 adds lazy-loading audit gate)

**Before (after C's 7.8 lands):**
```markdown
- [ ] Pre-Wave-0 lazy-loading audit spike completed (Agent B Spike 1)
```

**After (add this block underneath):**
```markdown
- [ ] Pre-Wave-0 lazy-loading audit spike completed (Agent B Spike 1)
- [ ] Pre-Wave-0 driver-compat spike completed (Agent B Spike 2)
- [ ] Agent F pre-Wave-0 hard gate items completed:
  - [ ] `psycopg2-binary` → `asyncpg>=0.30.0,<0.32` swap verified on all platforms (F1.1.1-F1.1.4)
  - [ ] `[tool.pytest.ini_options]` added to `pyproject.toml` with `asyncio_mode = "auto"` (F1.7.1, F8.2.1)
  - [ ] `DATABASE_URL` rewriter (`sslmode` → `ssl`) landed (F1.5.1)
  - [ ] `DatabaseConnection` class rewrite / delete plan agreed (F1.4.1)
  - [ ] Alembic `env.py` async rewrite validated via Spike 6 (F2.5.1)
  - [ ] CI Postgres version aligned to 17 across all workflows (F2.4.1)
  - [ ] Dead `test-migrations` pre-commit hook removed (F2.3.1)
  - [ ] 3 new structural guards added (F6.2.1, F6.2.5, F6.2.6)
  - [ ] New docs `async-debugging.md` + `async-cookbook.md` drafted (F5.3.1, F5.3.2)
  - [ ] Full `asyncpg` wheel availability verified for glibc + macOS (F1.1.3)
  - [ ] Duplication baseline snapshotted at Wave 4 start (F7.4.1)
```

**Rationale:** These 18 MUST-0 items are what this agent's inventory surfaces. They need to be in the checklist to be tracked.

**Priority:** MUST

---

### Edit F-6.2 — `implementation-checklist.md` — add Wave 4 tooling section

**Target file:** same
**Section:** §4 Wave 4 section (which Agent C adds via Edit 1.18)

**Add new subsection at the end of Wave 4:**
```markdown
### Wave 4 — Tooling gate (Agent F findings)

In addition to the code conversion work:

- [ ] `check_database_health()` in `scripts/deploy/run_all_services.py` rewritten or deleted (F3.5.1)
- [ ] `check_schema_issues()` in `scripts/deploy/run_all_services.py` rewritten (F3.5.2)
- [ ] `init_database()` in `scripts/deploy/run_all_services.py` audited for async safety (F3.5.3)
- [ ] `scripts/deploy/entrypoint_admin.sh` psycopg2 probe rewritten or script deleted (F3.6.1)
- [ ] `Dockerfile` `libpq-dev` / `libpq5` removal (F1.2.1, F3.1.1)
- [ ] `docker-compose*.yml` DATABASE_URL compatibility verified (F3.2.1, F3.3.1, F3.4.1)
- [ ] New structural guards for admin routes async (F6.2.2), async DB access (F6.2.3), templates no ORM (F6.2.4) landed
- [ ] `tox -e driver-compat` env added and runs in CI (F2.1.2, F2.4.5)
- [ ] `/health/pool` + `/metrics` endpoints added (F4.1.1, F4.1.3)
- [ ] DB pool Prometheus gauges added (F4.1.4)
- [ ] `contextvars` request-ID propagation landed (F4.3.1)
- [ ] CLAUDE.md + `/docs` async updates complete (F5.1.*, F5.2.*)
- [ ] All 5 scripts with top-level `database_session` imports audited for Risk #33 (F7.3.1)
```

**Priority:** MUST

---

### Edit F-6.3 — `implementation-checklist.md` — add Wave 5 polish section

**Add Wave 5 tooling checklist:**

```markdown
### Wave 5 — Tooling polish (Agent F findings)

- [ ] `.duplication-baseline` ratcheted back to ≤ Wave 4 start level (F7.4.2)
- [ ] Benchmark comparison CI job passing (F2.4.6, F8.4.1)
- [ ] `pyproject.toml` version bumped to 2.0.0 (F8.4.2)
- [ ] FIXME comments for async landmines closed (F7.5.2)
- [ ] Auto-memory `flask_to_fastapi_migration_v2.md` updated to reflect pivot (F7.6.1)
- [ ] Release notes include: driver swap, new env vars, new endpoints, new guards
```

**Priority:** SHOULD

---

### Edit F-6.4 — `flask-to-fastapi-migration.md` — add Non-Code Surface Impact section

**Target file:** `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi/flask-to-fastapi-migration.md`
**Section:** after §18 (which C's Edit 1.16 replaces with the absorbed-async content)

**Add new section §19:**

```markdown
## 19. Non-Code Surface Impact (Agent F inventory)

The async pivot touches 27+ categories of non-code surface. Highlights:

### Dep graph (9 action items, ~2h effort)
- `psycopg2-binary` → `asyncpg>=0.30.0,<0.32` swap
- `types-psycopg2` removal (mypy)
- `greenlet` explicitly verified in uv.lock
- `pytest-asyncio>=1.1.0,<2.0` pinned
- `factory-boy` needs async adapter (custom wrapper at `tests/factories/_async_adapter.py`)

### Build + CI (15 action items, ~6h effort)
- `[tool.pytest.ini_options] asyncio_mode = "auto"` MUST be added pre-Wave-0
- Postgres version aligned to 17 across all CI/compose/agent-db
- New `tox -e driver-compat` env
- New `tox -e benchmark` env
- Dead `test-migrations` pre-commit hook removed
- 5 new pre-commit hooks for async patterns

### Docker + deployment (12 action items, ~4h effort)
- `Dockerfile` libpq removal (save ~8MB)
- `scripts/deploy/run_all_services.py` has THREE sync-psycopg2 paths that break
- `scripts/deploy/entrypoint_admin.sh:9` has a direct `psycopg2.connect()` probe
- `DatabaseConnection` class at `src/core/database/db_config.py:105-172` is a separate sync-DB path

### Observability (10 action items, ~8h effort)
- `/health/pool`, `/health/schedulers`, `/metrics` endpoints (new)
- DB pool Prometheus gauges (new)
- asyncio task gauge, event loop lag histogram (new)
- `contextvars`-based request-ID propagation (replaces thread-local)

### Docs (20+ action items, ~15h effort)
- CLAUDE.md Pattern #3, #5, #8 async updates
- `docs/development/patterns-reference.md` repository example
- `docs/development/troubleshooting.md` async error section (new)
- `docs/deployment/environment-variables.md` DB_POOL_SIZE etc
- NEW: `docs/development/async-debugging.md`
- NEW: `docs/development/async-cookbook.md`
- NEW: `docs/development/lazy-loading-guide.md`
- NEW: `docs/development/asyncpg-migration.md`

### Structural guards (10 action items, ~25h effort)
- **3 new MUST-0 guards:** no-default-lazy-relationship, no-server-default-without-refresh, no-module-level-get-engine
- **3 new MUST-4 guards:** admin-routes-async, admin-async-db-access, templates-no-orm-objects
- Update 22 existing guards' allowlists as Wave 4 progresses

**Total non-code effort:** ~60 engineer-hours = ~8 person-days spread across Waves 0, 4, 5. See `async-audit/agent-f-nonsurface-inventory.md` for the per-item breakdown.
```

**Priority:** SHOULD

---

### Edit F-6.5 — `flask-to-fastapi-deep-audit.md` — add section on deployment entrypoint findings

**Target file:** `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi/flask-to-fastapi-deep-audit.md`
**Section:** near end of §2 (risk register) or in §3 (blockers) — add as a new sub-blocker or risk

**Add new block:**

```markdown
### Deployment entrypoint has THREE sync-psycopg2 paths (Agent F finding)

Deep audit did not catch `scripts/deploy/run_all_services.py` as a sync-DB path because it uses the `DatabaseConnection` class from `src/core/database/db_config.py` instead of `get_db_session()`. Under the async pivot:

1. **`scripts/deploy/entrypoint_admin.sh:9`** does `python -c "import psycopg2; psycopg2.connect('${DATABASE_URL}')"`. This is a shell probe that runs before any Python app starts. When `psycopg2` is removed from `pyproject.toml`, this line fails and the container refuses to start. **Hard blocker for Wave 4 merge.**
2. **`scripts/deploy/run_all_services.py:65-125`** (`check_database_health()`) calls `get_db_connection()` → `psycopg2.connect(...)` for a startup health check. Same issue.
3. **`scripts/deploy/run_all_services.py:128-164`** (`check_schema_issues()`) calls `get_db_connection()` → `psycopg2.connect(...)` for a schema audit. Same issue.
4. **`src/core/database/db_config.py:105-172`** `DatabaseConnection` class is a sync-psycopg2 wrapper independent from SQLAlchemy. Used only by the three call sites above.

**Mitigation plan (Option D, recommended):**
- Delete `DatabaseConnection` class and `get_db_connection()` helper
- Replace with a 5-line `asyncio.run(asyncpg.connect(...).fetchval(...))` utility in `scripts/deploy/run_all_services.py`
- Delete `entrypoint_admin.sh` in Wave 3 cleanup (it references `flask_caching` which is also going away, and is not wired up in the current `Dockerfile` entrypoint — orphan)

**Why this matters:** deep audit Blocker #4 focused on SQLAlchemy scoped_session. This is a parallel sync-DB path that deep audit did not surface because it's in deployment scripts, not application code.
```

**Priority:** MUST

---

### Edit F-6.6 — `flask-to-fastapi-adcp-safety.md` — confirm non-code scope is AdCP-safe

**Target file:** `/Users/quantum/Documents/ComputedChaos/salesagent/.claude/notes/flask-to-fastapi/flask-to-fastapi-adcp-safety.md`
**Section:** §9 "What the audit did NOT cover" or similar

**Add confirmation block:**

```markdown
### Non-code surface AdCP impact (Agent F confirmation)

All 105 action items in Agent F's non-code surface inventory have been verified AdCP-safe:

| Surface | AdCP impact |
|---|---|
| Dep swap (psycopg2 → asyncpg) | NONE — wire format unchanged |
| Pre-commit hooks + structural guards | NONE — dev-only |
| CI workflows + tox envs | NONE — testing only |
| Docker Dockerfile / compose | NONE — runtime env unchanged |
| Deployment entrypoints | NONE — health check paths unchanged |
| New `/health/pool` + `/health/schedulers` endpoints | ADDITIVE — new paths, no existing path changes |
| `/metrics` endpoint (new) | ADDITIVE — new path, no existing path changes |
| DB pool Prometheus metrics | NONE — operational telemetry only |
| `contextvars` request-ID | NONE — internal propagation, log field only |
| `DATABASE_URL` rewriter | INTERNAL — rewrites at engine construction, env var unchanged |
| CLAUDE.md / docs updates | NONE |
| Alembic env.py async rewrite | NONE — wire format stable under sync or async migration |
| New env vars (`DB_POOL_SIZE`, `DB_POOL_MAX_OVERFLOW`, etc.) | ADDITIVE — defaults preserve current behavior |
| Nginx tuning (worker_connections, proxy timeouts) | NONE — proxy behavior identical |

**Verdict:** no non-code surface change touches AdCP protocol wire format. Side-effect of `/metrics` and `/health/pool` being additive endpoints means OpenAPI spec gains entries but does not remove or alter existing ones. AdCP contract preserved.
```

**Priority:** MUST

---

## Section 7 — Summary

**Report stats:**
- Total action items: 105
- MUST (pre-Wave-0 hard gate): 18
- MUST (Wave 4): 15
- MUST (Wave 5): 1
- SHOULD: 47
- CAN: 24

**Top 5 most-critical findings (in priority order):**

1. **F1.4.1 + F3.5.1 + F3.6.1** — THREE sync-psycopg2 paths in deployment (`entrypoint_admin.sh`, `run_all_services.py` × 2, `DatabaseConnection` class). Not caught by any other agent. Hard blocker for Wave 4 merge.
2. **F2.4.1** — PG version skew (`postgres:15` in CI, `postgres:17-alpine` locally). Must align before Spike 2.
3. **F1.5.1** — `sslmode` → `ssl` URL rewriter is required because asyncpg has different TLS query param vocabulary.
4. **F1.7.1** — `asyncio_mode = "auto"` in pytest config is mandatory for clean async test conversion.
5. **F6.2.1** — `no_default_lazy_relationship` structural guard must land pre-Wave-0 to prevent regression during conversion.

**Non-blockers but important:**
- Dead `test-migrations` pre-commit hook (F2.3.1) — cleanup opportunity
- `entrypoint_admin.sh` is orphaned and should be deleted (F3.6.2) rather than patched
- `Dockerfile` libpq removal saves ~8MB (F1.2.1, F3.1.1)
- 5 new pre-commit hooks needed to catch async antipatterns (F2.3.3-F2.3.5, F6.2.7-F6.2.8)
- 3 new docs files needed for engineer onboarding (F5.3.1-F5.3.3)

**Integration with other agents:**
- Agent A handles code-level conversion lists; this report handles everything around them
- Agent B's 8 spikes are the pre-requisite for many of this report's Wave 0 items
- Agent C's 45 plan-file edits must land before this report's docs updates (F5.*)
- Agent D has confirmed AdCP surface is untouched; this report confirms non-code surface is also AdCP-safe (Section 6.6 edit)
- Agent E's FastAPI idiom prescriptions supersede this report's tentative pattern notes (F4.3.1, CLAUDE.md examples)

**End of report.**
