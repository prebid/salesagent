# Manifest: Drop `impl` from BDD — change sites & per-row disposition

> Phase 2 of epic `salesagent-5yst`. Design: [bdd-drop-impl.md](bdd-drop-impl.md).
> Line numbers are hints — **anchor on symbols/markers** (conftest churns).
> Candidate dispositions for the 34 coverage rows are **hypotheses to confirm by
> running the wire variant** in Phase 3, not settled conclusions.

## Slices (Phase 3 units)

| Slice | Scope | Depends on |
|-------|-------|-----------|
| **S0 core** | C1–C4 parametrization + fallback removal | — |
| **S1 ledger** | L1–L6 conftest xfail-ledger cleanup | S0 |
| **S2 uc004** | 25 UC-004 rows disposition | S0,S1 |
| **S3 uc005** | 8 UC-005 rows disposition (+ MCP prod gap) | S0,S1 |
| **S4 uc011** | 1 UC-011 row disposition | S0,S1 |
| **S5 reconcile** | full wire-suite run; bulk 375; verify gate | S2,S3,S4 |

## Section 1 — Core change sites (S0, deterministic)

| ID | File · anchor | Action |
|----|---------------|--------|
| C1 | `tests/bdd/conftest.py::pytest_generate_tests` (~2456) | Drop `Transport.IMPL` + `"impl"` from the default `transports`/`ids` lists → `[A2A, MCP, REST]` / `["a2a","mcp","rest"]`. Leave the `BDD_E2E_ENABLED` append unchanged. |
| C2 | `tests/bdd/conftest.py` `_IMPL_ONLY` (~2414-2419) + its check (~2450-2454) | Remove the set and the early-`return`. (Its only member, UC-002 `@account`, is handled by becoming wire-parametrized → see S2 note / bulk.) |
| C3 | `tests/bdd/steps/generic/_dispatch.py::dispatch_request` (~57-61) | Delete the `else: env.call_impl(**kwargs)` fallback. `ctx["transport"]` is always set post-C1; if absent, raise (BDD must dispatch on a wire transport). |
| C4 | `tests/bdd/steps/generic/when_request.py::_call`/`_call_via` (~27-36, ~45) | Delete the `call_impl` fallback and the "unrecognized → `Transport.IMPL`" default mapping; require an explicit wire transport. |

**Keep (do NOT touch):** `Transport.IMPL` enum, `ImplDispatcher`, `DISPATCHERS[IMPL]`,
`env.call_impl`, `synthesized_error_envelope` — used by unit/integration tests.

## Section 2 — Ledger cleanup (S1)

Each entry cross-checked against the 34-row list so no real pass is dropped.

| ID | File · anchor | Action |
|----|---------------|--------|
| L1 | conftest `is_impl` def (~376) | Keep (harmless dead var) or remove; no behavior change. |
| L2 | conftest UC-004 selective sets with `impl-…` substrings (`_UC004_*` ~1206-1208, ~1230, ~1258-1262, ~1282-1283, ~1370) | Remove the `impl-…` substrings (no impl variant post-C1). Keep the wire (`a2a-/mcp-/rest-`) substrings. |
| L3 | conftest UC-005 disclosure `is_impl` branch (~606) | Drop the `not is_impl` guard; keep the wire xfail (S3 confirms gap) with a note + correct `strict`. |
| L4 | conftest UC-004 sampling (~1455) + date-range (~1481) `not is_impl` branches | Drop `not is_impl` guard; reconcile with S2 dispositions (sampling = real spec gap xfail; date-range = candidate stale-xfail → verify). |
| L5 | conftest UC-004 account OR-condition (~1535 `(is_impl or is_a2a)`) | Simplify (impl branch dead). |
| L6 | conftest UC-019/principal `is_impl` branch (~1810-1827) | Reconcile with S2 `principal_ownership` row (UNREACHABLE → xfail+note or remove). |

## Section 3 — The 34 coverage rows (S2/S3/S4)

Disposition codes: **FIX-WIRE-TEST** (harness drops param → carry it), **FIX-WIRE-PROD**
(prod wrapper missing param → add it), **STALE-XFAIL** (validation is shared-schema →
should already pass on wire; remove xfail — confirm by running), **XFAIL+NOTE** (real
prod/spec gap), **UNREACHABLE** (no wire equivalent → xfail+note or remove).
**Every row: Phase 3 runs the wire variant and records the actual outcome before finalizing.**

### S2 — UC-004 `get_media_buy_delivery` (25 rows)

Wire request building: `tests/harness/delivery_poll.py::build_rest_body` (`_BODY_FIELDS`) +
`tests/harness/_mixins.py::call_impl`; prod wrappers `src/core/tools/media_buy_delivery.py`
(MCP ~537-549, A2A `_raw` ~598-609), `_impl` ~90-126.

| Param group (rows) | Candidate disposition | Action / note |
|--------------------|----------------------|---------------|
| attribution_window: interval=0 / unit∉enum / model∉enum (3, invalid) | STALE-XFAIL **or** XFAIL+NOTE — **verify** | Param IS in wrappers + `_BODY_FIELDS`. If schema rejects on wire → pass, remove xfail. If not (adcp Duration "interval=1 when campaign" is description-only, no validator) → XFAIL+NOTE "no validator in adcp Duration". |
| delivery_account: account_id+exists / brand+operator / sandbox(ONLY) / partition explicit+natural (5, valid) | XFAIL+NOTE (ties to `l9wn`) | Param on wrapper signature but resolved via `enrich_identity_with_account` at boundary, not `_impl`; account-aware delivery not wired. Note: "account accepted at wire; account-aware delivery resolution not wired (`l9wn`)". |
| delivery_date_range: start>end / start==end (2, invalid) | STALE-XFAIL — **verify** | `_impl` validates `start_dt >= end_dt` (media_buy_delivery.py ~125-126); param carried. Likely passes on wire → remove stale xfail. Confirm. |
| identification_mode: media_buy_ids / buyer_refs / both (3) | media_buy_ids+both = should-pass; buyer_refs = UNREACHABLE | `buyer_refs` removed in adcp 3.12 — not on any wrapper. Note: "buyer_refs not in AdCP 3.12; unreachable on wire". media_buy_ids/both carried → verify pass. |
| principal_ownership: principal≠owner (1, invalid) | UNREACHABLE | Identity comes from transport auth, not a request field; "different principal" has no wire form without auth mocking. XFAIL+NOTE "ownership is authz on resolved identity, not a request param" (or remove). Reconcile with L6. |
| reporting_dimensions: geo w/o geo_level / limit<0 / limit=0 (3, invalid) | STALE-XFAIL — **verify** | Param in wrappers + `_BODY_FIELDS`; constraints in adcp `ReportingDimensions` schema → should reject on wire. Confirm → remove xfail; else XFAIL+NOTE. |
| sampling_method: unknown∉enum (1, invalid) | XFAIL+NOTE (spec gap) | `sampling_method` not a field in GetMediaBuyDeliveryRequest (adcp 3.1) and absent from `_BODY_FIELDS`/wrappers. Note: "sampling_method not in AdCP GetMediaBuyDeliveryRequest; requires spec extension". |
| status_filter: [] empty / [active,paused] / all 7 / partition singles+arrays (7) | STALE-XFAIL (valid) + verify (empty) | Param carried (wrapper + `_BODY_FIELDS`). Valid enum/array rows → should pass; empty `[]` → schema `minItems` should reject on wire. Confirm; selective per-transport gaps → XFAIL+NOTE. |

### S3 — UC-005 `list_creative_formats` (8 rows)

Wire building: `tests/harness/creative_formats.py` + `when_request.py` partition helpers;
prod MCP wrapper `src/core/tools/creative_formats.py` (~486-499, build ~119-133),
REST body `src/routes/api_v1.py::ListCreativeFormatsBody` (~127-130); request model
`src/core/schemas/creative.py:514` (adcp `minItems=1`).

| Param (rows) | Candidate disposition | Action / note |
|--------------|----------------------|---------------|
| disclosure_positions: 8 valid + no-support + empty + single + partition all/no-match (6) | **FIX-WIRE-PROD** (MCP) + verify (REST/A2A) | **Prod gap:** `disclosure_positions` (and `disclosure_persistence`) MISSING from MCP wrapper signature + build (`creative_formats.py` ~498-499, ~132, pass at ~150). Add them. REST (`ListCreativeFormatsBody`) + A2A carry it; verify pass. Empty `[]` → `minItems` rejects. |
| output_format_ids: empty `[]` (1, invalid) | STALE-XFAIL — **verify** | In MCP wrapper (~488) + REST body (~129); `minItems=1` in model. Should reject on wire. REST historically drops body params (conftest ~101-102) → if so, FIX-WIRE-TEST/PROD on REST body building (overlaps `j2qj`). |
| input_format_ids: empty `[]` (1, invalid) | STALE-XFAIL — **verify** | Same as output_format_ids (MCP ~489, REST body ~130). |

### S4 — UC-011 (1 row)

| Row | Candidate disposition | Action / note |
|-----|----------------------|---------------|
| `test_context_echoed_in_sync_error_response` (impl pass; a2a/mcp/rest xfailed) | **verify** → FIX-WIRE or XFAIL+NOTE | `context` is a protocol-envelope field (`core/protocol-envelope.json`); echo on a *sync error* response is wire/envelope behavior. Run wire: if wrappers echo `context` on error → pass; else XFAIL+NOTE "context not echoed on wire error envelope" (envelope work, relates to D2/`egnl`). |

## Section 4 — Bulk & side-effects (S5)

- **Removing `_IMPL_ONLY` (C2) re-parametrizes the UC-002 `@account` rows onto wire.**
  They were impl-exclusive; post-change they run on a2a/mcp/rest. They must xfail
  cleanly there (account resolution not wired on wire = `l9wn`). Add/verify a wire
  xfail-tag for `@account` with note "account resolution not wired on wire (`l9wn`)".
- **375 impl-exclusive-xfailed rows** lose the impl variant; ensure their existing
  tag-based xfails (`_XFAIL_TAGS`) apply on the wire variants so they don't surface
  as failures. (Most already keyed by tag, not transport — verify in S5.)
- **MediaBuyAccountEnv** (`tests/harness/media_buy_account.py`, impl-only) is no longer
  reached from BDD after C2; leave it (used directly / future wire wiring), or its
  `@account` scenarios xfail on wire per above.

## Section 5 — Verification gate (S5)

1. Full serial wire run: `pytest tests/bdd/ -n0 -p no:randomly --json-report …` (agent-db).
2. Compare to pre-drop baseline (`/tmp/bdd_full.json`, wire union ≈ a2a 375 / mcp 377 / rest 358).
3. **Pass criteria:** 0 new failures (only honest xfails); every one of the 34 rows
   accounted for (now wire-passing, or xfail+note); wire passing ≥ pre-drop wire passing.
4. Each XFAIL+NOTE references its gap ticket (`l9wn` account, `egnl` context/status,
   `j2qj` REST body) or a new spec-gap note (sampling_method, buyer_refs).

## Open items folded forward (not blockers for the drop)

- `l9wn` (wire account resolution) → graduates delivery_account + UC-002 `@account` xfails.
- `j2qj` (REST body building) → graduates REST format_ids/disclosure rows.
- `egnl`/D2 (envelope status/context) → graduates UC-011 context-echo.
- Spec gaps (`sampling_method`, `buyer_refs`) → upstream reconciliation at next derivation bump.
