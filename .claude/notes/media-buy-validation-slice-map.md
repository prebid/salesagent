# Media-Buy Validation Slice — Map

Goal: tame the media-buy monster by extracting the **validation** seam, delivering
material gains early. Mapping first, then decide SDK-upgrade ordering.

## The monster

| file | lines | biggest fn |
|---|---|---|
| `media_buy_create.py` | 4,097 | `_create_media_buy_impl` = **2,383 lines**, 48 `raise AdCP*` |
| `media_buy_update.py` | 1,685 | `_update_media_buy_impl`, 16 `raise AdCP*` |

No standalone cancel tool — cancel is update-status. So the mutating surface is
**create + update**, and their validations overlap heavily.

## The validation slice

64 `raise AdCP*` across create/update. Validation-flavored scenarios
(`@error`/`@invariant`/`@boundary`): **110 of 181** (UC-002 Create) + **93 of 132**
(UC-003 Update) ≈ **200 scenarios**. Shared categories (line-mentions, create/update):
budget 72/87 · dates 62/562 · currency 31/15 · product 71/41 · pricing 41/8 ·
creative 93/98 · format 36/16 · targeting 67/49 · duplicate 13/9 · required 43/12.

## The split — protocol-level vs business-rule

Evidence from the create input-validation block (`media_buy_create.py:1743-1862`):

**Protocol-level (~80%) — belongs in the AdCP request model, not hand-rolled:**
- `total_budget <= 0` → budget must be positive (`gt=0`)
- `start_time`/`end_time` is None → required fields
- `end_time <= start_time` → ordering (model validator)
- `not product_ids` / `not package.product_id` → required / `min_length`
- duplicate `product_id` across packages → model validator
- `StartTiming` `asap`/ISO/datetime unwrapping with `"adcp 2.16.0+"` defensive
  handling → **this is hand-handling SDK type evolution; a current SDK deletes it**

**Business-rule (~20%) — runtime/DB context, the real refactor target:**
- currency-specific budget limits (`CurrencyLimit` table)
- product existence / tenant ownership (`Product` query)
- account resolution + status (active/suspended/payment)
- format existence in registry; creative/product format compatibility
- pricing-model ↔ adapter compatibility

The protocol-level half is **shared and identical** between create and update; the
business-rule half is **mostly shared** (currency, existence, compatibility).

## SDK finding

Pinned: `adcp==4.3.0` (targets spec **3.0.1**). Latest stable: **5.7.0** (6.x beta).
Feature files are **3.1**. So the request/response types we use lag the spec the
scenarios target. A 3.1-aligned `CreateMediaBuyRequest`/`UpdateMediaBuyRequest`
would enforce the protocol-level constraints **at parse time (the boundary)** and
clean up the `StartTiming`/`asap` type juggling — deleting a large fraction of the
64 raises rather than refactoring them.

Caveats: 4.3→5.7 is a **major jump** (breaking type changes likely); 6.x is beta;
the adcp **server framework is unproven** (use the **types only**, not the server).

## Recommended sequence

1. **(this) Map** — done.
2. **SDK type upgrade (4.3 → 3.1-aligned).** Move protocol-level validation into the
   request models; delete the redundant inline raises + the `adcp 2.16.0+` cruft.
   **Net:** the protocol-level scenarios (budget>0, required, date ordering,
   duplicates, format shape) — wire-envelope `VALIDATION_ERROR` assertions, real by
   construction. Material gain: less code, protocol-aligned validation, a wired net.
   *Open question:* size the 4.3→5.x type-breakage before committing.
3. **Extract shared `validate_business_rules()`** across create/update (cancel via
   update). The 20% that the SDK can't subsume (currency limits, existence,
   compatibility). **Net:** the business-rule scenarios. Material gain: a cohesive,
   unit-testable validator lifted out of both monsters; `_impl` shrinks on both sides.

## Verification (answers "was it really wired?")

Per slice: wire the net → make the change (delete protocol raises / extract validator)
→ the net stays green AND a seeded mutation (break one rule) turns it red. The
refactor proves the wiring; "done" becomes binary, not a judgment of agent output.
The 4-transport `MediaBuyCreateEnv` (impl/a2a/mcp/rest, real DB via factories) is a
sufficient net for an `_impl` change; the Docker/real-HTTP e2e transport is a
separate, later investment (catches wire/serialization regressions only).

## SDK-breakage spike (4.3.0 → 5.7.0)

Ran `adcp==5.7.0` in a throwaway, measured, reverted.

**Correction to the docs table:** `adcp==5.7.0` (latest *stable*) reports
`spec 3.1.0-beta.3` — i.e., the latest stable SDK already targets **3.1**, the same
spec the feature files use. The `docs/adcp-spec-version.md` mapping (5.x = 3.0.7) is
stale. So a 3.1-aligned SDK is a **stable release**, not a 6.x beta. Resolves cleanly
(transitively bumps mcp 1.25→1.27.2).

**Breakage surface (bounded — days, not weeks):**
- **34 unit collection errors**, dominated by **one root cause**: `Account` moved out
  of `adcp.types.generated_poc.account.sync_accounts_request` (still exported at the
  public `adcp.types.Account`). We import adcp via the **fragile deep generated path
  in 85 sites** — that's the real liability the spike exposed.
- **5 contract-drift failures** (`test_adcp_contract`): request/response shapes changed
  in 3.1 (sync_creatives, list_creatives, get_products, task_status, all-request-match).
  Reconcile our extending schemas with 5.7's models.
- **1 schema-redefinition guard**: 5.7's `GetMediaBuyDeliveryRequest` now *has*
  `time_granularity`/`include_window_breakdown` — fields we hand-declared. The guard
  says delete our redeclarations. **This is the thesis confirmed: the SDK subsumes
  what we hand-rolled.**
- **1 spec-version guard**: expected — bump `EXPECTED_SPEC_VERSION` 3.0.1 → 3.1.0-beta.3.

**Verdict:** SDK-first is viable and confirms the strategy. The dominant effort is
**hygiene that pays off regardless**: repoint the 85 deep `generated_poc` imports to
the public `adcp.types` (decouples us from SDK internals → future upgrades become
trivial). Then reconcile ~5 schema-drift areas, delete the now-redundant hand-rolled
fields, bump the spec-version constant. Only caveat: the *spec* is 3.1-beta (the SDK
release is stable, and the feature files already target it).

**Revised sequence:** (1) map ✓ · (2) **SDK upgrade 4.3→5.7** — repoint deep imports,
reconcile schema drift, delete hand-rolled fields/validation the models now enforce;
net with the protocol-level scenarios · (3) extract shared `validate_business_rules()`;
net with business-rule scenarios.

## SDK type-collision blocker (found mid-2b)

The adcp SDK codegen **inlines each JSON-Schema `$ref` into a per-message local
class instead of sharing one type.** Across `adcp.types.generated_poc`: **431 class
names are defined in 2+ modules** (of 3,712). Worst: `Account`×37, `Error`×35,
`Brand`×33, `Authentication`×7. So the same *concept* (a brand, an account) is many
different Python classes that don't `isinstance`-match and can't be shared. The
top-level `adcp.types` picks one representative per name; the real types live in the
fragile deep modules. This is why "I can't use it properly."

**Reframe (the e2e rationale is the answer):** tests should assert the **wire
contract** — the AdCP JSON shape the scenario defines — NOT the SDK Python types.
Then the 431-collision mess is irrelevant to the test/refactor goal; it only bites
the **production boundary** (request parsing / response serialization). So "fix the
SDK before anything meaningful" applies to the *boundary*, not the *tests* — and the
test goal can proceed on contract assertions while the SDK question is decided
separately.

**SDK-fix options (boundary only):** (a) upstream the shared-`$ref` fix to the adcp
codegen; (b) fork/vendor and fix the `datamodel-code-generator` config to emit shared
types (we already depend on `datamodel-code-generator` + `jsonref`); (c) generate our
own clean boundary models from the AdCP JSON Schema (the source of truth), using the
SDK only as schema provenance; (d) stay on 4.3 and route around. 2b (4.3→5.7) is
**blocked** until this is decided.
