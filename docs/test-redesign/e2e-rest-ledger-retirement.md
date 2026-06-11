# e2e_rest ledger retirement — transport-aware harness setup

**Status:** TODO / tracked design. Beads epic `salesagent-x0nl`.
**Live ledger:** [`tests/bdd/e2e_rest_known_failures.txt`](../../tests/bdd/e2e_rest_known_failures.txt) (293 nodeids, loaded by `tests/bdd/conftest.py` to `xfail(strict=False)`).

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
| creative formats | **the live creative agent** | capture the agent's real format set **once per session** (session fixture), expose to harness; scenarios select/reference from the captured set; formats the agent doesn't serve get registered in the agent's own registry. Never mint synthetic in-process formats. |
| products / properties / principals / tenant billing config | **server DB** | seed rows into the server DB (env session already binds to it; `set_billing_policy` already writes the tenant row) |
| adapter delivery numbers | **Mock adapter reading a `DeliverySimulationConfig` row from the server DB** | write the simulation-config row; the live server's Mock adapter reads it. Requires recovering the stranded `DeliverySimulationConfig` mechanism. |

Why this is the right direction (not just a CI workaround): `e2e_rest` is the
only transport that *cannot cheat* — the only way to set it up is through the
server's real configuration surfaces. Any setup that genuinely can't be
expressed that way is a true signal that the server lacks a configuration
mechanism it should have (e.g. fault injection for `set_adapter_error`), not a
"mock incompatibility" to be hidden in a nodeid list.

## Ledger breakdown by required mechanism (293 total)

| Mechanism | Test files | Count | % | Beads |
|-----------|-----------|------:|--:|-------|
| **Formats** — capture creative-agent set once/session, seed/reference | `test_uc005_discover_creative_formats` (115), `test_uc006_sync_creatives` (16), `test_get_products_inventory_profile` (6) | **137** | 47% | `salesagent-8kpo` |
| **Adapter delivery** — `DeliverySimulationConfig` DB row | `test_uc004_deliver_media_buy_metrics` (113) | **113** | 39% | `salesagent-asfb` |
| **Account / billing** — server DB seed (partly done) | `test_uc011_manage_accounts` (43) | **43** | 15% | `salesagent-gy01` (triage) |

## Execution order

1. `salesagent-asfb` — recover `DeliverySimulationConfig` mock-adapter mechanism (server-side delivery seeding). Unblocks 113.
2. `salesagent-8kpo` — formats: capture creative-agent set once/session, seed/reference. Unblocks 137 (the largest bucket).
3. `salesagent-n48i` — make env mock-setup methods transport-aware (depends on 1 + 2).
4. `salesagent-gy01` — per-scenario triage of all 293; confirm tractability **empirically via a harness run**, not from the armchair; migrate scenarios off the ledger as each mechanism lands; shrink the `.txt` to ~0.

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
