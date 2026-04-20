# Flask → FastAPI v2.0.0 Migration — Mission Briefing

> **L0 STATUS: COMPLETE (2026-04-19, tip `a2d3b350`).** All 33 L0 work items landed (L0-00..L0-32). Commit range `d2841632..a2d3b350`. 48 structural guards green, 5 Category-1 golden fingerprint baselines captured. Next layer entry: **L1a**. See §L1a pre-flight gotchas below for 6 L0-learned traps before beginning L1a.

> **v2.0 STRATEGIC LAYERING (2026-04-14) — Layers 0-4 use SYNC, Layers 5-7 convert to ASYNC and polish.**
>
> v2.0 includes the FULL migration: Flask removal (L0-L2, sync handlers), test-harness modernization (L3), FastAPI-native pattern refinement still sync (L4), async conversion (L5), native refinements (L6), polish and ship (L7). The April 11 async pivot was reversed for L0-L4 after cost-benefit analysis — sync `def` admin handlers (Option C from deep audit §1.4) are correct during Flask removal. Async SQLAlchemy is Layer 5+ within v2.0, sequenced after Flask is gone and after the sync `SessionDep` boundary exists (so async becomes a 1-file alias flip at L5b, followed by mechanical conversion of ~60 commit sites and ~200 scalars/execute sites).
>
> The `async-pivot-checkpoint.md` and `async-audit/` reports are the L5-L7 implementation roadmap.

**Mission:** Migrate `src/admin/` (Flask blueprints + Jinja templates + session auth) to FastAPI with **sync `def` admin handlers** (L0-L4), then convert to full async (L5) and polish (L6-L7), without breaking the AdCP MCP/REST surface, OAuth callbacks, or the ~147 template refs that depend on `request.script_root`. Async SQLAlchemy is Layer 5+ within v2.0.

**Branch:** `feat/v2.0.0-flask-to-fastapi` — Flask removal + admin rewrite using sync handlers, one PR per layer (or per sub-layer for L1a–L1d and L5a–L5e), merged to main.

---

## Read me first

This file is the **entry point** for any Claude Code session or engineer touching this migration. The companion docs are large (1k–3k lines each); this file is the map, not the territory. If you read nothing else, read the **Critical Invariants** section below — those are the six things that are easiest to forget and most destructive to miss.

The **source of truth** for "am I ready to ship Wave N?" is `implementation-checklist.md`. Everything else is context.

---

## L1a pre-flight gotchas (learned during L0)

These are six non-obvious traps surfaced while shipping the 33 L0 work items. Read all six before starting L1a — every one has the shape "looks fine at review time, ships as a regression."

**Gotcha 1 — `/metrics` shadowing:** A naïve `/metrics` handler in FastAPI will shadow Flask's. The L0-19 delegate (`src/routes/metrics.py` at commit `aefa2a4d`) MUST forward to `src.core.metrics.get_metrics_text()`; do NOT stub as a placeholder. Both the Flask legacy handler and the L0-19 FastAPI delegate emit byte-equivalent output — if the delegate is stubbed, the FastAPI path silently returns empty metrics while the Flask path keeps working, and the divergence is invisible until L2 Flask-removal.

**Gotcha 2 — `request.state.identity` vs `request.session` dual-resolution:** `UnifiedAuthMiddleware` (L0-10, commit `b643f29b`) populates `request.state.identity`; legacy Flask handlers still read `request.session["user"]`. L1a deps MUST accept both during dual-stack operation; unify on `request.state.identity` by end of L1a. Do NOT silently fall back to one or the other — the dep should fail fast if both are absent.

**Gotcha 3 — `admin_redirect()` open-redirect contract:** The caller validates the target URL — the helper does NOT (L0-32 commit `4a4cbbf4`, doc at commit `9e11f6b1`). L1a handlers passing user-supplied URLs to `admin_redirect()` must pre-validate against an allowlist or a host-match check. The helper intentionally does no validation so that internal constants (OAuth callback URLs, static strings) don't pay a re-validation cost, but every caller that forwards untrusted input is an open-redirect waiting to ship.

**Gotcha 4 — `SessionMiddleware same_site="lax"` is load-bearing:** `CSRFOriginMiddleware`'s "no Origin + no Referer → pass" branch (L0-06 commit `7ebffb40`) is not a loophole — it relies on SameSite=Lax blocking cross-site POSTs at the cookie level, so any request that reaches the CSRF middleware without an Origin header is already same-site by construction. Do NOT flip `SameSite` to `none` (or remove the cookie attribute) without re-architecting CSRF. The OIDC callback transit cookie is the ONLY `SameSite=None` cookie in the stack and it is CSRF-exempt by path.

**Gotcha 5 — Template split:** `templates/error.html` (legacy Flask-variable shape: `error_title` / `error_message` / `back_url` / `error`) vs `templates/_fastapi_error.html` (L0-14 contract: `error_code` / `message` / `status_code`). Regression fix `273ede24` restored the legacy `error.html` variable contract after L0-14 accidentally rewrote it; the FastAPI-shape partial is used ONLY by L0-14's `content_negotiation.py` handler. L1a must NOT merge these two templates — the legacy form is still rendered by Flask handlers during dual-stack, and the variable names differ.

**Gotcha 6 — `/metrics` dual-provider:** Post-L0-19, `/metrics` is served by BOTH Flask (legacy `src/admin/routers/core.py:403`) AND the L0-19 FastAPI delegate — outputs are byte-equivalent. L2 Flask-removal drops the Flask path; do NOT remove the FastAPI delegate during L1a–L1d cleanup. The feature-flag routing during L1a may send metrics scrapes to either side; both must work until L2.

---

## Execution model (user + agent team)

> **Why this section exists:** This migration is driven by a single user orchestrating a team of agents (implementation, review, refactor, evaluation). The human-team scaffolding that normally protects against bus-factor-1 (rotating reviewers, on-call handoffs, vacation blackouts) does not apply. What DOES still apply: defending against pattern drift across long layer sequences, demanding independent eyes on security-critical changes, and keeping irreversible cuts recoverable from cold context. The rules below map those concerns to agent-workflow equivalents.

### Principles

1. **User is the incident commander.** Agents cannot own pager duty. During bake windows (L1a flag flip, L1b OAuth cutover, L2 Flask removal, L5b alias flip, L5c/L5d*/L7 releases) the user watches the `admin-migration-health` dashboard (§6.5); agents assist with log triage and dashboard-query formulation on request.
2. **Every layer-exit PR gets a fresh-agent review pass.** Spawn a reviewer agent with NO prior session context (cold context = independent eyes). This is the anti-drift mechanism that replaces human "no reviewer approves >3 consecutive layer PRs." Driving-agent context leakage is precisely what this guards against.
3. **Security-critical layers get a `/security-review` skill pass.** L1b, L2, and any PR touching `src/admin/csrf.py`, `src/admin/sessions.py`, `src/admin/oauth.py`, `src/admin/rate_limits.py`, or `src/admin/middleware/security_headers.py` — invoke the `/security-review` skill before merging. This replaces the "named security reviewer signs off" gate.
4. **Rollback runbooks must be executable from cold context.** Every rollback procedure in §5 of `implementation-checklist.md` must read like a fresh-agent briefing: exact commands, exact file paths, no "ask <person>" instructions. The docs ARE the bus-factor protection.
5. **Handoff protocol replaced by docs.** Agent sessions are stateless. There is no "incoming lead shadows outgoing lead" — instead, `execution-plan.md` is the handoff. If a fresh agent cannot enter a layer from just the docs, the docs are the bug.
6. **No calendar blackouts.** Agents do not take leave. The user schedules bake windows when the user is available to monitor; that is the only calendar constraint.

### Per-layer gates (agent-workflow form)

| Gate | When | How |
|------|------|-----|
| Fresh-agent review pass | Before every layer-exit merge | Spawn a reviewer agent (no session history) with the layer's PR diff + the layer's exit-gate checklist. Agent reports pass/fail per checklist item. |
| `/security-review` skill pass | L1b, L2, any PR touching the 5 security-critical files | Invoke `/security-review` on the PR branch. Findings addressed before merge. |
| Rollback cold-read | Before entering L1a, L1b, L2, L5b, L5c, L5d*, L7 | User (or fresh agent) reads the rollback procedure for that layer and confirms every command is executable as-written (no unresolved references, no missing credentials). |
| Release monitoring | L1a, L1b, L2, L5b, L5c, L5d*, L7 (bake windows) | User watches `admin-migration-health` dashboard + alert rules from §6.5. Agents assist with triage on request. |

### What was removed

Prior versions of this file declared a Primary/Backup lead, a named Security Reviewer, an Incident Commander, a 2-day handoff protocol, a reviewer-rotation rule, and 5 time-off blackout windows. All of that assumed a multi-human team and is inapplicable to a user+agent workflow. The engineering concerns those roles addressed are preserved in the Principles above — just expressed as agent-workflow mechanics.

---

## Critical Invariants (the 6 deep-audit blockers)

These were surfaced by the 2nd/3rd-order audit. Every one of them has shipped-breaking potential. Do not touch admin code without understanding all six.

1. **`script_root` template breakage — use `url_for` everywhere (greenfield).** Starlette's `include_router(prefix="/admin")` does NOT populate `scope["root_path"]` the way Flask's blueprint mounting populated `request.script_root`. ~147 template references would break silently. **Fix:** every admin route has `name="admin_<blueprint>_<endpoint>"` on its decorator; `StaticFiles(..., name="static")` is mounted on the outer app; every URL in every template uses `{{ url_for('admin_...', **params) }}` or `{{ url_for('static', path='/...') }}`. NO `admin_prefix`/`static_prefix`/`script_root`/`script_name` Jinja globals exist — these are strictly forbidden and guarded by `test_templates_no_hardcoded_admin_paths.py`. Missing route names raise `NoMatchFound` at render time; `test_templates_url_for_resolves.py` catches this at CI time. See `flask-to-fastapi-deep-audit.md` §1 (blocker 1).

2. **Trailing slashes.** Flask's `strict_slashes=False` accepts both `/foo` and `/foo/`; Starlette does not by default. ~111 `url_for` call sites at risk. **Fix:** every admin router constructed as `APIRouter(redirect_slashes=True, include_in_schema=False)`. See `flask-to-fastapi-deep-audit.md` §1 (blocker 2).

3. **`@app.exception_handler(AdCPError)` HTML regression.** Admin user clicks a button, the handler returns a JSON blob to the browser, user sees raw JSON. **Fix:** Accept-aware handler — render `templates/error.html` when `Accept: text/html` and the request path starts with `/admin/` OR `/tenant/` (the `/tenant/{tenant_id}/...` mount is the canonical admin URL post-D1 2026-04-16; `/admin/...` is the legacy/operator-bookmark form). JSON otherwise. See `foundation-modules.md §11.10` for the canonical `_response_mode()` helper that implements `request.url.path.startswith(("/admin/", "/tenant/"))`. This is intentionally different from the JSON-only handler currently at `src/app.py:82-88`. See `flask-to-fastapi-deep-audit.md` §1 (blocker 3).

4. **Admin handlers default to sync `def`, NOT `async def` (bare-sessionmaker + threadpool model).** Layers 0-4: Admin handlers use **sync `def`** with sync SQLAlchemy. Each `with get_db_session()` returns a fresh Session from a bare `sessionmaker` — no `scoped_session` registry, so threadpool thread reuse cannot leak state between requests. This is safe because FastAPI runs sync handlers in a threadpool where each request gets its own Session instance bound to a pooled connection; `Session.close()` returns the connection to the QueuePool on `with`-block exit. `run_in_threadpool` wrapping is NOT needed for admin handlers — FastAPI handles it automatically. MCP and A2A handlers remain `async def`. Full async SQLAlchemy (`AsyncSession`, `asyncpg`) is Layer 5+ within v2.0 (after Flask removal in L2 and FastAPI-native pattern refinement in L4). **Exception:** L1 permits `async def` in OAuth callback handlers where Authlib's Starlette integration requires it — these are the only async-def admin handlers through L4. Because the OAuth callback body runs on the event loop, any sync DB helper it calls **MUST** be wrapped `await run_in_threadpool(_sync_helper, ...)` to avoid blocking the event loop (directly calling `with get_db_session()` inside async-def is FORBIDDEN — it would block every concurrent request for the duration of the DB hit). See `flask-to-fastapi-deep-audit.md` §1.4 (Option C) and `flask-to-fastapi-worked-examples.md §OAuth` for the canonical threadpool-wrap pattern. The `async-pivot-checkpoint.md` and `async-audit/` reports are the L5-L7 implementation roadmap.

5. **Middleware ordering: Approximated BEFORE CSRF.** Counterintuitive but correct. If CSRF fires first, an external-domain POST user fails CSRF validation (403) before the Approximated redirect can fire (should be 307). Also switch the redirect from 302 → 307 to preserve the POST body. **CSRF strategy decided (2026-04-11): Option A — `SameSite=Lax` session cookie + `CSRFOriginMiddleware` (~70 LOC pure-ASGI Origin header validation).** NOT double-submit cookie — that would require changing ~80 fetch calls + ~47 forms for zero practical security gain. Key insight: the planned `SameSite=None` in production was solely for EventSource (SSE); Decision 8 deletes SSE, so `SameSite=Lax` is correct in all environments. `HttpOnly=True` also restored (SSE was the only reason for `False`). Zero JavaScript changes, zero template changes, zero form changes. See `flask-to-fastapi-deep-audit.md` §1 (blocker 5). **OIDC callback footnote:** `/admin/auth/oidc/callback` is CSRF-exempt and uses a separate `SameSite=None; Secure; HttpOnly` transit cookie for `{state, nonce, code_verifier}` across the cross-origin `form_post` response. See `foundation-modules.md §11.6.1` for the full `oauth_transit.py` module + exempt-path entry + state-validation replacement for Origin validation on this path.

6. **OAuth redirect URI byte-immutability.** The paths `/admin/auth/google/callback`, `/admin/auth/oidc/callback` (**NOT** `/admin/auth/oidc/{tenant_id}/callback` — tenant context is in the session, not the URL; corrected per FE-3 audit 2026-04-11), and `/admin/auth/gam/callback` (**NOT** `/auth/gam/callback` — the `/admin` prefix is part of the registered URI; corrected per FE-3 audit 2026-04-11) are registered in Google Cloud Console and per-tenant OIDC provider configs. Any path change — including trailing slash, case, or prefix drift — yields `redirect_uri_mismatch` and login is dead. See `flask-to-fastapi-deep-audit.md` §1 (blocker 6).

**Session cookie rename (`session` → `adcp_session`):** SessionMiddleware writes and reads `adcp_session` only. Legacy `session=...` cookies are silently ignored and users are bounced through Google OAuth once at the L1a deploy. Decision rationale: admin-only surface (no external AdCP API users affected, advertisers use bearer `x-adcp-auth`), avoids ~150 LOC of custom HMAC-SHA1 Flask-compat middleware during a security-critical layer. Customer-communication plan at L1a is a hard gate. On the login-redirect response, emit `Set-Cookie: session=; Max-Age=0; Domain=<cookie-domain>; Path=/` matching Flask's original emission to clear the stale legacy cookie from the browser.

**Reverse-proxy headers:** `uvicorn --proxy-headers --forwarded-allow-ips='*'` must be enabled in production entrypoints so `request.client.host` reflects the end-user IP (not the Fly proxy's IP) and `request.url.scheme` reflects `https` (not `http`). Audit-log call sites previously using `request.remote_addr` (Flask) now read `request.client.host` (Starlette) via FlyHeadersMiddleware.

**Multi-tenant canonical URL routing (D1, 2026-04-16):** admin routes are mounted at a SINGLE canonical prefix `/tenant/{tenant_id}/...` (per-tenant deploys use the subdomain form `https://<tenant>.sales-agent.example.com/...` which is path-rewritten to the canonical form by `TenantSubdomainMiddleware`). `/admin/...` is NOT a separate mount — instead, a pure-ASGI `LegacyAdminRedirectMiddleware` (~60 LOC, lands at L1c) 308-redirects `/admin/<x>/<rest>` requests to `/tenant/<session.tenant_id>/<x>/<rest>` using `request.state.identity.tenant_id` resolved by `UnifiedAuthMiddleware`. Exceptions: `/admin/auth/*`, `/admin/login`, `/admin/logout`, `/admin/public/*`, and the static mount stay at `/admin/*` (OAuth URI byte-immutability per Invariant 6; pre-auth routes don't have a session tenant to redirect with).

Rationale: Starlette's `Router.url_path_for()` returns the FIRST match by registration order (verified at `starlette/routing.py:657-663`). A dual `include_router()` with the same `name=` would silently collapse to one resolver target, making the alternate prefix reachable only by raw path — unsafe for templates that use `url_for`. Canonicalizing to one prefix eliminates the name-collision landmine, halves the admin route count, and gives audit logs a single URL shape per action. Operator bookmarks under `/admin/*` keep working via the 308 redirect.

Guards: `tests/unit/admin/test_architecture_admin_routes_single_mount.py` (L1c entry) — AST-scans `src/app.py` + `src/admin/app_factory.py` and asserts each admin router is `include_router()`-ed exactly once at `/tenant/{tenant_id}` prefix (auth/public/static allowlisted). `tests/integration/test_admin_legacy_redirect.py` (L1c entry) — for each of the 14 feature routers, asserts `GET /admin/<feature>` returns 308 with `Location: /tenant/<session_tenant>/...` when an authenticated session exists.

The 14 admin routers that move to the canonical tenant-prefix mount: `accounts, products, principals, users, tenants, gam, inventory, inventory_profiles, creatives, creative_agents, operations, policy, settings, workflows`. Templates continue to use `url_for("admin_<feature>_<endpoint>", tenant_id=...)` — no renaming needed (each router has exactly one mount, so `url_for` resolves deterministically to the `/tenant/{tenant_id}/<feature>/...` path).

---

## Test-Before-Implement Discipline

Every work item in L0-L7 follows this 7-step cycle. The discipline is enforced at PR review — the feature branch preserves Red/Green granularity even when the PR squash-merges.

### The 7-step cycle

1. **Spec the obligation** — write a single sentence describing the invariant. Example: "Admin handlers never return JSON to browsers for AdCPError."
2. **Pick the test layer** — structural guard (AST scan), unit (behavior in isolation), integration (DB + FastAPI), e2e (full stack), or golden-fingerprint (byte-parity comparison).
3. **Write the failing test** — run it; verify it fails for the **expected semantic reason**, NOT ImportError/NameError/SyntaxError. The failure must demonstrate the obligation is unmet by current code.
4. **Commit the test alone** — `git commit -m "test: obligation X — expected failing"`. The Red state is now in feature-branch history; CI will show one red build.
5. **Implement the minimum change** — no gold-plating, no adjacent refactors, no speculative generality.
6. **Commit impl with the same test now passing** — `git commit -m "feat: X satisfies obligation"`. CI shows green.
7. **Codify as structural guard if category-recurring** — if the violation could appear in any future file (not just this one), convert to an AST-scanning guard with ratcheting allowlist. Use the `/write-guard` skill.

### Escape hatches

The discipline is NOT ceremonial theater — it has legitimate escapes for:

- **Pure mechanical renames** (codemod-style `blueprint → router`, `Phase → Layer`): the test IS the codemod. Re-running the codemod on a pristine tree and diffing against the expected output is the assertion. One commit is fine.
- **Documentation-only edits** (`docs/`, `.claude/notes/`, `CLAUDE.md` prose): no test needed.
- **Infrastructure/config** (`pyproject.toml`, `tox.ini`, Dockerfile, `.pre-commit-config.yaml`): CI itself is the assertion — `make quality` passing is the test.
- **Dependency bumps**: golden-fingerprint diff is the test; no new test file needed.
- **Deletion of dead code** (verified unused via existing structural guard): the guard's green state is the assertion.

Each escape uses a commit trailer declaring the waiver:

```
docs: clarify L5 async conversion sub-layering

discipline: N/A - docs-only edit
```

```
chore: bump httpx to 0.28.0

discipline: N/A - dep bump; golden fingerprints unchanged
```

### Post-hoc verification

On the feature branch, BEFORE squash-merge, run:

```bash
git log main..HEAD --pretty=format:"%H %s" | awk '
  /^[a-f0-9]+ test:/ { red_sha=$1; red_msg=$0; next }
  /^[a-f0-9]+ (feat|fix|refactor):/ {
    if (red_sha) {
      print "PAIR OK: " red_msg " → " $0
      red_sha=""; red_msg=""
    } else {
      # Non-paired impl — check for discipline: N/A trailer in commit body
      cmd = "git show --no-patch --format=%B " $1 " | grep -q \"^discipline: N/A\""
      if (system(cmd) != 0) {
        print "MISSING TEST: " $0
      }
    }
  }
  /^[a-f0-9]+ (chore|docs|style):/ { next }
'
```

Strict mode: any `MISSING TEST` fails the pre-merge check. Reviewer runs this before hitting Squash Merge.

### Migration-specific applications

- **Porting a Flask route** → golden-fingerprint test written (fails because FastAPI router doesn't exist yet) → commit red → port route → fingerprint matches → commit green.
- **Deleting `src/admin/app.py`** → `test_architecture_no_flask_imports.py` with empty allowlist written → fails because `create_app` is imported by 5+ files → commit red → sweep imports → commit green.
- **Adding CSRFOriginMiddleware** → 7 Origin-scenario pytest cases written against unimplemented `src.admin.csrf.CSRFOriginMiddleware` → fail with ImportError (expected — module doesn't exist yet) → commit red → implement middleware → all 7 pass → commit green.
- **Converting a handler to `async def`** → `test_<handler>_async.py` using `AsyncClient` written → fails because sync `def` can't be awaited → commit red → flip signature + body → commit green.

### What the discipline PREVENTS

- Reviewer can't tell if a test was written ad-hoc after the fix vs designed to catch the bug (the Red state in history is the proof).
- "I'll add tests later" drift — there is no "later"; Red-first is the entry condition.
- Gold-plating — the minimum-to-green rule constrains implementation scope.
- Mystery regressions from refactors — each refactor carries a test that proves it didn't break the pre-existing behavior.

### Cross-references

- Matrix row 48 in `implementation-checklist.md` §4.6 (meta-guard) enforces allowlist monotonicity across all tests written under this cycle.
- Layer-scope commit-lint in `flask-to-fastapi-foundation-modules.md` §11.27 enforces that the per-cycle commits stay within a single declared layer.

---

## 2026 FastAPI-native baseline

The plan targets 2026 best practices. When in doubt, default to these idioms and flag any older pattern proposal:

- FastAPI: lifespan context manager (not `@app.on_event`)
- Pydantic v2 + `pydantic-settings` for configuration
- SQLAlchemy 2.0 `Mapped[]` annotations + async engine at L5+
- `structlog` with `contextvars` for logging
- `TestClient` (L0–L4) → `httpx.AsyncClient` (L5+)
- Pure-ASGI middleware (`BaseHTTPMiddleware` only where streaming mutation is needed)
- `SessionDep` / `Depends()` injection for auth, DB session, tenant context, identity

---

## Recommended reading order (fresh reader, ~2 hours)

1. **This file** — you are here. Mission, blockers, map.
2. **`execution-plan.md`** — START HERE for implementation. Canonical sync patterns and 8-layer (L0-L7) definitions.
3. **`implementation-checklist.md`** — per-layer acceptance criteria; the "am I ready?" source of truth.
4. **`flask-to-fastapi-deep-audit.md` §1–§2** — the 6 blockers and the risk register in full detail.
5. **`flask-to-fastapi-adcp-safety.md`** — confirm the AdCP boundary is clear; note the 8 first-order action items.
6. **`flask-to-fastapi-foundation-modules.md`** — reference only. Read the module you are about to implement; do not read end-to-end.
7. **`flask-to-fastapi-worked-examples.md`** — reference only. Read the example that matches the blueprint you are translating.
8. **`flask-to-fastapi-execution-details.md`** — reference only. Read the layer you are currently shipping.
9. **[Historical reference]** **`flask-to-fastapi-migration.md` §1–§2.8** — predates 8-layer rename. Skim only for high-level context.
10. **[Decision history]** **`async-pivot-checkpoint.md`** and **`async-audit/*.md`** — archived verification artifacts. Not current guidance.

---

## File index

| File | When to read | Detail level |
|---|---|---|
| `CLAUDE.md` (this file) | First, always | Entry point / map |
| **`execution-plan.md`** | **START HERE for implementation** | **8 layers (L0-L7) in strict order, standalone briefings** |
| `implementation-checklist.md` | Tick boxes AFTER completing work | Verification tracking, 55 audit findings |
| `async-pivot-checkpoint.md` | **[Historical reference]** Decision-history artifact; not current guidance | Target state (corrected 2026-04-12) |
| `flask-to-fastapi-migration.md` | **[Historical reference]** Predates 8-layer rename; skim for context only | Overview |
| `flask-to-fastapi-deep-audit.md` | Before writing admin code | 6 blockers + 20 risks |
| `flask-to-fastapi-adcp-safety.md` | Before touching MCP/REST surface | 1st-order audit |
| `flask-to-fastapi-foundation-modules.md` | When implementing a foundation module | Full code + tests |
| `flask-to-fastapi-worked-examples.md` | When translating a specific blueprint | 5 worked examples |
| `flask-to-fastapi-execution-details.md` | Rollback procedures reference | Per-wave rollback |

---

## Derivative audit reports (2026-04-11)

After the (subsequently reversed) 2026-04-11 async pivot, six parallel opus agents (agents A-F) produced deep-audit reports on different facets of the absorbed-async v2.0 scope. The reports remain authoritative for L5+ planning even though the overall pivot was superseded by the 2026-04-14 layering decision (L0–L4 sync, L5+ async). All reports live in `async-audit/` and are committed at `3e0afa02` / `d8957931` on `feat/v2.0.0-flask-to-fastapi`. A fresh session should consult these before making scope or idiom decisions.

| Report | Lines | When to read |
|---|---|---|
| `async-audit/agent-a-scope-audit.md` | 765 | **Before estimating Wave 4-5 LOC or spike scope.** File-by-file async conversion inventory, lazy-load audit (~50 sites all mechanically fixable), refined scope estimate (~16-18k total LOC, **under** checkpoint's 30-35k upper bound), and **9 open decisions** (listed below) that need user input before Wave 4 can start. |
| `async-audit/agent-b-risk-matrix.md` | 2,392 | Before attempting any risk mitigation. 33 risks (15 checkpoint + 18 new), severity table, per-risk 4-part deep dive (root cause / detection / mitigation / fallback), **9-pattern lazy-load cookbook**, 7-spike pre-Wave-0 gate, driver fallback to `psycopg[binary,pool]>=3.2.0`. |
| `async-audit/agent-c-plan-edits.md` | 2,074 | Source record only — its 45 edits were applied in commit `d8957931`. Consult for traceability. |
| `async-audit/agent-d-adcp-verification.md` | 1,433 | **Before touching any AdCP surface.** 21 surfaces PASS with zero current Risk #5 hits. 9 mitigations M1-M9, including **10 missing `await` sites** identified at exact line numbers: `src/routes/api_v1.py` lines 200, 214, 252, 284, 305, 324, 342, 360 + `src/core/tools/capabilities.py` lines 265, 310 + parallel `src/a2a_server/adcp_a2a_server.py` lines 1558, 1587, 1774, 1798, 1842, 1892, 1961, 2000. These must land in the same PR that converts the corresponding `_raw`/`_impl` to async. |
| `async-audit/agent-e-ideal-state-gaps.md` | 2,849 | Before writing foundation code. Current plan graded B+; 14 idiom upgrades (SessionDep DI pattern, DTO boundary, lifespan-scoped engine, structlog, no-UoW repository pattern). Minimum apply set: E1/E2/E3/E5/E6/E8. |
| `async-audit/agent-f-nonsurface-inventory.md` | 1,782 | Before Dockerfile, CI, pre-commit, or deployment script changes. 105 non-code action items across 27 categories. **Hard blocker:** 3 sync-psycopg2 deployment paths in `scripts/deploy/entrypoint_admin.sh:9`, `scripts/deploy/run_all_services.py::check_database_health/check_schema_issues`, and `src/core/database/db_config.py::DatabaseConnection`. Also PG version skew (CI=15, local=17), missing `[tool.pytest.ini_options]` section, `DATABASE_URL` sslmode→ssl rewriter. |

### Database deep-audit (2026-04-11)

After the 9 decisions were resolved, a second audit round focused specifically on the database layer. 6 parallel Opus subagents (ultrathink, 2nd/3rd/4th-order derivative analysis) audited: ORM models + relationships, session lifecycle + connection pool, repository pattern + queries, Alembic migrations, test DB infrastructure, and data integrity + performance. Total: ~8,000+ lines of production code + ~4,000+ lines of test infrastructure read.

| Report | When to read |
|---|---|
| `async-audit/database-deep-audit.md` | **Before starting Spike 1 or any Wave 4 code.** 3 critical blockers (statement_timeout crash under asyncpg, CreativeRepository.commit() atomicity break, 20+ uow.session MissingGreenlet sites), 8 high-severity issues (Product @property lazy-load trap, 5 backref= invisible attributes, 3 missing ondelete cascades, deploy connection budget overflow, 45+ server_default stale columns, no engine.dispose() in shutdown, no application_name on engines, N+1 in products admin). Key recommendation: **keep Alembic env.py sync with psycopg2** (eliminates Spike 6 risk). Full prioritized action list with wave assignments and effort estimates. |

### Frontend deep-audit (2026-04-11)

After the database audit, a third audit round focused on the frontend surfaces impacted by Flask removal. 6 parallel Opus subagents audited: Jinja templates + url_for, JavaScript + fetch endpoints, OAuth + session + auth flows, static assets + CSS, error pages + flash + admin UX, and route parity + handler migration.

| Report | When to read |
|---|---|
| `async-audit/frontend-deep-audit.md` | **Before touching any template, JS file, or admin route handler.** 7 critical blockers (OIDC callback path wrong in docs, base.html cascade, 302→307 redirect default, CSRF added where none existed, tojson filter missing, AJAX Accept false-positive, duplicate adapter route). 10 high-severity issues. Key numbers: 197 Flask routes, 74 templates, 366 flash() calls, ~147 script_root refs, ~115 fetch() calls, 338 redirect() calls. CSRF recommendation: SameSite=Lax + Origin validation (0.5 day) instead of adding tokens to all 80+ fetch calls (2-3 days). |

### Comprehensive testing strategy (2026-04-11)

6 parallel Opus subagents designed a multi-tier testing strategy covering unit tests + structural guards, integration test async conversion, E2E + admin UI, performance benchmarks, BDD behavioral tests, and migration safety + rollback verification.

| Report | When to read |
|---|---|
| `async-audit/testing-strategy.md` | **Before writing any test or defining any wave gate.** 6 tiers, ~6,000+ existing tests as safety net, 18 new structural guards, ~217 new migration-specific tests, performance benchmarks at 4 concurrency levels, chaos/fault injection for 7 failure modes. Key: conftest.py autouse fixture overhaul is highest-blast-radius single change (affects all 4,052 unit tests). Integration test async conversion (1,817 tests) via libcst AST rewriter. BDD stays sync with asyncio.run() bridge. Wave 3 Flask removal is the only irreversible wave. Total testing effort: ~35-40 person-days across Waves 0-5. |

### ~~Open decisions blocking Wave 4~~ Decision triage (async pivot reversed 2026-04-12)

> **Async DB layer is Layer 5+ within v2.0.** Of the 9 decisions below, 4 are L5+ (Decisions 1, 3, 7, 9 — activated after Flask removal and FastAPI-native pattern refinement), 4 are UNCHANGED / applied throughout (Decisions 2, 4, 5, 8), and 1 is REDUCED (Decision 6). Decision 7 (ContextManager refactor) is specifically scheduled for **L4** (not L5d) so that L5 is a pure async-idiom conversion with no structural surgery. The full text below is the L5+ implementation plan.

The 9 questions Agent A identified. **Decisions 1, 7, and 9 were resolved via ultrathink deep-think analysis on 2026-04-11** (3 parallel Opus subagents, each producing 1st/2nd/3rd-order derivative analysis). Decisions 2, 3, 5, 8 were resolved earlier by Audit 06 (see meta-audit round). Decisions 4 and 6 are mechanical Wave 4 work, not blockers. **Ledger closed.**

1. **Adapter base class async conversion strategy** — **RESOLVED: Path B (sync adapters + `run_in_threadpool` wrap).** Full async requires porting `googleads==49.0.0` off `suds-py3` and rewriting 4 `requests`-based adapters (~1500 LOC) for zero AdCP-visible benefit. Path B keeps adapters sync; the **30 `adapter.` call sites** in `src/core/tools/*.py` across 7 files (media_buy_create: 16, media_buy_update: 7, capabilities: 2, media_buy_list: 2, performance: 1, media_buy_delivery: 1, products: 1; verified 2026-04-17) wrap in `await run_in_threadpool(...)`. (Previously claimed "+1 in `src/admin/blueprints/operations.py`" was not confirmed at 2026-04-17 re-verification — zero `adapter.` calls in that file; the file moves to `src/admin/routers/operations.py` post-L0 codemod regardless.) Requires `get_sync_db_session()` factory in `src/core/database/database_session.py` alongside async path (adapters touch DB 40 times). `AuditLogger.log_operation` splits into `_log_operation_sync` (internal, used by adapters) + async public wrapper. Threadpool tune: `anyio.to_thread.current_default_thread_limiter().total_tokens = 80` in lifespan startup (default 40 is too low for burst adapter load), env-override via `ADCP_THREADPOOL_TOKENS` (canonical; `ADCP_THREADPOOL_SIZE` is the deprecated older draft name). See `foundation-modules.md §11.14.F` and `implementation-checklist.md §L0 lifespan` for the canonical code block. Structural guard `tests/unit/test_architecture_adapter_calls_wrapped_in_threadpool.py` prevents drift. See Wave 4a-pilot acceptance criteria in `flask-to-fastapi-execution-details.md`. **2026-04-11 decision.**
2. **Delete `DatabaseConnection` + `get_db_connection()` in `src/core/database/db_config.py`?** — **RESOLVED (Audit 06 OVERRULE, REFINED 2026-04-11): KEEP.** Real rationale is **fork safety**, not loop collision (the original Audit 06 reasoning is technically false — `run_all_services.py` is PID 1 and forks uvicorn into a child subprocess via `subprocess.Popen([sys.executable, "scripts/run_server.py"])` at line 231, so parent/child have independent Python interpreters and there is no shared event loop to collide with). The actual reason `DatabaseConnection` stays: using `get_sync_db_session()` (Decision 1 factory) here would eagerly initialize a SQLAlchemy engine with 10 pooled connections **in the parent process** that then get duplicated into the uvicorn child via `Popen`'s file-descriptor inheritance → PG socket corruption (the canonical SQLAlchemy fork-safety bug). Raw psycopg2 connect-query-close is fork-safe because the connection is fully closed before the fork. **Corrected caller list (2026-04-11):** `scripts/deploy/run_all_services.py:84,135` and `examples/upstream_quickstart.py:137`. **NOT** `scripts/setup/init_database.py` or `init_database_ci.py` — the original Audit 06 ledger and Agent F §1.4 incorrectly named these; they actually use SQLAlchemy `get_db_session()`, not raw psycopg2. **Scope additions for the same PR:** (a) **delete dead `scripts/deploy/entrypoint_admin.sh`** — unreferenced by Dockerfile/compose, still shell-imports psycopg2 in a subshell, calls non-existent `migrate.py`, imports `src.admin.server` which is scheduled for Wave 3 deletion; (b) **migrate `examples/upstream_quickstart.py:137` to `get_db_session()`** (example is standalone async-capable, leaves DatabaseConnection with exactly 2 callers); (c) **harden `DatabaseConnection.connect()`** with `connect_timeout=10` + `options="-c statement_timeout=5000"` so a hanging DB cannot brick container startup; (d) **two structural guards** (not one): `tests/unit/test_architecture_no_runtime_psycopg2.py` (AST allowlist of 3 import sites) AND `tests/unit/test_architecture_get_db_connection_callers_allowlist.py` (runtime call-site allowlist of 1 file — `run_all_services.py`). **NEW Risk #34** surfaced: `run_all_services.py:175` imports `init_db()` which under async pivot opens the SQLAlchemy async engine in the parent before `Popen` — same fork-safety bug class. Mitigation: `init_db()` must call `await reset_engine()` in `finally`, OR `run_all_services.py` must run init via `subprocess.run([sys.executable, "-m", "scripts.setup.init_database"])` like migrations already do at `:207`. **2026-04-11 decision (refined).**
3. **Factory-boy async strategy** — **RESOLVED: custom `AsyncSQLAlchemyModelFactory` shim that overrides `_save` (not `_create`) with `sqlalchemy_session_persistence = None` (refined 2026-04-11 deep-think).** The Audit 06 recipe had 3 bugs: (a) overrode `_create` instead of `_save`, which would break `AccountFactory`'s existing `_create` override at `tests/factories/account.py:28-30`; (b) used `session.sync_session.add(instance)` redundantly — `AsyncSession.add()` is a sync method (verified at `sqlalchemy/ext/asyncio/session.py:1111-1143`) that proxies directly to `sync_session.add()`; (c) called `session.sync_session.flush()` which **raises `MissingGreenlet` under asyncpg** — any sync DB I/O on an AsyncSession-owned connection must go through `greenlet_spawn(...)`. No current factory needs DB-materialized PKs across SubFactory boundaries (all 15 ORM factories generate cross-referenced keys Python-side via `Sequence`), so flush is NOT called by the shim. Polyfactory rejected: ~10× migration cost (15 factory rewrites + 166 test rewrites), incompatible async semantics with savepoint-deferred-commit pattern. **Wave 4b-4c hard cliff:** all 166 consuming integration tests must flip to async BEFORE factory base classes flip — enforced by pre-PR diff-scope gate. Three new structural guards: `test_architecture_factory_inherits_async_base.py`, `test_architecture_factory_no_post_generation.py`, `test_architecture_factory_in_all_factories.py`. New Spike 4.25 (factory async-shim validation, 0.5 day soft blocker). Full recipe in `foundation-modules.md` §11.13.1 (D). **2026-04-11 decision (refined from Audit 06).**
4. **`src/core/database/queries.py`** — **RESOLVED: Option 4A (convert-and-prune), refined 2026-04-11 deep-think.** File has **6 functions** (not 7 as previously stated — verified by file read), **zero production callers** (only consumer is `tests/integration/test_creative_review_model.py`), and **3 of the 6 functions are dead code** with zero callers anywhere in the repo (`get_recent_reviews`, `get_creatives_needing_human_review`, `get_ai_accuracy_metrics`). All functions are pure reads (no `session.commit()`, no `session.add()`, no `session.flush()`). Wave 4 work: (a) **delete** the 3 dead functions + their 3 allowlist entries in `test_architecture_no_raw_select.py:287,291,292` (~−158 LOC); (b) **convert** the 3 live functions to `async def` using `(await session.execute(stmt)).scalars().first()/all()` (~+10 LOC); (c) **convert** the 5 test functions + 1 helper in `test_creative_review_model.py` to `async def`/`async with` (~50 LOC of edits). No dual session factory needed (zero sync callers). Net scope: **~−100 LOC**, not +50 LOC. Structural move of the 3 live functions onto `CreativeRepository` + full test rewrite to factory-boy/harness deferred to v2.1 (Option 4B). **Not a blocker. 2026-04-11 decision (refined).**
5. **`src/core/database/database_schema.py` + `product_pricing.py`** — **RESOLVED (Decision 5 deep-think REFINEMENT of Audit 06, 2026-04-11).** Both files are **deleted**, not patched — the Audit 06 SUBSTITUTE prescription (RuntimeError) is overkill and technically ineffective because it targets the wrong crash point. (a) `database_schema.py` is a **confirmed orphan** (zero Python importers; stale pre-Alembic DDL with 10 tables missing `pricing_options`, `workflow_steps`, `creative_agents`, etc.; self-declares "reference only, should not be used"). Delete in Wave 5 cleanup; in the same commit, strip the stale docstring reference in `src/core/database/__init__.py:12`. (b) `product_pricing.py` has exactly **ONE external caller** (`src/admin/blueprints/products.py:18,479` inside `list_products`) and that caller already uses `joinedload(Product.pricing_options)` upstream at line 443 — the `inspect(product).unloaded` guard is never exercised. Furthermore, the guard at line 38 is **defeated by the log statement at line 43** — `bool(product.pricing_options)` in the f-string triggers relationship access UNCONDITIONALLY before the guarded branch, so under async + `lazy="raise"` the crash point is the LOG, not the early-return that Audit 06 proposed to replace with RuntimeError. `get_primary_pricing_option` (line 74) has **zero callers** (dead code). **Wave 4 fix:** delete `src/core/database/product_pricing.py` entirely (~81 LOC); inline the pricing-option conversion at the single caller, preferably as `AdminPricingOptionView` Pydantic DTO per agent-e E6 DTO-boundary recommendation. Spike 1's blanket `lazy="raise"` on `Product.pricing_options` (via the models.py sweep) is the ongoing enforcement — no extra guard needed. **2026-04-11 decision (supersedes Audit 06 SUBSTITUTE).**
6. **Flask-caching in pyproject.toml** — **RESOLVED (Decision 6 deep-think 2026-04-11): replace with `src/admin/cache.py::SimpleAppCache`.** 3 active consumer sites confirmed (grep-verified, zero `@cache.memoize`/`@cache.cached` decorators): `src/admin/blueprints/inventory.py:874` (tree), `:1133` (list), `src/services/background_sync_service.py:472` (post-sync invalidation). Both inventory sites cache `jsonify(...)` **Response objects** — a Flask-ism that breaks under FastAPI (must cache the payload dict and reconstruct `JSONResponse(dict)` on hit). `cache_key` + `cache_time_key` written as separate entries (non-atomic pair) — recommended refactor: fold into single 2-tuple entry `(response_dict, timestamp)` under one key. Background sync invalidation at Site 3 was **latently broken even in Flask** (`threading.Thread` has no Flask app context; `try/except` at `:479` silently eats `RuntimeError: Working outside of application context`). SimpleAppCache is **~90 LOC** (not 40): `cachetools.TTLCache(maxsize=1024, ttl=300)` + `threading.RLock` (NOT `asyncio.Lock` — Site 3 is a sync thread that cannot `await`) + `install_app_cache(app)` lifespan hook + `get_app_cache()` module global with `_NullAppCache` fallback for the startup race window + `CacheBackend` Protocol for v2.2 Redis swap. Admin handlers use `request.app.state.inventory_cache`; background threads use `get_app_cache()`. Env vars `ADCP_INVENTORY_CACHE_MAXSIZE` and `ADCP_INVENTORY_CACHE_TTL` override defaults. Site 2 has **NO invalidation** (pre-existing 5-min stale data gap). Strict 12-step migration order (a→i) in a single Wave 3 PR. Two structural guards: `test_architecture_no_flask_caching_imports.py` + `test_architecture_inventory_cache_uses_module_helpers.py`. Full recipe in `foundation-modules.md` §11.15. **2026-04-11 decision (refined from Decision 9 correction).**
7. **`src/core/context_manager.py`** — **RESOLVED: refactor to stateless async module functions taking `session: AsyncSession`.** The `ContextManager(DatabaseManager)` inheritance caches `self._session` on a process-wide singleton; under `async_sessionmaker` on the single event-loop thread, every concurrent task shares the same cached session → transaction interleaving. `async_sessionmaker` does NOT fix this because the singleton sits above the session factory. **Refactor:** delete the class, delete `_context_manager_instance` + `get_context_manager()`, convert 12 public methods to module-level `async def` functions taking `session` as first positional parameter, delete `DatabaseManager` entirely (only ContextManager subclassed it). 7 production callers (incl. dead `main.py:166` + module-load side effect in `mcp_context_wrapper.py:345`). ~400 LOC across ~15 files; ~50 test patches, 20 collapsible via single `tests/harness/media_buy_update.py` update. `mock_ad_server.py` has a `threading.Thread` background task that becomes `asyncio.create_task` + `async with session_scope()`. Validated by pre-Wave-0 **Spike 4.5** (0.5-1 day, soft blocker). Structural guard `tests/unit/test_architecture_no_singleton_session.py` prevents regressions (3 test methods: no session-typed class attrs, no `_X_instance` singleton getters, no module-level `*Manager()` instantiations). Zero interaction with Decisions 1 and 9 (grep-verified). **2026-04-11 decision.**
8. **SSE session lifetime** — **RESOLVED (Decision 8 deep-think 2026-04-11): DELETE the SSE route entirely.** Audit 06 said "already correct, just async I/O upgrades" — correct about session-per-tick (verified: `get_recent_activities()` at `activity_stream.py:167` does open/close per call, ~5ms), but wrong about the scope. The `/tenant/{id}/events` SSE route at `activity_stream.py:226-364` is **orphan code** — `templates/tenant_dashboard.html:972` literally says `// Use simple polling instead of EventSource for reliability` and fetch-polls `/tenant/{id}/activity` (JSON) at 5s intervals (`:978`). Zero `new EventSource(` exists in `templates/` or `static/`. Only `/events` callers are `tests/integration/test_admin_ui_routes_comprehensive.py:367-370` (smoke probe) and `docs/development/troubleshooting.md:74`. Wave 4 deletes: SSE route + generator + rate-limit state (`MAX_CONNECTIONS_PER_TENANT`, `connection_counts`, `connection_timestamps`) + HEAD probe + smoke test + docs line + `sse_starlette` dependency in `migration.md:749`. Net: **−170 LOC, −3 unwritten test files**. (`sse_starlette` is NOT currently in `pyproject.toml` — it was a planned dep for the original SSE port that never landed; "-1 pip dep" claim from the 2026-04-11 analysis is inflated.) Two surviving routes (`/activity` JSON poll + `/activities` REST) convert mechanically: `def → async def`, `with → async with get_db_session()`, `db_session.scalars(stmt).all() → (await db_session.execute(stmt)).scalars().all()`. Additionally fix `api_mode=False → api_mode=True` on the JSON poll route (pre-existing bug — JS `fetch` sees HTML 302 redirect on auth failure, never gets the 401 the template expects). Structural guard `tests/unit/test_architecture_no_sse_handlers.py` asserts zero `EventSourceResponse`/`StreamingResponse(mimetype="text/event-stream")` in `src/admin/routers/`. **2026-04-11 decision (supersedes Audit 06 SUBSTITUTE).**
9. **`src/services/background_sync_service.py`** — **RESOLVED (D3 2026-04-16): v2.0 rearchitect to `asyncio.create_task` + checkpoint-per-GAM-page (supersedes Option B sync-bridge).** Service today runs multi-hour GAM inventory sync jobs via `threading.Thread` workers, incompatible with async SQLAlchemy — a naïve async conversion holding a single `AsyncSession` for hours breaks under `pool_recycle=3600`, Fly.io TCP keepalives, and GAM API pagination. **Previously planned: Option B sync-bridge** (separate sync psycopg2 engine at `src/services/background_sync_db.py`, sunset-deferred-to-v2.1). **User-authorized alternative (D3, accepted 2026-04-16):** rearchitect to async + checkpoint-per-batch. Pattern: each GAM-page (~30s) opens its own short-lived `async with get_db_session() as session:`, writes progress to a `sync_checkpoint` row, commits, closes. Resume logic reads checkpoint and continues from next cursor on next tick. Session lifetime is always << `pool_recycle`; no sync-bridge needed; `psycopg2-binary` is NOT retained for this purpose (still retained for Decision 2 fork-safety). ~2 engineer-days of rearchitecture + tests (eliminates `src/services/background_sync_db.py` entirely — never written). `threading.Thread` workers become `asyncio.create_task(...)` in lifespan, registered on `app.state.active_sync_tasks: dict[str, asyncio.Task]`, cancellable on shutdown. GAM idempotency is preserved via per-page cursor — re-running a partial page is safe. **Structural guard NOT introduced:** `test_architecture_sync_bridge_scope.py` is deleted from plan. **New guard introduced:** `test_architecture_no_threading_thread_for_db_work.py` — AST-scans `src/` for `threading.Thread(target=...)` where the target body contains `get_db_session` or `session.` calls; allowlist EMPTY. **Lands at L5d1** (renamed from "sync-bridge" to "background_sync async rearchitect"). Validated by Spike 5.5 at L5a entry (checkpoint-session viability — 4 test cases: 4-hour sync, concurrent tenants, cancellation, resume from checkpoint). Other long-running services (`background_approval_service`, `order_approval_service`) have bounded durations < `pool_recycle=3600` and convert to async normally. **2026-04-16 decision (D3 supersedes 2026-04-11 Option B).**

### v2.0 Spike Sequence (Canonical — 10 technical spikes + 1 decision gate)

Pre-L5 research spikes. Each has a HARD or SOFT gate; HARD = STOP and reassess if fail, SOFT = document and proceed. Spikes 1-7 are technical; Spike 8 is the aggregate go/no-go decision gate at L5a EXIT.

| # | Spike | Gate | Layer | Estimate | Fail action |
|---|-------|------|-------|----------|-------------|
| 1 | Lazy-load audit (`lazy="raise"` on 68 relationships) | HARD | L5a | 1-2 days | If >40 fixes or >2 days: ship L0-L4 only; defer async to v2.1. Do not abandon L0-L4. |
| 2 | asyncpg vs psycopg3 driver compatibility | HARD | L5a | 1 day | If asyncpg incompatible: use psycopg[binary,pool]>=3.2.0; document perf delta |
| 3 | Perf baseline capture (sync) | HARD | **L4 EXIT** | 0.5 day | Cannot skip — L5 comparison oracle |
| 4 | 5-representative-test async conversion | SOFT | L5a | 1 day | Document patterns; feed into L5c pilot |
| 4.25 | Factory-boy async shim (8 edge cases, Decision 3) | SOFT | L5a | 1 day | If fails: STOP Wave 4; reconsider polyfactory |
| 4.5 | ContextManager stateless refactor validation (Decision 7) | SOFT | **L4** | 0.5-1 day | Refactor gets dedicated L4 sub-phase PR |
| 5 | Scheduler alive-tick conversion (2 ticks) | SOFT | L5a | 0.5 day | Forced-shutdown deadlock check per Risk #26 |
| 5.5 | Checkpoint-session viability (D3 — supersedes two-engine) | SOFT | L5a | 0.5 day | Revert to pre-D3 Option B sync-bridge; file v2.1 sunset ticket |
| 6 | Alembic async env.py evaluation | SOFT | L5a | 0.5 day | Default: keep env.py sync per database-deep-audit |
| 7 | GAM adapter threadpool saturation test | SOFT | L5a | 0.5 day | Informs threadpool size + per-adapter CapacityLimiter |
| **8** | **L5 go/no-go decision gate** (aggregates 1-7 + writes spike-decision.md) | **HARD** | **L5a EXIT** | 0.5 day | **Decision point**: proceed full async, reduce scope, OR ship L0-L4 only and defer async to v2.1 |

**Total spike budget:** 7.5-9 days (corrected 2026-04-16 — previous "5.5-8" undercounted by 2 days per table-row sum).

Spike 8 is NOT a technical spike — it is the formal decision gate at L5a end. This resolves the 10-vs-11 count ambiguity: **"10 technical spikes + 1 decision gate = 11 total pre-L5b work items."**

**Per-spike detail** (retained from prior draft; used by `execution-details.md` and `implementation-checklist.md` for acceptance criteria):

1. **Spike 1 — Lazy-load audit** (HARD GATE): set `lazy="raise"` on all 68 relationships (verified 2026-04-12 by grep of `models.py`), run `tox -e integration`. Pass: <40 failures fixable in <2 days. **Fail = STOP L5, reassess scope, and either reduce the async surface or defer residual async to a v2.1 epic; do not abandon L0-L4 (sync Flask removal is already valuable standalone).** See `foundation-modules.md §11.29` for the eager-load decision matrix and Spike 1 failure-triage procedure.
2. **Spike 2 — Driver compat**: run tests under `asyncpg`. Fail = switch to `psycopg[binary,pool]>=3.2.0`.
3. **Spike 3 — Performance baseline**: capture sync latency on 20 admin routes + 5 MCP tool calls as `baseline-sync.json` at the **L4 EXIT** (not L5a entry) — the async flip at L5b must be measurable against a baseline that already reflects the L4 pattern refinement (DTO boundary, structlog, SessionDep as sync alias). **Under Path B (Decision 1), the baseline includes adapter `run_in_threadpool` wraps** — L5 benchmark parity measurements must NOT compare sync baseline vs "bare async" but vs "async + threadpool-wrapped adapters" since that is the v2.0 production shape.
4. **Spike 4 — Test harness**: convert `tests/harness/_base.py` + 5 representative tests; verify xdist + factory-boy work.
5. **Spike 4.25 — Factory async-shim validation** (soft blocker, Decision 3): create `tests/factories/_async_shim.py` per §11.13.1(D) recipe; temporarily flip `TenantFactory` to `AsyncSQLAlchemyModelFactory` base; run 8 edge-case tests. Pass: all 8 green, no `MissingGreenlet`. Fail action (HARD): recipe has a bug → STOP Wave 4 and re-analyze; reconsider polyfactory.
6. **Spike 4.5 — ContextManager refactor smoke test** (soft blocker, Decision 7, **runs at L4 ENTRY — gates the Decision 7 refactor before the L4 PR lands**): rewrite `src/core/context_manager.py` as stateless module functions (sync at L4, converted to `async def` at L5c), delete `DatabaseManager`, convert smallest caller end-to-end. Pass: refactor size <400 LOC AND <15 files AND <50 test patches AND error-path composition test proves outer `session_scope()` rollback does NOT wipe error-logging writes. Fail action (SOFT): refactor gets a dedicated L4 sub-phase PR; NOT a hard gate on L5a entry.
7. **Spike 5 — Scheduler alive-tick**: convert 2 scheduler tick bodies; observe container logs.
8. **Spike 5.5 — Checkpoint-session viability** (soft blocker, Decision 3 rearchitect per D3 2026-04-16; supersedes two-engine coexistence spike): prove the async sessionmaker pool sustains high per-tick checkpoint session churn without `QueuePool limit` errors. 4 test cases: (a) single 4-hour sync with per-page short-lived sessions completes; (b) 3 concurrent multi-tenant syncs share the pool without contention; (c) cancellation via `task.cancel()` cleanly closes any in-flight session; (d) resume from a persisted checkpoint after container restart. Pass: all 4 green; no `QueuePool limit` under p95 load. Fail action (SOFT): revert to pre-D3 Option B sync-bridge (retain psycopg2-binary; file v2.1 sunset ticket) — document in `spike-decision.md`.
9. **Spike 6 — Alembic async**: rewrite `alembic/env.py`; run upgrade/downgrade roundtrip. Fallback: keep env.py sync.
10. **Spike 7 — `server_default` audit**: grep + categorize columns; confirm <30 to rewrite. (In the canonical table above, row 7 is re-themed as "GAM adapter threadpool saturation test" per the 2026-04-14 canonicalization — both test series run in parallel; the `server_default` audit rolls into the Spike 1 fix inventory.)
11. **Spike 8 — L5 go/no-go decision gate** (HARD): at L5a EXIT, commit `spike-decision.md` with: pass/fail summary per spike 1-7 (plus 4.25, 4.5, 5.5), `baseline-sync.json` comparison (if any L5 experiments were run on the spike branch), resolved status of the 9 open decisions (1-9), and the final go/no-go call. **Spike classification (canonical table above):** HARD = 1, 2, 3, 8 (4 spikes). Non-HARD/soft = 4, 4.25, 4.5, 5, 5.5, 6, 7 (**7 soft spikes**). **Go condition:** Spike 1 passes AND ≤ 2 of the 7 non-HARD spikes (4, 4.25, 4.5, 5, 5.5, 6, 7) fail. **No-go condition:** Spike 1 fails OR more than 2 of the 7 non-HARD spikes fail — narrow L5 scope or defer async to v2.1 (L0-L4 ships regardless).

**NO-GO release-tag naming rule (hard):** If Spike 8 returns NO-GO and L5+ is deferred to v2.1, the release tag is **`v1.99.0`** (or equivalent pre-v2 identifier), NOT `v2.0.0`. `v2.0.0` is reserved for the full async shipment per the user-stated v2.0 contract ("v2.0 addresses every possible issue including async"). A sync-only ship would violate the contract. This naming rule is a hard gate at L5a EXIT and is re-checked at L7 release time — mismatch blocks the tag. Structural guard is impractical (release-tag naming is a human decision), but the L5a Spike 8 checklist + L7 pre-release checklist both enforce.

---

## Apps loaded at runtime (4 before → 3 after)

The migration removes **one** of the four framework-level apps currently loaded by `src/app.py`. The MCP and A2A apps are AdCP-protocol surfaces and stay untouched.

| # | App | Where | Attached at | Disposition |
|---|---|---|---|---|
| 1 | **Root FastAPI `app`** | `src/app.py:64` | (is the root ASGI object) | **STAYS** — gains middleware + admin routers, loses the Flask mount |
| 2 | **`mcp_app` (Starlette from `mcp.http_app(path="/")`)** | `src/app.py:59` + `src/core/main.py:127` | `app.mount("/mcp", mcp_app)` at `src/app.py:72`; lifespan merged via `combine_lifespans` at `src/app.py:68` | **STAYS** — AdCP MCP protocol surface |
| 3 | **`a2a_app` (A2AStarletteApplication)** | `src/app.py:110` | **NOT mounted** — routes grafted onto root via `a2a_app.add_routes_to_app(app, ...)` at `src/app.py:118-123` | **STAYS** — AdCP A2A protocol surface |
| 4 | **`flask_admin_app` (Flask)** | `src/admin/app.py:107` via `create_app()` at `src/app.py:303` | `a2wsgi.WSGIMiddleware` wrapper, mounted at **both** `/admin` and `/` (root catch-all) via `_install_admin_mounts()` | **REMOVED Wave 3** — the whole point of the migration |

Plus orphan: `src/admin/server.py` (~103 LOC, standalone Flask runner via Waitress/Werkzeug/`asgiref.wsgi.WsgiToAsgi`) and `scripts/run_admin_ui.py` (38-line launcher) — not loaded by `src/app.py`, **removed in Wave 3 cleanup**.

**Subtleties a fresh reader MUST understand:**

- **A2A is grafted, not mounted.** `add_routes_to_app` at line 118 injects the SDK's Starlette `Route` objects directly into `app.router.routes`. So A2A handlers sit at the top level of the router tree, NOT inside a mounted sub-app. This is load-bearing for FastAPI middleware propagation (`UnifiedAuthMiddleware`, `CORSMiddleware`, `RestCompatMiddleware` all reach A2A handlers because they share the root scope). `_replace_routes()` at `src/app.py:192-215` also depends on this flat structure — it walks `app.routes` to swap the SDK's static agent-card routes for dynamic header-reading versions. **Any future refactor that mounts A2A as a sub-app would break middleware propagation AND `_replace_routes()`.**

- **MCP schedulers are lifespan-coupled.** `src/core/main.py:82-103` starts `delivery_webhook_scheduler` and `media_buy_status_scheduler` inside `lifespan_context`. That lifespan reaches uvicorn's event loop **only because of `combine_lifespans(app_lifespan, mcp_app.lifespan)` at `src/app.py:68`**. A future refactor that drops the MCP mount, rewires lifespans, or moves schedulers outside the MCP lifespan context will **silently stop the schedulers**. Not touched by v2.0 but document as a hard constraint and consider adding a startup-log assertion.

- **The `/a2a/` trailing-slash redirect shim at `src/app.py:127-135` exists ONLY because the Flask root catch-all (`app.mount("/", admin_wsgi)`) would otherwise eat the request.** When Flask is removed in Wave 3, this shim gets deleted — the causal chain is "no more Flask catch-all → no more route collision → no more shim needed."

- **`_install_admin_mounts()` is a lifespan hook at `src/app.py:25-45`** that re-filters and re-installs the `/admin` and `/` Flask mounts at the **tail** of `app.router.routes` on every startup. This ordering is load-bearing: landing routes (inserted at positions 0 and 1 via the `routes.insert(0, ...)` hack at lines 351-352) must win, A2A grafted routes must win, FastAPI-native REST routes must win, and the Flask catch-all must be last. The whole dance goes away in Wave 3 once Flask is gone.

- **Flask has its own internal WSGI middleware stack** at `src/admin/app.py:187-194` (`CustomProxyFix`, `FlyHeadersMiddleware`, werkzeug `ProxyFix`). These rewrite `Fly-Forwarded-Proto` → `X-Forwarded-Proto` and handle `X-Script-Name` for reverse-proxy deployments. **Wave 3 deletes Flask but the proxy-header handling must be reimplemented** via `uvicorn --proxy-headers --forwarded-allow-ips='*'` (already in the plan per deep-audit §R4). If this is missed, `request.url.scheme` returns `http` in production and OAuth redirect URIs fail with `redirect_uri_mismatch` on Fly.io.

---

## Migration conventions that differ from the rest of the codebase

These are the places where "copy what the rest of the repo does" is **wrong**. Admin is different.

- **L0-L4: Admin handlers use sync `def` with sync SQLAlchemy (Decision D2 bare `sessionmaker`).** FastAPI auto-runs sync handlers in AnyIO's threadpool. Each `with get_db_session()` block yields a fresh `Session` from a bare `sessionmaker` — **no `scoped_session` registry** (the D2 retirement happens at L0 in `src/core/database/database_session.py`). Thread reuse is safe precisely because there is no thread-local state to leak; each `with` block constructs a Session bound to a pooled connection and closes it on block exit, scoped to one request. DB access uses `with get_db_session() as session:` inside the handler. No `SessionDep`, no `Depends(get_session)`, no `AsyncSession`, no `run_in_threadpool` for DB. L4 introduces sync `SessionDep = Annotated[Session, Depends(get_session)]`; L5b re-aliases `SessionDep` to `AsyncSession` as a 1-file flip; L5c+ mechanically converts commit sites. See `execution-plan.md` Layer 0 for the canonical handler pattern.
- **Middleware order: Approximated BEFORE CSRF.** Counterintuitive relative to standard stacks where CSRF sits near the outside. Here, Approximated's external-domain redirect must fire before CSRF sees the form body. See blocker 5.
- **Templates use `{{ url_for('name', **params) }}` exclusively** — for admin routes AND static assets. No prefix variables, no Jinja globals holding URL strings, no `script_root`, no `admin_prefix`, no `static_prefix`. Every admin route has `name="admin_<blueprint>_<endpoint>"`; the static mount is `name="static"`. This is the FastAPI canonical pattern from the official docs, verified in `Jinja2Templates._setup_env_defaults` at `starlette/templating.py:118-129` (auto-registers `url_for` as a Jinja global that calls `request.url_for(...)` via `@pass_context`). `NoMatchFound` at render time on a missing name is caught pre-merge by `test_templates_url_for_resolves.py`.
- **`AdCPError` handler branches on `Accept`.** For admin HTML browser users, render `templates/error.html`. For JSON API callers, return JSON. Different from the plain JSON-only handler at `src/app.py:82-88` — do not copy that one.
- **L0-L3: Admin handlers use `with get_db_session() as session:` for DB access.** No `SessionDep`, no `Depends(get_session)`, no `AsyncSession`. The handler owns the session lifecycle via the sync context manager. Repositories are instantiated inside the `with` block. This is the same pattern as the existing Flask blueprints, just in FastAPI syntax. L4 introduces `SessionDep = Annotated[Session, Depends(get_session)]` (still sync); L5b re-aliases to `AsyncSession`.
- **`FLASK_SECRET_KEY` is hard-removed at L2** (same PR as Flask removal). v2.0 is a major release with breaking env-var changes (session-cookie rename `session` → `adcp_session` ships in the same release). Dev onboarding reads `SESSION_SECRET` only. No dual-read window. Failure mode: startup `KeyError: SESSION_SECRET` — the exact signal a dev needs to update their `.env`. Release notes call out the env-var rename alongside the cookie-rename gate announcement.

---

## First-order audit action items (quick reference)

Catalogued in `flask-to-fastapi-adcp-safety.md`; listed here so they are not lost:

- `tenant_management_api.py` route count in the main plan is **stale (19 → 6)** — re-verify before scoping.
- `gam_reporting_api.py` is **session-authed → Category 1**, not Category 2.
- `schemas.py` serves external AdCP JSON-Schema validators — preserve URLs **byte-for-byte**.
- `creatives.py` / `operations.py` construct outbound AdCP webhooks — **do not** use AdCP types as `response_model=`.
- Every admin router: `include_in_schema=False`.
- `/_internal/` must be added to the CSRF exempt list.
- Three new structural guards to add: `csrf_exempt_covers_adcp`, `approximated_path_gated`, `admin_excluded_from_openapi`.

---

## Branch and folder cleanup intent

- **Branch:** `feat/v2.0.0-flask-to-fastapi`. All migration work lives here.
- **Merge cadence:** one PR per layer (or per sub-PR where noted). L0 (spike + foundation), L1a-L1d (Flask parity — OAuth-not-yet / OAuth / low-risk HTML / high-risk + APIs), L2 (Flask removal + Dockerfile `--proxy-headers` + TrustedHostMiddleware + pre-commit hook rewrites), L3 (test harness modernization), L4 (sync SessionDep, DTO boundary, structlog, pydantic-settings extension, `render()` deletion, ContextManager refactor, `baseline-sync.json` capture at EXIT), L5a (spikes 1/2/3/4/4.25/4.5/5.5), L5b (SessionDep alias flip), L5c (3-router async pilot), L5d1-L5d5 (background_sync async rearchitect per D3 / adapter Path-B wrap / bulk router conversion / SSE deletion / mop-up), L5e (final async sweep), L6 (app.state singletons for `SimpleAppCache`, router subdir reorg, `logfire` instrumentation — flash.py NOT deleted here since it was never created per D8 #4), L7 (polish, allowlists → zero, perf baseline comparison, mypy strict ratcheting, `docs/ARCHITECTURE.md` refresh, v2.0.0 tag). See `execution-plan.md`.
- **Post-migration cleanup:** `.claude/notes/flask-to-fastapi/` is a planning-phase artifact. After v2.0.0 ships and stabilizes (~2 releases later), archive or delete this folder. Anything worth keeping long-term gets promoted to `docs/` or `CLAUDE.md` at the repo root.

---

## Post-L2 items (do NOT mix into L0-L2)

These are intentionally sequenced AFTER Flask removal (L0-L2). They are part of v2.0 but belong in L3-L7. If you find yourself wanting to do them during Flask removal, stop and file them as the appropriate layer's work item.

- **Test harness modernization (factories, `dependency_overrides`, `TestClient` patterns)** — L3
- **Sync `SessionDep` + DTO boundary + structlog + pydantic-settings extension + `app.state` singletons + `render()` deletion + ContextManager refactor** — L4 (FastAPI-native patterns, still sync; perf baseline `baseline-sync.json` captured at L4 EXIT)
- **REST routes ratchet to `Annotated[...]` form** — L4 (part of SessionDep + Annotated consistency)
- **`require_tenant_access` to check `is_active`** — L4 (small fix, breaking change OK on v2.0 branch)
- **Pre-L5 spike sequence (1, 2, 4, 4.25, 5, 5.5, 6, 7)** — L5a entry. **Spike 3 lands at L4 EXIT** (sync baseline capture; see L4 work item 13). **Spike 4.5 lands at L4 ENTRY** (gates Decision 7 ContextManager refactor; L4 work item 10 depends on pass). Neither gates L5a entry.
- **Async SQLAlchemy conversion** — L5b (SessionDep alias flip) → L5c (3-router pilot) → L5d1-L5d5 (background_sync async rearchitect per D3 / adapter Path-B wrap / bulk routers / SSE deletion / mop-up) → L5e (final sweep). See `async-pivot-checkpoint.md` (archived historical reference) for pre-D3 material.
- **Native refinements (`app.state` for `SimpleAppCache`, router subdir reorg, `logfire` instrumentation — NOT `opentelemetry-sdk`)** — L6. Note: `flash.py` wrapper module is NOT deleted in L6 because it was never created — per D8 #4, message state uses `MessagesDep` on `request.session["_messages"]` from L1a onward.
- **Allowlists → zero, perf baseline comparison vs `baseline-sync.json`, mypy strict ratcheting, `docs/ARCHITECTURE.md` refresh, v2.0.0 tag** — L7 (final cleanup and ship). `FLASK_SECRET_KEY` hard-removal moved to L2 per v2.0 breaking-change alignment with cookie rename.
- Drop nginx — post-v2.0 (needs battle-testing first)
- `Apx-Incoming-Host` IP allowlist — post-v2.0 (ops concern)
- `/_internal/` auth hardening — post-v2.0 (currently network-gated only)

---

## v2.0 Strategic Layering (8 layers, L0-L7)

> **Naming note:** Archived docs (`async-pivot-checkpoint.md` and all `async-audit/*.md`) were written referring to a "7-layer model". That terminology predates the L6→L6+L7 split on 2026-04-14 (polish/ship separated from native refinements). The canonical count is now 8 layers, L0 through L7 inclusive.

v2.0 is structured as 8 layers, each producing a working, testable system:

| Layer | Thesis | Scope | Gate |
|---|---|---|---|
| **L0** Spike & Foundation | Pure addition — Flask serves 100% | Foundation modules (templating, flash, deps, middleware stubs), codemod script (not executed), structural guards (stubs), feature flag + `X-Served-By` header | `make quality` green; Flask traffic share = 100% |
| **L1** Flask Parity (sync) | Feature-flag-gated byte-identical port | Middleware + public/core routers (L1a), auth + OIDC + session cutover (L1b), low-risk HTML (L1c), high-risk HTML + APIs (L1d). OAuth handlers may be `async def` per Authlib requirement (exception allowlist) | Parity golden fixtures match Flask within tolerance; Flask catch-all 0 traffic for 48h |
| **L2** Flask Removal | Delete Flask — single irreversible cut | Delete `src/admin/app.py`, Flask blueprints, Flask WSGI mount; `flask` out of `pyproject.toml`; `scripts/run_server.py` invokes `uvicorn.run(app, proxy_headers=True, forwarded_allow_ips='*', ...)` (canonical source of truth per migration.md §11.8 — Dockerfile/run_all_services.py/fly.toml all inherit); `TrustedHostMiddleware` added; pre-commit hooks (`check_route_conflicts.py` etc.) rewritten for FastAPI AST | `rg -w flask src/ | wc -l` = 0; v2.0.0-rc1 tag |
| **L3** Test Harness Modernization | Factories + `dependency_overrides` + `TestClient` become the norm | Consolidate factories in `tests/factories/`, adopt `app.dependency_overrides[get_db_session] = lambda: session` pattern, retire inline `session.add()` in tests, ratchet allowlist of pre-existing debt | All new integration tests use factories; ratcheting allowlist shrinks |
| **L4** Pattern Refinement (sync) | FastAPI-native idioms without async risk | Sync `SessionDep = Annotated[Session, Depends(get_session)]`, DTO boundary at repo layer, pydantic-settings extension, `structlog` wiring, `app.state` singletons (no async), `render()` wrapper **deleted** in favor of Jinja2Templates via dependency, ContextManager refactor (Decision 7), **`baseline-sync.json` captured at EXIT** for L5 comparison | All admin handlers use `SessionDep`; baseline file committed; ContextManager guard green |
| **L5** Async Conversion | `SessionDep` alias flip + mechanical await conversion of ~60 commit sites and ~200 scalars/execute sites | **L5a** spikes 1/2/3 (re-run vs baseline)/4/4.25/4.5/5.5. **L5b** one-line re-alias `SessionDep` to `AsyncSession`. **L5c** 3-router async pilot. **L5d** sub-PRs: **L5d1** `background_sync_service` async rearchitect — `asyncio.create_task` + checkpoint-per-GAM-page (D3 supersedes 2026-04-11 Option B sync-bridge), **L5d2** adapter Path-B `run_in_threadpool` wrap (Decision 1), **L5d3** bulk router conversion, **L5d4** SSE deletion (Decision 8), **L5d5** mop-up. **L5e** final sweep. | All admin handlers `async def`; perf within budget vs `baseline-sync.json`; zero `MissingGreenlet` in suite |
| **L6** Native Refinements | Post-async cleanup now safe | `app.state` singletons for `SimpleAppCache`, router subdir reorg, `logfire` instrumentation (**NOT** `opentelemetry-sdk`). (Note: `flash.py` deletion removed per D8 #4 — wrapper module was never created; `MessagesDep` from `src/admin/deps/messages.py` has been in use since L1a.) | All post-async cleanups landed; no Flask-era modules remaining |
| **L7** Polish & Ship | Allowlists → zero; release | Structural-guard allowlists at 0, perf comparison vs `baseline-sync.json` green, mypy strict ratcheting green, `docs/ARCHITECTURE.md` refreshed, `v2.0.0` tag (note: `FLASK_SECRET_KEY` dual-read hard-removal moved to L2) | `v2.0.0` released |

**Key insight:** `SessionDep` does not require async. **L4** introduces `SessionDep = Annotated[Session, Depends(get_session)]` with sync `Session`. **L5b** re-aliases it to `AsyncSession` — a 1-file change in `deps.py`. The rest of L5 is mechanical: `await` the ~60 commits and ~200 scalars/execute sites that the alias flip makes async at the type-checker layer. This separation dramatically reduces L5's risk and is the reason L5 is labelled "conversion", not "rewrite."

**AdCP safety:** None of the 8 layers changes any AdCP surface. MCP tool signatures, A2A protocol, REST endpoints, webhook payloads, and OpenAPI schema are all unchanged throughout.

### Wave ↔ Layer mapping

Legacy "Wave" section headings predate the 8-layer rename. Translation:

| Legacy Wave | Current Layer | Scope |
|-------------|---------------|-------|
| Wave 0 | L0 | Foundation + template codemod |
| Wave 1 | L1a + L1b | Middleware + public/core routers; auth + OIDC cutover |
| Wave 2 | L1c + L1d | Low-risk HTML routers; medium/high-risk + APIs |
| Wave 3 | L2 | Flask removal + cache migration + cleanup |
| Wave 4 | L3 + L4 + L5a-L5e | Test harness; sync refinement; async conversion |
| Wave 5 | L6 + L7 | Native refinements; polish & ship |

---

## v2.0 Timeline Summary

| Layer | Estimate (engineer-days) | Notes |
|-------|-------------------------:|-------|
| L0    | 5–7 (✅ COMPLETE)        | 33 tracked work items, 48 structural guards, golden fingerprints (5 Category-1 baselines), L0-00..L0-32 |
| L1a   | 3–4                      | Middleware stack + codemod + flag |
| L1b   | 4–5                      | OAuth + OIDC + session cutover |
| L1c   | 4–5                      | 8 low-risk HTML routers (parallelizable across ~3 engineers) |
| L1d   | 8–12                     | 14 medium/high-risk HTML routers + 4 JSON APIs (revised up) |
| L2    | 5–7                      | Flask removal — irreversible cut point; 48h zero-flask-traffic bake |
| L3    | 3–4                      | Test harness modernization (test-side only) |
| L4    | 6–8                      | SessionDep + DTOs + pydantic-settings + structlog + render() deletion + ContextManager refactor (revised up) |
| L5a   | 5–7                      | 10 technical + 1 decision gate (11 items); lazy-load audit is HARD GATE for L5b |
| L5b   | 1–2                      | SessionDep alias flip + engine refactor |
| L5c   | 3–5                      | 3-router async pilot + async test harness adoption |
| L5d1  | 2–3                      | `background_sync_service` async rearchitect (D3: `asyncio.create_task` + checkpoint-per-GAM-page) |
| L5d2  | 3–4                      | Adapter Path-B threadpool wrap |
| L5d3  | 8–12                     | Bulk router + repository async conversion (~300 repo methods, ~2400 LOC) |
| L5d4  | 1–2                      | SSE deletion |
| L5d5  | 2–4                      | Async mop-up of `_impl`/`tools.py`/`main.py` |
| L5e   | 3–4                      | Final async sweep + perf baseline vs L4 exit |
| L6    | 3–4                      | app.state cache, logfire, router subdir reorg (flash.py deletion removed — module never created per D8 #4) |
| L7    | 3–5                      | Allowlists → 0, mypy strict, docs refresh, v2.0.0 tag |
| **Total** | **72–104 engineer-days** | Sequential baseline (sum of per-row lows / highs; corrected 2026-04-16 — previous "72–110" stated upper was off by 6 days) |

**Calendar time:** 14–22 weeks with single-engineer sequencing + staging bakes (L1c 3d, L2 48h, L5b 48h, L7 1 week).

**Confidence:** Estimates revised upward per final verification audit. L5d3 is largest/most uncertain; high-end may extend to 15 days if repository method count exceeds ~300.

**Parallelization:** L0 foundation modules / L1c routers / L1d routers / L5a spikes each parallelize across ~3 engineers. Critical path single-threaded through L1a → L1b → L2 → L4 → L5b → L5c → L5d3 → L5e → L7.
