# e2e_rest ledger retirement — transport-aware harness setup

**Status:** Wave 3 landed — ledger reduced from 312 to **47 genuine-gap nodeids**. Beads epic `salesagent-x0nl`.
**Live ledger:** [`tests/bdd/e2e_rest_known_failures.txt`](../../tests/bdd/e2e_rest_known_failures.txt) (47 nodeids, loaded by `tests/bdd/conftest.py` to `xfail(strict=False)`; pinned by `tests/unit/test_e2e_rest_ledger_state.py`).

## Wave 3 outcome (#1418) — read this first

Waves 1+2 landed the harness mechanisms (subdomain fix, `DeliverySimulationConfig`,
the 54-format reference fixture + `pick_reference_formats`, discovery seeding, and
the `E2EUnsupportedSetup` → xfail report hook). Wave 3 triaged the resulting
in-network run (`wave2_bdd.json`) and reduced the ledger from 312 to 47:

| Disposition | Count | Where it went |
|-------------|------:|---------------|
| **Graduated** (now passes in-network) | 163 | removed from ledger — these xpassed in the gate run |
| **Stale** (renamed by #1370 merge, absent from the run) | 4 | removed from ledger |
| **Env-owned** (`E2EUnsupportedSetup` raised during harness realization) | 98 | removed from ledger — the env declaration owns them; the conftest report hook (`pytest_runtest_makereport`) surfaces them as xfail with a declared reason. These are uc005/uc006 scenarios whose Given requests synthetic format IDs (`fmt_3`, `fmt_918`, …) not in the 54-format reference catalog. |
| **Genuine gaps** (kept, annotated by gap in the ledger) | 47 | real production / server-seed / harness-observability gaps — see the ledger's section comments. |

The 47 remaining are NOT format-injection cases; they are real gaps: uc004
invalid-input validation (16: some 422-wire-shape, some live-server-accepts),
uc006 account billing-state not server-seeded (12), uc011 account read-back not
server-seeded (6), get_products tenant-seed duplicate-key (6), uc004
webhook/log observability F-bucket (4), explicit inline-xfail prod gaps (2), and
one missing Then step definition.

**Correction to earlier claims in this doc** (kept below for design history, but
superseded here): (1) the live e2e server does **not** call the real creative
agent per request — under `ADCP_TESTING` it serves the **same checked-in
reference fixture** the in-process harness reads, so in-process and e2e formats
match by construction (this is exactly why the 98 synthetic-format scenarios are
unrealizable over e2e: the fixture catalog has no `fmt_3`). (2) uc006's
first-failure layer was **auth/account-seed**, not formats — the "Formats"
attribution for uc006 in the mechanism table below was wrong; uc006's residual
12 are account billing-state, not format injection.

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
| creative formats | **a persisted checked-in fixture** captured from the creative agent | both the harness and the server's `ADCP_TESTING` path read formats from that one fixture (the e2e server does **not** call the live agent per request under `ADCP_TESTING`), so in-process and e2e serve identical formats by construction. The fixture is refreshed only when formats change (rare), via an explicit `make` target, not re-fetched per session. Formats the agent doesn't serve get registered in the agent's own registry. Never mint synthetic in-process formats. |
| products / properties / principals / tenant billing config | **server DB** | seed rows into the server DB (env session already binds to it; `set_billing_policy` already writes the tenant row) |
| adapter delivery numbers | **Mock adapter reading a `DeliverySimulationConfig` row from the server DB** | write the simulation-config row; the live server's Mock adapter reads it. Requires recovering the stranded `DeliverySimulationConfig` mechanism. |

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
> only over real HTTP — same mock-visibility class, not a regression.

| Mechanism (as triaged statically pre-Wave-3) | Test files | Count | % | Beads |
|-----------|-----------|------:|--:|-------|
| **Formats** — capture creative-agent set, seed/reference | `test_uc005_discover_creative_formats` (119), `test_uc006_sync_creatives` (16, *misattributed — actually account-seed*), `test_get_products_inventory_profile` (6) | **141** | 45% | `salesagent-8kpo` |
| **Adapter delivery** — `DeliverySimulationConfig` DB row | `test_uc004_deliver_media_buy_metrics` (127) | **127** | 41% | `salesagent-asfb` |
| **Account / billing** — server DB seed (partly done) | `test_uc011_manage_accounts` (44) | **44** | 14% | `salesagent-gy01` (triage) |

## Execution order

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
