# e2e_rest XPASS reconciliation plan

Source: first clean in-network run `test-results/innet_170626_1402` (the e2e_rest 5th
transport ran end-to-end under adcp 5.7 for the first time). 7 scenarios marked
`xfail(strict=True)` now XPASS over e2e_rest → fail the gate. Grounded by 3 parallel
investigators against the **pinned** AdCP schema `~/projects/adcp@04f59d2d5`
(tag `v3.1-04f59d2d5`, 3.0.x schema lineage) and cross-checked vs installed adcp 5.7.0.

## Meta-finding (root cause of 5 of 7) — CORRECTED after empirical verification

The adcp **5.7 SDK dropped validation constraints / fields the pinned spec still mandates**
(type filter, disclosure_positions `uniqueItems`, buyer_refs). This is the "SDK is not
authoritative" case.

**PREMISE CORRECTION (verified empirically, run innet_170626_1622):** the original
investigators ASSUMED the e2e_rest stack runs `ENVIRONMENT=production` → `extra="ignore"`.
That is FALSE. `is_production()` defaults to `"development"` (`src/core/config.py:169`) and
`ENVIRONMENT` is set nowhere (Dockerfile/compose/scripts/.env), so the e2e server has always
run `development` → `extra="forbid"`. Proof: the 7 strict-XPASS are byte-identical with
`ENVIRONMENT: development` pinned explicit vs unset. So `salesagent-hm9q` is a defensive pin,
NOT a behavior change, and the per-scenario *mechanism* explanations below were re-grounded
under `forbid`.

- **type-filter mechanism (corrected):** NOT "extra=ignore drops the type key". The real cause
  is (a) the `type` field is removed from the 5.7 SDK model, and (b) `build_rest_body()`
  returns `{}` for list_creative_formats (`tests/harness/creative_formats.py` — the REST route
  body `ListCreativeFormatsBody` has no filter params), so the filter is never transmitted over
  e2e_rest at all. Production cannot filter by or reject `type`. **Disposition unchanged**
  (scope marker out of e2e_rest + ledger); only the mechanism/reason-string is corrected.
- The other 4 dispositions are extra-mode-independent and survive the correction unchanged.

## Disposition table (pin-grounded)

| # | Scenario (tag) | Pin | SDK 5.7 | Disposition | conftest refs |
|---|---|---|---|---|---|
| 2 | `format_type` partition `[invalid_type]` (`T-UC-005-partition-type-filter`) | `type` enum present (`04f59d2d5:dist/schemas/3.0.12/creative/list-creative-formats-request.json`) | dropped | re-evaluate under dev-env; scope marker out of e2e_rest + ledger (pin keeps `type`; NOT a retirement) | `_SELECTIVE_XFAIL` 327-331, applied 659-662 |
| 4 | `format_type` boundary `[invalid type (rejected)]` (`T-UC-005-boundary-type-filter`) | same | dropped | same | 332-336, applied 659-662 |
| 1 | `inv-031` AND-combination (`T-UC-005-inv-031-1-violated`) | `type` present; obligation BR-RULE-031-01 authoritative | dropped | scope out of e2e_rest + ledger | `_XFAIL_TAGS` 164, applied 697-699 |
| 3 | `disclosure_positions` partition `[duplicate]` (`T-UC-005-partition-disclosure`) | `uniqueItems:true`+`minItems:1` present (same pin file) | `uniqueItems` dropped | **fix production**: add `uniqueItems`/`minItems`(+enum) to route body `src/routes/api_v1.py` so server emits real INVALID_REQUEST; then graduate marker. Current XPASS is spurious (mock-leak coincidence, not real rejection) | substring `{"duplicate_positions"}` at 312 |
| 5 | `disclosure_positions` boundary `[duplicate]` (`T-UC-005-boundary-disclosure`) | same | same | same | substring `{"duplicate positions"}` at 320 |
| 6 | `buyer_refs` "both provided (priority rule)" (`T-UC-004-boundary-resolution`) | **`buyer_refs` excised** (gone since 3.0.0; `04f59d2d5:.../get-media-buy-delivery-request.json` has no buyer_ref) | absent | **graduate + retire obligation** BR-RULE-030 INV-3 (no second identifier → priority rule obsolete). Remove marker, retire feature rows, mark obligation excised. Sweep all `buyer_refs` (PLURAL) ghosts repo-wide — NOT `buyer_ref` singular (valid field) | strict block ~1686-1694 |
| 7 | `sort_by`/`by_placement` spend-fallback (`T-UC-004-dim-sortby-fallback`) | `by_placement` present (`04f59d2d5:.../get-media-buy-delivery-response.json`); BR-RULE-091 INV-6 valid | present | **fix test wiring**: `_inject_placement_data` (`uc004_delivery.py:3046`) is dead code wired to no Given step; test passes hollowly. Wire it into `given_seller_supports_dimension` (`:611`) / add `by_placement` to `set_adapter_response` (`_mixins.py:89`) so the fallback is genuinely tested, THEN remove e2e marker. Pre-existing debt, not e2e_rest-specific | strict block ~1710-1717 |

## Sequencing

1. **(first, blocks all)** Switch e2e_rest stack `ENVIRONMENT` → development (`extra="forbid"`).
   Find where it's set (server default when ENVIRONMENT unset; set it in `docker-compose.e2e.yml`).
   Re-run e2e_rest and re-triage: dev-env will turn the type-filter/disclosure silent-accepts
   into real `extra=forbid` rejections, changing several dispositions.
2. type-filter reconciliation (#1/#2/#4) — after dev-env, scope markers + ledger per pin.
3. disclosure production constraint (#3/#5) — add pin-mandated `uniqueItems`/`minItems`/enum.
4. buyer_refs retirement + ghost sweep (#6) — the recurring problem; target PLURAL only.
5. sort_by hollow-test wiring (#7).

## Already landed (this PR — harness/ui reconciliation) — VERIFIED over e2e_rest

- idempotency[e2e_rest]: e2e_config wiring (2668c62b2) + AuthorizedProperty seed (34a80f008)
  → **PASSES** over e2e_rest (run innet_170626_1622, all 4 transports green).
- only_end_date[e2e_rest]: Gap-G40 strict e2e_rest tripwire (2668c62b2) → **xfailed** ✅.
- explicit `ENVIRONMENT: development` pin (889a73966) → defensive, verified no behavior change.
- ui default-tenant seed (2668c62b2) → NOT yet verified (ui suite not in the bdd-only runs).

After these, the **only** remaining in-network bdd failures are the 7 strict-XPASS below
(epic salesagent-qmq1).

## Related

- Ledger retirement epic: `salesagent-rlgl` (XPASS = inspect, never bulk-delete)
- Ledger header NOTE claim that `-invalid]` schema-violation cases are "closed" by the
  INVALID_REQUEST handler does NOT hold for disclosure duplicate (no `uniqueItems` in prod).
