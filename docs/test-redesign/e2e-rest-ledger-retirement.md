# e2e_rest ledger retirement — transport-aware harness setup

**Status:** Harness landed on this branch (#1430) — Wave 3 (#1418) reduced the
ledger from 312 to 47 genuine-gap nodeids; post-Wave-3 graduations (REST 422
wire-shape, idempotent tenant seeding, webhook tag declarations, the
attribution campaign-interval boundary retired at the main merge, and the 12
uc006 account billing-state entries graduated by PR #1417's account-resolution
wiring) net of 3 uc002 creative-extension entries imported from #1417 brought
it to **21**; the #1430 item-4 roas/cpa retirement (Then steps written,
tag-declared production gap) brought it to **20**; #1430 items 1-3 graduated
the 6 uc011 read-back entries (`_db_scope_for` repoint + agent auth_token fix)
and the 2 uc002 ext-o/ext-p entries (auto-approval seeding) — all 8 xpassed
in-network (innet_050726_2030) — bringing it to **12**; the uc002 ext-q upload
entry graduated (fail_on_upload mock fidelity + catalog format +
`run_async_in_sync_context` format resolution) bringing it to **11**. Tracked
publicly as **#1423**; the in-network Docker CI runner that recovered e2e_rest
as the 5th BDD transport landed on main as **#1420**.
(Internal epic `salesagent-x0nl`; the per-mechanism sub-task ids below roll up
to #1423.)
**Live ledger:** [`tests/bdd/e2e_rest_known_failures.txt`](../../tests/bdd/e2e_rest_known_failures.txt) (11 nodeids, loaded by `tests/bdd/conftest.py` to `xfail(strict=False)`; pinned by `tests/unit/test_e2e_rest_ledger_state.py`).

## Wave 3 outcome (#1418) — read this first

Waves 1+2 landed the harness mechanisms (subdomain fix, `DeliverySimulationConfig`,
the 54-format reference fixture + `pick_reference_formats`, discovery seeding, and
the `E2EUnsupportedSetup` → xfail report hook). Wave 3 triaged the resulting
in-network run (`wave2_bdd.json`) and reduced the ledger from 312 to 47:

| Disposition | Count | Where it went |
|-------------|------:|---------------|
| **Graduated** (now passes in-network) | 163 | removed from ledger — these xpassed in the gate run |
| **Stale** (renamed by #1370 merge, absent from the run) | 4 | removed from ledger — main independently removed the same 4 param-renamed nodeids (1 formats, 3 uc004) in #1420, taking its copy of the ledger 312 → 308 before this branch merged |
| **Env-owned** (`E2EUnsupportedSetup` raised during harness realization) | 98 | removed from ledger — the env declaration owns them; the conftest report hook (`pytest_runtest_makereport`) surfaces them as xfail with a declared reason. These are uc005/uc006 scenarios whose Given requests synthetic format IDs (`fmt_3`, `fmt_918`, …) not in the 54-format reference catalog. |
| **Genuine gaps** (kept, annotated by gap in the ledger) | 47 | real production / server-seed / harness-observability gaps — see the ledger's section comments. |

The 47 remaining were NOT format-injection cases; they were real gaps: uc004
invalid-input validation (16: some 422-wire-shape, some live-server-accepts),
uc006 account billing-state not server-seeded (12), uc011 account read-back not
server-seeded (6), get_products tenant-seed duplicate-key (6), uc004
webhook/log observability F-bucket (4), explicit inline-xfail prod gaps (2), and
one missing Then step definition.

### Post-Wave-3 graduations (47 → 21, `salesagent-jdy1` + main/#1417 merges)

Each validated by an in-network BDD run with 0 failures:

- **M1 — REST 422 wire shape (6 uc004):** `parse_rest_error`'s `STATUS_TO_ERROR`
  map lacked 422, so a FastAPI request-validation envelope (`{"detail":[...]}`)
  surfaced as a plain `Exception` instead of the `AdCPError` the Then step
  expects. Mapped 422 → `AdCPValidationError`. Graduated.
- **M3 — idempotent tenant seed (6 get_products):** `given_tenant` seeded
  `TenantFactory(test_tenant)` non-idempotently into the shared server DB
  (`tenants_pkey` UniqueViolation on the 2nd e2e_rest scenario). Made it
  get-or-create. Graduated.
- **M4 — webhook observability (4 uc004):** the webhook retry/sequence scenarios
  assert on in-process surfaces (`env.mock['post']` call counts, CircuitBreaker
  state) with no Docker-HTTP equivalent. Declared impl-only **by tag**
  (`_UC004_E2E_WEBHOOK_INTERNAL_TAGS` in conftest) — env-owned xfail, off the
  nodeid ledger.
- **Main-merge graduation (1 uc004):** the attribution campaign-interval
  boundary (`interval=2, unit=campaign`). Upstream regenerated the scenario's
  expected cell `invalid` → `error "VALIDATION_ERROR" with suggestion`, the old
  `-invalid]` nodeid no longer collects, and the renamed scenario passes
  in-network on main (absent from main's #1420 ledger). Removed from the ledger
  and `EXPECTED_LEDGER` together.
- **#1417-merge graduation (12 uc006):** the account billing-state block
  (ACCOUNT_PAYMENT_REQUIRED / SETUP_REQUIRED / SUSPENDED / NOT_FOUND /
  AMBIGUOUS). PR #1417's account-resolution + canonical error-code wiring makes
  the live server raise them; all 12 xpassed in-network (innet_040726_0013).
- **#1417-merge import (+3 uc002):** creative extension scenarios newly wired
  by #1417 fail in-network (server-side creative state not observable/seeded
  over HTTP — confirmed innet_040726_0013); imported with the merge, same
  seeding family as exec-n48i.
- **#1430 item 4 — roas/cpa Then steps (1 uc004, 21 → 20):** the "missing Then
  step definition" entry. The three steps (roas, cost_per_acquisition,
  media_buy_count) are now defined in `uc004_delivery.py`; production computes
  none of roas / cost_per_acquisition / conversion_value, so the scenario is a
  **tag-declared strict xfail on ALL transports**
  (`T-UC-004-aggregated-roas-and-cpa` in conftest `_UC004_XFAIL_ADDITIONAL`) —
  off the e2e nodeid ledger; the production feature is ticketed separately.
- **#1430 items 1-2 — uc011 read-back (6 uc011, 20 → 14):** the wrong-DB class.
  `integration_db` repointed production's cached engine at an empty per-test DB
  while the env's factories wrote to the live server DB, so raw
  `get_db_session()` read-backs and TRANSPORT-BYPASS `_impl` Givens inside e2e
  scenarios read the wrong database. Closed structurally: every e2e-capable
  `_harness_env` branch now routes through `_db_scope_for` (integration_db
  in-process; `_production_db_pointed_at(e2e_config.postgres_url)` over e2e).
  The scoped-to-agent scenario additionally needed agent identities to carry
  `auth_token` (the live server 401'd tokenless agent syncs and the Given
  errors were swallowed — both fixed, plus a structural guard banning
  swallowed dispatch errors). All 6 xpassed in-network (innet_050726_2030).
- **#1430 item 3 — uc002 ext-o/ext-p auto-approval seeding (2 uc002, 14 → 12):**
  over e2e the live tenant defaulted to `human_review_required=True` and the
  real adapter requires approval for create_media_buy, so the scenarios landed
  on the PENDING-approval path (which silently skips missing creatives and
  emits VALIDATION_ERROR for format mismatch) instead of the auto path's
  CREATIVE_REJECTED they assert. The ext-o/ext-p Givens now seed auto-approval
  via the shared `_seed_auto_approval` helper. Both xpassed in-network
  (innet_050726_2030). The pending-path validation divergence itself is
  ticketed as production bug work.
- **Side effect worth auditing:** the `_db_scope_for` repoint also flipped 13
  uc004 webhook/circuit-breaker/sort_by e2e_rest scenarios (declared impl-only
  by tag, jdy1-M4) to xpass — their in-process webhook services now see the
  server DB. Tag-family retirement is tracked separately.
- **#1430 — uc002 ext-q upload mock fidelity (1 uc002, 12 → 11):** three
  stacked gaps. (a) The ext-q Given seeded the synthetic `display_300x250`
  format, absent from the 54-format reference catalog — the live server
  rejected it before the upload; the Given now seeds
  `display_300x250_image` (asset_id `banner_image`) and the in-process
  harness format resolver falls back to the same catalog, so both sides
  resolve identically by construction. (b) `MockAdServer.add_creative_assets`
  never read test-behavior injection; a DB-injected `fail_on_upload` flag now
  reproduces the upload failure server-side (shared `_raise_injected_failure`
  helper, deduplicating fail_on_create/fail_on_update). (c) A genuine
  production bug: `_get_format_spec_sync` wrapped the async registry in bare
  `asyncio.run()`, which ALWAYS fails inside the live server's async
  transports — every server-side format lookup errored and valid catalog
  formats were rejected as unknown. It now uses `run_async_in_sync_context`.
  Verified in-network: all 4 transports pass, wire error SERVICE_UNAVAILABLE.
- **Tenant-seed idempotency extended (0 ledger impact):** the newly wired
  uc005 format_id-roundtrip and uc018 list-creatives scenarios hit the same
  `tenants_pkey` shared-DB collision jdy1-M3 fixed for get_products; the
  get-or-create pattern is now the shared `tests/factories/core.py::
  get_or_create` helper used by all four seeding sites.

**Correction to earlier claims in this doc** (kept below for design history, but
superseded here): (1) the live e2e server does **not** call the real creative
agent per request — under `ADCP_TESTING` it serves the **same checked-in
reference fixture** the in-process harness reads, so in-process and e2e formats
match by construction (this is exactly why the 98 synthetic-format scenarios are
unrealizable over e2e: the fixture catalog has no `fmt_3`). (2) uc006's
first-failure layer was **auth/account-seed**, not formats — the "Formats"
attribution for uc006 in the mechanism table below was wrong; uc006's residual
12 are account billing-state, not format injection.

### The 98 env-owned entries are a settled end state — graduation is upstream

The 98 synthetic-format scenarios are **correctly declared env-owned** (the
`E2EUnsupportedSetup` raised during harness realization, surfaced as xfail with a
declared reason by the conftest report hook). This is their settled end state —
they are **not** bare nodeids and they are **not** to be graduated by local Given
rewrites.

A local rewrite (swapping `FormatFactory.build()` synthetic ids for
`pick_reference_formats` real-catalog ids in `tests/bdd/steps/`) is the wrong
move: each such scenario sources its request params **and** its Then assertions
from the **generated** feature-file example tables (`BR-UC-005-*.feature`), which
are overwritten on every regen and must never be edited locally. Rewriting the
Given to seed a real catalog format while the table still sends/asserts a
synthetic `fmt_3` is fragile and, for explicit-format-id scenarios, impossible.

The future graduation path is **upstream**, per the source-of-truth hierarchy:
regenerate the uc005 example tables with real reference-catalog format ids in the
requirements repo (`adcp-req`), then re-run the in-network gate — the scenarios
graduate the same way the 163 already did, and the env declaration stops firing
because the requested ids are now in the catalog. Do this in the requirements
repo, not here.

## Problem

The 293-entry ledger is a symptom of a leaky abstraction, not a property of the
scenarios. Transport awareness has bled up into the scenario/step-def layer.

The BDD suite dispatches every scenario through 7 transports. Four run
**in-process** (`impl`, `mcp`, `a2a`, `rest`) — test, harness, and server logic
share one Python process and one DB session. `e2e_rest` dispatches over **real
HTTP** through nginx to a **separate live server process**. The harness has no
handle on that server's internals.

The harness env mock-setup methods hard-assume in-process injection:

| Method | Location | What it patches |
|--------|----------|-----------------|
| `set_adapter_response` | `tests/harness/_mixins.py:89` | in-process `MagicMock` adapter return value |
| `set_registry_formats` | `tests/harness/creative_formats.py:67` | in-process `registry.list_all_formats` |
| `set_billing_policy` / `set_approval_mode` | `tests/harness/account_sync.py:71` | in-memory tenant overrides **and** DB tenant row (already half-correct) |

A separate server process sees none of the in-process patches, so the scenario
cannot pass over `e2e_rest`. That is not a code bug; the scenario's setup
contract is in-process-only by construction.

## Core invariant (the fix)

> The Gherkin scenario and the step definition are transport-agnostic. The
> harness env realizes setup **intent** through the single real source-of-truth
> surface for each concept. No in-process mock is the source of truth for any
> e2e-capable scenario.

```
Scenario (Gherkin)   — unchanged across all 7 transports
Step def             — unchanged: env.set_adapter_response("mb_001", impressions=5000)
Env method           — dispatches on transport:
    in-process:  patch the MagicMock                    (today's behavior)
    e2e:         realize intent on the real surface     (the missing branch)
```

The env is already the seam — it became transport-aware for **DB binding**
(`e2e_config` / `_database_url` rebind the engine to the server DB in e2e mode).
The mock-setup methods simply never got the same dispatch-on-transport treatment.

## One source of truth per concept

| Concept | Real surface | e2e realization |
|---------|-------------|-----------------|
| creative formats | **a persisted checked-in fixture** captured from the creative agent | both the harness and the server's `ADCP_TESTING` path read formats from that one fixture (the e2e server does **not** call the live agent per request under `ADCP_TESTING`), so in-process and e2e serve identical formats by construction. The fixture is refreshed only when formats change (rare — they barely move between runs), via an explicit `make` target, not re-fetched per session. Formats the agent doesn't serve get registered in the agent's own registry. Never mint synthetic in-process formats. |
| products / properties / principals / tenant billing config | **server DB** | seed rows into the server DB (env session already binds to it; `set_billing_policy` already writes the tenant row) |
| adapter delivery numbers | **Mock adapter reading a `DeliverySimulationConfig` row from the server DB** | write the simulation-config row; the live server's Mock adapter reads it. Requires recovering the stranded `DeliverySimulationConfig` mechanism. |
| a pinned clock (`today is "…"`) | **seeded flight windows relative to the server's real clock** | the live server classifies lifecycle status with `datetime.now()`, and the spec offers no wire-level clock control — AdCP 3.1.1 **deprecates** `X-Mock-Time` ("Sellers MUST NOT alter behavior based on these headers", `media-buy/advanced-topics/sandbox.mdx`), so a server-side time surface would be built against the spec. Instead, translate every seeded flight window by `(real today − pinned today)` before dispatch: status resolution is pure date comparison, so a uniform shift preserves every phase/boundary relationship the scenario asserts. Implemented for UC-019 in `_shift_seeded_windows_to_real_clock` (`tests/bdd/steps/domain/uc019_query_media_buys.py`). |

Why this is the right direction (not just a CI workaround): `e2e_rest` is the
only transport that *cannot cheat* — the only way to set it up is through the
server's real configuration surfaces. Any setup that genuinely can't be
expressed that way is a true signal that the server lacks a configuration
mechanism it should have (e.g. fault injection for `set_adapter_error`), not a
"mock incompatibility" to be hidden in a nodeid list.

## Formats: current implementation — keep what works, replace what drifts

Two caches already exist. Keep both:

- **Persisted, checked-in fixture** — `src/core/format_cache.py` →
  `tests/fixtures/creative_formats/reference_formats.json`. Stated design
  principles: "tests never depend on external infrastructure"; "cache is updated
  periodically but not required for operation." Right idea, persisted and
  offline. Caveat: today it is **shallow** — only `format_id_string -> agent_url`,
  used to upgrade legacy string IDs. It does not hold full `Format` definitions.
- **In-memory TTL cache** — `creative_agent_registry.CachedFormats`
  (`creative_agent_registry.py:243`), 1-hour TTL of full `Format` objects keyed
  by agent URL. Fine as a runtime cache.

The piece that **does** drift: `_get_mock_formats()`
(`creative_agent_registry.py:208`) — a hardcoded list of 11 `Format` objects
returned whenever `ADCP_TESTING=true`. Its docstring claims it "match[es] what
the real creative agent returns," but nothing enforces that. It is the synthetic
in-process source that `e2e_rest` (hitting the real agent) diverges from.

**Plan (landed in Waves 1+2):** the persisted fixture now holds full `Format`
definitions (54 of them) captured from the creative agent; it is refreshed via an
explicit `make` target only when the agent's formats change (not per session).
Both the in-process harness and the live server's `ADCP_TESTING` path read that
same fixture — the e2e server does **not** call the live agent per request — so
in-process and e2e see identical formats by construction. `pick_reference_formats`
(in `tests/factories/format.py`) selects from this catalog so a scenario's format
is guaranteed to exist on the live server too.

## Ledger breakdown by required mechanism (original 312 — design history)

> The original static breakdown is kept for history. Wave 3's empirical run
> superseded it (see "Wave 3 outcome" above). Note the **correction**: uc006's
> 16 entries were attributed to **Formats** here, but their first-failure layer
> was auth/account-seed — uc006's residual 12 are account billing-state, not
> format injection. uc005 (formats) graduated/became env-owned; uc004 (adapter)
> mostly graduated once `DeliverySimulationConfig` + the subdomain fix landed.

> Was 293; grew to **312** after the `origin/main` merge (2026-06-11, #1370) whose
> feature-file updates added/renamed 19 e2e_rest scenarios (uc004 +14, uc005 +4,
> uc011 +1). Each was verified to **pass on all 4 in-process transports** and fail
> only over real HTTP — same mock-visibility class, not a regression. Expected
> behavior: the ledger grows when main adds scenarios and shrinks as the harness
> mechanisms land. On main, #1420 removed 4 stale param-renamed nodeids
> (1 formats, 3 uc004) → 308 live there; on this branch the same 4 were dropped
> as "Stale" in Wave 3's triage, which superseded the static table below (kept
> at the 312 snapshot).

| Mechanism (as triaged statically pre-Wave-3) | Test files | Count | % | Beads |
|-----------|-----------|------:|--:|-------|
| **Formats** — capture creative-agent set, seed/reference | `test_uc005_discover_creative_formats` (119), `test_uc006_sync_creatives` (16, *misattributed — actually account-seed*), `test_get_products_inventory_profile` (6) | **141** | 45% | `salesagent-8kpo` |
| **Adapter delivery** — `DeliverySimulationConfig` DB row | `test_uc004_deliver_media_buy_metrics` (127) | **127** | 41% | `salesagent-asfb` |
| **Account / billing** — server DB seed (partly done) | `test_uc011_manage_accounts` (44) | **44** | 14% | `salesagent-gy01` (triage) |

## Execution order

> Sub-task ids are internal beads tracked under #1423 (above); the public issue
> is the resolvable anchor for outside readers.

1. `salesagent-asfb` — recover `DeliverySimulationConfig` mock-adapter mechanism (server-side delivery seeding). Unblocks 113.
2. `salesagent-8kpo` — formats: capture creative-agent set once/session, seed/reference. Unblocks 137 (the largest bucket).
3. `salesagent-n48i` — make env mock-setup methods transport-aware (depends on 1 + 2).
4. `salesagent-gy01` — per-scenario triage of all 312; confirm tractability **empirically via a harness run**, not from the armchair; migrate scenarios off the ledger as each mechanism lands; shrink the `.txt` to ~0.

## Honest caveats

- **Nothing is cleanly "fix it now" without these mechanisms.** The ledger
  entries bundle setup; a scenario is only e2e-tractable if *all* its setup maps
  to a real surface. The two dominant buckets (formats 137, adapter 113) are both
  blocked on a mechanism that does not exist on `main` yet.
- **uc011 (43) is partly DB-backed already** (`set_billing_policy` writes the
  tenant row), yet all 43 are still in the ledger — they touch something else
  in-process. Requires per-scenario triage to confirm what; do **not** assume
  tractable.
- A few setups have no DB/server representation at all (`set_adapter_error` —
  "make the adapter raise this exact exception"). Those need a test-mode
  fault-injection control on the server (gated behind `ADCP_TESTING`), or an
  explicit harness-level declaration that the scenario is impl-only — declared
  **in the env**, not as a nodeid in a text file.
