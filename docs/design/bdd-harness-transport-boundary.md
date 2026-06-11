# BDD Harness: Exercising Transport-Boundary Logic ("True E2E")

> Status: **in progress** — UC-003 update dispatch foundation landed (commit
> `c8849dd8a`); remaining work tracked under the BDD-harness-boundary epic.
> This document is the **source of truth for the decisions** below; beads
> reference it and carry only an actionable scope + a short copy of the decision.

## Summary

The `tests/bdd/` harness parametrizes every scenario across four transports
(`impl`, `a2a`, `mcp`, `rest`) but dispatches most of them by calling the
`_impl` function (or a transport wrapper) **directly** from a per-use-case
`Env`. Several AdCP behaviors do **not** live in `_impl` — they live at the
**transport boundary** (the MCP/A2A/REST wrappers) or in the **protocol
envelope**. When the harness calls `_impl` directly, or when its transport
methods drop/transform fields, those behaviors are never exercised, so the
scenarios that assert them cannot pass on any transport.

This is the structural reason a family of UC-002 and UC-003 BDD scenarios were
"0 passing" despite having correct step definitions.

## Problem

Boundary/resolution logic that the scenarios assert but the harness bypasses:

| Behavior | Where it actually runs | Why the harness misses it |
|----------|------------------------|---------------------------|
| **Account resolution** (`ACCOUNT_NOT_FOUND`) | Transport wrappers: `create_media_buy_raw` (`src/core/tools/media_buy_create.py:3927`) and the MCP wrapper (`:4004`) call `enrich_identity_with_account` → `resolve_account` (`src/core/transport_helpers.py:106`). | `_create_media_buy_impl` does **not** resolve/enrich (`src/core/tools/media_buy_create.py:1533+` has no `enrich`/`resolve_account`). `MediaBuyCreateEnv.call_impl` calls `_impl` directly; `call_a2a`/`call_mcp` **strip** the `account` field (`flat.pop("account")`) because the flat `*_raw` wrappers don't accept it. So no transport surfaces account-not-found — the request proceeds to product validation and returns `SERVICE_UNAVAILABLE`. |
| **Update dispatch** | `_update_media_buy_impl` / `update_media_buy_raw` / MCP / REST update endpoint. | The UC-003 extension conftest branch ran scenarios through `MediaBuyCreateEnv`, whose `call_*` dispatch to the **create** path. An `UpdateMediaBuyRequest` hit `_create_media_buy_impl` → `AttributeError: 'UpdateMediaBuyRequest' object has no attribute 'get_total_budget'`. **(FIXED — see below.)** |
| **Protocol status** (`TaskStatus="completed"` on success) | `ProtocolEnvelope.wrap(payload, status=...)` (`src/core/protocol_envelope.py:113`), added by MCP/A2A/REST. | The harness captures the **domain** response (`UpdateMediaBuySuccess`, whose `status` is `None` at impl; create carries `MediaBuyStatus`, a *different* notion). `_update_media_buy_impl` never sets `status` on its success responses (`media_buy_update.py:359/383/532/1229`). `then_success.py:then_response_status` sees `status` in `model_fields` and asserts `None == "completed"`. |
| **REST update body** | `PUT /api/v1/media-buys/{id}`. | `MediaBuyDualEnv._build_update_rest_body` drops `packages`, so production returns `VALIDATION_ERROR: must include at least one updatable field`. |

### Concrete impact (at investigation time)

- `tests/bdd/test_uc003_update_media_buy.py`: **0** passing before the fix
  (all 30 baseline passers were UC-002); the 30 UC-002 passers were all error
  scenarios that don't need boundary resolution.
- `tests/bdd/test_uc002_create_media_buy.py`: `account_not_found` scenarios
  (rkb9) produce `SERVICE_UNAVAILABLE`, never `ACCOUNT_NOT_FOUND`.

## What landed (commit `c8849dd8a`)

UC-003 **update dispatch** is now wired:

- The UC-003 extension conftest branch (`tests/bdd/conftest.py`) uses
  `MediaBuyDualEnv` (`tests/harness/media_buy_dual.py`) instead of
  `MediaBuyCreateEnv`. `MediaBuyDualEnv` detects `UpdateMediaBuyRequest` and
  routes through the update wrappers across all four transports, against the
  real DB.
- `tests/factories/core.py::set_adapter_test_behavior(env, tenant_id, **behavior)`
  added (was imported by steps but never written → `ImportError`). Upserts
  `AdapterConfig.config_json["test_behavior"]` (read by
  `mock_ad_server._read_test_behavior`, `src/adapters/mock_ad_server.py:270`)
  and `mock_manual_approval_required`.
- `MediaBuyCreateEnv._build_mock_context_manager` (`tests/harness/media_buy_create.py`)
  now stubs `get_or_create_context` (used by the async update path) delegating
  to the real manager, so the persisted `context_id` is a real string
  (was: `psycopg2 can't adapt MagicMock` on the `workflow_steps` insert).
- `MediaBuyDualEnv._call_update_mcp` makes the mock `ctx.get_state` key-aware so
  `get_state("context_id")` returns `None` rather than the `ResolvedIdentity`
  (was: `psycopg2 can't adapt ResolvedIdentity`).

Result: `uc003` 0 → 4 passing, **0 regressions**, `make quality` green.

## Solution architecture (remaining work)

The guiding principle: **the harness must exercise the same boundary/resolution
path a real client would**, per transport — not call `_impl` directly when the
behavior under test lives at the boundary. Two complementary mechanisms:

1. **Env-level transport methods that mirror the boundary.** This is the
   `MediaBuyDualEnv` pattern: each `call_*` method runs the real wrapper /
   resolution for that transport. Extend the same pattern to account
   resolution in the create envs.
2. **Capture the protocol envelope, not just the domain payload.** Transports
   that wrap (`a2a`/`mcp`/`rest`) must surface `ProtocolEnvelope.status` so
   success scenarios can assert `TaskStatus`. `impl` has no envelope by
   definition (see Decision D2).

### Work areas

- **A. UC-003 success protocol-status semantics** (cross-cutting; shared with
  create). Decide where success `TaskStatus="completed"` is observed and make
  the harness/step honor it across transports. See Decision **D2**.
- **B. UC-003 REST update body** — fix `MediaBuyDualEnv._build_update_rest_body`
  so package updates survive to `PUT /api/v1/media-buys/{id}`.
- **C. UC-003 mcp/rest update error-path capture** — error scenarios pass on
  `impl`/`a2a` but still fail on `mcp`/`rest` (e.g. `budget_validation`), so the
  update error envelope isn't captured on those transports yet.
- **D. UC-002 create account-resolution across transports** (rkb9's real fix) —
  make the create envs exercise `enrich_identity_with_account`/`resolve_account`
  for each transport. Requires the flat `*_raw`/MCP wrappers to accept an
  `account` reference (today they don't), then un-strip it in
  `MediaBuyCreateEnv.call_a2a`/`call_mcp` and enrich in `call_impl`.
- **E. Parser/leaf fixes** (necessary but not sufficient on their own):
  - et3g: accept `invoice_recipient` in the update-field datatable parser
    (`tests/bdd/steps/domain/uc003_update_media_buy.py:235`).
  - 18eq: accept `product_id` in the package datatable parser (`:301`).
  - d61o: handle the `new_packages` `update_fields` pattern (`:1905`).
  Each then asserts a production rejection
  (`VALIDATION_ERROR`/`INVALID_REQUEST`/`UNSUPPORTED_FEATURE` + `suggestion`)
  across all four transports — which depends on C.

## Decisions

### D1 — Wire UC-003 update dispatch via `MediaBuyDualEnv` (DECIDED, landed)

The UC-003 extension scenarios run on `MediaBuyDualEnv`, which routes
`UpdateMediaBuyRequest` through the update wrappers per transport against the
real DB. Rationale: the env already existed for UC-026 (create-then-update) and
keeps update routing in one place instead of duplicating dispatch logic in the
When step. Consequence: all UC-003 ext scenarios now execute the update flow;
the dead duplicate `elif uc == "UC-003"` branches were removed.

### D2 — Success `TaskStatus` is a protocol-envelope concern (OPEN — needs spec check)

`status="completed"` asserted by the success scenarios is the protocol
`TaskStatus` from `ProtocolEnvelope`, **not** the domain response's `status`
field (which is `MediaBuyStatus` for create and unset for update). Open
question, to be resolved against the pinned AdCP spec
(`~/projects/adcp`, tag `v3.1.0-beta.3`): does the update-media-buy response
carry a status at the domain level, or only via the envelope? Candidate
resolutions:
- Harness-level: `a2a`/`mcp`/`rest` capture `ProtocolEnvelope.status`; `then_response_status`
  becomes transport-aware (no status assertion on `impl`).
- Domain-level: `_update_media_buy_impl` sets a status on success (only if the
  spec puts it on the domain response — unlikely given the envelope design).

Do **not** hack `then_response_status` to probe `model_fields` for branching
(see memory `feedback_sdk_not_authoritative`: the SDK/model is not authoritative
for shape decisions).

### D3 — Account resolution belongs at the boundary, mirrored by the harness (DECIDED-direction)

Account resolution stays at the transport boundary (it is identity-shaping,
not business logic — consistent with the transport-boundary architecture in
`CLAUDE.md` Pattern #5). The harness, not `_impl`, must mirror it. This means
the create envs gain per-transport account enrichment rather than pushing
resolution into `_create_media_buy_impl`.

## References

- Architecture pattern: `CLAUDE.md` §"Transport Boundary: Layer Separation" (Pattern #5)
- Harness guide: `tests/CLAUDE.md` §"The Harness System", §"Transport dispatching"
- Foundation commit: `c8849dd8a`
- Pinned AdCP spec: `~/projects/adcp` tag `v3.1.0-beta.3` (target for D2)
