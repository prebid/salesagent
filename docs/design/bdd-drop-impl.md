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
- No loss of real (passing) coverage: every scenario `impl` uniquely passes today
  becomes EITHER a passing full BDD-wire scenario (fix the wire path so it carries
  the request faithfully) OR an honest `xfail` with a note ("not wired / not
  implemented in prod on wire — <reason>").
- Clean up transport-keyed xfail ledgers that assume `impl` is present.
- Keep the suite green (no new unexplained failures; xfails honest).

**Non-goals (explicitly out of scope)**
- **Do NOT delete the impl machinery.** `env.call_impl`, `Transport.IMPL`,
  `ImplDispatcher`, `synthesized_error_envelope` STAY — unit/integration tests
  call `call_impl` directly, and `call_via(IMPL)` remains a valid non-BDD path.
  Removing them is a separate later cleanup, not this ticket.
- **Do NOT create unit tests to "re-home" impl coverage.** Coverage lives in BDD,
  executed end-to-end on the wire. If a behavior can't be expressed as a full
  wire BDD scenario, it is either a prod gap (`xfail` with note) or genuinely
  unreachable on the wire (`xfail` with note, or removed if not a real wire
  behavior). No new unit/integration shims are written for the dropped `impl`
  cases. (BDD-wire *is* the integration coverage — see "Coverage philosophy".)
- Not fixing the underlying prod/wire-path gaps that newly surface (those become
  honest xfails here; the real fixes are independent tickets, e.g. `l9wn`,
  `egnl`, `j2qj`, `ihwl`).
- Not the test-DB/topology unification (`pwqw`).

## Coverage philosophy (owner-directed)

BDD scenarios execute the whole system over the wire — that is the integration
coverage. We do **not** add unit/integration tests to compensate for dropping
`impl`. Anything `impl` covered is re-expressed as a full BDD-wire scenario, or
marked `xfail` with a note stating exactly what is not wired/implemented in prod.
A green `impl` result that has no passing wire equivalent is, by definition, a
wire gap being masked — surface it (xfail+note) or fix the wire path; never hide
it in a unit test.

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

## Verified audit (re-run, outline-aware parser)

The first audit under-counted: its regex matched only bare `[impl]` ids and
skipped scenario-outline rows like `[impl-random-random]`. Re-run over
`/tmp/bdd_full.json` keying by `(scenario, example)`, transport = first token of
the trailing bracket:

- distinct (scenario, example) rows: **3079**
- **passing per transport: impl 402 / a2a 375 / mcp 377 / rest 358**
- **impl-only-passing (wire variant present but not passing): 2** —
  `test_context_echoed_in_sync_error_response` (UC-011) and a
  `test_delivery_account_boundary` sandbox natural-key row (UC-004).
- **impl-EXCLUSIVE passing (no wire variant at all): 32** — almost all UC-004 /
  UC-005 / UC-019 **request-validation** boundary/partition rows (attribution
  window, delivery account, date range, disclosure positions, identification mode,
  input/output format_ids, principal ownership, reporting dimensions, sampling
  method, status filter).
- impl-EXCLUSIVE xfailed: **375** (no coverage; just lose the impl variant).

**So `impl` uniquely contributes a PASS in ~34 rows** (32 + 2), not 1. Phase 2
enumerates all 34 individually.

**Root characterization:** they pass on `impl` because `impl` hands `_impl` the
full typed request, so request-level validation runs. On the wire the harness
paths **drop or transform the param** before validation (same class as the REST
update-body bug, `j2qj`), so the wire variant can't exercise it and was
deselected/xfailed. Fix = make the wire path carry the request faithfully (then it
passes as full BDD-wire) or `xfail`+note. Never a unit test.

## Design — categories of change

**A. Parametrization core.** Drop `Transport.IMPL`/`"impl"` from the default list
(~2456). Remove `_IMPL_ONLY` + its check (~2414-2419, ~2450-2454).

**B. Remove dispatch fallbacks (no fallback).** Every scenario is parametrized
across all wire transports, so `ctx["transport"]` is ALWAYS set — there is no
"unset" case. Delete the `call_impl` fallbacks outright (`_dispatch.py:~57-61`,
`when_request.py:~27-36, ~45`). BDD never calls `_impl`.

**C. xfail-ledger cleanup (verify each row before acting).** ~6 `is_impl` branches
+ ~12 `impl-…` substrings; cross-check each against the 34-row list so no real
pass is silently lost:
- `impl-…` substrings (UC-004 selective sets ~1206-1208/1230/1258-1262/1282-1283/1370):
  remove — no impl variant exists post-drop.
- `is_impl` branches that xfailed wire because impl was the pass baseline (UC-005
  disclosure ~606, UC-004 sampling ~1455, date-range ~1481): scenario now runs only
  on wire; keep the wire `xfail` (real wire gap) with correct `strict` + a note;
  drop the `not is_impl` guard.
- `is_impl` OR-conditions (~1535): simplify (impl branch dead).

**D. Handle the 34 impl-unique-passing rows — wire-or-xfail, NEVER unit tests.**
Per row:
1. **Prod implements it on the wire, the test wire-path just drops the param** →
   fix the wire request-building so the param survives → it becomes a passing full
   BDD-wire scenario. (Most of the 32 validation rows; overlaps `j2qj`.)
2. **Prod does not implement it on the wire** → `xfail` the wire variant with note
   "not wired/not implemented in prod on wire — <param/behavior>".
3. **Genuinely unreachable on the wire** (e.g. `test_principal_ownership_boundary`
   null/empty principal — auth resolves a real principal before any handler) →
   `xfail` with note "unreachable via wire — auth resolves a real principal before
   dispatch"; or remove if it asserts no real wire behavior. Phase 2 decides
   xfail-note vs removal per row. **No conversion to unit tests.**
- The 2 impl-only-passing: UC-011 context-echo and UC-004 sandbox natural-key →
  fix on wire, or `xfail`+note (the latter is account-resolution, `l9wn`).
- The 375 impl-exclusive-xfailed: lose the impl variant; already no coverage.

**E. Harness (KEEP, do not delete).** `Transport.IMPL`, `ImplDispatcher`,
`call_impl`, `synthesized_error_envelope` remain for unit/integration use. Only
their use *in the BDD default path* goes away.

## Why proceed (and why `l9wn` is orthogonal)

`impl` passing (402) is essentially matched by each wire transport
(a2a 375 / mcp 377 / rest 358); the unique-pass set is ~34, each of which is
either a fixable wire-path param-drop or a wire gap that *should* be a visible
`xfail`. A green `impl` with no passing wire equivalent is a masked wire gap.

The drop is **self-contained** — it does NOT require `l9wn`/`egnl`/`j2qj`/`ihwl`
first. Those are independent greening tasks that later turn specific xfail-notes
into passes. **`l9wn` is orthogonal**: the `@account` rows simply become wire
`xfail`+note on drop; `l9wn` graduates them whenever it lands.

## Sequencing

1. Parametrization core (A) + remove fallbacks (B).
2. xfail-ledger cleanup (C), cross-checked against the 34-row list.
3. Per-row disposition of the 34 (D): fix wire path → pass, else `xfail`+note.
4. Full wire-suite verification.

(`l9wn`/`egnl`/`ihwl`/`j2qj` run independently and convert specific xfail-notes
into passes; none gate the drop.)

## Verification

- **Baseline:** the pre-drop full run (`/tmp/bdd_full.json`: 1540 passed / 42
  failed / 7319 xfailed across impl+a2a+mcp+rest). Re-baseline as a stored
  artifact in Phase 2.
- **Gate:** after the drop, wire-only run must show **passing(a2a∪mcp∪rest) ≥
  pre-drop wire passing (≈ a2a 375 / mcp 377 / rest 358 union)**, **0 new
  failures** (only honest xfails), and every one of the 34 impl-unique-passing
  rows is accounted for (now passing on wire, or `xfail`+note).
- Run serial (`-n0`) on the agent-db (xdist deadlocks on a single DB).

## Risks

- **The 34 impl-unique-passing rows** must each be dispositioned (wire-pass or
  `xfail`+note) — a row silently dropped is lost coverage. Phase 2 lists all 34.
- **UC-019 / `principal_ownership` defensive rows** are unreachable on the wire;
  honest treatment is `xfail`+note (or removal), not a unit test.
- **Transport-asymmetric xfails** (UC-004/UC-005) flip from "impl passes" to
  "wire xfails": ensure `strict` is correct so they don't become silent xpasses.
- **`rest` lags `a2a`/`mcp`** (358 vs ~375): dropping impl doesn't cause this, but
  the wire-only gate will surface it — track separately, don't block the drop.
- conftest is large and churny — Phase 2 must re-pin line numbers at execution
  time, and Phase 3 agents should anchor on symbols/markers, not line numbers.

## Manifest scope (what Phase 2 enumerates)

One row per change site with `file:symbol/marker`, category (A–E), action, and the
slice/agent it belongs to. Dimensions:
- parametrization core; `_IMPL_ONLY`; dispatch fallbacks;
- each `is_impl` branch + each `impl-…` substring set;
- **the 34 impl-unique-passing rows, one row each**, with per-row disposition
  (fix-wire-path / `xfail`+note / remove) — the coverage-preservation core;
- the 375 impl-exclusive-xfailed rows (bulk: lose impl variant, no coverage);
- impl-only env (`MediaBuyAccountEnv`) usage sites.

No row prescribes a unit test. Independent greening tickets
(`l9wn`/`egnl`/`j2qj`/`ihwl`) are referenced where they would later convert an
`xfail`+note into a wire pass, but none gate the drop.
