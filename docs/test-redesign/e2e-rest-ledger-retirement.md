# e2e_rest ledger retirement — transport-aware harness setup

**Status:** TODO / tracked design. Beads epic `salesagent-x0nl`.
**Live ledger:** [`tests/bdd/e2e_rest_known_failures.txt`](../../tests/bdd/e2e_rest_known_failures.txt) (308 nodeids, loaded by `tests/bdd/conftest.py` to `xfail(strict=False)`).

## Problem

The 308-entry ledger is a symptom of a leaky abstraction, not a property of the
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
| creative formats | **the live creative agent**, materialized into a persisted checked-in fixture | capture the agent's real format set into a persisted fixture, **refreshed only when formats change** (rare — they barely move between runs), not re-fetched per session; both the harness and the server's `ADCP_TESTING` path read formats from that one fixture, so in-process and e2e serve identical formats by construction. Formats the agent doesn't serve get registered in the agent's own registry. Never mint synthetic in-process formats. |
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

**Plan:** extend the persisted fixture to hold full `Format` definitions captured
from the live creative agent; refresh it via an explicit script/`make` target run
only when the agent's formats change (not per session). Point `_get_mock_formats()`
(in-process) and the harness seed at that same fixture. The live server in e2e
already calls the real agent, so all three see the same formats by construction.

## Ledger breakdown by required mechanism (308 total)

> Was 293; grew to **312** after the `origin/main` merge (2026-06-11, #1370) whose
> feature-file updates added/renamed 19 e2e_rest scenarios (uc004 +14, uc005 +4,
> uc011 +1). Each was verified to **pass on all 4 in-process transports** and fail
> only over real HTTP — same mock-visibility class, not a regression. Expected
> behavior: the ledger grows when main adds scenarios and shrinks as the harness
> mechanisms below land. #1420 then removed 4 stale param-renamed nodeids
> (1 formats, 3 uc004) → **308** live; the breakdown below is reconciled to that
> live count. On the harness branch (#1430) the ledger reaches 47.

| Mechanism | Test files | Count | % | Beads |
|-----------|-----------|------:|--:|-------|
| **Formats** — capture creative-agent set, seed/reference | `test_uc005_discover_creative_formats` (118), `test_uc006_sync_creatives` (16), `test_get_products_inventory_profile` (6) | **140** | 45% | `salesagent-8kpo` |
| **Adapter delivery** — `DeliverySimulationConfig` DB row | `test_uc004_deliver_media_buy_metrics` (124) | **124** | 40% | `salesagent-asfb` |
| **Account / billing** — server DB seed (partly done) | `test_uc011_manage_accounts` (44) | **44** | 14% | `salesagent-gy01` (triage) |

## Execution order

1. `salesagent-asfb` — recover `DeliverySimulationConfig` mock-adapter mechanism (server-side delivery seeding). Unblocks 113.
2. `salesagent-8kpo` — formats: capture creative-agent set once/session, seed/reference. Unblocks 137 (the largest bucket).
3. `salesagent-n48i` — make env mock-setup methods transport-aware (depends on 1 + 2).
4. `salesagent-gy01` — per-scenario triage of all 308; confirm tractability **empirically via a harness run**, not from the armchair; migrate scenarios off the ledger as each mechanism lands; shrink the `.txt` to ~0.

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
