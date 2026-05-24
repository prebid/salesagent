# BDD strict-marker debt

Inventory of BDD scenarios still wearing non-strict xfail markers after the
2026-05-08 cleanup that removed ~244 stale markers from `tests/bdd/conftest.py`.
Every entry below corresponds to a marker that requires either a production
change or a test rewrite before it can be flipped to `strict=True`.

**Goal:** zero non-strict xfail markers. Every scenario must be `pass`, `fail`,
or `xfail-strict`. No gray zone.

This file is the single source of truth referenced from FIXME comments in
`tests/bdd/conftest.py`. When the underlying gap is closed, remove the entry
here and the corresponding marker block.

---

## 2026-05-19 — 18h.10 Phase-2 reconciliation & bead tracking

Every remaining item now has a full-context bead (the doc is no longer the
only record). Reconciled items were fixed by the Phase-2 wave (run
`190526_2039`, bdd 0-failed) and need no bead.

| Item | Status | Bead |
|------|--------|------|
| C1 + C2 (account not enforced at _impl boundary; +9d5 REST FIXME) | OPEN P1 sec | `salesagent-xpcd` |
| C3 (cross-principal 200+empty, not 403) | OPEN P1 sec | `salesagent-h25j` |
| C4 (ValidationError→AdCPError boundary translator) | OPEN P2 broad | `salesagent-l6ev` |
| C5 (`include_package_daily_breakdown` no-op) | OPEN P2 | `salesagent-kzk0` |
| C6 (date-range validation in success envelope) | OPEN P3 | `salesagent-t6y9` |
| C7 (end-only date_range default) | OPEN P3 | `salesagent-losz` |
| C8 (MCP list_creative_formats missing fmt-id params) | OPEN P2 | `salesagent-95q3` |
| C10 (description-only spec constraints) | OPEN P3 | `salesagent-o9w4` |
| C11 (reporting_period echo) | RECONCILED (salesagent-18h.1) | — |
| B1 (Gherkin `pending_activation`) | OPEN P2 | `salesagent-8c78` |
| B2 (date_range fake kwarg) | RECONCILED (pilot a56621ea) | — |
| B3 (resolution/ownership symbolic names) | RECONCILED (exec-uc004) | — |
| B4 (sampling_method wrong feature) | OPEN P3 (relocate) | `salesagent-uofj` |
| B5 (webhook_credentials wrong dispatch) | RECONCILED (exec-uc004 f8u4) | — |
| B6 test half (disclosure enum literals) | RECONCILED (exec-uc005 9z2t) | — |
| B6 production half (disclosure filter impl) | OPEN P2 | `salesagent-1z7m` |
| B7 (UC-006 fake AdCPValidationError) | RECONCILED (salesagent-miva) | — |
| H1, H2 (`_assert_partition_outcome` weak) | RECONCILED (salesagent-6oq) | — |
| reporting_dimensions breakdowns (was phantom "zk1") | OPEN P2 | `salesagent-z8nf` |
| creative per-format resilience (one bad fmt nukes all) | OPEN P2 | `salesagent-az8d` (← `w8yn`) |
| non-UC004/5/6 audit remainder | OPEN | epic `salesagent-pvo2` |

---

## How to read this

- **Scope** lists the affected scenario tags or selective entries in
  `tests/bdd/conftest.py`.
- **Impact** is the count of currently-skipped/xfailed test runs (rows ×
  transports) that block.
- **Unblocks** describes the production change or test rewrite that closes
  the item; once landed, the marker can flip to `strict=True` (or be removed
  if no rows remain xfail).
- **Origin** points to the audit batch that surfaced the item.

---

## Production gaps (P0–P2) — require code changes

### C1 — A2A skill silently discards `account` parameter
- **Scope:** `T-UC-004-partition-account` and `T-UC-004-boundary-account`,
  error-code rows on the `[a2a]` transport
- **Where:** `src/a2a_server/adcp_a2a_server.py:1937-1980` (handler does not
  forward `account` to `_raw()` and does not call `enrich_identity_with_account`)
- **Impact:** Security gap. A buyer with a token scoped to one tenant could
  request delivery and the account scope would be ignored. Surfaces as
  asymmetric pass/fail across transports for the boundary/partition account
  scenarios.
- **Unblocks:** flip the error-code rows to `strict=True` once A2A forwards
  the parameter and `enrich_identity_with_account` runs on the A2A path.
- **Severity:** P1 (security gap)
- **Origin:** Batch 3 audit

### C2 — IMPL `_get_media_buy_delivery_impl` does not resolve `AccountReference`
- **Scope:** Same as C1 (account error-code rows)
- **Where:** `src/core/tools/media_buy_delivery.py` — only REST currently calls
  `enrich_identity_with_account` (`src/routes/api_v1.py:265-289`); IMPL receives
  `account` as a request kwarg but never resolves it against the DB
- **Impact:** `account_not_found` rows pass through IMPL silently; cross-
  transport contract is broken
- **Unblocks:** move account resolution into the `_impl` boundary so all
  transports share the validation step
- **Severity:** P1 (correctness; pairs with C1)
- **Origin:** Batch 3 audit

### C3 — Cross-principal media-buy access returns 200+empty instead of 403
- **Scope:** `T-UC-004-partition-ownership` / `-boundary-ownership`,
  mismatch rows
- **Where:** `src/core/database/repositories/media_buy.py:99-107`
  (`get_by_principal` filters silently)
- **Impact:** Security gap. A request with a foreign principal_id receives
  an empty deliveries list rather than `AdCPAuthorizationError`.
- **Unblocks:** raise `AdCPAuthorizationError` (or 404 with suggestion) when
  the requesting principal does not own any of the requested media_buys.
  Then the ownership mismatch rows can flip to `strict=True`.
- **Severity:** P1 (security gap)
- **Origin:** Batch 3 audit

### C4 — Pydantic `ValidationError` not translated to `AdCPError(INVALID_REQUEST, suggestion)`
- **Scope:** ~32 partition rows across UC-004 (`reporting_dimensions`,
  `attribution_window`, possibly more) — currently `strict=True` xfail
  pointing at this item
- **Where:** transport boundary in `_get_media_buy_delivery_impl` and other
  `_impl` functions
- **Impact:** Many invalid partition rows correctly fail validation but
  produce a Pydantic `ValidationError`, not an `AdCPError` with `error_code
  == "INVALID_REQUEST"` and `details["suggestion"]`. The BDD step's stricter
  invalid-with-error-code path requires the AdCPError shape.
- **Unblocks:** add a transport-boundary translator that wraps Pydantic
  `ValidationError` in `AdCPError(INVALID_REQUEST, suggestion=…)`. One change
  clears ~32 currently-xfailed rows across UC-004 and probably more across
  UC-005.
- **Severity:** P2 (broad payoff)
- **Origin:** Batch 1, Batch 2, Batch 4 audits

### C5 — `include_package_daily_breakdown` is a no-op
- **Scope:** `T-UC-004-partition-daily-breakdown` and
  `-boundary-daily-breakdown` valid rows (currently strict=False)
- **Where:** `src/core/tools/media_buy_delivery.py:480` hard-codes
  `daily_breakdown=None` regardless of `req.include_package_daily_breakdown`
- **Impact:** The schema declares the field; production silently ignores it.
  All 6 valid rows pass vacuously. The Then-step does not verify response
  shape, so coverage is illusory.
- **Unblocks:** populate `daily_breakdown` per-package when the flag is True.
  Strengthen the Then-step to verify shape differential. Then strict-flip.
- **Severity:** P2 (feature gap, not security)
- **Origin:** Batch 4 audit

### C6 — Date-range validation returned in success envelope, not raised
- **Scope:** `T-UC-004-daterange-invalid` and `T-UC-004-daterange-equal`
  (currently strict=True with stale reason)
- **Where:** `src/core/tools/media_buy_delivery.py:141-161` returns a
  `GetMediaBuyDeliveryResponse` with `errors=[Error(code="VALIDATION_ERROR",
  …)]` instead of raising. The BDD Then-step inspects `ctx["error"]` only.
- **Impact:** 2 scenarios stuck in strict=True xfail; production validation
  IS happening, just in the wrong shape.
- **Unblocks:** either change production to raise `AdCPValidationError`,
  OR change the Then-step to also inspect `response.errors[]`. Refresh
  the strict=True reason text.
- **Severity:** P3 (cosmetic — already strict)
- **Origin:** Batch 2 audit

### C7 — End-only date_range defaults to today-30d, not creation date (Gap G40)
- **Scope:** `T-UC-004-daterange-end-only` (currently strict=False)
- **Where:** `src/core/tools/media_buy_delivery.py:162-165` — when only
  `end_date` provided, code sets `start_dt = now - 30d`. Spec says start
  defaults to media buy creation date.
- **Impact:** 1 scenario stuck non-strict.
- **Unblocks:** when `end_date` provided alone, default `start_date` to
  media buy creation date (look up `MediaBuy.created_at`).
- **Severity:** P3
- **Origin:** Batch 2 audit

### C8 — MCP `list_creative_formats` wrapper missing `output_format_ids`, `input_format_ids`
- **Scope:** `T-UC-005-inv-049-9-violated`, `-9-nofield`, `-10-violated`,
  `-10-nofield` on the `[mcp]` transport (currently strict=False, labeled
  "vacuous pass")
- **Where:** `src/core/tools/creative_formats.py:440-494` — MCP wrapper
  signature does not accept these filter params; `mcp_compat_middleware`
  strips them
- **Impact:** Tests pass on MCP for the wrong reason (the param disappears
  before reaching the filter). 4 vacuous xpasses.
- **Unblocks:** add both params to the MCP wrapper signature so MCP becomes
  a real test surface. Then flip strict=True (the underlying filter is
  already implemented in `_impl`).
- **Severity:** P2
- **Origin:** Batch 5 audit

### C10 — Description-only spec constraints in adcp library
- **Scope:** Two specific Examples rows already xfailed strict=True:
  - `geo with geo_level=metro but no system` (`T-UC-004-boundary-reporting-dims`)
  - `unit=campaign with interval=2` (`T-UC-004-boundary-attribution`,
    `T-UC-004-attr-campaign-invalid`, `campaign_interval_not_one` partition row)
- **Where:** `.venv/.../adcp/types/generated_poc/core/duration.py:27` and
  `.venv/.../media_buy/get_media_buy_delivery_request.py:57-62` —
  constraints exist in field descriptions only, no Pydantic validators
- **Impact:** 3 scenarios xfailed strict=True awaiting either a model
  validator addition OR an upstream AdCP spec PR
- **Unblocks:** either (a) add a `model_validator(mode="after")` on
  `Duration` and `Geo` in our extending classes, OR (b) file an upstream
  AdCP PR adding the validators
- **Severity:** P3 (cosmetic)
- **Origin:** Batch 1 audit

### C11 — `start_date` / `end_date` not echoed in `response.reporting_period` — RETIRED (salesagent-18h.1, 2026-05-19)
- **Status:** RETIRED. The "production ignores buyer start_date" failure was
  an artefact of the greedy `with {request_params}` step shadowing
  `when_request_date_range` and mis-parsing the request — not real production
  behaviour. With correct step routing (greedy step restricted to `\w+=`),
  production echoes the buyer-supplied `start_date`/`end_date` in
  `response.reporting_period` and all 4 transport variants pass. The
  `T-UC-004-daterange` strict-xfail row was removed from `conftest.py`.
- **Scope:** `T-UC-004-daterange` ("Custom date range used as reporting
  period" — added to strict=True genuine-xfails on 2026-05-08)
- **Where:** `src/core/tools/media_buy_delivery.py` — when buyer supplies
  explicit `start_date` and `end_date`, response's `reporting_period.start`
  is computed as `now - 30d` instead of echoing the request value
- **Impact:** Discovered during the 2026-05-08 strict-flip executor run.
  4 transport variants of one scenario.
- **Unblocks:** populate `reporting_period.start = req.start_date or
  default` and `reporting_period.end = req.end_date or default` in the
  response constructor.
- **Severity:** P2 (visible buyer-facing bug)
- **Origin:** discovered during executor verification, 2026-05-08

---

## Test rewrites (P2–P3) — no production change needed

### B1 — Gherkin uses `pending_activation` which is not a valid `MediaBuyStatus`
- **Scope:** `T-UC-004-filter` (Examples row at feature line 154);
  `T-UC-004-partition-status-filter` rows `single_pending` and
  `all_statuses_array`; `T-UC-004-boundary-status-filter` rows
  `pending_activation (first enum value)` and `all 6 statuses`
- **Where:** `tests/bdd/features/BR-UC-004-deliver-media-buy-metrics.feature`
- **Source of truth:** AdCP library
  `.venv/.../enums/media_buy_status.py:11-17` —
  `{pending_creatives, pending_start, active, paused, completed, rejected,
  canceled}`. Library/spec is authoritative; existing test/code is not.
- **Fix:** replace `pending_activation` with `pending_creatives` (true first
  enum value) or `pending_start` per scenario context. Update
  `all_statuses_array` to `["pending_start", "active", "paused", "completed",
  "rejected", "canceled"]`.
- **Impact:** ~5 substring matches across 3 scenarios; once fixed, the
  `T-UC-004-filter` selective entry shrinks to empty (remove entirely)
- **Severity:** P2 (Gherkin error)
- **Origin:** Batch 2 audit
- **Update 2026-05-18 (salesagent-18h.1):** status_filter *selection* now
  uses the persisted `MediaBuy.status` (was date-derived and ignored the
  column entirely). `rejected`/`canceled` rows now pass and were removed
  from the `_UC004_FILTER_SELECTIVE` substring set. The `paused`/`completed`
  rows still xfail because the *response* delivery status (`d.status`) is
  still computed from flight dates — a buy persisted as `completed` with a
  current flight window is selected correctly but reported as `active`.
  Remaining substrings: `{pending_activation, paused, completed}`. Closing
  the response-status-display gap (re-deriving `d.status` from the persisted
  column) plus the `pending_activation` Gherkin rewrite empties this entry.

### B2 — `_dispatch_partition` for `date_range` sends fake `date_range="…"` kwarg
- **Scope:** `T-UC-004-boundary-date-range` and
  `T-UC-004-partition-date-range` (currently in `_UC004_BOUNDARY_TAGS` /
  `_UC004_PARTITION_TAGS` blanket — scheduled for removal once this is
  fixed)
- **Where:** `tests/bdd/steps/domain/uc004_delivery.py:937-945`
- **Fix:** translate symbolic partition labels (`start_before_end`,
  `dates_omitted`, `start_equals_end`, `start_after_end`, etc.) to actual
  `start_date` / `end_date` request kwargs
- **Impact:** ~16 rows × 4 transports currently dispatch through Pydantic
  `extra="forbid"` rejection by accident; once wired, those rows test the
  real validation path
- **Severity:** P2
- **Origin:** Batch 2 audit

### B3 — Resolution and ownership scenarios use symbolic names that never exercise the code path
- **Scope:** `T-UC-004-partition-resolution`,
  `T-UC-004-boundary-resolution`, `T-UC-004-partition-ownership`,
  `T-UC-004-boundary-ownership` (currently in blanket strict=False)
- **Where:** `tests/bdd/steps/domain/uc004_delivery.py:2076-2098`
  (`_dispatch_partition`)
- **Fix:** for resolution rows, construct concrete `media_buy_ids` /
  `buyer_refs` request shapes per partition. For ownership rows, set
  `ctx["principal_id"]` before dispatch (existing pattern at line 670-676).
- **Impact:** ~37 scenarios currently pass vacuously; the `owner_mismatch`
  variant cannot fail at all because identity is never swapped
- **Severity:** P2 (test bug; pairs with C3 security gap to fully validate)
- **Origin:** Batch 3 audit

### B4 — `sampling_method` scenarios live on the wrong feature
- **Scope:** `T-UC-004-boundary-sampling`, `T-UC-004-partition-sampling`
  (currently in blanket strict=False)
- **Where:** `tests/bdd/features/BR-UC-004-deliver-media-buy-metrics.feature`
- **Source of truth:** AdCP library
  `.venv/.../media_buy/get_media_buy_delivery_request.py:142-202` does NOT
  declare `sampling_method`. The field belongs to content standards
  (`docs/test-obligations/constraints.md:1396` `CONSTR-SAMPLING-METHOD-01`).
- **Fix:** delete from `BR-UC-004.feature`; if needed under UC-024
  (content standards) re-author there
- **Impact:** ~17 scenarios become real passes/fails after relocation
- **Severity:** P3 (cleanup)
- **Origin:** Batch 3 audit

### B5 — `webhook_credentials` scenarios dispatch through `get_media_buy_delivery`
- **Scope:** `T-UC-004-boundary-credentials`,
  `T-UC-004-partition-credentials` (currently in blanket strict=False;
  tagged `@BR-RULE-029` which is the wrong rule — that's about retry/
  backoff, not credentials)
- **Where:** `tests/bdd/steps/domain/uc004_delivery.py:954-957`
- **Fix:** either rewire through `CircuitBreakerEnv` and the real
  `WebhookDeliveryService.send_delivery_webhook` (which has the 32-char
  HMAC check at `webhook_delivery_service.py:337,463`), OR delete in favor
  of the dedicated scenarios at feature lines 373-396 which already test
  the rule properly with `when_validate_webhook_config`
- **Impact:** 10 scenarios with mismatched dispatch
- **Severity:** P3 (test mis-routing; rule already tested elsewhere)
- **Origin:** Batch 4 audit

### B6 — `disclosure_positions` filter not implemented; `all_positions` step uses non-existent enum values
- **Scope:** `T-UC-005-partition-disclosure`, `-boundary-disclosure`,
  `T-UC-005-inv-049-8-violated`, `-8-nofield` (currently strict=False)
- **Where:** production filter missing in `src/core/tools/creative_formats.py`;
  step bug in `tests/bdd/steps/generic/when_request.py:405-411`
  (uses `corner, inline, before, after` — none in
  `.venv/.../enums/disclosure_position.py:10-19`)
- **Fix (production):** add `if req.disclosure_positions: …` filter to
  `_list_creative_formats_impl`. AND-match per BR-RULE-049 INV-8
  (all requested positions must be supported).
- **Fix (test):** correct `all_positions` step to use the 8 real enum
  values: `prominent, footer, audio, subtitle, overlay, end_card, pre_roll,
  companion`
- **Impact:** ~28 vacuous xpasses + 2 vacuous-exclusion xpasses
- **Severity:** P2 (production feature gap)
- **Origin:** Batch 5 audit

### B7 — UC-006 account_resolution step layer fakes the `AdCPValidationError` — RECONCILED
- **Scope:** `T-UC-006-partition-account` (rows `missing_account`,
  `invalid_oneOf_both`) and `T-UC-006-boundary-account` (rows
  `account field absent`, `both account_id and brand`)
- **Status:** RECONCILED (salesagent-miva, 18h.10 Phase-2). The step layer
  no longer synthesizes an `AdCPValidationError`. `when_sync_creative`
  (`tests/bdd/steps/domain/uc006_sync_creatives.py`) now genuinely
  dispatches the absent payload to production (`account=None`) and parses
  the both-keys payload through the real `adcp` `AccountReference` union
  before dispatch. The rows now genuinely exercise production and fail for
  the real, named gap below — they are strict=True xfails tied to that gap
  (`tests/bdd/conftest.py` `_UC006_VALIDATION_XFAIL`), no longer xpassing
  for the wrong reason.
- **Underlying production gap (now genuinely exercised):**
  - `missing_account` / `account field absent`: production performs no
    required-account schema validation. `enrich_identity_with_account()`
    returns identity unchanged and `_sync_creatives_impl` succeeds — no
    `INVALID_REQUEST` is raised.
  - `invalid_oneOf_both` / `both account_id and brand`: the `adcp`
    `AccountReference` union raises a Pydantic `ValidationError` at parse
    time, which production does not translate into
    `AdCPError(INVALID_REQUEST, suggestion)` — the same C4 gap.
- **Unblocks:** add a transport-boundary translator that wraps Pydantic
  `ValidationError` in `AdCPError(INVALID_REQUEST, suggestion=…)` (C4) plus
  required-account enforcement. The moment those land, these rows xpass and
  the strict=True markers force their removal.
- **Severity:** P2 (real production gap, no longer masked by a fake test)
- **Origin:** Batch 6 audit; reconciled salesagent-miva

---

## Cross-cutting test-helper weaknesses

### H1 — `_assert_partition_outcome` invalid branch under-asserts
- **Where:** `tests/bdd/steps/generic/then_payload.py:209-230`
- **Issue:** the `invalid` branch only checks `"error" in ctx`. Any
  exception type satisfies it, including unrelated bugs (e.g., `KeyError`
  in step setup masquerading as "production rejected the input").
- **Fix:** add `isinstance(error, (AdCPError, ValidationError))` guard,
  matching UC-004's richer helper at
  `tests/bdd/steps/domain/uc004_delivery.py:1966-1968`
- **Severity:** P3 (latent risk; no current coverage harm)

### H2 — `_assert_partition_outcome` valid branch lacks domain content checks
- **Where:** same file
- **Issue:** `valid` branch only asserts `"response" in ctx` and (for the
  formats domain) `formats is not None`. A degenerate response with empty
  `media_buy_deliveries` or empty `formats=[]` passes.
- **Fix:** route by the captured `field` to a domain-specific assertion
  (the regex already captures `field` but discards it). UC-004 has the
  pattern at `_assert_valid_content`.
- **Severity:** P3

---

## Reference: how to track these

The expected lifecycle of an entry:

1. Item filed here with a unique ID (B1, C1, C11, etc.).
2. The corresponding `tests/bdd/conftest.py` marker has a FIXME pointing
   at this doc and the item ID.
3. Engineer fixes the gap (production change, test rewrite, or both).
4. They flip the marker to `strict=True` (or remove if no rows remain
   xfail), removing the FIXME.
5. They delete the entry from this doc.

Step 4 forces step 5 because once `strict=True` is set, further drift on
that scenario causes a hard suite failure rather than silent xpass.

---

## Tally

- **Production gaps:** 9 (C1–C11; C9 retired by 2026-05-08 cleanup, C11 retired by salesagent-18h.1 on 2026-05-19)
- **Test rewrites:** 7 (B1–B7)
- **Test-helper improvements:** 2 (H1, H2)
- **Total:** ~19 items

Severity distribution: 3× P1 (security/correctness), 7× P2, 9× P3.

If filed as one umbrella GH issue with a checklist, this fits comfortably
in a single tracking issue. If filed individually, only the 3 P1s warrant
separate issues; the rest can stay in this doc as the canonical reference.
