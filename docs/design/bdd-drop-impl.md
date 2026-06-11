# Design: Drop `impl` from the BDD default transport set

> Phase 1 (DESIGN) of epic `salesagent-5yst`. Companion to
> [bdd-harness-transport-boundary.md](bdd-harness-transport-boundary.md)
> (decision **D4**). Phase 2 = MANIFEST (enumerate every change site); Phase 3 =
> multi-agent IMPLEMENTATION.

## Summary

BDD scenarios are parametrized across `impl | a2a | mcp | rest` (plus optional
`e2e_rest`). `impl` calls `_impl` directly and therefore cannot exercise the
transport boundary (envelope status, error envelopes, account resolution,
identity/context) — so it cannot validate AdCP wire conformance, and the audit
(`xqx7`) shows it provides **~zero unique passing coverage**. This design removes
`impl` from the BDD **default** parametrization so BDD asserts conformance on the
wire transports (`a2a`/`mcp`/`rest`), and re-homes the small amount of genuinely
impl-only coverage.

## Goals / Non-goals

**Goals**
- BDD default parametrization = `[a2a, mcp, rest]` (E2E_REST conditional unchanged).
- No loss of real (passing) coverage: graduate / re-home what only `impl` covered.
- Clean up transport-keyed xfail ledgers that assume `impl` is present.
- Keep the suite green (no new unexplained failures; xfails honest).

**Non-goals (explicitly out of scope)**
- **Do NOT delete the impl machinery.** `env.call_impl`, `Transport.IMPL`,
  `ImplDispatcher`, `synthesized_error_envelope` STAY — unit/integration tests
  call `call_impl` directly, and `call_via(IMPL)` remains a valid non-BDD path.
  Removing them is a separate later cleanup, not this ticket.
- Not fixing production gaps that the wire transports newly expose (those become
  honest xfails here; real fixes are their own tickets, e.g. `l9wn`, `egnl`).
- Not the test-DB/topology unification (`pwqw`).

## Current machinery (verified)

- **Parametrizer:** `tests/bdd/conftest.py::pytest_generate_tests` (~2426-2463).
  Default list (~2456): `[Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST]`,
  ids `["impl","a2a","mcp","rest"]`; appends `e2e_rest` when `BDD_E2E_ENABLED=true`.
- **No `@impl` Gherkin tags exist.** Transport-specific scenarios use `@a2a/@mcp/@rest`
  (skip parametrization, ~2442). Impl-exclusivity is pure Python:
  - `_IMPL_ONLY = {("UC-002","account")}` (~2414-2419, applied ~2450-2454): matching
    scenarios skip parametrization → run impl-only.
  - `_harness_env` branches that wire an impl-only env (e.g. `MediaBuyAccountEnv`,
    `tests/harness/media_buy_account.py` — only `call_impl`, calls `resolve_account`
    directly; conftest UC-002 `@account` branch ~2606).
- **Transport-keyed xfail ledgers** in conftest read `is_impl/is_rest/is_mcp/is_e2e_rest`
  and match `impl-…` nodeid substrings:
  - `is_impl` def + branches: ~376, ~606 (UC-005 disclosure), ~1455 (UC-004 sampling),
    ~1481 (UC-004 date-range), ~1535 (UC-004 account), ~1810-1827 (UC-019 boundary-principal).
  - `impl-…` substrings in selective sets: ~1206-1208, ~1230, ~1258-1262, ~1282-1283, ~1370.
- **Dispatch fallbacks to impl** when `ctx["transport"]` is unset:
  `tests/bdd/steps/generic/_dispatch.py` (~57-61), `…/when_request.py` (~27-36, ~45).
- **Impl machinery (KEEP):** `Transport.IMPL` (`tests/harness/transport.py:26`),
  `ImplDispatcher` + `DISPATCHERS[IMPL]` (`tests/harness/dispatchers.py:87-108,188`),
  `synthesized_error_envelope` (impl-only error view).

(Phase 2 re-pins exact line numbers; conftest shifts.)

## Design — categories of change

**A. Parametrization core.** Drop `Transport.IMPL`/`"impl"` from the default list
(~2456). Remove `_IMPL_ONLY` + its check (~2414-2419, ~2450-2454) — its only member
(UC-002 `@account`) is handled by category D.

**B. Dispatch fallbacks.** `dispatch_request` / `when_request` currently fall back
to `call_impl` when no transport is set. After the change every BDD scenario is
wire-parametrized, so the fallback should become an explicit error ("BDD dispatch
requires a wire transport") rather than silently calling `_impl`. (Keeps tests
honest; surfaces any scenario that lost its parametrization.)

**C. xfail-ledger cleanup.** For each `is_impl` branch and `impl-…` substring:
- If it xfailed wire transports *because impl was the pass baseline* (e.g. UC-005
  disclosure ~606, UC-004 sampling ~1455, date-range ~1481): the scenario now runs
  only on wire → keep/adjust the wire xfail (these are real production gaps); drop
  the `not is_impl` guard. Make `strict` correct (the case truly fails on wire).
- If `impl-…` substrings only selected the impl variant for xfail: remove them
  (no impl variant exists anymore).
- `is_impl`-containing OR conditions (e.g. ~1535 `(is_impl or is_a2a)`): simplify
  (impl branch is dead).

**D. Re-home impl-only coverage.** (The gating work — preserve coverage.)
- **UC-002 `@account` (~23 rows, impl-only via `MediaBuyAccountEnv`)**: account
  resolution must run on the wire. Gated on **`l9wn`** (wire account resolution:
  wrappers accept `account`, harness un-strips + enriches). Until `l9wn`, either
  keep these on `impl` via an explicit `@impl-only`-style carve-out OR xfail their
  wire variants — pick one in Phase 2 (prefer: land `l9wn` first).
- **UC-019 boundary-principal (null/empty/ghost principal_id, ~1810-1827)**:
  unreachable via HTTP (auth middleware resolves a real principal before `_impl`).
  These are genuine `_impl`-level defensive tests → **re-home to unit/integration
  tests** that call `_impl`/`_update_impl` directly; remove from BDD.
- **`uc002_nfr.py` steps reading `result.status` / `result.response` internals
  (~339-349, ~430-451)**: assert on the wire envelope/payload instead, or move the
  internal-state assertions to unit tests.
- **`given_*_not_found` preconditions calling `env.call_impl(account_ref=…)`
  (`uc002_create_media_buy.py:79-98`, `_account_resolution.py:75-81`)**: replace
  with a transport-independent precondition (canonical `resolve_account` via UoW,
  or factory/DB assertion) so they don't depend on an impl dispatch. (Overlaps
  `rkb9`/`l9wn`.)
- **The 1 impl-only-*passing* scenario** `test_context_echoed_in_sync_error_response`
  (UC-011): graduate its `a2a/mcp/rest` variants (currently xfailed).
- **99 impl-exclusive scenarios (all xfailed)**: give them wire variants as their
  UC harness gets wired; no passing coverage to preserve.

**E. Harness (KEEP, do not delete).** `Transport.IMPL`, `ImplDispatcher`,
`call_impl`, `synthesized_error_envelope` remain for unit/integration use. Only
their use *in the BDD default path* goes away.

## Why proceed despite the "keep impl" argument

One investigation recommended keeping `impl` (account scenarios lack wire
wrappers; transport-asymmetric xfails use impl as the pass baseline). Rebuttal,
grounded in the audit: those are **test-infrastructure artifacts, not coverage**.
`impl` passing count (118) is matched by the wire transports (a2a 117 / mcp 119),
and only **1** scenario passes impl-only. The "impl baseline for xfail detection"
is exactly the by-construction confusion D4 removes: a scenario that "passes only
on impl" is asserting boundary behavior the wire doesn't yet implement — that
should be an honest wire xfail or a real fix (`l9wn`/`egnl`), not a green impl
result masking a wire gap. The account-wrapper gap is real and is `l9wn`, which is
sequenced first.

## Sequencing

1. **`l9wn`** (wire account resolution) lands first — unblocks UC-002 `@account`
   re-homing (D) and removes the biggest impl-exclusive cluster.
2. Re-home UC-019 defensive + `uc002_nfr` internal-state steps to unit tests (D).
3. Parametrization core (A) + dispatch fallback (B).
4. xfail-ledger cleanup (C) + graduate the UC-011 scenario.
5. Full wire-suite verification.

(`egnl`/`ihwl`/`j2qj` greening can proceed in parallel; they make more wire
scenarios pass but are not blockers for the drop.)

## Verification

- **Baseline:** the pre-drop full run (`/tmp/bdd_full.json`: 1540 passed / 42
  failed / 7319 xfailed across impl+a2a+mcp+rest). Re-baseline as a stored
  artifact in Phase 2.
- **Gate:** after the drop, wire-only run must show **passing(a2a∪mcp∪rest) ≥
  pre-drop wire passing**, **0 new failures** (only honest xfails), and the
  re-homed unit tests cover what `impl` uniquely asserted.
- Run serial (`-n0`) on the agent-db (xdist deadlocks on a single DB).

## Risks

- **UC-019 defensive tests** lose their only execution path if re-homing is
  skipped — must land the unit tests in the same change.
- **Transport-asymmetric xfails** (UC-004/UC-005) flip from "impl passes" to
  "wire xfails": ensure `strict` is correct so they don't become silent xpasses.
- **`rest` lags `a2a`/`mcp`** (97 vs ~117): dropping impl doesn't cause this, but
  the wire-only gate will surface it — track separately, don't block the drop.
- conftest is large and churny — Phase 2 must re-pin line numbers at execution
  time, and Phase 3 agents should anchor on symbols/markers, not line numbers.

## Manifest scope (what Phase 2 enumerates)

One row per change site with `file:symbol/marker`, category (A–E), action, the
slice/agent it belongs to, and its dependency (e.g. "after `l9wn`"). Dimensions:
parametrization core; `_IMPL_ONLY`; dispatch fallbacks; each `is_impl` branch;
each `impl-…` substring set; each impl-only env + its scenarios; each re-home
target (UC-002 account, UC-019 defensive, `uc002_nfr` internals, `given_*_not_found`);
the UC-011 graduation; the 99 impl-exclusive scenarios (per-UC).
