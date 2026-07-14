# Step 3 scope — extract `validate_business_rules()` (the real refactor)

Per `media-buy-validation-slice-map.md`. Step 1 (map) ✓. Step 2 (SDK 5.7 / protocol-level
at the boundary) landed accidentally via the #1399 merge. **Step 3 — the business-rule
extraction — is the unfinished refactor the branch was named for.**

## The monster (still)
- `_create_media_buy_impl` ≈ 2,383 lines, **47 `raise AdCP*`**
- `_update_media_buy_impl`, **21 `raise AdCP*`**
- `financial_validation.py` already extracts the **budget** validators (~10% of Step 3 done).

## The split (of the 68 raises)
- **Protocol-level (~80%, 39× `AdCPValidationError`)** — shape/required/ordering/duplicate.
  Belongs at the request-model boundary (Step 2, mostly there). NOT this ticket. Where any
  remain hand-rolled, push to model validators so they're `VALIDATION_ERROR` by construction.
- **Business-rule (~20%) — the extraction target of `validate_business_rules()`:**

| business rule | raise class → wire code | gh8p net (safety net) |
|---|---|---|
| currency supported (CurrencyLimit) | `AdCPCapabilityNotSupportedError` → UNSUPPORTED_FEATURE | gh8p.3 (done) |
| product existence + tenant ownership | `AdCPProductNotFoundError` | gh8p.7 (done) |
| media-buy / package existence (update) | `AdCPGoneError` / not-found | gh8p.7 (done) |
| account resolution + status | `AdCPAuthorizationError` | gh8p.6 |
| budget min / daily-spend-cap | `AdCPBudgetTooLowError` / BudgetExceeded | gh8p.2 (done) |
| pricing-model ↔ adapter compatibility | `AdCPAdapterError` → PRICING_ERROR | gh8p.4 |
| format registry + creative/product compat | `AdCPFormatNotFoundError` / `AdCPCreativeRejectedError` | gh8p.5 |
| placement validity | invalid_placement_ids | gh8p.8 |

## Shape
`validate_business_rules(req, *, session/repos, op: Literal["create","update"]) -> None`
- Raises the typed `AdCPError` (→ wire envelope) on first violation, OR accumulates into the
  multi-error `errors[]` envelope (decide: fail-fast vs drain — match current behavior).
- Shared across create + update; op-specific rules (media-buy existence on update) gated by `op`.
- Extends `financial_validation.py` (don't start fresh — it's the seed).

## Sequence — NET-FIRST (slice-map verification standard; gh8p IS the net)
1. **Finish the gh8p net green** — gh8p.4/5/6/8/10/11/12/13, schema-grounded, green on all 5
   transports (e2e_rest works now). gh8p stays — it's the safety net that proves behavior.
2. **Extract** `validate_business_rules()` — move the business-rule raises out of both `_impl`s
   into it. `_create/update_impl` shrink materially.
3. **Verify** the slice-map way — binary, not judgment: net stays green AND a **seeded mutation**
   (break one rule) turns it red. That proves the extraction preserved behavior + the net is real.

## Net effect
The budget/currency/spend/pricing/creative/account scenarios graduate **together** (not 8 one-off
patches), `_create_media_buy_impl` drops from 2,383 lines toward something reviewable, and create+
update stop duplicating validation. THIS is the 100→~150 that delivers the original goal.
