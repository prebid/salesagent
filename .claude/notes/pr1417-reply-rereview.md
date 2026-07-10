Thanks for the deep re-review at `33d01093`, @ChrisHuie. All 10 items are addressed, and current `main` has since advanced — I've merged and reconciled it. CI is green on the current head.

### Your review items — all addressed

**Before-merge invariants:**
1. **type-ignore ratchet (69→71)** — coerced the wire-dict fields to typed models at the boundary; baseline now **67** (below main's 69). The ratchet hook now compares the committed baseline against `origin/main` and fails-closed on a baseline *raise* (the blind spot you flagged). `02efa2cbf`.
2. **REST↔A2A suggestion parity + dead-path test** — wrapped all bare A2A skill handlers in `adcp_validation_boundary` (the sweep found a 5th: `list_authorized_properties`). Closed the test gap at the harness: `MediaBuyListEnv.call_a2a` now drives the real `on_message_send` pipeline instead of the zero-caller `get_media_buys_raw`; added a guard banning tests from calling any dead `*_raw`. `6ffbd5a6e`.

**Fold-in:**
3. **Second T-UC-003 dup (early-return form)** — deleted `conftest.py:2929`; widened the guard to the *disease* (duplicate dispatch resolution), catching both elif-chain and early-return forms. `29dc0a691`.
4. **`to_*` boundary sweep** — built the boundary *into* the raise-capable helpers via one shared `_coerce_wire_object` (collapsing 6 duplicated isinstance ladders); the guard now derives the raise-capable set from the `schema_helpers` AST and models `to_*` / `model_validate_json` / `parse_obj`. `d2e748764`.
5. **BR-UC-002 citation** — refreshed to published GA 3.1.0/3.1.1 (+ storyboard line move). `4410011a8`.
6. **Stale "RED today" docstrings** — reframed to guarded-invariant framing; header count corrected. `a174292db`.
7. **`get_db_session()` in test body** — rewrote to the UoW/harness; extended the repo-pattern guard to scan `tests/` too. `b91cf6e4e`.

**Nits:** `_require_account_access` fail-closed on falsy principal (`d4ebae172`); local ids stripped from the new guard docstrings (`eb7ac2568`); `to_push_notification_config` added to `schema_helpers.__all__` (`33ea3fce2`).

### Since your review — main advanced, reconciled with 0 regressions

`origin/main` moved several times (`#1543`/`#1519`, `#1506`, `#1560`, `#1545`). Each merge was reconciled per-diff. The consequential one is **#1545** (delivery wire + media-buy status taxonomy):

- **get_media_buys status is now date-refined** per the GA 3.1 lifecycle state machine (`active→completed at flight end`). I verified this against the GA protocol (not the storyboard) and reconciled our earlier persisted-authoritative read to it, and **routed `update_media_buy`'s `media_buy_status` through the same resolver** so the two agree.
- Retired a schema-impossible `get_media_buys` missing-dates phantom scenario (`MediaBuy.start_date/end_date` are NOT NULL).
- Seeded the uc004 `delivery_account` "valid" scenarios (they asserted a valid account without seeding it — passed historically only because the a2a account param was wire-dropped).
- **Strengthened 4 uc004 a2a validation scenarios** from a vague `invalid` to the named AdCP 3.1.1 code (`VALIDATION_ERROR` + suggestion, tagged `@schema-v3.1`) — a2a now enforces these validations (an improvement), so the scenarios pass on a spec-faithful assertion rather than "some error occurred."

### Verification

CI green on the current head. On a full local + remote-box suite: unit / integration / admin / e2e / ui all pass, **0 passed→failed regressions** across the whole #1545 merge (confirmed by diffing against the last green pre-merge run). The only outstanding reds are pre-existing `e2e_rest` mock-incompatibility scenarios tracked under the `e2e_rest` ledger-retirement epic — a transport CI does not gate on, and none of them are breakage from this branch.
