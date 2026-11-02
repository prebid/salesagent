# BDD strict-marker debt

`tests/bdd/conftest.py` names item ids from this file in the `reason` text of
about a dozen xfail markers, so this file is the register those reasons point
at. Each item is either a production gap or a test defect that keeps a scenario
xfailed.

**Goal:** zero non-strict xfail markers. A scenario is `pass`, `fail`, or
`xfail-strict`.

This page was rewritten against the source after the boundary rework. The
mechanisms several items described — per-transport account resolution,
`enrich_identity_with_account`, the `*_raw` transport wrappers, and
`Transport.IMPL` — no longer exist. So this page restates the items that named
them in terms of what the code does, and it marks a closed gap as closed rather
than deleting the item, because conftest reasons still cite the ids.

## Where each item stands

| Item | Status | Basis |
|------|--------|-------|
| C1 / C2 (account not resolved per transport) | Restated — resolution closed, result scoping open | `src/core/resolved_identity.py:408-412`, `src/core/tools/media_buy_delivery.py:697` |
| C3 (cross-principal access) | Closed | reconciled against adcp 3.1.1; the gap was in the grading |
| C4 (pydantic `ValidationError` not translated) | Closed | `src/core/exceptions.py:1204-1216` |
| C5 (`include_package_daily_breakdown` no-op) | Open | `src/core/tools/media_buy_delivery.py:467` |
| C6 (date-range validation in the success envelope) | Closed | `src/core/tools/media_buy_delivery.py:187` |
| C7 (end-only `date_range` default) | Open, restated | `src/core/tools/media_buy_delivery.py:181-191` |
| C10 (description-only spec constraints) | Half closed | `src/core/tools/media_buy_delivery.py:29,132`; no geo validator |
| C11 (`reporting_period` echo) | Closed | `tests/bdd/conftest.py:2344-2348` records the graduation |
| B1 (Gherkin `pending_activation`) | Closed in the feature, dead rows in conftest | `tests/bdd/features/BR-UC-004-deliver-media-buy-metrics.feature:991,995` |
| B2 (`date_range` sent as a fake kwarg) | Closed | `tests/bdd/steps/domain/uc004_delivery.py:3909-3929` |
| B3 (symbolic resolution/ownership labels) | Closed | `tests/bdd/steps/domain/uc004_delivery.py:1438,1453,3932` |
| B4 (`sampling_method` on the wrong feature) | Closed | the two outlines are deleted; `BR-UC-004-deliver-media-buy-metrics.feature` carries a RETIRED note in their place |
| B5 (`webhook_credentials` wrong dispatch) | Closed | `tests/bdd/steps/domain/uc004_delivery.py:1412-1421,3737` |
| B6 (`disclosure_positions` filter) | Closed | `src/core/tools/creative_formats.py:402-404`; `tests/bdd/steps/generic/when_request.py:419-441` |
| B7 (UC-006 faked `AdCPValidationError`) | Closed | `SyncCreativesRequest.account` is required; the impl takes `AccountIdentity` |
| H1 / H2 (`_assert_partition_outcome` under-asserts) | Closed | `tests/bdd/steps/generic/then_payload.py:366-396` |

## Open items

### C1 / C2 — the resolved account does not narrow the delivery read

The original pair said the A2A skill dropped the `account` parameter and only
REST resolved it. Neither half survives: every transport hands the raw payload
to `serve`, and the resolver loads the account inside the single identity it
builds (`src/core/resolved_identity.py:408-412`), refusing one the principal may
not use (`src/core/database/repositories/account_lookup.py:36-64`). So an
inaccessible account is a typed refusal on all transports.
`GetMediaBuyDeliveryRequest` declares `account` as an optional field, which makes
the identity an `AccountIdentity` whenever a request names one.

What is still true is narrower: `get_media_buy_delivery` never reads
`identity.account`. It selects by tenant and principal —
`repo.get_by_principal(principal_id, media_buy_ids=...)`
(`src/core/tools/media_buy_delivery.py:697,699`) — so naming an account changes
which account must be accessible and nothing about which buys come back. Before
flipping the scenarios that assert an account-scoped read, ground the obligation
in the pinned spec: it is not settled here whether delivery results must be
narrowed by account.

- **Where conftest cites it:** the `T-UC-004-boundary-account` row
  (`tests/bdd/conftest.py:2485-2503`), whose only remaining substring is
  `impl-account_id present + not found` — an `impl` nodeid that can no longer
  exist.
- **Severity:** P2.

### C5 — `include_package_daily_breakdown` is a no-op

`GetMediaBuyDeliveryRequest` declares the field and production hard-codes
`daily_breakdown=None` (`src/core/tools/media_buy_delivery.py:467`). The valid
rows pass without the response differing, so the coverage is vacuous until the
Then step grades the shape differential too.

- **Unblocks:** populate `daily_breakdown` per package when the flag is set,
  strengthen the Then step, then flip to strict.
- **Severity:** P2.

### C7 — an end-only `date_range` reports the last 30 days

Production uses the buyer's dates only when BOTH are present
(`src/core/tools/media_buy_delivery.py:181`); otherwise the window is
`now - 30d` to `now` (`:191`). So an `end_date` supplied alone is ignored
entirely, not merely paired with a default start. The spec asks for the start to
default to the media buy's creation date.

- **Unblocks:** honour a one-sided window, defaulting the missing start to
  `MediaBuy.created_at`.
- **Where conftest cites it:** `tests/bdd/conftest.py:2442`
  (`T-UC-004-daterange-end-only`).
- **Severity:** P3.

### C10 — the geo `system` requirement has no validator

The attribution half of this item is closed: `_validate_attribution_window`
refuses a campaign-unit window with `interval != 1`
(`src/core/tools/media_buy_delivery.py:29,132`), and the controller calls it
before delegating, so every transport gets the same refusal.

The geo half is open. The pin states the `metro` / `postal_area` requirement for
`system` in a field description only, and no local extending class adds a model
validator for it, so production accepts `geo_level=metro` with no `system`.

- **Unblocks:** a `model_validator(mode="after")` on the extending geo model, or
  an upstream AdCP change adding the constraint to the schema.
- **Where conftest cites it:** `tests/bdd/conftest.py:2380`
  (`T-UC-004-boundary-reporting-dims`, row `geo with geo_level=metro but no
  system`).
- **Severity:** P3.

## Closed items, kept for the ids conftest still names

- **B4** — the two `sampling_method` outlines are deleted, not corrected. The
  field appears nowhere in the pinned 3.1 schemas:
  `get-media-buy-delivery-request.json` declares twelve properties and none is
  it, and no pinned enum carries `random` / `stratified` / `recent` /
  `systematic`. So the outlines' own "valid" rows asked this seller to accept an
  invented field, and their "invalid" row asked for a refusal the specification
  never requests. They passed anyway, which is what hid the defect: the DTO
  declares no `sampling_method`, so the accepted-shape strip refuses it as an
  undeclared field with `INVALID_REQUEST` — the identical rejection the "valid"
  rows received, so the Then could not tell "this enum value is refused" from
  "this field does not exist". `BR-UC-004-deliver-media-buy-metrics.feature`
  carries a RETIRED note where they were. The one real sampling concept in the
  pin is `failures_only`, a boolean on a different tool, already graded at
  `BR-UC-024-content-compliance.feature`.

- **C3** — production answers a non-owned media buy like a nonexistent one: no
  delivery data plus a `MEDIA_BUY_NOT_FOUND` advisory per id. That is the
  fail-closed answer L1 security asks for, and the entry's demand for a hard 403
  discloses that someone else's buy exists. The scenarios were the defect: they
  asserted a verdict word the step had to interpret, and their When injected a
  fabricated identity as a request kwarg, so no transport tested ownership. Both
  outlines name the outcome in their Examples, and the When presents a second
  principal's token.
- **C4** — `adcp_error_for` maps a pydantic `ValidationError` to
  `AdCPInvalidRequestError`, with the field and the `issues[]` entries derived
  from the error, and it checks that mapping before the `ValueError` branch the
  error subclasses (`src/core/exceptions.py:1204-1216`). Rows that failed only
  for the missing translation were graduated; `tests/bdd/conftest.py:2355-2364`
  records which, and that `geo_metro_missing_system` stayed because it did not
  xpass.
- **C6** — an inverted or equal window raises `AdCPValidationError`
  (`src/core/tools/media_buy_delivery.py:187`) instead of returning the error
  inside a success envelope.
- **B1** — the UC-004 feature no longer uses `pending_activation`. The
  status-filter boundary Examples name `pending_creatives` as the first enum
  value and list all seven statuses in the array row (feature lines 991, 995).
- **B2** — `_dispatch_date_range_partition` translates the symbolic label into
  real `start_date` / `end_date` kwargs
  (`tests/bdd/steps/domain/uc004_delivery.py:3909-3929`).
- **B3** — both the partition and the boundary When route through
  `_dispatch_ownership_partition`, which swaps the identity
  (`tests/bdd/steps/domain/uc004_delivery.py:1438,1453,3932`).
- **B5** — the credentials rows validate the reporting-webhook authentication at
  the `create_media_buy` boundary through
  `_validate_reporting_webhook_credentials`
  (`tests/bdd/steps/domain/uc004_delivery.py:1412-1421,3737`), not through
  `get_media_buy_delivery`.
- **B6** — production filters on `disclosure_positions` with AND semantics, and
  resolves a format's positions through `disclosure_capabilities` with a
  fallback to `supported_disclosure_positions`
  (`src/core/tools/creative_formats.py:299-328,402-404`). The `all_positions`
  step uses the eight real enum values
  (`tests/bdd/steps/generic/when_request.py:419-441`).
- **B7** — `SyncCreativesRequest.account` is required, so `_sync_creatives_impl`
  declares `AccountIdentity` and the boundary resolves the account before it
  runs; validation refuses a request that names none. The
  `_UC006_VALIDATION_XFAIL` set no longer exists in conftest.
- **H1 / H2** — `_assert_partition_outcome` has no bare `invalid` branch left.
  The Examples cell names the wire code and the assertion goes through the
  harness's `assert_wire_error` against the envelope the buyer received
  (`tests/bdd/steps/generic/then_payload.py:366-396`).

## Stale reason text in conftest

These three stay unfixed here, because `tests/bdd/conftest.py` is not this
file's to edit:

- The `T-UC-004-boundary-account` row and the `T-UC-004-boundary-status-filter`
  row both key on `impl-` / `pending_activation` substrings that no nodeid can
  match (`tests/bdd/conftest.py:2485-2503`, `:2613-2628`).
- Three reasons still cite C4 as the cause (`:2368`, `:2461`, `:2467`) although
  the translation landed; the rows that remain stayed for a different, unnamed
  reason.
- `is_impl` (`:1452`) and the `impl-` ledger substrings are dead with the
  transport.

## Lifecycle of an entry

1. File the item here with a stable id.
2. Name that id in the conftest marker's reason.
3. Close the gap in production or in the test.
4. Flip the marker to `strict=True`, or remove it when no rows stay xfailed.
5. Move the item to "Closed items" here with the file and line that closed it.
6. Delete the item once no conftest reason names its id.

Step 4 forces step 5: once you set `strict=True`, drift on that scenario fails
the suite instead of passing quietly.

## UC-026 connected to the suite, and what it now admits

UC-026 (package media buy) had never graded anything. Its 75 scenarios, its
2784-line step module and its own xfail tag set were all written and maintained,
while every node xfailed at fixture setup with `No harness wired for UC-026`.
Nothing flagged it, because the use case is absent from `dormant_scenarios.txt`
too, so it read as ordinary xfail volume.

The harness was never the missing piece. `MediaBuyDualEnv`'s first line says it
is "a composite environment for UC-026 and UC-003 BDD scenarios": it was built
for this and left unreferenced. UC-026 was disconnected in two independent
places, either of which alone was fatal:

1. no row in `_UC_BUCKET_ROUTES`, so no environment was ever built;
2. `tests.bdd.steps.domain.uc026_package_media_buy` absent from
   `pytest_plugins`, so no sentence had a binding.

Wiring both turns several hundred nodes that reported nothing into a few hundred
that pass and a smaller set that states precisely what is missing. Nothing
fails.

### Read this before trusting an xfail here

A tag in `_UC026_XFAIL_TAGS` xfails every parametrized row it carries, so
whole-tag xfail is a coverage decision rather than bookkeeping. Listing an
outline that fails two rows out of twelve converts the other ten from passing to
xpassed, which removes grading instead of adding it. That mistake was made and
measured here: routing the keyword, replacement and format-id outlines by tag
cut the passing count by more than half. Those are keyed by ROW in
`_UC026_PARTITION_SELECTIVE` instead. Only four tags fail wholesale on a2a, mcp
and rest alike, and only those four are in the set.

Every reason is the assertion the scenario reports. The misclassification
tripwire in `conftest.py` refuses a dormancy or a Given-side error dressed as a
production gap, and each entry below cleared it.

### Production is not ready — graded, not assumed

| Gap | Tag or rows |
|---|---|
| `format_ids` is neither defaulted to the product's formats when omitted nor echoed when supplied, though pinned 3.1 `core/package.json` declares the field on the returned package | `main-required-fields`, `main-explicit-formats` |
| a `format_id` the product does not carry is accepted instead of refused | `boundary-format-ids`, `partition-format-ids` (unsupported rows only) |
| `catalogs` is accepted but not echoed on the created package | `inv-089-2` |
| `price_breakdown` is absent from the create response, so the default `list_price == option rate` claim has nothing to read | `inv-196-3` |
| keyword and negative-keyword conflict validation, and empty-value validation, are missing on the REST update path (a2a and mcp pass the same rows) | the eight `kw` and `neg-kw` row entries |
| `targeting_overlay` is not replaced wholesale on update | `boundary-replacement`, `partition-replacement` (overlay rows only) |

### The harness is not ready — one scenario, stated as such

`@T-UC-026-ext-j` opens "the Buyer owns a media buy with a package that has
already settled". Nothing reaches that state: settlement is billing-side, no
buyer-facing request performs it, and no seeding path writes one. Its Given
creates the package and stops, so production receives an ordinary active package
and cancels it rather than refusing with `NOT_CANCELLABLE`.

Production is not failing there. It is answering a different question than the
scenario means to ask, and the xfail says so rather than blaming the seller.
Graduating it needs a way to persist a settled package, after which the scenario
grades the refusal for real.

The sibling precondition — an already-canceled package — is realized rather than
asserted: the Given creates the package and then cancels it through the real
update path, which is a buyer-facing operation. That scenario grades for real.

### Two defects this exposed, both fixed

- **A label mistaken for a key.** The feature names its product `prod-1` while
  the shared seed creates `prod_1`, and five steps asserted the two matched.
  Requests are built from the seeded row's own id, so the feature's spelling
  never reached the wire and the two were never required to agree. The package
  builder now resolves the label the way `pricing_option_id` already did, and
  only for the label the scenario declared, so a scenario deliberately naming an
  absent product still gets its refusal.
- **Two scenarios that could not run on any transport.** "Create package via
  MCP" and "Create package via REST" graded identical behaviour, differed only
  in an arbitrary budget, and the second contradicted its own title by naming an
  A2A task. Their `@mcp` and `@rest` tags put them in `_TRANSPORT_SPECIFIC_TAGS`,
  which skips parametrization on the premise that the When steps dispatch
  explicitly — UC-026's do not. They are one transport-independent scenario now,
  running on all three.
