# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)

@analysis-2026-03-09 @schema-v3.1
Feature: BR-UC-002 Create Media Buy
  As a Buyer (via Buyer Agent)
  I want to create a media buy for advertising inventory
  So that my advertising campaign is live on the publisher's ad server

  # Postconditions verified:
  #   POST-S1: Buyer knows their media buy has been created and is activating
  #   POST-S2: Buyer can track the media buy via media_buy_id
  #   POST-S3: Buyer knows each package's allocation, product, and pricing
  #   POST-S4: Buyer's advertising campaign is live (or activating) on the ad server
  #   POST-S5: Buyer receives an unambiguous success confirmation
  #   POST-S6: Buyer knows the request completed successfully
  #   POST-S7: Buyer knows their media buy is awaiting seller approval
  #   POST-S8: Seller knows there is a media buy requiring their review
  #   POST-S9: Buyer can track the pending media buy via task_id
  #   POST-S10: Buyer knows how to check approval progress
  #   POST-S11: Buyer knows the proposal was successfully executed with their total budget
  #   POST-S12: Buyer knows the media buy was rejected and the reason for rejection
  #   POST-F1: System state is unchanged on failure (all-or-nothing semantics)
  #   POST-F2: Buyer knows what failed, the specific error code, and the recovery classification
  #   POST-F3: Buyer knows how to fix the issue and retry (correctable) or escalate (terminal)

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant exists with completed setup checklist
    And the Buyer is authenticated



  @T-UC-002-main @main-flow @post-s1 @post-s2 @post-s3 @post-s4 @post-s5 @post-s6
  Scenario: Auto-approved media buy with valid package-based request
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request with:
    | field          | value                        |
    | account        | account_id "acc-001"         |
    | brand          | domain "acme.com"            |
    | start_time     | {1 day from now}             |
    | end_time       | {30 days from now}           |
    And the request includes 2 packages with valid product_ids
    And each package has a positive budget meeting minimum spend
    And all packages use the same currency "USD"
    And each package has a valid pricing_option_id
    And the account "acc-001" exists and is active
    And the ad server adapter is available
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    And the response status should be "completed"
    And the response should include a "media_buy_id"
    And the response should include packages with allocations
    And each package should include product_id, budget, and pricing details
    # POST-S1: Buyer knows their media buy has been created and is activating
    # POST-S2: Buyer can track the media buy via media_buy_id
    # POST-S3: Buyer knows each package's allocation, product, and pricing
    # POST-S4: Buyer's advertising campaign is live (or activating) on the ad server
    # POST-S5: Buyer receives an unambiguous success confirmation
    # POST-S6: Buyer knows the request completed successfully

  @T-UC-002-alt-manual @alternative @alt-manual @post-s7 @post-s8 @post-s9 @post-s10
  Scenario: Manual approval required -- media buy enters pending state
    Given the tenant has "human_review_required" set to true
    And a valid create_media_buy request with account "acc-001"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy submitted spec
    And the response status should be "submitted"
    # Reconciled to spec 3.1.1 (PR #1567 round-2 item 2): create-media-buy-response.json
    # oneOf CreateMediaBuySubmitted requires status="submitted" + task_id and has
    # NO media_buy_id/confirmed_at/revision — they land on the task's completion
    # artifact. The pre-3.1.1 assertions (media_buy_id + workflow_step_id) claimed
    # confirmation of a not-yet-committed buy. Mirrors the BR-UC-003 reconciliation.
    And the response should contain a task_id
    And the response should NOT contain "media_buy_id" field
    And the response should NOT contain "confirmed_at" field
    And the response should NOT contain "revision" field
    And a Slack notification should be sent to the Seller
    # POST-S7: Buyer knows their media buy is awaiting seller approval
    # POST-S8: Seller knows there is a media buy requiring their review
    # POST-S9: Buyer can track the pending media buy via task_id
    # POST-S10: Buyer knows how to check approval progress

  @T-UC-002-alt-manual-reject @alternative @alt-manual @post-s12
  Scenario: Seller rejects a pending media buy
    Given a media buy exists in "pending_approval" state
    When the Seller rejects the media buy with reason "Budget too low for Q1 campaign"
    Then the media buy status should be "rejected"
    And the response should include "rejection_reason" containing "Budget too low"
    And the Buyer should be notified via webhook
    # POST-S12: Buyer knows the media buy was rejected and the reason for rejection
    # --- Alt: ASAP Start Timing ---

  @T-UC-002-alt-asap @alternative @alt-asap @post-s1 @post-s4
  Scenario: ASAP start timing resolves to current UTC
    Given a valid create_media_buy request with start_time "asap"
    And the account exists and is active
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the system should resolve start_time to current UTC
    And the campaign should be immediately activating
    And the response should include resolved start_time (not literal "asap")
    # POST-S1: Buyer knows their media buy has been created and is activating
    # POST-S4: Buyer's advertising campaign is live (or activating) on the ad server
    # --- Alt: With Inline Creatives ---

  @T-UC-002-alt-creatives @alternative @alt-creatives @post-s1
  Scenario: Media buy with inline creative uploads
    Given a valid create_media_buy request with inline creatives
    And the account exists and is active
    And each creative has a valid format_id, name, and assets with URL and dimensions
    And the creative agent has the referenced formats registered
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the system should upload the creatives to the creative library
    And the system should assign the uploaded creatives to packages
    And the response should include the created media buy with creative assignments
    When the Buyer Agent reads the created media buy back with get_media_buys
    Then the created package should report creative approvals for exactly the inline creatives
    # POST-S1: Buyer knows their media buy has been created and is activating
    # LOCAL CORRECTION: the readback is added. The create response's creative_assignments
    # (core/package.json, the create_media_buy.mdx example reads
    # packages[0].creative_assignments) say what the seller answered; the readback says what
    # it kept, as a buyer reads it later. This seller declares
    # media_buy.features.inline_creative_management and no creative library, so at the pin
    # get_media_buys creative_approvals is the buyer's readback surface for an inline
    # creative, as the inline_creatives_without_sync storyboard reads it after its update.
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/create_media_buy.mdx
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/package.json pointer=/properties/creative_assignments
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/get_media_buys.mdx
    # The inline creatives come from the create Given. The former line "the request includes
    # packages with inline "creatives" array" bound UC-003's update-request catch-all, which
    # builds an update body and refuses that wording.
    # --- Alt: Proposal-Based ---

  @T-UC-002-alt-proposal @alternative @alt-proposal @post-s1 @post-s2 @post-s3 @post-s11
  Scenario: Proposal-based media buy executes get_products proposal
    Given a valid create_media_buy request with:
    | field          | value                        |
    | proposal_id    | prop-2026-001                |
    | total_budget   | amount 5000, currency "USD"  |
    | account        | account_id "acc-001"         |
    | brand          | domain "acme.com"            |
    | start_time     | 2026-04-01T00:00:00Z         |
    | end_time       | 2026-04-30T23:59:59Z         |
    And the account "acc-001" exists and is active
    And proposal "prop-2026-001" exists and has not expired
    And the proposal has 3 product allocations
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the system should derive packages from proposal allocations
    And the total_budget should be distributed per allocation percentages
    And the response should include the created media buy with derived packages
    # POST-S1: Buyer knows their media buy has been created and is activating
    # POST-S2: Buyer can track the media buy via media_buy_id
    # POST-S3: Buyer knows each package's allocation, product, and pricing
    # POST-S11: Buyer knows the proposal was successfully executed with their total budget

  @T-UC-002-ext-a @extension @ext-a @error @post-f1 @post-f2 @post-f3
  Scenario: Budget validation failure -- total budget is zero
    Given a valid create_media_buy request
    And the account exists and is active
    But all package budgets sum to 0
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "BUDGET_TOO_LOW"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the error should include "field" field with value "packages"
    # The ARRAY, not an element: this rejection comes from req.get_total_budget(),
    # which sums EVERY package, so no single entry is at fault. The old
    # "packages[].budget" named neither the array nor an element (salesagent-rfxfu).
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-b: Product Not Found ---

  @T-UC-002-ext-b @extension @ext-b @error @post-f1 @post-f2 @post-f3
  Scenario: Product not found in tenant catalog
    Given a valid create_media_buy request
    And the account exists and is active
    But a package references product_id "prod-nonexistent" which does not exist
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "PRODUCT_NOT_FOUND"
    And the error recovery should be "correctable"
    And the wire error details should include missing_product_ids "prod-nonexistent"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed -- the id the request named reaches the buyer as a
    # VALUE in errors[0].details, not in the message, which is a function of the CODE
    # through CODE_TABLE and cannot carry request data. Before salesagent-3dawm.9 the guard
    # computed this id into a local and the raise discarded it, so it reached neither the
    # buyer's wire nor the server log.
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-c: DateTime Validation Failure ---

  @T-UC-002-ext-c @extension @ext-c @error @post-f1 @post-f2 @post-f3
  Scenario: Start time is in the past
    Given a valid create_media_buy request
    And the account exists and is active
    But a valid create_media_buy request with start_time "2020-01-01T00:00:00Z"
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the wire error details should include start_time "2020-01-01 00:00:00+00:00"
    And the wire envelope should not carry the marker "root="
    And the error should include "suggestion" field
    # POST-F2 hardening (locally added): the submitted start_time must reach the buyer as its
    # VALUE. It now travels in errors[0].details, not in the message: the buyer-facing sentence
    # is a function of the error CODE through CODE_TABLE and cannot carry request data.
    # req.start_time is adcp StartTiming (a pydantic RootModel), so str() of it yields
    # "root=datetime.datetime(2020, ...)"; production must stringify the UNWRAPPED
    # computed_start_time instead. The marker check now scans the WHOLE envelope, so it also
    # covers the details slot the value just moved into — strictly stronger than the old
    # message-scoped scan, and non-vacuous precisely because of that move.
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue

  @T-UC-002-ext-c-end @extension @ext-c @error
  Scenario: End time is before start time
    Given a valid create_media_buy request
    And the account exists and is active
    But end_time is before start_time
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the wire envelope should not carry the marker "root="
    And the error should include "suggestion" field
    # POST-F2 hardening (locally added): this message interpolates BOTH times; the
    # start_time side is a StartTiming RootModel and must render as its value, not
    # the model repr (see the sibling start-time-in-the-past scenario).
    # --- ext-d: Currency Not Supported ---

  @T-UC-002-ext-d @extension @ext-d @error @post-f1 @post-f2 @post-f3
  Scenario: Currency not supported by tenant
    Given a valid create_media_buy request
    And the account exists and is active
    But the packages use currency "XYZ" which is not in the tenant's CurrencyLimit table
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-e: Duplicate Products ---

  @T-UC-002-ext-e @extension @ext-e @error @post-f1 @post-f2 @post-f3
  Scenario: Duplicate product_id across packages
    Given a valid create_media_buy request
    And the request includes 2 packages with valid product_ids
    And the account exists and is active
    But both packages reference the same product_id "prod-001"
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the wire error details should include duplicate_product_ids "prod-001"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-f: Targeting Validation Failure ---

  @T-UC-002-ext-f @extension @ext-f @error @post-f1 @post-f2 @post-f3
  Scenario: Targeting overlay contains unknown field
    Given a valid create_media_buy request
    And the account exists and is active
    But a package targeting_overlay contains unknown field "weather_targeting"
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue

  # Retargeted (salesagent-3cs7o.22): this used to send key_value_pairs as "a managed-only
  # dimension", a concept the pinned core/targeting.json does not declare and a field it
  # does not have. The field is deleted; the same payload is now exactly a targeting
  # dimension the pin does not declare, and the boundary answers it under CLAUDE.md
  # pattern 7 as measured on every in-process transport: INVALID_REQUEST, recovery
  # "correctable", suggestion present. In production mode (extra="ignore") the field is
  # dropped and the buy is created; the harness has no production-mode Given, so that
  # half is recorded for the reconciliation ticket rather than graded here.
  @T-UC-002-ext-f-managed @extension @ext-f @error
  Scenario: Targeting overlay sets a targeting dimension the pin does not declare
    Given a valid create_media_buy request
    And the account exists and is active
    But a package targeting_overlay sets a targeting dimension the pin does not declare
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field

  @T-UC-002-ext-f-geo @extension @ext-f @error
  Scenario: Targeting overlay has geo include/exclude overlap
    Given a valid create_media_buy request
    And the account exists and is active
    But a package targeting_overlay includes "US" in both geo_countries and geo_countries_exclude
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the wire error details should include geo_overlaps "US"
    And the error should include "suggestion" field
    # The colliding VALUE the scenario itself supplied reaches the buyer in
    # errors[0].details, not in the message -- the sentence is a function of the CODE
    # through CODE_TABLE. Before salesagent-3dawm.9 the validators rendered
    # "geo_countries/geo_countries_exclude conflict: values US appear in both ..." and
    # the raise discarded it, so the buyer learned only that targeting was rejected.
    # details now carries {include, exclude, values} per conflicting pair, so all four
    # targeting violation kinds stay distinguishable under one INVALID_REQUEST code.
    # --- ext-g: Creative Validation Failure ---

  @T-UC-002-ext-g @extension @ext-g @error @post-f1 @post-f2 @post-f3
  Scenario: Creative missing required URL field
    Given a valid create_media_buy request with inline creatives
    And the account exists and is active
    But a creative is missing the required URL in assets
    And the creative format is not generative
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the response contains error code INVALID_REQUEST
    And the response error field is packages[0].creatives[0].assets.banner_image.url
    # The two Thens that stood here — "the operation should fail" and "the error should
    # include \"suggestion\" field" — were satisfied by ANY error, so this scenario
    # reported green while saying nothing about which refusal arrived. It stayed green
    # through a repair that changed the refusal from union_tag_not_found to url_parsing.
    # Code AND field now, so a request that dies for an unrelated reason fails here.
    # THE EXPECTED PATH IS THE PIN'S, not pydantic's. core/error.json @ 3.2 makes `field`
    # "the field path associated with the error in JSONPath-lite format", MUST-set to
    # issues[0].pointer translated — a path over the BUYER'S OWN DOCUMENT. This line read
    # `...assets.primary.AssetVariant.image.url` and both extra segments were pydantic's:
    # `AssetVariant` (adcp 9 spells it `tagged-union[ImageAsset,...21 more...]`) is a union
    # ARM NAME and `image` is the discriminator TAG VALUE. image-asset.json declares `url`
    # directly and no `image` member, so what the buyer sent at `assets.primary` is
    # `{asset_type, url, width, height}` and the only conformant path to the blank URL is
    # the one above (salesagent-lkgmg).
    #
    # THE `banner_image` SEGMENT IS THE FIXTURE'S OWN ASSET KEY, not a spec constant — it
    # moves whenever the inline-creative Given moves, and it has moved once: it read
    # `primary` while the harness carried a per-id format-spec stub whose asset_id was
    # `primary`. With the stub deleted the fixture keys its asset the way the pinned capture
    # declares it for display_300x250_image, so the pointer follows. WHAT IS GRADED here is
    # unchanged by that: the path must be over the buyer's own document, with no union-arm
    # or discriminator-tag segments. A future fixture move updates this segment and nothing
    # else; if a change wants to alter the SHAPE of the path, that is a different claim and
    # needs the salesagent-lkgmg reasoning re-done.
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-h: Format ID Validation Failure ---

  @T-UC-002-inv-015-6 @invariant @BR-RULE-015 @error
  Scenario: INV-6 holds -- creative asset lacking a valid asset_type discriminator is rejected
    Given a create_media_buy request with an inline creative whose assets map value lacks an "asset_type" field
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the error code should be "INVALID_REQUEST"
    And the error should reference the unresolvable asset_type discriminator
    And the error should include "suggestion" field
    # BR-RULE-015 INV-6: assets value lacking asset_type or carrying an unregistered value (not one of the 14 AssetVariant types) -> INVALID_REQUEST
    # The line "the response should indicate a validation error" is gone: its step asserts
    # VALIDATION_ERROR, which contradicts the INVALID_REQUEST line beside it. Each asset slot
    # value is a oneOf over asset-union.json selected by its asset_type, so a value without
    # one violates the schema, which is INVALID_REQUEST.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/creative-asset.json pointer=/properties/assets
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/INVALID_REQUEST
    # --- ext-h: Format ID Validation Failure ---
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-ext-h @extension @ext-h @error @post-f1 @post-f2 @post-f3
  Scenario: Format ID is a plain string instead of object
    Given a valid create_media_buy request
    And the account exists and is active
    But a package format_id is a plain string "banner_300x250" instead of a FormatId object
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue

  @T-UC-002-ext-h-agent @extension @ext-h @error
  Scenario: Format ID references unregistered creative agent
    Given a valid create_media_buy request
    And the account exists and is active
    But a package format_id references an unregistered agent_url
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "suggestion" field
    # A legacy format_id the seller cannot normalize, or one that resolves and satisfies
    # nothing the product declares, is coded by the package selector contract:
    # UNSUPPORTED_FEATURE in both cases. REFERENCE_NOT_FOUND is the generic fallback "when
    # no resource-specific not-found code applies", and this contract names one. Same
    # code as the unregistered_agent rows of @T-UC-002-partition-format-id-structure.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/description
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/properties/format_ids/description
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/REFERENCE_NOT_FOUND
    # --- ext-i: Authentication Error ---

  @T-UC-002-ext-i @extension @ext-i @error @post-f1 @post-f2 @post-f3
  Scenario: Authentication failure -- no principal in context
    Given the Buyer has no authentication credentials
    And a valid create_media_buy request
    When the Buyer Agent sends the create_media_buy request
    # The request has to be OTHERWISE VALID or this scenario grades the wrong refusal.
    # Without the second Given it dispatched a near-empty body, so the boundary answered
    # INVALID_REQUEST for three missing required fields and the AUTH_MISSING assertion
    # below was measuring schema validation. The subject is the absent credential.
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "AUTH_MISSING"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-j: Adapter Execution Failure ---

  @T-UC-002-ext-j @extension @ext-j @error @post-f1 @post-f2 @post-f3
  Scenario: Adapter execution failure -- ad server returns error
    Given a valid create_media_buy request that passes all validation
    And the account exists and is active
    But the ad server adapter returns an error
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And no media buy record should be persisted in the database
    And the response status should be "failed"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure (all-or-nothing)
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-k: Maximum Daily Spend Exceeded ---

  @T-UC-002-ext-k @extension @ext-k @error @post-f1 @post-f2 @post-f3
  Scenario: Daily budget exceeds maximum daily spend cap
    Given a valid create_media_buy request
    And the account exists and is active
    And the tenant has max_daily_package_spend configured at 1000
    But a package has budget 50000 over a 2-day flight (daily = 25000)
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    # BUDGET_EXCEEDED, not BUDGET_TOO_LOW. 3.1/enums/error-code.json separates them by
    # DIRECTION and says so in BUDGET_EXCEEDED's own text: "Operation would exceed the
    # allocated budget for the media buy or package. Distinct from BUDGET_EXHAUSTED
    # (already spent) and BUDGET_TOO_LOW (below minimum)." 50000 over 2 days is 25000/day
    # against a 1000/day ceiling — exceeding a maximum, not falling below a minimum. The
    # scenario asserted BUDGET_TOO_LOW and was ledgered as a "spec-production gap pending
    # upstream regen"; the pin agrees with production, so the scenario was the stale half.
    And the error code should be "BUDGET_EXCEEDED"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-l: Proposal Not Found or Expired ---

  @T-UC-002-ext-l @extension @ext-l @error @post-f1 @post-f2 @post-f3
  Scenario: Proposal not found or expired
    Given a valid create_media_buy request with proposal_id "prop-expired"
    And the account exists and is active
    But proposal "prop-expired" does not exist or has expired
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "PROPOSAL_EXPIRED"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-m: Proposal Budget Mismatch ---

  @T-UC-002-ext-m @extension @ext-m @error @post-f1 @post-f2 @post-f3
  Scenario: Proposal total budget below guidance minimum
    Given a valid create_media_buy request with proposal_id and total_budget amount 10
    And the account exists and is active
    But the proposal's total_budget_guidance.min is 1000
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "BUDGET_TOO_LOW"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-n: Pricing Option Validation Failure ---

  @T-UC-002-ext-n @extension @ext-n @error @post-f1 @post-f2 @post-f3
  Scenario: Pricing option not found on product
    Given a valid create_media_buy request
    And the account exists and is active
    But a package references pricing_option_id "po-nonexistent" not found on the product
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    # INVALID_PRICING_OPTION: "at the request level for create_media_buy when the submitted
    # pricing option cannot be resolved" -- not VALIDATION_ERROR.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/INVALID_PRICING_OPTION
    And the error code should be "INVALID_PRICING_OPTION"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue

  @T-UC-002-ext-n-bid @extension @ext-n @error
  Scenario: Auction pricing without bid_price
    Given a valid create_media_buy request
    And the account exists and is active
    And a package selects an auction pricing option but provides no bid_price
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field

  @T-UC-002-ext-n-floor @extension @ext-n @error
  Scenario: Bid price below floor price
    Given a valid create_media_buy request
    And the account exists and is active
    And a package has bid_price 0.50 but floor_price is 1.00
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field

  # Two siblings were written here and removed: a fixed option with no rate, and a product
  # with no options at all. Neither state can exist -- check_fixed_has_rate rejects the
  # first at INSERT and the enforce_min_one_pricing_option trigger rejects the second -- so
  # production can never meet either, and a scenario demanding its response graded nothing.

  @T-UC-002-ext-n-min-spend @extension @ext-n @error
  Scenario: Package budget below the pricing option's minimum spend
    # pricing-options/*.json declare min_spend_per_package. A budget under it cannot buy
    # the option, and the buyer can fix it by raising the budget — so it is correctable,
    # not the seller's own defect.
    Given a valid create_media_buy request
    And the account exists and is active
    And a package budget of 500 against a pricing option requiring a minimum spend of 1000
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    # BUDGET_TOO_LOW: "Budget is below the seller's minimum" -- the option's minimum spend is
    # the seller's minimum for this package, the same code the tenant minimum answers with.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/BUDGET_TOO_LOW
    And the error code should be "BUDGET_TOO_LOW"

    # --- ext-o: Creative Not Found in Library ---

  @T-UC-002-ext-webhook-ssrf @extension @ext-webhook-ssrf @error @post-f1 @post-f2 @post-f3
  Scenario: Reporting webhook URL targeting a blocked host is rejected
    Given a valid create_media_buy request
    And the request includes a reporting_webhook with url "http://169.254.169.254/latest/meta-data/"
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error recovery should be "correctable"
    And the error field should contain "reporting_webhook.url"
    And the error should include "suggestion" field
    # WHY THE FIELD LINE IS HERE, and it is the only informative assertion of the four.
    # VALIDATION_ERROR + correctable + a suggestion is the shape of EVERY correctable
    # refusal of a create_media_buy, and message/suggestion are pure functions of the
    # code via CODE_TABLE — so without the field pointer this scenario passed on any
    # correctable refusal, not on the SSRF gate it names. Refusing the metadata URL and
    # refusing for an unrelated reason were the same observable
    # (salesagent-8qela.172). field=reporting_webhook.url is what
    # reject_unsafe_webhook_registration_url passes at
    # media_buy_create.py:2102-2106, and the integration sibling
    # (test_webhook_url_ingest_refusal.py) already grades exactly this.
    # Repo-local SSRF policy (ungraded extension): reuses AdCP 3.1.1
    # VALIDATION_ERROR / recovery=correctable enum values + suggestion on
    # MCP/REST/A2A tool transports. Schema is silent on SSRF. A2A-native
    # push-config endpoints (message/send configuration,
    # setTaskPushNotificationConfig) map the same gate to InvalidParamsError
    # with the AdCP VALIDATION_ERROR envelope in data= — unit-pinned, not this scenario.
    # recovery=correctable comes from error-code.json's enumMetadata.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/INVALID_REQUEST

  @T-UC-002-ext-o @extension @ext-o @error @post-f1 @post-f2 @post-f3
  Scenario: Creative IDs not found in library
    Given a valid create_media_buy request
    And the account exists and is active
    But a package creative_assignment references creative_id "cr-nonexistent"
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    # 3.1.1 enums/error-code.json: "Sellers MUST return this code uniformly for any
    # creative_id not owned by the calling account". create_media_buy is not exempt
    # from a MUST that update_media_buy already honours (@T-UC-003-ext-i) -- one
    # condition, one code, whichever tool the buyer reached it through.
    And the error code should be "CREATIVE_NOT_FOUND"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-p: Creative Format Mismatch ---

  @T-UC-002-ext-p @extension @ext-p @error @post-f1 @post-f2 @post-f3
  Scenario: Creative format does not match product supported formats
    Given a valid create_media_buy request
    And the account exists and is active
    But a creative's format_id does not match any of the product's supported format_ids
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    # 3.1.1 enums/error-code.json codes VALIDATION_ERROR as "violates business rules
    # beyond schema validation", which is what a format outside the product's declared
    # set is. CREATIVE_REJECTED reads "Creative failed content policy review" -- the
    # creative is fine here, the ASSIGNMENT is what the product does not permit. Same
    # reading as @T-UC-006-ext-k and @T-UC-006-rule-039-inv2 on sync_creatives.
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-q: Creative Upload Failed ---

  @T-UC-002-ext-q @extension @ext-q @error @post-f2 @post-f3
  Scenario: Creative upload to ad server fails
    Given a valid create_media_buy request with inline creatives that passes all validation
    And the account exists and is active
    But the ad server rejects the creative upload
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "SERVICE_UNAVAILABLE"
    # A retryable ad-server failure must reach the buyer as retryable. Several
    # sibling scenarios encode this same pairing, all of them in feature files
    # nothing binds, so none of them executes; this is the first place it is
    # asserted on a wire a buyer actually reads.
    And the error recovery should be "transient"
    And the error should include "suggestion" field
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue

  # --- A canonical creative's click destination reaches the ad server ---
  #
  # The sibling above grades that the upload HAPPENS and that its failure reaches the buyer.
  # These grade what the upload CARRIES, which is a separate obligation and an unobserved
  # one: the click destination has no response field, so a seller that drops it answers the
  # buyer success and ships an ad nobody can click. Nothing reports that.
  #
  # core/assets/url-asset.json states the receiver's rule normatively: "`url_type` declares
  # the mechanism a receiver uses to invoke the URL ... Senders SHOULD include `url_type` on
  # every URL asset. When `url_type` is absent, receivers SHOULD fall back to the format's
  # `url-asset-requirements.role` ... When neither `url_type` nor a format-side `role` is
  # available, receivers MUST NOT silently pick a mechanism; they SHOULD reject the manifest."
  # core/asset-group-vocabulary.json, which marks itself normative, declares
  # `landing_page_url` as "Click-through destination URL for the ad" with six aliases, and
  # describes itself as codifying a shared vocabulary "for the most common slot ROLES" --
  # which is what makes resolving an asset_group_id through it a role lookup and not a guess.
  #
  # WHAT WAS WRONG BEFORE. extract_click_url matched a hardcoded CLICKTHROUGH_ASSET_IDS list
  # of guessed names. Measured against that registry it overlapped in two of six members and
  # did not contain `landing_page_url` -- the id fourteen of the sixteen canonical kinds
  # declare -- so the declared click destination was dropped on every canonical creative.
  #
  # STORYBOARD. Partly graded, and the graded part decides the design rather than this
  # obligation. Three files under dist/compliance/3.1.1 carry `landing_page_url`
  # (universal/canonical-format-validate-input.yaml and native_in_feed.yaml under both
  # protocols/creative and domains/creative), and in every one the asset is
  # {asset_type: "url", url: ...} with NO `url_type` and the graded expectation is
  # ACCEPTANCE. So the registry lookup is mandatory: without it those fixtures carry neither
  # signal and the SHOULD would demand rejecting the pin's own scenarios. The obligation
  # these scenarios grade -- that the destination REACHES THE AD SERVER, and that an
  # unidentified one is refused -- is ungraded there, under the media-buy protocol or any
  # other.
  #
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/assets/url-asset.json pointer=/description
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/asset-group-vocabulary.json
  # @source repo=adcp ref=v3.2.7 path=static/compliance/source/universal/canonical-format-validate-input.yaml

  # The tag carries the `T-UC-002-ext-` prefix because this IS an extension scenario and it
  # needs exactly what the other ext scenarios need: the create chain plus
  # dispatch_mode="create", so the shared When dispatches the request the Givens built. That
  # prefix is what the uc002-ext env route keys on, so no new wiring is added. Without it the
  # scenario lands on the uc002-not-wired catch-all, which xfails at FIXTURE SETUP -- it
  # would dispatch nothing and grade nothing while appearing in the corpus, which is the
  # dormancy this feature's own comments record twice.
  @T-UC-002-ext-canonical-clickthrough @extension @creative @post-f2
  Scenario Outline: a canonical creative's declared click destination reaches the ad server - <signal>
    Given a valid create_media_buy request with a canonical creative signalling its clickthrough by "<signal>"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the ad server receives that creative with its clickthrough URL

    Examples:
      | signal                        |
      | url_type                      |
      | the canonical slot name alone |

    # THE TWO RUNGS, as inputs, and the rows differ in exactly one thing: which single
    # signal the url asset carries.
    #   url_type                      -> the pin's first step, what senders SHOULD do. Keyed
    #                                    `mystery`, a non-canonical asset_group_id the
    #                                    registry explicitly permits, so ONLY this rung can
    #                                    answer.
    #   the canonical slot name alone -> keyed `landing_page_url` with no url_type, so ONLY
    #                                    the registry lookup can answer. A seller that
    #                                    implemented the first rung alone passes the row
    #                                    above and reddens here.
    # BOTH Thens on each row, because either alone is satisfiable by the wrong behaviour: the
    # buy succeeding says nothing about what the ad server got (that is the silence this
    # grades), and the upload assertion alone would pass on a seller that uploaded a creative
    # for a buy it then failed to create.

  @T-UC-002-ext-canonical-clickthrough-unnamed @extension @creative @post-f2
  Scenario: a url asset the manifest never identifies is not guessed to be the clickthrough
    Given a valid create_media_buy request with a canonical creative signalling its clickthrough by "nothing the pin recognises"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the ad server receives that creative with no clickthrough

    # core/assets/url-asset.json at the GOVERNING version: "When neither `url_type` nor a
    # format-side role is available, receivers MUST NOT silently pick a mechanism." This
    # creative carries a perfectly good URL under an asset_group_id the registry does not
    # define, so the seller has been told a URL exists and has NOT been told what it is for,
    # and it must not ship as the click destination.
    #
    # THE CONTROL for the outline above, and the reason those two rows are not satisfied by a
    # reader that returns the first url asset it finds. Without this row the registry lookup
    # is indistinguishable from a re-invented guess list.
    #
    # THE BUY STILL SUCCEEDS, deliberately. At 3.1.1 the sentence continued "; they SHOULD
    # reject the manifest" and this scenario demanded VALIDATION_ERROR; that clause was
    # REMOVED by 3.2.3, and a version dropping a remedy is declining to require it. Refusing
    # would make this seller stricter than the spec on a buyer-facing path, and the costs are
    # not symmetric -- reject the manifest and the buyer loses the buy, return no clickthrough
    # and they lose a clickthrough. Measured, so the change is on spec grounds and not test
    # grounds: the refusal reddened no graded storyboard step, because the seven
    # dist/compliance/3.2.3 fixtures that ship a url asset with no `url_type` all key it
    # `landing_page_url`, which the registry rung identifies.
    #
    # WHAT THE SELLER STILL OWES is that this is not SILENT, and that part is not graded here
    # because it cannot be yet: the sentence naming the asset goes to the log, since
    # CreateMediaBuySuccess declares no `errors[]` for the advisory lane and the `warnings[]`
    # array that is the right carrier takes a code from a closed three-member enum with no
    # member meaning this. The sentence itself is graded by
    # tests/unit/test_url_asset_identification.py; the wire gap is filed.
    # POST-F2: Buyer knows what the seller did with the manifest

  # --- A canonical audio buy is accepted, dimensions and all ---

  @T-UC-002-ext-canonical-audio @extension @creative @post-f2
  Scenario: a canonical audio creative carries no dimensions and the buy is still accepted
    Given a valid create_media_buy request with a canonical audio creative
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the ad server receives that creative with its media URL

    # The pre-adapter asset gate demanded a width and a height of every creative whatever its
    # kind. An audio asset has neither -- formats/canonical/audio_hosted.json declares
    # `audio_main` as asset_type "audio" -- so a canonical audio_hosted creative resolved its
    # URL, correctly produced no dimensions, and was refused VALIDATION_ERROR however
    # correctly its manifest was authored.
    #
    # The manifest deliberately carries ONLY `audio_main`. The kind also declares
    # `companion_image`, and including one would supply dimensions and hide the obligation.
    # That is also why the fix cannot be "does this kind declare a sized slot": audio_hosted
    # does, so that predicate refuses this very manifest.
    #
    # @source repo=adcp ref=v3.2.7 path=dist/schemas/3.2.7/formats/canonical/audio_hosted.json

  # --- A product may rename the slot its media arrives in ---

  @T-UC-002-ext-canonical-product-slots @extension @creative @post-f2
  Scenario: a product that renames the canonical media slot can still be bought
    Given a valid create_media_buy request whose product renames the canonical media slot
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the ad server receives that creative with its media URL

    # core/product-format-declaration.json: "Products MAY override the default -- make
    # headline required, narrow cta to a value enum, or remove slots the surface doesn't
    # consume." Those three narrow WITHIN the kind's vocabulary, so a kind-only reader is a
    # safe superset for them. The same schema also shows a declaration REPLACING the list
    # outright -- its audio_hosted example declares `script` and `offering_ref`, which no
    # kind declares -- and a product that renames a MEDIA slot that way names an id the
    # kind's vocabulary does not contain.
    #
    # THE ASSET ID IS THE CONTROL. `hero_shot` is named by no slot of any canonical kind, no
    # member of MEDIA_ASSET_FALLBACK_IDS, and no entry or alias of
    # core/asset-group-vocabulary.json. So there is no source other than the product's own
    # declaration that could tell the seller where this creative's media is: the scenario
    # reddens unless that declaration was consulted, which a demonstration keyed to a name
    # some other list also carries could not establish.
    #
    # @source repo=adcp ref=v3.2.7 path=dist/schemas/3.2.7/core/product-format-declaration.json

  @T-UC-002-ext-canonical-product-slots-inline @extension @creative @post-f2
  Scenario: an inline creative keyed to the slot its product renames is uploaded and assigned
    Given a valid create_media_buy request uploading an inline creative keyed to the slot its product renames
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response should succeed
    And the system should upload the creatives to the creative library
    And the system should assign the uploaded creatives to packages

    # THE SAME CREATIVE AS THE SCENARIO ABOVE, SENT INLINE. create_media_buy syncs a
    # package's inline creatives before the package exists, so that sync names no
    # assignment -- and a sync that assigns nothing holds a canonical manifest to its kind's
    # slots, where `image` requires `image_main`. This creative is assigned the moment the
    # buy exists, and creative-manifests.mdx § Basic structure keys its assets "by
    # asset_group_id from the matched declaration's params.slots[]": the product's, which
    # names `hero_shot` and not `image_main`. Held to the kind, it was refused for a slot
    # it was never owed, and the refusal came back as CREATIVE_REJECTED, a content-policy
    # code. update_media_buy passes its assignments to the sync and already graded the same
    # creative against the declaration, so the two tools gave the same input two answers.
    # @source repo=adcp ref=v3.2.7 path=docs/creative/creative-manifests.mdx section="Basic structure"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product-format-declaration.json
    # --- ext-r: Account Not Found ---

  @T-UC-002-ext-r @extension @ext-r @error @post-f1 @post-f2 @post-f3
  Scenario: Account not found -- explicit account_id
    Given a valid create_media_buy request with account_id "acc-nonexistent"
    But the account_id does not exist in the seller's account store
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "ACCOUNT_NOT_FOUND"
    And the error recovery should be "terminal"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows to escalate (terminal)

  @T-UC-002-ext-r-nk @extension @ext-r @error
  Scenario: Account not found -- natural key
    Given a valid create_media_buy request with account natural key brand "unknown.com" operator "unknown.com"
    But no account matches the brand + operator combination
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "ACCOUNT_NOT_FOUND"
    And the error recovery should be "terminal"
    And the error should include "suggestion" field
    # --- ext-s: Account Setup Required ---

  @T-UC-002-ext-s @extension @ext-s @error @post-f1 @post-f2 @post-f3
  Scenario: Account requires setup before use
    Given a valid create_media_buy request with account_id "acc-new"
    And the account "acc-new" exists but requires setup (billing not configured)
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "ACCOUNT_SETUP_REQUIRED"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the error should include "details" with setup instructions
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-t: Account Ambiguous ---

  @T-UC-002-ext-t @extension @ext-t @error @post-f1 @post-f2 @post-f3
  Scenario: Account ambiguous -- natural key matches multiple accounts
    Given a valid create_media_buy request with account natural key brand "multi-brand.com" operator "agency.com"
    And the natural key matches 3 accounts
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "ACCOUNT_AMBIGUOUS"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # --- ext-u: Optimization Goal Validation Failure ---

  @T-UC-002-ext-u @extension @ext-u @error @post-f1 @post-f2 @post-f3
  Scenario: Optimization goal with unsupported metric
    Given a valid create_media_buy request
    And the account exists and is active
    But a package has optimization_goal with kind "metric" and metric "attention_score" not in supported set
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "TERMS_REJECTED"
    And the error recovery should be "correctable"
    And the response error field is packages[0].optimization_goals[0]
    And the error should include "suggestion" field
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue
    # CORRECTED to the pin: the code was UNSUPPORTED_FEATURE. A metric goal MUST match the
    # product's declared capability, and the pin names the refusal: "A package's
    # optimization_goals[] entry MUST match a product capability —
    # metric_optimization.supported_metrics for kind: "metric" ... Sellers reject mismatches
    # with TERMS_REJECTED." The seeded product declares no metric_optimization, and
    # core/product.json says its "Presence indicates the product supports optimization_goals
    # with kind: 'metric'", so this goal matches no capability. The field names the
    # mismatched entry. Production accepts the goal today (prebid/salesagent#2479).
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/media-buys/optimization-reporting.mdx section="Required coherence between layers"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/metric_optimization/description

  @T-UC-002-ext-u-event @extension @ext-u @error
  Scenario: Optimization goal with unregistered event source
    Given a valid create_media_buy request
    And the account exists and is active
    But a package has optimization_goal with kind "event" and unregistered event_source_id
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # --- ext-v: Catalog Validation Failure ---

  @T-UC-002-ext-v @extension @ext-v @error @post-f1 @post-f2 @post-f3
  Scenario: Duplicate catalog types on a package
    Given a valid create_media_buy request
    And the account exists and is active
    But a package has two catalogs both with type "product"
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    # LOCAL CORRECTION: the code line asserted INVALID_REQUEST. The pinned package-request.json
    # catalogs: "sellers MUST reject requests containing multiple catalogs of the same type
    # with a validation_error"; enumMetadata gives VALIDATION_ERROR recovery correctable.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/properties/catalogs
    # POST-F1: System state is unchanged on failure
    # POST-F2: Buyer knows what failed
    # POST-F3: Buyer knows how to fix the issue

  @T-UC-002-ext-v-notfound @extension @ext-v @error
  Scenario: Catalog ID not found in synced catalogs
    Given a valid create_media_buy request
    And the account exists and is active
    But a package references catalog_id "cat-nonexistent" not found in synced catalogs
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field

  @T-UC-002-inv-006-1 @invariant @BR-RULE-006
  Scenario: INV-1 holds -- fixed_price set and floor_price null (valid fixed pricing)
    Given a valid create_media_buy request
    And the account exists and is active
    And a package pricing option has fixed_price set and floor_price null
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the pricing validation should pass

  @T-UC-002-inv-006-2 @invariant @BR-RULE-006
  Scenario: INV-2 holds -- floor_price set and fixed_price null (valid auction pricing)
    Given a valid create_media_buy request
    And the account exists and is active
    And a package pricing option has floor_price set and fixed_price null
    And the package has a bid_price above the floor
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the pricing validation should pass

  @T-UC-002-inv-006-3 @invariant @BR-RULE-006 @error
  Scenario: INV-3 violated -- both fixed_price and floor_price set
    Given a valid create_media_buy request
    And the account exists and is active
    But a package pricing option has both fixed_price and floor_price set
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error should include "suggestion" field

  @T-UC-002-inv-006-5 @invariant @BR-RULE-006 @v31
  Scenario: INV-5 holds -- bid-based auction with max_bid treats bid_price as a ceiling
    Given a valid create_media_buy request
    And the account exists and is active
    And a package uses a bid-based auction pricing model with max_bid set to true
    And the buyer supplies a bid_price above the floor_price
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response package should carry bidding max_bid equal to the buyer bid_price
    # BR-RULE-006 INV-5: max_bid is deprecated legacy input the seller normalizes. The pin's
    # package-request bid_price: "Legacy normalization: selected pricing_option.max_bid=true
    # maps to bidding.max_bid"; package.json bid_price: "3.2 sellers normalize accepted legacy
    # input and SHOULD echo bidding instead." Where the seller then clears is not observable at
    # creation, so "the seller may clear at any price between floor and bid" is not asserted.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/properties/bid_price
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/package.json pointer=/properties/bid_price
    # --- BR-RULE-008: Budget Positivity ---
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-008-1 @invariant @BR-RULE-008
  Scenario: INV-1 holds -- total budget greater than zero
    Given a valid create_media_buy request with total budget 5000
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the budget validation should pass

  @T-UC-002-inv-008-2 @invariant @BR-RULE-008 @error
  Scenario: INV-2 violated -- total budget is zero or negative
    Given a valid create_media_buy request with total budget 0
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "BUDGET_TOO_LOW"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the error should include "field" field with value "packages"
    # The field line is what makes this grade the positivity rule. The seeded tenant keeps a
    # min_package_budget of 100, so a zero budget would still be refused BUDGET_TOO_LOW by the
    # seller minimum if the positivity check were gone -- but that refusal names
    # packages[0].budget, while the positivity check sums every package and names the
    # array, as @T-UC-002-ext-a grades.
    # --- BR-RULE-013: DateTime Validity ---

  @T-UC-002-inv-010-3 @invariant @BR-RULE-010 @v31
  Scenario: INV-3 holds -- proposal mode has no buyer packages array for the uniqueness check
    Given a valid create_media_buy request in proposal mode with a proposal_id and total_budget
    And the account exists and is active
    And the request supplies no buyer packages array
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "PROPOSAL_NOT_FOUND"
    And the error recovery should be "correctable"
    And the error should include "field" field with value "proposal_id"
    # BR-RULE-010 INV-3 (v3.1): uniqueness binds the buyer-supplied manual packages array, absent in proposal mode
    # Corrected to the pin. The request is the "Committed proposal execution" shape of
    # create-media-buy-request.json (proposal_id + total_budget, no packages), and the
    # proposal_id names nothing this seller issued: it declares no supports_proposals
    # (default false) and issues no proposals, so the pin's answer is PROPOSAL_NOT_FOUND --
    # "not recognized by the seller -- ... was never issued" (enums/error-code.json).
    # The field line is what grades INV-3. The uniqueness rule refuses on the packages
    # array, and so does every other packages-array check; a seller that ran them against
    # this request answered on `packages` (this one answered VALIDATION_ERROR there,
    # asking for the field the shape forbids). Answering on proposal_id is the request
    # being judged on what it carries. The two lines this replaces -- "the product-
    # uniqueness check should have no applicable buyer input" and "the request should not
    # be rejected on the product-uniqueness rule" -- asserted the same thing with nothing to
    # read it from: no response field reports which checks ran.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/properties/packages
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/PROPOSAL_NOT_FOUND
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/supports_proposals
    # --- BR-RULE-012: Maximum Daily Spend Cap ---
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-012-5 @invariant @BR-RULE-012 @v31
  Scenario: INV-5 -- a request with total_budget but neither packages nor proposal_id is refused
    Given a valid create_media_buy request
    And the account exists and is active
    And the request uses legacy mode with no per-package budgets
    And the tenant has max_daily_package_spend configured
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    # BR-RULE-012 INV-5: the pin has no "legacy mode". create-media-buy-request.json anyOf
    # admits three shapes -- packages each with budget; packages + total_budget +
    # budget_allocation seller_optimized; proposal_id + total_budget -- so a total_budget with
    # no packages and no proposal_id matches none, and the daily cap is never reached. Schema
    # violation is INVALID_REQUEST (enums/error-code.json).
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/anyOf
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/INVALID_REQUEST
    # --- BR-RULE-013: DateTime Validity ---
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-013-4 @invariant @BR-RULE-013
  Scenario: INV-4 holds -- start_time is literal "asap" (case-sensitive)
    Given a valid create_media_buy request with start_time "asap"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the system should resolve start_time to current UTC
    And the date validation should pass

  @T-UC-002-inv-013-5 @invariant @BR-RULE-013 @error
  Scenario: INV-5 violated -- start_time is "ASAP" wrong case
    Given a valid create_media_buy request with start_time "ASAP"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # --- BR-RULE-017: Approval Workflow Determination ---

  @T-UC-002-inv-017-1 @invariant @BR-RULE-017
  Scenario: INV-1 holds -- both flags false results in auto-approval
    Given a valid create_media_buy request
    And the account exists and is active
    And tenant human_review_required is false
    And adapter manual_approval_required is false
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the approval path should be auto-approved
    And the media buy should proceed to adapter execution

  @T-UC-002-inv-017-2 @invariant @BR-RULE-017
  Scenario: INV-2 holds -- tenant human_review_required triggers manual approval
    Given a valid create_media_buy request
    And the account exists and is active
    And tenant human_review_required is true
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the approval path should be manual
    And the media buy should enter pending state

  @T-UC-002-inv-017-3 @invariant @BR-RULE-017
  Scenario: INV-3 holds -- adapter manual_approval_required triggers manual approval
    Given a valid create_media_buy request
    And the account exists and is active
    And adapter manual_approval_required is true
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the approval path should be manual
    And the media buy should enter pending state
    # --- BR-RULE-018: Atomic Response Semantics ---

  @T-UC-002-inv-017-4 @invariant @BR-RULE-017 @v31
  Scenario: INV-4 holds -- a buy awaiting the seller's human review stays a submitted task
    Given a valid create_media_buy request
    And the account exists and is active
    And tenant human_review_required is true
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy submitted spec
    And the approval path should be manual
    And "pending_approval" should never be used as a MediaBuyStatus value
    # BR-RULE-017 INV-4: approval is modelled at the task layer. specification.mdx: "Sales agents
    # MAY require internal human review (e.g. IO signing) by keeping the task in status:
    # "submitted" until the reviewer acts. Sales agents MAY also use status: "input-required"
    # when the buyer must respond ... there is no pending_approval MediaBuy status". The seller's
    # own reviewer is not the buyer, so "input-required" was the wrong status here. The task's
    # resolution (completed with media_buy_id, or rejected) follows the reviewer's decision,
    # which no buyer request makes; it needs a When that drives the seller's decision.
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/specification.mdx
    # --- BR-RULE-018: Atomic Response Semantics ---
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-018-1 @invariant @BR-RULE-018
  Scenario: INV-1 holds -- successful creation has success fields only
    Given a valid create_media_buy request that passes all validation
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should have success fields
    And the response should NOT have an "errors" field

  @T-UC-002-inv-018-2 @invariant @BR-RULE-018 @error
  Scenario: INV-2 holds -- validation failure has errors array only
    Given a create_media_buy request that fails validation
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the response should have an "errors" array
    And the error code should be "PRODUCT_NOT_FOUND"
    And the response should NOT have success fields (media_buy_id, packages)
    And each error should include "suggestion" field
    # The Given names a product the tenant does not hold. Without the code line any refusal
    # carrying a suggestion satisfied this scenario, an unseeded tenant's CONFIGURATION_ERROR
    # included; PRODUCT_NOT_FOUND is the refusal the Given causes, as @T-UC-002-ext-b grades.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/PRODUCT_NOT_FOUND

  @T-UC-002-inv-018-4 @invariant @BR-RULE-018
  Scenario: INV-4 holds -- transient error includes retry_after hint
    Given a create_media_buy request
    And the account exists and is active
    And the system returns a transient error (RATE_LIMITED)
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the error recovery should be "transient"
    And the error should include "retry_after" field

  @T-UC-002-inv-018-5 @invariant @BR-RULE-018
  Scenario: INV-5 holds -- correctable error includes suggestion and field
    Given a create_media_buy request that fails with a correctable error
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the error should include "field" field

  @T-UC-002-inv-018-6 @invariant @BR-RULE-018
  Scenario: INV-6 holds -- terminal error signals agent to escalate
    Given a create_media_buy request with account_id that does not exist
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the error code should be "ACCOUNT_NOT_FOUND"
    And the error recovery should be "terminal"
    # --- BR-RULE-020: Adapter Atomicity ---

  @T-UC-002-inv-018-8 @invariant @BR-RULE-018 @v31
  Scenario: INV-8 holds -- a fatal failure populates both the envelope and the payload error layers
    Given a create_media_buy request that fails validation
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And both the protocol envelope "adcp_error" field and the payload "errors" array should be populated
    And the envelope should not emit the legacy "task_status" or "response_status" fields
    # BR-RULE-018 INV-8: error-handling.mdx "A fatal task failure SHOULD populate both layers";
    # task-lifecycle.mdx "Agents MUST NOT emit the legacy task_status or response_status fields
    # alongside status". The non-fatal half ("Non-fatal errors populate only the payload": a
    # submitted task's warning rides errors[] with severity "warning" and never adcp_error)
    # was asserted here against a FATAL failure, where no warning exists; it needs its own
    # scenario with a request whose submitted answer carries an advisory.
    # @source repo=adcp ref=v3.2.7 path=docs/building/by-layer/L3/error-handling.mdx
    # @source repo=adcp ref=v3.2.7 path=docs/building/by-layer/L3/task-lifecycle.mdx
    # --- BR-RULE-020: Adapter Atomicity ---
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-020-1 @invariant @BR-RULE-020
  Scenario: INV-1 holds -- adapter success persists all records
    Given a valid create_media_buy request that passes all validation
    And the account exists and is active
    And the ad server adapter returns success
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the media buy record should be persisted in the database
    And the package records should be persisted
    And the creative assignment records should be persisted

  @T-UC-002-inv-020-2 @invariant @BR-RULE-020 @error
  Scenario: INV-2 holds -- adapter failure creates no records
    Given a valid create_media_buy request that passes all validation
    And the account exists and is active
    But the ad server adapter returns an error
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And no media buy record should be persisted
    And no package records should be persisted
    And the error should include "suggestion" field

  @T-UC-002-inv-020-3 @invariant @BR-RULE-020
  Scenario: INV-3 holds -- manual approval path persists in pending state
    Given a valid create_media_buy request
    And the account exists and is active
    And approval path is manual
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the media buy record should be persisted with status "pending_approval"
    And the package records should be persisted
    # --- BR-RULE-026: Creative Assignment Validation ---

  @T-UC-002-inv-020-4 @invariant @BR-RULE-020 @v31
  Scenario: INV-4 holds -- pre-adapter validation failure never invokes the adapter
    Given a valid create_media_buy request
    And the account exists and is active
    But a referenced creative is missing its required assets
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the error code should be "VALIDATION_ERROR"
    And the ad server adapter should never be invoked
    And no media buy record should be persisted
    And no package records should be persisted
    # BR-RULE-020 INV-4 (v3.1): validation precedes the adapter call; failure persists nothing.
    # One named pre-adapter failure (a stored creative with no assets, a business rule beyond
    # schema validation: VALIDATION_ERROR) rather than "creative completeness or pricing/budget",
    # which a single request cannot be. "no database records should be created" passed on any
    # error without reading the store; the two persistence lines read it.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/VALIDATION_ERROR
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-020-5 @invariant @BR-RULE-020 @v31
  Scenario: INV-5 holds -- the deprecated X-Dry-Run header does not turn a create into a preview
    Given a valid create_media_buy request
    And the account exists and is active
    And the request carries the deprecated "X-Dry-Run" header
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the media buy should proceed to adapter execution
    And the media buy record should be persisted in the database
    # BR-RULE-020 INV-5: create_media_buy has no dry-run. create-media-buy-request.json declares
    # no dry_run, and sandbox.mdx scopes dry_run to "Sync tasks only". The one dry-run signal a
    # buyer can still send a create is the deprecated header, and the pin rules on it: "Sellers
    # MUST NOT alter behavior based on these headers." A preview that skipped the ad server and
    # persisted nothing would be exactly that alteration. Sandbox is the spec's no-side-effect
    # mode, graded by @T-UC-002-sandbox-*.
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/advanced-topics/sandbox.mdx
    # --- BR-RULE-026: Creative Assignment Validation ---
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-026-1 @invariant @BR-RULE-026
  Scenario: INV-1 holds -- all creatives valid and formats compatible
    Given a valid create_media_buy request with creative assignments
    And the account exists and is active
    And all referenced creatives exist in valid state with compatible formats
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the creative assignment should proceed

  @T-UC-002-inv-026-2 @invariant @BR-RULE-026 @error
  Scenario: INV-2 violated -- a creative in the terminal rejected state cannot be assigned
    Given a valid create_media_buy request with creative assignments
    And the account exists and is active
    But a referenced creative is in "rejected" state
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_STATE"
    And the error should include "suggestion" field
    # enums/creative-status.json has no "error" member; "rejected" is its terminal refusal.
    # Assigning it is an "Operation ... not permitted for the resource's current status":
    # INVALID_STATE (enums/error-code.json).
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/creative-status.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/INVALID_STATE

  @T-UC-002-inv-026-4 @invariant @BR-RULE-026 @error
  Scenario: INV-4 violated -- creative format incompatible with product
    Given a valid create_media_buy request with creative assignments
    And the account exists and is active
    But a creative format is incompatible with the product's supported formats
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the error should include "suggestion" field
    # Same condition and code as @T-UC-002-ext-p: a format outside the product's declared set
    # "violates business rules beyond schema validation".
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/VALIDATION_ERROR
    # --- BR-RULE-080: Account Resolution Validation ---

  @T-UC-002-inv-080-1 @invariant @BR-RULE-080 @error
  Scenario: INV-1 violated -- account field absent from request
    Given a create_media_buy request without account field
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the error should include "field" field with value "account"
    # account is in /required; the refusal names the absent member, so a refusal of any
    # other missing field cannot pass this scenario.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/required
    # --- BR-RULE-087: Optimization Goal Validation ---

  @T-UC-002-inv-080-11 @invariant @BR-RULE-080 @v31
  Scenario Outline: INV-11 -- an account in terminal lifecycle status <status> cannot create media buys
    Given a create_media_buy request
    But the resolved account has terminal lifecycle status "<status>"
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the error code should be "ACCOUNT_NOT_FOUND"
    And no media buy record should be persisted
    # BR-RULE-080 INV-11: the protocol does specify this. accounts/overview.mdx lists
    # create_media_buy as "No" for rejected/closed and rules: "Sellers MUST reject operations on
    # accounts in terminal states with ACCOUNT_NOT_FOUND or an appropriate error".
    # ACCOUNT_NOT_FOUND is the code it names, and the one this seller answers.
    # @source repo=adcp ref=v3.2.7 path=docs/accounts/overview.mdx
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/account-status.json

    Examples: Terminal account statuses
      | status   |
      | rejected |
      | closed   |

    # --- BR-RULE-087: Optimization Goal Validation ---
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-inv-087-5 @invariant @BR-RULE-087
  Scenario: INV-5 holds -- duplicate priority values in optimization goals are accepted
    Given a valid create_media_buy request
    And the account exists and is active
    And a package has two optimization goals with the same priority value
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the media buy should be created
    # The pin permits ties and resolves them by array order. optimization-goal.json priority:
    # "Ties use array order, so the earliest goal at the lowest explicit priority is primary";
    # the schema description: "This array-order tie-break makes duplicate priorities
    # deterministic." Refusing them would refuse a request the pin accepts.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/optimization-goal.json

  @T-UC-002-inv-087-6 @invariant @BR-RULE-087 @error
  Scenario: INV-6 violated -- optimization_goals array empty
    Given a valid create_media_buy request
    And the account exists and is active
    But a package has optimization_goals as an empty array
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field

  @T-UC-002-inv-087-7 @invariant @BR-RULE-087 @error
  Scenario: INV-7 violated -- per_ad_spend target without value_field on event source
    Given a valid create_media_buy request
    And the account exists and is active
    But a package has an event kind optimization goal with target kind "per_ad_spend"
    And no event_sources entry has value_field set
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "VALIDATION_ERROR"
    And the response error field is packages[0].optimization_goals[0].event_sources
    And the error should include "suggestion" field
    # The field is the member at fault, as the per_ad_spend row of
    # @T-UC-002-boundary-optimization-goals and the value_target_without_value_field row of
    # @T-UC-002-partition-optimization-goals name it for the same rule.
    # optimization-goal.json value_field: "sellers must reject these target kinds when no event
    # source entry includes value_field". The schema does not encode it (no if/then ties
    # target.kind to value_field), so the refusal is a business rule beyond schema validation:
    # VALIDATION_ERROR, not INVALID_REQUEST (enums/error-code.json).
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/optimization-goal.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/VALIDATION_ERROR

  @T-UC-002-partition-budget-amount @partition @budget-amount
  Scenario Outline: Budget amount partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And a package budget is set to <value>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- pricing_option_xor partitions (BR-RULE-006) ---
    # A negative budget violates package-request.json `budget` ("minimum": 0), a schema
    # constraint: INVALID_REQUEST at the budget, not BUDGET_TOO_LOW (a business minimum).
    # Zero passes the schema and is refused by the seller's minimum.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/properties/budget/minimum

    Examples: Valid partitions
      | partition        | value    | outcome                          |
      | positive_amount  | 1000.00  | budget validation passes         |

    Examples: Invalid partitions
      | partition        | value | outcome                                      |
      | zero_amount      | 0     | error BUDGET_TOO_LOW with suggestion          |
      | negative_amount  | -50   | error INVALID_REQUEST on packages[0].budget with suggestion |

  @T-UC-002-partition-pricing-option-xor @partition @pricing-option-xor
  Scenario Outline: Pricing option XOR partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the pricing option configuration is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- currency_consistency partitions (BR-RULE-009) ---

    Examples: Valid partitions
      | partition         | outcome                         |
      | fixed_pricing     | pricing validation passes       |
      | auction_pricing   | pricing validation passes       |
      | both_set          | pricing validation passes       |

    # both_set: the XOR is a constraint on the PUBLISHED option -- floor_price is "mutually
    # exclusive with fixed_price" -- and this seller publishes a fixed option as fixed_price
    # alone, so a buyer selecting it holds a valid fixed option. Nothing in the pin refuses
    # the buy, and CONFIGURATION_ERROR is for a misconfiguration that "prevents handling the
    # request", which a stray floor does not.
    # neither_set: the seller cannot hold an option with neither (fixed needs a rate, auction a
    # floor), so what a buyer can send is a pricing_option_id that resolves to no option.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/pricing-options/cpm-option.json pointer=/properties/floor_price/description
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/INVALID_PRICING_OPTION
    # cpa_model: the XOR holds for a fixed CPA option, but this seller's ad server does not
    # sell CPA. The pin lets a seller support a subset of pricing models
    # ("Pricing models this seller supports across its product portfolio"), and the mock ad
    # server lists no cpa in get_supported_pricing_models, which is what get_adcp_capabilities
    # advertises. Production refuses the package before the ad server is called:
    # VALIDATION_ERROR, "violates business rules beyond schema validation", with one
    # details.problems[] entry (UNSUPPORTED_FEATURE on packages[0].pricing_option_id,
    # rejected_value "cpa", accepted_values the supported set). The row was "pricing
    # validation passes", which only the in-process harness answered, because its adapter
    # double returned no problems for every request; e2e_rest refused it all along.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/supported_pricing_models/description
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/VALIDATION_ERROR
    Examples: Invalid partitions
      | partition         | outcome                                                                     |
      | neither_set       | error INVALID_PRICING_OPTION on packages[0].pricing_option_id with suggestion |
      | cpa_model         | error VALIDATION_ERROR with suggestion                                      |

  @T-UC-002-partition-currency-consistency @partition @currency-consistency
  Scenario Outline: Currency consistency partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the currency scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- product_uniqueness partitions (BR-RULE-010) ---

    Examples: Valid partitions
      | partition                | outcome                      |
      | single_package           | currency validation passes   |
      | all_same_currency        | currency validation passes   |
      | currency_in_tenant_table | currency validation passes   |
      | currency_in_ad_server    | currency validation passes   |

    # CORRECTED against the pin. mixed_currencies is not an unsupported seller feature: the
    # media buy has one currency for every seller, and "Every selected pricing option MUST
    # declare that currency". The refusal the pin names for an incompatible currency is
    # TERMS_REJECTED identifying the incompatible package -- the second, since the first
    # package's option sets the buy's currency.
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/create_media_buy.mdx
    # @source repo=adcp ref=v3.2.7 path=dist/compliance/3.2.7/domains/media-buy/scenarios/seller_optimized_budget.yaml
    Examples: Invalid partitions
      | partition                  | outcome                                   |
      | mixed_currencies           | error TERMS_REJECTED on packages[1].pricing_option_id with suggestion |
      | currency_not_in_tenant     | error UNSUPPORTED_FEATURE with suggestion  |
      | currency_not_in_ad_server  | error UNSUPPORTED_FEATURE with suggestion  |

  @T-UC-002-partition-product-uniqueness @partition @product-uniqueness
  Scenario Outline: Product uniqueness partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the product scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- minimum_spend partitions (BR-RULE-011) ---
    # Duplicate product_ids: the pin does not forbid them, so the refusal is this seller's
    # business rule -- VALIDATION_ERROR ("violates business rules beyond schema validation"),
    # pointed at the packages array because no single package is at fault.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/VALIDATION_ERROR

    Examples: Valid partitions
      | partition           | outcome                         |
      | single_package      | product validation passes       |
      | distinct_products   | product validation passes       |

    Examples: Invalid partitions
      | partition           | outcome                         |
      | duplicate_product   | error VALIDATION_ERROR on packages with suggestion |

  @T-UC-002-partition-minimum-spend @partition @minimum-spend
  Scenario Outline: Minimum spend partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the minimum spend scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- daily_spend_cap partitions (BR-RULE-012) ---
    # BUDGET_TOO_LOW: "Budget is below the seller's minimum" -- the product (pricing option)
    # minimum and the tenant minimum alike -- pointed at the refused package budget.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/BUDGET_TOO_LOW

    Examples: Valid partitions
      | partition                  | outcome                      |
      | budget_meets_product_min   | minimum spend passes         |
      | budget_meets_tenant_min    | minimum spend passes         |
      | no_minimum_configured      | minimum spend check skipped  |

    Examples: Invalid partitions
      | partition                  | outcome                         |
      | budget_below_product_min   | error BUDGET_TOO_LOW on packages[0].budget with suggestion |
      | budget_below_tenant_min    | error BUDGET_TOO_LOW on packages[0].budget with suggestion |

  @T-UC-002-partition-daily-spend-cap @partition @daily-spend-cap
  Scenario Outline: Daily spend cap partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the daily spend cap scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- start_time partitions (BR-RULE-013) ---

    Examples: Valid partitions
      | partition             | outcome                          |
      | below_cap             | daily spend validation passes    |
      | cap_not_configured    | daily spend check skipped        |
      | at_cap_exactly        | daily spend validation passes    |

    Examples: Invalid partitions
      | partition             | outcome                                      |
      | exceeds_cap           | error BUDGET_EXCEEDED with suggestion         |

  @T-UC-002-partition-start-time @partition @start-time
  Scenario Outline: Start time partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the start_time is <value>
    And end_time is 2099-04-30T23:59:59Z
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- end_time partitions (BR-RULE-013) ---

    # Future instants are written in 2099, as the pinned storyboards write theirs: a date
    # this close to the run expires, and a create refuses a start_time in the past.
    # The end_time is written in 2099 too, after every start a row sends. The request's
    # default end is a month from the run, so a 2099 start against it is an inverted
    # flight, which the seller refuses on end_time ("Flight date validation": "Out-of-range
    # or inverted dates SHOULD return an INVALID_REQUEST error"). The row was graded on the
    # flight, not the start.
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/create_media_buy.mdx
    # future_naive_datetime is INVALID: start-timing.json types the instant as
    # "format": "date-time", which JSON Schema takes from RFC 3339, and an RFC 3339
    # date-time carries its offset (time-offset is not optional). A naive value violates
    # the schema, so it is refused, not "treated as UTC".
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/start-timing.json pointer=/oneOf/1
    Examples: Valid partitions
      | partition              | value                      | outcome                      |
      | asap_literal           | asap                       | start time resolves to now   |
      | future_iso_datetime    | 2099-04-01T00:00:00Z       | start time accepted          |

    Examples: Invalid partitions
      | partition              | value                      | outcome                                             |
      | future_naive_datetime  | 2099-04-01T00:00:00        | error INVALID_REQUEST on start_time with suggestion  |
      | past_datetime          | 2020-01-01T00:00:00Z       | error INVALID_REQUEST on start_time with suggestion  |
      | absent                 | null                        | error INVALID_REQUEST on start_time with suggestion  |
      | wrong_case_asap        | ASAP                        | error INVALID_REQUEST on start_time with suggestion  |

  @T-UC-002-partition-end-time @partition @end-time
  Scenario Outline: End time partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And start_time is 2099-04-01T00:00:00Z
    And end_time is <value>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- targeting_overlay partitions (BR-RULE-014) ---

    # The start_time above was a QUOTED literal, which the single-token step passed through
    # with its quotes, so every row was refused on start_time and the end_time rows passed
    # on the wrong field. The field below is what makes that visible.
    Examples: Valid partitions
      | partition              | value                      | outcome                    |
      | after_start_time       | 2099-04-30T23:59:59Z       | end time accepted          |

    Examples: Invalid partitions
      | partition              | value                      | outcome                                           |
      | equal_to_start         | 2099-04-01T00:00:00Z       | error INVALID_REQUEST on end_time with suggestion  |
      | before_start           | 2099-03-15T00:00:00Z       | error INVALID_REQUEST on end_time with suggestion  |
      | absent                 | null                        | error INVALID_REQUEST on end_time with suggestion  |

  @T-UC-002-partition-targeting-overlay @partition @targeting-overlay
  Scenario Outline: Targeting overlay partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the targeting overlay scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- creative_asset partitions (BR-RULE-015) ---

    Examples: Valid partitions
      | partition                          | outcome                         |
      | absent_overlay                     | targeting validation passes     |
      | valid_overlay                      | targeting validation passes     |
      | empty_overlay                      | targeting validation passes     |
      | single_geo_dimension               | targeting validation passes     |
      | multiple_dimensions                | targeting validation passes     |
      | frequency_cap_suppress_only        | targeting validation passes     |
      | frequency_cap_max_impressions_only | targeting validation passes     |
      | frequency_cap_combined             | targeting validation passes     |

    Examples: Invalid partitions
      | partition                          | outcome                                      |
      | unknown_field                      | error INVALID_REQUEST with suggestion          |
      | undeclared_dimension               | error INVALID_REQUEST with suggestion          |
      | geo_overlap                        | error INVALID_REQUEST with suggestion          |
      | device_type_overlap                | error INVALID_REQUEST with suggestion          |
      | proximity_method_conflict          | error INVALID_REQUEST on packages[0].targeting_overlay.geo_proximity[0] with suggestion |
      | frequency_cap_missing_fields       | error INVALID_REQUEST with suggestion          |
      | keyword_duplicate                  | error INVALID_REQUEST with suggestion          |
      | keyword_targeting                  | error UNSUPPORTED_FEATURE on packages[0].targeting_overlay.keyword_targets with suggestion |
      | proximity_travel_time              | error UNSUPPORTED_FEATURE on packages[0].targeting_overlay.geo_proximity with suggestion   |
      | proximity_radius                   | error UNSUPPORTED_FEATURE on packages[0].targeting_overlay.geo_proximity with suggestion   |
      | proximity_geometry                 | error UNSUPPORTED_FEATURE on packages[0].targeting_overlay.geo_proximity with suggestion   |
    # CORRECTED against the pin: keyword_targeting and the three proximity_* rows expected
    # acceptance. core/targeting.json says of keyword_targets and geo_proximity "Seller must
    # declare support in get_adcp_capabilities", and this seller declares neither
    # (media_buy.execution.targeting carries geo_countries, geo_regions, geo_metros and
    # geo_postal_areas only), so a create carrying one is refused UNSUPPORTED_FEATURE: the same
    # file's "A fresh create/update request MUST be applied exactly or rejected", and
    # create_media_buy.mdx "Non-null additions must compile exactly or be rejected". A request
    # fault is refused first: proximity_method_conflict breaks the geo_proximity item oneOf
    # (INVALID_REQUEST at the entry), keyword_duplicate the "SHOULD reject duplicate (keyword,
    # match_type) pairs" rule.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/targeting.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/protocol/get-adcp-capabilities-response.json
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/create_media_buy.mdx

  @T-UC-002-partition-creative-asset @partition @creative-asset
  Scenario Outline: Creative asset partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the creative scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- approval_workflow partitions (BR-RULE-017) ---

    Examples: Valid partitions
      | partition                           | outcome                       |
      | no_creatives                        | creative validation passes    |
      | assignments_only                    | creative validation passes    |
      | uploads_only                        | creative validation passes    |
      | both_paths                          | creative validation passes    |
      | assignment_with_weight_zero         | creative validation passes    |
      | assignment_with_placement_targeting | creative validation passes    |

    # Three conditions, three codes. They read CREATIVE_REJECTED for all three and
    # passed for years because production emitted that one code for all three too --
    # a test that encodes production's bug is not coverage. Per 3.1.1
    # enums/error-code.json: a creative_id not owned by the caller is CREATIVE_NOT_FOUND
    # (a MUST, uniform); a format the product does not accept and a stored creative
    # missing its required assets both "violate business rules beyond schema
    # validation", i.e. VALIDATION_ERROR. CREATIVE_REJECTED is content-policy review,
    # which none of the three reaches.
    Examples: Invalid partitions
      | partition                           | outcome                                        |
      | creative_not_found                  | error CREATIVE_NOT_FOUND with suggestion         |
      | format_mismatch                     | error VALIDATION_ERROR with suggestion           |
      | missing_required_assets             | error VALIDATION_ERROR with suggestion           |
      | exceeds_max_creatives               | error INVALID_REQUEST with suggestion            |

  @T-UC-002-partition-approval-workflow @partition @approval-workflow
  Scenario Outline: Approval workflow partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the approval scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- account_ref partitions (BR-RULE-080) ---

    Examples: All partitions (no invalid -- all are valid workflow paths)
      | partition                    | outcome                      |
      | auto_approve                 | auto-approved path taken     |
      | pending_human_review         | manual approval required     |
      | pending_adapter_approval     | manual approval required     |

  @T-UC-002-partition-account-ref @partition @account
  Scenario Outline: Account reference partition validation - <partition>
    Given a create_media_buy request with account configuration <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- optimization_goals partitions (BR-RULE-087) ---

    Examples: Valid partitions
      | partition                    | outcome                               |
      | explicit_account_id          | account resolution succeeds           |
      | natural_key_unambiguous      | account resolution succeeds           |
      | natural_key_sandbox          | account resolution succeeds           |

    Examples: Invalid partitions
      | partition                    | outcome                                         |
      # account is REQUIRED by create-media-buy-request.json /required, so an omitted
      # account field is refused. This row sat under "Valid partitions" saying the
      # omission is accepted and the buy created -- reconciled to a production that
      # deviated from the pin, rather than to the pin (salesagent-prkv.68).
      | missing_account              | error INVALID_REQUEST                             |
      | invalid_oneOf_both           | error INVALID_REQUEST                             |
      | explicit_not_found           | error ACCOUNT_NOT_FOUND terminal                  |
      | natural_key_not_found        | error ACCOUNT_NOT_FOUND terminal                  |
      | natural_key_ambiguous        | error ACCOUNT_AMBIGUOUS correctable               |
      | account_setup_required       | error ACCOUNT_SETUP_REQUIRED correctable           |
      | account_payment_required     | error ACCOUNT_PAYMENT_REQUIRED terminal            |
      | account_suspended            | error ACCOUNT_SUSPENDED terminal                  |

  @T-UC-002-partition-optimization-goals @partition @optimization-goals
  Scenario Outline: Optimization goals partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the optimization goal scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- catalog_distinct_type partitions (BR-RULE-089) ---
    # A value the schema refuses is INVALID_REQUEST ("violates schema constraints"); a value
    # the schema admits but this product does not support is UNSUPPORTED_FEATURE. In
    # core/optimization-goal.json: view_duration_seconds and target.value are
    # "exclusiveMinimum": 0 (so 0 and -1 are schema violations), target.kind is a closed
    # discriminator (an unknown kind matches no branch), and metric is a closed enum with no
    # "viewability" member. Those rows are INVALID_REQUEST.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/optimization-goal.json
    # "optimization validation passes" grades the confirmed package: core/package.json carries
    # optimization_goals, and packages[0].optimization_goals must hold every goal sent.
    # CORRECTED to the pin (v3.2.7):
    # - duplicate_priority passes: core/optimization-goal.json "This array-order tie-break makes
    #   duplicate priorities deterministic"; tied priorities are not a refusal.
    # - exceeds_max_goals passes: core/product.json max_optimization_goals "When absent, no limit
    #   is declared" (the seeded product declares none), and above a declared cap "buyers ...
    #   should expect the seller to use only the highest-priority ... goal", not a refusal.
    # - value_target_without_value_field is VALIDATION_ERROR on the goal's event_sources: the
    #   value_field rule ("sellers must reject these target kinds when no event source entry
    #   includes value_field") is a business rule the schema does not express, and
    #   enums/error-code.json VALIDATION_ERROR is "violates business rules beyond schema
    #   validation".
    # - unsupported_metric is TERMS_REJECTED on packages[0].optimization_goals[0], not
    #   UNSUPPORTED_FEATURE: "A package's optimization_goals[] entry MUST match a product
    #   capability — metric_optimization.supported_metrics for kind: "metric" ... Sellers
    #   reject mismatches with TERMS_REJECTED." The seeded product declares no
    #   metric_optimization, so the attention_score goal matches no capability. Production
    #   accepts it today (prebid/salesagent#2479).
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/optimization-goal.json pointer=/description
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/max_optimization_goals
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/media-buys/optimization-reporting.mdx section="Required coherence between layers"
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/metric_optimization/description
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/VALIDATION_ERROR

    Examples: Valid partitions
      | partition                              | outcome                              |
      | single_metric_goal                     | optimization validation passes       |
      | single_event_goal                      | optimization validation passes       |
      | multiple_goals_unique_priorities       | optimization validation passes       |
      | metric_completed_views_with_duration   | optimization validation passes       |
      | metric_reach_with_unit                 | optimization validation passes       |
      | event_goal_with_attribution_window     | optimization validation passes       |
      | metric_goal_with_target                | optimization validation passes       |
      | event_goal_with_roas_target            | optimization validation passes       |
      | goals_at_max_count                     | optimization validation passes       |
      | reach_with_target_frequency            | optimization validation passes       |
      | event_multi_source_dedup               | optimization validation passes       |
      | duplicate_priority                     | optimization validation passes       |
      | exceeds_max_goals                      | optimization validation passes       |

    Examples: Invalid partitions
      | partition                              | outcome                                          |
      | unsupported_metric                     | error TERMS_REJECTED on packages[0].optimization_goals[0] with suggestion |
      | unregistered_event_source              | error INVALID_REQUEST with suggestion              |
      | unsupported_view_duration              | error INVALID_REQUEST with suggestion              |
      | unsupported_reach_unit                 | error UNSUPPORTED_FEATURE with suggestion          |
      | unsupported_attribution_window         | error UNSUPPORTED_FEATURE with suggestion          |
      | empty_array                            | error INVALID_REQUEST with suggestion              |
      | unsupported_target_kind                | error INVALID_REQUEST with suggestion              |
      | value_target_without_value_field       | error VALIDATION_ERROR on packages[0].optimization_goals[0].event_sources with suggestion |
      | metric_not_supported_by_product        | error INVALID_REQUEST with suggestion              |
      | event_not_supported_by_product         | error UNSUPPORTED_FEATURE with suggestion          |

  @T-UC-002-partition-catalog-distinct-type @partition @catalog-distinct-type
  Scenario Outline: Catalog distinct type partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the catalog scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- format_id_structure partitions ---

    Examples: Valid partitions
      | partition              | outcome                            |
      | no_catalogs            | catalog validation passes          |
      | single_catalog         | catalog validation passes          |
      | distinct_types         | catalog validation passes          |
      | max_distinct_types     | catalog validation passes          |

    Examples: Invalid partitions
      | partition              | outcome                                      |
      | duplicate_catalog_type | error VALIDATION_ERROR on packages[0].catalogs with suggestion |
      | multiple_duplicates    | error VALIDATION_ERROR on packages[0].catalogs with suggestion |
      | catalog_not_found      | error INVALID_REQUEST with suggestion          |
    # LOCAL CORRECTION: the two duplicate-type rows asserted INVALID_REQUEST. The pinned
    # package-request.json catalogs: "sellers MUST reject requests containing multiple catalogs
    # of the same type with a validation_error", so the code is VALIDATION_ERROR, located at
    # the package's catalogs array (no single catalog is at fault).
    # "catalog validation passes" grades the confirmed package: core/package.json catalogs are
    # "Echoed from the create_media_buy request", so packages[0].catalogs must hold every
    # catalog sent, and none when none was sent.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/properties/catalogs
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/package.json pointer=/properties/catalogs

  @T-UC-002-partition-format-id-structure @partition @format-id-structure
  Scenario Outline: Format ID structure partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the format ID scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- persistence_timing partitions (BR-RULE-020) ---
    # A plain string or a FormatId missing agent_url/id violates core/format-id.json ("never a
    # plain string"; required agent_url, id): INVALID_REQUEST, field = issues[0].pointer.
    # A well-formed id that no canonical declaration of the product resolves: "an entry that
    # cannot be normalized is rejected with UNSUPPORTED_FEATURE" (package-request.json).
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-id.json pointer=/required
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/properties/format_ids/description
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/INVALID_REQUEST

    Examples: Valid partitions
      | partition              | outcome                      |
      | valid_format_id        | format validation passes     |

    Examples: Invalid partitions
      | partition              | outcome                                                                  |
      | plain_string           | error INVALID_REQUEST on packages[0].format_ids[0] with suggestion          |
      | missing_agent_url      | error INVALID_REQUEST on packages[0].format_ids[0].agent_url with suggestion |
      | missing_id             | error INVALID_REQUEST on packages[0].format_ids[0].id with suggestion       |
      | unregistered_agent     | error UNSUPPORTED_FEATURE on packages[0].format_ids with suggestion         |
      | unknown_format         | error UNSUPPORTED_FEATURE on packages[0].format_ids with suggestion         |

  @T-UC-002-partition-persistence-timing @partition @persistence-timing
  Scenario Outline: Persistence timing partition validation - <partition>
    Given a valid create_media_buy request
    And the account exists and is active
    And the persistence timing scenario is <partition>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- tasks_sort_field partitions ---

    Examples: Valid partitions
      | partition                       | outcome                                      |
      | auto_approve_adapter_success    | all records persisted after adapter success   |
      | manual_approval_pending         | records persisted in pending state            |

    Examples: Invalid partitions
      | partition                       | outcome                                      |
      | auto_approve_adapter_failure    | no records persisted after adapter failure    |

  @T-UC-002-partition-tasks-sort-field @partition @tasks-sort-field
  Scenario Outline: Tasks sort field partition validation - <partition>
    Given the Buyer Agent owns tasks of several types, statuses and update times
    And the task list sort field is <partition>
    When the Buyer Agent queries the task list
    Then the response is compliant with the list_tasks spec
    And the result should be <outcome>
    # --- sort_direction partitions ---
    # sort.field is the enum [created_at, updated_at, status, task_type, protocol], default
    # created_at; sort.direction defaults to desc. "domain" is not a sort field: the pin names
    # that axis protocol.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/protocol/list-tasks-request.json pointer=/properties/sort
    # The protocol row claims no order. The only task types this seller lists are
    # create_media_buy, update_media_buy and sync_creatives, and the pin puts all three in
    # the media-buy domain, so every task has the same protocol and any order is "sorted by
    # protocol". The row grades what can be observed: sort.field protocol is accepted, and
    # every task is listed with its pinned task_type, status and domain.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/task-type.json pointer=/enumDescriptions

    Examples: Valid partitions
      | partition       | outcome                               |
      | created_at      | tasks sorted by created_at desc        |
      | updated_at      | tasks sorted by updated_at desc        |
      | status          | tasks sorted by status desc            |
      | task_type       | tasks sorted by task_type desc         |
      | protocol        | tasks of every protocol                |
      | omitted         | tasks sorted by created_at desc        |

    Examples: Invalid partitions
      | partition       | outcome                                          |
      | unknown_value   | error INVALID_REQUEST correctable on sort.field  |
      | domain          | error INVALID_REQUEST correctable on sort.field  |

  @T-UC-002-partition-sort-direction @partition @sort-direction
  Scenario Outline: Sort direction partition validation - <partition>
    Given the Buyer Agent owns tasks of several types, statuses and update times
    And the task list sort direction is <partition>
    When the Buyer Agent queries the task list
    Then the response is compliant with the list_tasks spec
    And the result should be <outcome>
    # --- adcp_domain partitions ---
    # sort.direction is enums/sort-direction.json [asc, desc], default desc; sort.field
    # defaults to created_at.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/protocol/list-tasks-request.json pointer=/properties/sort

    Examples: Valid partitions
      | partition       | outcome                               |
      | asc             | tasks sorted by created_at asc         |
      | desc            | tasks sorted by created_at desc        |
      | omitted         | tasks sorted by created_at desc        |

    Examples: Invalid partitions
      | partition       | outcome                                             |
      | unknown_value   | error INVALID_REQUEST correctable on sort.direction |

  @T-UC-002-partition-adcp-domain @partition @adcp-domain
  Scenario Outline: AdCP protocol filter partition validation - <partition>
    Given the Buyer Agent owns tasks of several types, statuses and update times
    And the protocol filter is <partition>
    When the Buyer Agent queries the task list
    Then the response is compliant with the list_tasks spec
    And the result should be <outcome>
    # --- task_status partitions ---
    # The pin has no "domain" filter: filters.protocol / filters.protocols (minItems 1) take
    # enums/adcp-protocol.json, kebab-case ("media-buy", not "media_buy"). Each task reports
    # the same axis as its "domain".
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/protocol/list-tasks-request.json pointer=/properties/filters

    Examples: Valid partitions
      | partition         | outcome                                     |
      | media-buy         | tasks with protocol media-buy only          |
      | signals           | tasks with protocol signals only            |
      | governance        | tasks with protocol governance only         |
      | creative          | tasks with protocol creative only           |
      | media-buy+signals | tasks with protocol media-buy, signals only |
      | omitted           | tasks of every protocol                     |

    Examples: Invalid partitions
      | partition         | outcome                                              |
      | unknown_value     | error INVALID_REQUEST correctable on filters.protocol  |
      | empty array       | error INVALID_REQUEST correctable on filters.protocols |

  @T-UC-002-partition-task-status @partition @task-status
  Scenario Outline: Task status partition validation - <partition>
    Given the Buyer Agent owns tasks of several types, statuses and update times
    And the task status filter is <partition>
    When the Buyer Agent queries the task list
    Then the response is compliant with the list_tasks spec
    And the result should be <outcome>
    # --- task_type partitions ---
    # filters.status / filters.statuses (minItems 1) take enums/task-status.json, whose
    # members are kebab-case ("input-required", "auth-required").
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/protocol/list-tasks-request.json pointer=/properties/filters

    Examples: Valid partitions
      | partition        | outcome                                   |
      | submitted        | tasks with status submitted only          |
      | working          | tasks with status working only            |
      | input-required   | tasks with status input-required only     |
      | completed        | tasks with status completed only          |
      | canceled         | tasks with status canceled only           |
      | failed           | tasks with status failed only             |
      | rejected         | tasks with status rejected only           |
      | auth-required    | tasks with status auth-required only      |
      | unknown          | tasks with status unknown only            |
      | submitted+working | tasks with status submitted, working only |
      | omitted          | tasks of every status                     |

    Examples: Invalid partitions
      | partition        | outcome                                              |
      | unknown_value    | error INVALID_REQUEST correctable on filters.status   |
      | empty array      | error INVALID_REQUEST correctable on filters.statuses |

  @T-UC-002-partition-task-type @partition @task-type
  Scenario Outline: Task type partition validation - <partition>
    Given the Buyer Agent owns tasks of several types, statuses and update times
    And the task type filter is <partition>
    When the Buyer Agent queries the task list
    Then the response is compliant with the list_tasks spec
    And the result should be <outcome>
    # filters.task_type / filters.task_types (minItems 1) take enums/task-type.json.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/protocol/list-tasks-request.json pointer=/properties/filters

    Examples: Valid partitions
      | partition                         | outcome                                                   |
      | create_media_buy                  | tasks with task_type create_media_buy only                |
      | update_media_buy                  | tasks with task_type update_media_buy only                |
      | sync_creatives                    | tasks with task_type sync_creatives only                  |
      | activate_signal                   | tasks with task_type activate_signal only                 |
      | get_signals                       | tasks with task_type get_signals only                     |
      | create_property_list              | tasks with task_type create_property_list only            |
      | update_property_list              | tasks with task_type update_property_list only            |
      | get_property_list                 | tasks with task_type get_property_list only               |
      | list_property_lists               | tasks with task_type list_property_lists only             |
      | delete_property_list              | tasks with task_type delete_property_list only            |
      | sync_accounts                     | tasks with task_type sync_accounts only                   |
      | get_creative_delivery             | tasks with task_type get_creative_delivery only           |
      | sync_event_sources                | tasks with task_type sync_event_sources only              |
      | log_event                         | tasks with task_type log_event only                       |
      | create_media_buy+update_media_buy | tasks with task_type create_media_buy, update_media_buy only |
      | omitted                           | tasks of every task_type                                  |

    Examples: Invalid partitions
      | partition              | outcome                                                |
      | unknown_value          | error INVALID_REQUEST correctable on filters.task_type  |
      | empty array            | error INVALID_REQUEST correctable on filters.task_types |

  @T-UC-002-boundary-budget-amount @boundary @budget-amount
  Scenario Outline: Budget amount boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the seller sets no minimum package budget
    And a package budget is set to <value>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- pricing_option_xor boundaries (BR-RULE-006) ---
    # A negative budget violates package-request.json `budget` ("minimum": 0): INVALID_REQUEST.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/properties/budget/minimum

    Examples: Boundary values
      | boundary_point                       | value | outcome                                      |
      | amount = 0 (rejected by rule)        | 0     | error BUDGET_TOO_LOW on packages with suggestion |
      | amount = 0.01 (minimum positive)     | 0.01  | budget validation passes                     |
      | amount negative                      | -1    | error INVALID_REQUEST on packages[0].budget with suggestion |
    # The boundary is the amount itself, so no seller minimum may answer for it: the factory
    # tenant's min_package_budget (100) refused 0.01 with BUDGET_TOO_LOW and refused 0 with the
    # same code, so neither row could tell the positivity rule from the minimum. With no minimum
    # set, 0.01 must be accepted and 0 is refused by the positivity rule alone, which sums every
    # package and names the array (packages); the seller-minimum refusal names packages[0].budget.

  @T-UC-002-boundary-pricing-option-xor @boundary @pricing-option-xor
  Scenario Outline: Pricing option XOR boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the pricing option configuration is <config>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- currency_consistency boundaries (BR-RULE-009) ---
    # fixed+floor: the seller publishes a fixed row as fixed_price alone, so the buyer holds
    # a valid fixed option (floor_price is "mutually exclusive with fixed_price" on the
    # published option). "neither": the seller cannot hold such a row, so the buyer-side
    # form is an id the product does not offer -- INVALID_PRICING_OPTION.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/pricing-options/cpm-option.json pointer=/properties/floor_price/description
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/INVALID_PRICING_OPTION

    Examples: Boundary values
      | boundary_point                                   | config                  | outcome                         |
      | fixed_price only (valid fixed)                   | fixed_price=10.00       | pricing validation passes       |
      | floor_price only (valid auction)                 | floor_price=1.00        | pricing validation passes       |
      | both present (mutually exclusive)                | fixed+floor             | pricing validation passes       |
      | neither present, v3.1 schema (auction, no floor) | neither (v3.1 schema)   | pricing validation passes       |
      | neither present, salesagent code enforcement     | neither (code rejects)  | error INVALID_PRICING_OPTION on packages[0].pricing_option_id with suggestion |
      | max_bid=true on bid-based auction model          | max_bid=true            | pricing validation passes       |

  @T-UC-002-boundary-currency-consistency @boundary @currency-consistency
  Scenario Outline: Currency consistency boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the currency configuration is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- product_uniqueness boundaries (BR-RULE-010) ---
    # CORRECTED against the pin, for the reason the currency partition outline records: two
    # currencies in one buy are refused TERMS_REJECTED naming the incompatible package.
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/create_media_buy.mdx
    # total_budget, when sent, sets the denomination: "Its currency is the single media-buy
    # denomination" and "every selected pricing option MUST declare that currency", so a
    # first package in another currency is the incompatible package the seller names.
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/create_media_buy.mdx

    Examples: Boundary values
      | boundary_point                           | config           | outcome                                  |
      | single package (trivially valid)         | 1 pkg USD        | currency validation passes               |
      | total_budget currency differs from the only package | total_budget USD, 1 pkg EUR | error TERMS_REJECTED on packages[0].pricing_option_id with suggestion |
      | two packages, same currency              | 2 pkg USD+USD    | currency validation passes               |
      | two packages, same currency (via pricing options) | 2 pkg same pricing_option_id currency | currency validation passes      |
      | two packages, different currencies       | 2 pkg USD+EUR    | error TERMS_REJECTED on packages[1].pricing_option_id with suggestion |
      | two packages, different currencies (via pricing options) | 2 pkg divergent pricing_option_id currency | error TERMS_REJECTED on packages[1].pricing_option_id with suggestion |
      | currency not in tenant table             | 1 pkg XYZ        | error UNSUPPORTED_FEATURE with suggestion |
      | currency not in ad-server network currencies | 1 pkg JPY (GAM no JPY) | error UNSUPPORTED_FEATURE with suggestion |

  @T-UC-002-boundary-product-uniqueness @boundary @product-uniqueness
  Scenario Outline: Product uniqueness boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the product configuration is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- minimum_spend boundaries (BR-RULE-011) ---
    # Duplicate product_ids: the pin does not forbid them, so the refusal is this seller's
    # business rule -- VALIDATION_ERROR ("violates business rules beyond schema validation"),
    # pointed at the packages array because no single package is at fault.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/VALIDATION_ERROR

    Examples: Boundary values
      | boundary_point                           | config            | outcome                      |
      | single package (trivially unique)        | 1 pkg prod-A      | product validation passes    |
      | two packages, different products         | 2 pkg prod-A,B    | product validation passes    |
      | two packages, same product_id            | 2 pkg prod-A,A    | error VALIDATION_ERROR on packages with suggestion |
      | proposal_id supplied, packages omitted (no buyer packages array) | proposal_id, no packages | product validation passes |

  @T-UC-002-boundary-minimum-spend @boundary @minimum-spend
  Scenario Outline: Minimum spend boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the minimum spend configuration is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- daily_spend_cap boundaries (BR-RULE-012) ---
    # BUDGET_TOO_LOW: "Budget is below the seller's minimum" -- the product (pricing option)
    # minimum and the tenant minimum alike -- pointed at the refused package budget.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/BUDGET_TOO_LOW

    Examples: Boundary values
      | boundary_point                                                 | config              | outcome                      |
      | budget = product min_spend (exact match)                       | budget=100 min=100  | minimum spend passes         |
      | budget = product min_spend - 0.01                              | budget=99.99 min=100 | error BUDGET_TOO_LOW on packages[0].budget with suggestion |
      | budget = tenant min_package_budget (exact, no product min)     | budget=50 tmin=50   | minimum spend passes         |
      | budget = tenant min_package_budget - 0.01 (no product min)    | budget=49.99 tmin=50 | error BUDGET_TOO_LOW on packages[0].budget with suggestion |
      | no min configured at any level                                 | budget=1 no-min     | minimum spend passes         |

  @T-UC-002-boundary-daily-spend-cap @boundary @daily-spend-cap
  Scenario Outline: Daily spend cap boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the daily spend scenario is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- start_time boundaries (BR-RULE-013) ---

    Examples: Boundary values
      | boundary_point                           | config              | outcome                                      |
      | daily budget = cap (at limit)            | daily=1000 cap=1000 | daily spend passes                           |
      | daily budget > cap (exceeds)             | daily=1001 cap=1000 | error BUDGET_EXCEEDED with suggestion         |
      | cap not configured (skipped)             | daily=9999 no-cap   | daily spend passes                           |
      | flight duration 0 days (floor to 1)      | 0-day-flight        | daily spend passes                           |

  @T-UC-002-boundary-start-time @boundary @start-time
  Scenario Outline: Start time boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And start_time is <value>
    And end_time is 2099-04-30T23:59:59Z
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- end_time boundaries (BR-RULE-013) ---

    # The end_time follows every start a row sends, so no row is an inverted flight; the
    # partition outline above says why.
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/create_media_buy.mdx

    Examples: Boundary values
      | boundary_point             | value                    | outcome                               |
      | literal 'asap'             | asap                     | start time resolves to now            |
      | future ISO datetime        | 2099-04-01T00:00:00Z     | start time accepted                   |
      | past datetime              | 2020-01-01T00:00:00Z     | error INVALID_REQUEST on start_time with suggestion |
      | 'ASAP' wrong case          | ASAP                     | error INVALID_REQUEST on start_time with suggestion |
      | absent (null)              | null                     | error INVALID_REQUEST on start_time with suggestion |

  @T-UC-002-boundary-start-time-package-scope @boundary @start-time
  Scenario Outline: Package-scope start_time boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And a package in packages[] carries its own start_time of <value>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- end_time boundaries (BR-RULE-013) ---
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

    Examples: Boundary values
      | boundary_point          | value | outcome                               |
      | 'asap' at package scope | asap  | error INVALID_REQUEST with suggestion |

  @T-UC-002-boundary-end-time @boundary @end-time
  Scenario Outline: End time boundary validation - <boundary_point>
    Given a valid create_media_buy request with start_time "2099-04-01T00:00:00Z"
    And the account exists and is active
    And end_time is <value>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- targeting_overlay boundaries (BR-RULE-014) ---

    Examples: Boundary values
      | boundary_point                       | value                    | outcome                               |
      | end_time after start_time            | 2099-04-30T23:59:59Z     | end time accepted                     |
      | end_time = start_time (rejected)     | 2099-04-01T00:00:00Z     | error INVALID_REQUEST on end_time with suggestion |
      | end_time before start_time           | 2099-03-15T00:00:00Z     | error INVALID_REQUEST on end_time with suggestion |
      | absent (null)                        | null                     | error INVALID_REQUEST on end_time with suggestion |

  @T-UC-002-boundary-targeting-overlay @boundary @targeting-overlay
  Scenario Outline: Targeting overlay boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the targeting overlay scenario is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- creative_asset boundaries (BR-RULE-015) ---

    Examples: Boundary values
      | boundary_point                                    | config                | outcome                                      |
      | absent overlay                                    | no overlay            | targeting validation passes                  |
      | empty {} overlay                                  | empty                 | targeting validation passes                  |
      | valid known fields                                | geo_countries=US      | targeting validation passes                  |
      | unknown field name                                | weather=sunny         | error INVALID_REQUEST with suggestion          |
      | undeclared dimension (key_value_pairs)            | key_value_pairs       | error INVALID_REQUEST with suggestion          |
      | geo include/exclude overlap                       | US in both lists      | error INVALID_REQUEST with suggestion          |
      | device_type include/exclude overlap               | mobile in both        | error INVALID_REQUEST with suggestion          |
      | geo_proximity with travel_time only               | travel_time=30m       | error UNSUPPORTED_FEATURE on packages[0].targeting_overlay.geo_proximity with suggestion   |
      | geo_proximity with radius only                    | radius=5km            | error UNSUPPORTED_FEATURE on packages[0].targeting_overlay.geo_proximity with suggestion   |
      | geo_proximity with geometry only                  | geometry=polygon      | error UNSUPPORTED_FEATURE on packages[0].targeting_overlay.geo_proximity with suggestion   |
      | geo_proximity with travel_time AND radius         | travel+radius         | error INVALID_REQUEST on packages[0].targeting_overlay.geo_proximity[0] with suggestion   |
      | frequency_cap suppress only                       | suppress=24h          | targeting validation passes                  |
      | frequency_cap max_impressions with per+window     | max=3 per=1 win=24h   | targeting validation passes                  |
      | frequency_cap max_impressions without per         | max=3 no-per          | error INVALID_REQUEST with suggestion          |
      | keyword_targets with unique tuples                | kw=shoes exact        | error UNSUPPORTED_FEATURE on packages[0].targeting_overlay.keyword_targets with suggestion |
      | keyword_targets with duplicate (keyword, match_type) | kw=shoes exact x2 | error INVALID_REQUEST with suggestion          |
    # CORRECTED against the pin, for the reason the partition outline above records: a
    # single-method geo_proximity entry and a keyword_targets list are refused
    # UNSUPPORTED_FEATURE by a seller that declares neither dimension, after the request-fault
    # rows (two proximity methods in one entry, a repeated keyword pair) are refused
    # INVALID_REQUEST.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/targeting.json

  @T-UC-002-boundary-creative-asset @boundary @creative-asset
  Scenario Outline: Creative asset boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the creative scenario is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- approval_workflow boundaries (BR-RULE-017) ---

    Examples: Boundary values
      | boundary_point                    | config               | outcome                                       |
      | no creatives (valid)              | no creatives         | creative validation passes                    |
      | valid library reference           | assignment cr-001    | creative validation passes                    |
      | valid inline upload               | upload with format   | creative validation passes                    |
      | creative_id not in library        | assignment cr-bad    | error CREATIVE_NOT_FOUND with suggestion       |
      | format not in product             | wrong format         | error VALIDATION_ERROR with suggestion         |
      | weight = 0 (paused)               | weight=0             | creative validation passes                    |
      | weight = 100 (max)                | weight=100           | creative validation passes                    |
      | 101 inline creatives              | 101 uploads          | error INVALID_REQUEST with suggestion          |

  @T-UC-002-boundary-approval-workflow @boundary @approval-workflow
  Scenario Outline: Approval workflow boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the approval configuration is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- account_ref boundaries (BR-RULE-080) ---

    Examples: Boundary values
      | boundary_point                        | config                | outcome                      |
      | both flags false (auto-approve)       | both=false            | auto-approved path taken     |
      | tenant flag true (pending)            | tenant_hr=true        | manual approval required     |
      | adapter flag true (pending)           | adapter_ma=true       | manual approval required     |

  @T-UC-002-boundary-account-ref @boundary @account
  Scenario Outline: Account reference boundary validation - <boundary_point>
    Given a create_media_buy request with account: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- optimization_goals boundaries (BR-RULE-087) ---

    Examples: Boundary values
      | boundary_point                                       | config                   | outcome                                          |
      | account_id present + account exists + active         | acc-001 active           | account resolution succeeds                      |
      | account_id present + not found                       | acc-bad not-found        | error ACCOUNT_NOT_FOUND terminal                 |
      | brand + operator present + single match + active     | brand+op single match    | account resolution succeeds                      |
      | brand + operator present + no match                  | brand+op no match        | error ACCOUNT_NOT_FOUND terminal                 |
      | brand + operator present + multiple matches          | brand+op multi match     | error ACCOUNT_AMBIGUOUS correctable              |
      | account resolved + setup incomplete                  | acc setup-needed         | error ACCOUNT_SETUP_REQUIRED correctable          |
      | account resolved + payment due                       | acc payment-due          | error ACCOUNT_PAYMENT_REQUIRED terminal           |
      | account resolved + suspended                         | acc suspended            | error ACCOUNT_SUSPENDED terminal                 |
      | account field absent                                 | no account               | error INVALID_REQUEST                            |
      | both account_id and brand/operator present           | both fields              | error INVALID_REQUEST                            |
      | brand + operator + sandbox:true present + sandbox account exists + active | brand+op+sandbox active | account resolution succeeds                      |

  @T-UC-002-boundary-optimization-goals @boundary @optimization-goals
  Scenario Outline: Optimization goals boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the optimization goals scenario is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- catalog_distinct_type boundaries (BR-RULE-089) ---
    # A value the schema refuses is INVALID_REQUEST ("violates schema constraints"); a value
    # the schema admits but this product does not support is UNSUPPORTED_FEATURE. In
    # core/optimization-goal.json: view_duration_seconds and target.value are
    # "exclusiveMinimum": 0 (so 0 and -1 are schema violations), target.kind is a closed
    # discriminator (an unknown kind matches no branch), and metric is a closed enum with no
    # "viewability" member. Those rows are INVALID_REQUEST.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/optimization-goal.json

    Examples: Boundary values
      | boundary_point                                                          | config                   | outcome                                          |
      | optimization_goals present with 1 element (minItems boundary)           | 1 metric goal            | optimization validation passes                   |
      | optimization_goals present with 0 elements (below minItems)             | empty array              | error INVALID_REQUEST with suggestion             |
      | optimization_goals array at max_optimization_goals (cap boundary)       | at max count             | optimization validation passes                   |
      | optimization_goals array at max_optimization_goals + 1 (above cap)      | above max count          | optimization validation passes                   |
      | priority = 1 (minimum valid)                                            | priority=1               | optimization validation passes                   |
      | priority = 0 (below minimum)                                            | priority=0               | error INVALID_REQUEST with suggestion             |
      | view_duration_seconds = 0.001 (just above exclusiveMinimum 0)           | vds=0.001                | optimization validation passes                   |
      | view_duration_seconds = 0 (at exclusiveMinimum boundary)                | vds=0                    | error INVALID_REQUEST with suggestion              |
      | target.value = 0.001 (just above exclusiveMinimum 0)                    | target=0.001             | optimization validation passes                   |
      | target.value = 0 (at exclusiveMinimum boundary)                         | target=0                 | error INVALID_REQUEST with suggestion              |
      | kind = 'metric' with metric field present (valid branch)                | metric kind valid        | optimization validation passes                   |
      | kind = 'event' with event_sources field present (valid branch)          | event kind valid         | optimization validation passes                   |
      | product has metric_optimization + metric goal submitted                 | metric capable           | optimization validation passes                   |
      | product lacks metric_optimization + metric goal submitted               | no metric capability     | error UNSUPPORTED_FEATURE with suggestion          |
      | product has conversion_tracking + event goal submitted                  | event capable            | optimization validation passes                   |
      | product lacks conversion_tracking + event goal submitted                | no event capability      | error UNSUPPORTED_FEATURE with suggestion          |
      | target_frequency.min = 1, max = 3 (min <= max, valid)                   | freq min=1 max=3         | optimization validation passes                   |
      | target_frequency.min = 5, max = 3 (min > max, invalid)                  | freq min=5 max=3         | error VALIDATION_ERROR on packages[0].optimization_goals[0].target_frequency with suggestion |
      | per_ad_spend target + value_field present on event source               | roas with value_field    | optimization validation passes                   |
      | per_ad_spend target + no value_field on any event source                | roas no value_field      | error VALIDATION_ERROR on packages[0].optimization_goals[0].event_sources with suggestion |
    # "optimization validation passes" grades the confirmed package: core/package.json carries
    # optimization_goals, and packages[0].optimization_goals must hold every goal sent.
    # CORRECTED to the pin (v3.2.7):
    # - above cap passes: core/product.json max_optimization_goals "When absent, no limit is
    #   declared" (the seeded product declares none), and above a declared cap "buyers ...
    #   should expect the seller to use only the highest-priority ... goal", not a refusal.
    # - min > max and per_ad_spend without value_field are VALIDATION_ERROR on the member at
    #   fault: "When both min and max are present, max must be greater than or equal to min"
    #   and "sellers must reject these target kinds when no event source entry includes
    #   value_field" are business rules the schema does not express (enums/error-code.json
    #   VALIDATION_ERROR: "violates business rules beyond schema validation").
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product.json pointer=/properties/max_optimization_goals
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/optimization-goal.json
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/VALIDATION_ERROR

  @T-UC-002-boundary-catalog-distinct-type @boundary @catalog-distinct-type
  Scenario Outline: Catalog distinct type boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the catalog configuration is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- format_id_structure boundaries ---

    Examples: Boundary values
      | boundary_point                                                               | config               | outcome                                      |
      | 0 catalogs (field absent)                                                    | absent               | catalog validation passes                    |
      | 0 catalogs (empty array)                                                     | empty array          | catalog validation passes                    |
      | 1 catalog (uniqueness trivially satisfied)                                   | 1 product            | catalog validation passes                    |
      | 2 catalogs, different types (product + store)                                | product+store        | catalog validation passes                    |
      | 2 catalogs, same type (product + product) — first duplicate violation        | product+product      | error VALIDATION_ERROR on packages[0].catalogs with suggestion |
      | 3 catalogs, two share same type (product + product + store)                  | 2prod+store          | error VALIDATION_ERROR on packages[0].catalogs with suggestion |
      | 13 catalogs, all 13 distinct enum values                                     | all 13 types         | catalog validation passes                    |
      | Two packages each with type=product (distinct per-package, not cross-package) | cross-pkg product    | catalog validation passes                    |
      | catalog_id references a synced catalog                                       | valid catalog_id     | catalog validation passes                    |
      | catalog_id references a non-existent catalog                                 | bad catalog_id       | error INVALID_REQUEST with suggestion          |
    # LOCAL CORRECTION: the two duplicate-type rows asserted INVALID_REQUEST. The pinned
    # package-request.json catalogs: "sellers MUST reject requests containing multiple catalogs
    # of the same type with a validation_error", so the code is VALIDATION_ERROR, and the
    # refused package's catalogs array is the field. "catalog validation passes" also grades
    # that every package echoes the catalogs it was sent (core/package.json catalogs: "Echoed
    # from the create_media_buy request"), so a seller that ignored catalogs cannot pass it.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/properties/catalogs
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/package.json pointer=/properties/catalogs

  @T-UC-002-boundary-format-id-structure @boundary @format-id-structure
  Scenario Outline: Format ID structure boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the format ID scenario is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- persistence_timing boundaries (BR-RULE-020) ---
    # A plain string or a FormatId missing agent_url/id violates core/format-id.json ("never a
    # plain string"; required agent_url, id): INVALID_REQUEST, field = issues[0].pointer.
    # A well-formed id that no canonical declaration of the product resolves: "an entry that
    # cannot be normalized is rejected with UNSUPPORTED_FEATURE" (package-request.json).
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/format-id.json pointer=/required
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/properties/format_ids/description
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/INVALID_REQUEST

    Examples: Boundary values
      | boundary_point                                | config              | outcome                      |
      | valid object (registered agent + known format) | valid FormatId      | format validation passes     |
      | plain string (wrong type)                      | "banner_300x250"    | error INVALID_REQUEST on packages[0].format_ids[0] with suggestion          |
      | missing agent_url                              | no agent_url        | error INVALID_REQUEST on packages[0].format_ids[0].agent_url with suggestion |
      | unregistered agent                             | bad agent_url       | error UNSUPPORTED_FEATURE on packages[0].format_ids with suggestion         |
      | unknown format id                              | unknown format      | error UNSUPPORTED_FEATURE on packages[0].format_ids with suggestion         |

  @T-UC-002-boundary-persistence-timing @boundary @persistence-timing
  Scenario Outline: Persistence timing boundary validation - <boundary_point>
    Given a valid create_media_buy request
    And the account exists and is active
    And the persistence timing scenario is: <config>
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # --- tasks_sort_field boundaries ---

    Examples: Boundary values
      | boundary_point                              | config                | outcome                                      |
      | adapter returns success (auto-approval)     | auto-approve success  | all records persisted after adapter success   |
      | adapter returns error (auto-approval)       | auto-approve failure  | no records persisted after adapter failure    |
      | manual approval detected (pending state)    | manual approval       | records persisted in pending state            |

  @T-UC-002-boundary-tasks-sort-field @boundary @tasks-sort-field
  Scenario Outline: Tasks sort field boundary validation - <boundary_point>
    Given the Buyer Agent owns tasks of several types, statuses and update times
    And the task list sort field is <config>
    When the Buyer Agent queries the task list
    Then the response is compliant with the list_tasks spec
    And the result should be <outcome>
    # --- sort_direction boundaries ---
    # sort.field enum [created_at, updated_at, status, task_type, protocol]: protocol, not
    # domain, is the last member.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/protocol/list-tasks-request.json pointer=/properties/sort
    # The protocol row claims no order: every task this seller lists is in the media-buy
    # domain; the partition outline above says why.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/task-type.json pointer=/enumDescriptions

    Examples: Boundary values
      | boundary_point                            | config          | outcome                                          |
      | created_at (first enum value, default)    | created_at      | tasks sorted by created_at desc                  |
      | protocol (last enum value)                | protocol        | tasks of every protocol                          |
      | Not provided (uses default created_at)    | omitted         | tasks sorted by created_at desc                  |
      | priority (not in enum)                    | priority        | error INVALID_REQUEST correctable on sort.field  |

  @T-UC-002-boundary-sort-direction @boundary @sort-direction
  Scenario Outline: Sort direction boundary validation - <boundary_point>
    Given the Buyer Agent owns tasks of several types, statuses and update times
    And the task list sort direction is <config>
    When the Buyer Agent queries the task list
    Then the response is compliant with the list_tasks spec
    And the result should be <outcome>
    # --- adcp_domain boundaries ---
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/sort-direction.json

    Examples: Boundary values
      | boundary_point                      | config      | outcome                                             |
      | asc (first enum value)              | asc         | tasks sorted by created_at asc                      |
      | desc (last enum value, default)     | desc        | tasks sorted by created_at desc                     |
      | Not provided (uses default desc)    | omitted     | tasks sorted by created_at desc                     |
      | ascending (not in enum)             | ascending   | error INVALID_REQUEST correctable on sort.direction |

  @T-UC-002-boundary-adcp-domain @boundary @adcp-domain
  Scenario Outline: AdCP protocol filter boundary validation - <boundary_point>
    Given the Buyer Agent owns tasks of several types, statuses and update times
    And the protocol filter is <config>
    When the Buyer Agent queries the task list
    Then the response is compliant with the list_tasks spec
    And the result should be <outcome>
    # --- task_status boundaries ---
    # enums/adcp-protocol.json runs media-buy .. measurement; creative is not the last member.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/adcp-protocol.json

    Examples: Boundary values
      | boundary_point                                      | config                    | outcome                                                |
      | media-buy (first enum value)                        | media-buy                 | tasks with protocol media-buy only                     |
      | measurement (last enum value)                       | measurement               | tasks with protocol measurement only                   |
      | ["media-buy", "signals"] (multi-protocol array)     | media-buy+signals         | tasks with protocol media-buy, signals only            |
      | Not provided (no protocol filtering)                | omitted                   | tasks of every protocol                                |
      | analytics (not in enum)                             | analytics                 | error INVALID_REQUEST correctable on filters.protocol  |
      | [] (empty array, violates minItems)                 | empty array               | error INVALID_REQUEST correctable on filters.protocols |

  @T-UC-002-boundary-task-status @boundary @task-status
  Scenario Outline: Task status boundary validation - <boundary_point>
    Given the Buyer Agent owns tasks of several types, statuses and update times
    And the task status filter is <config>
    When the Buyer Agent queries the task list
    Then the response is compliant with the list_tasks spec
    And the result should be <outcome>
    # --- task_type boundaries ---
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/task-status.json

    Examples: Boundary values
      | boundary_point                                                        | config                              | outcome                                                    |
      | submitted (first enum value)                                          | submitted                           | tasks with status submitted only                           |
      | unknown (last enum value)                                             | unknown                             | tasks with status unknown only                             |
      | ["submitted", "working", "input-required"] (multi-status array)       | submitted+working+input-required    | tasks with status submitted, working, input-required only  |
      | Not provided (no status filtering)                                    | omitted                             | tasks of every status                                      |
      | pending (not in enum)                                                 | pending                             | error INVALID_REQUEST correctable on filters.status        |
      | [] (empty array, violates minItems)                                   | empty array                         | error INVALID_REQUEST correctable on filters.statuses      |

  @T-UC-002-boundary-task-type @boundary @task-type
  Scenario Outline: Task type boundary validation - <boundary_point>
    Given the Buyer Agent owns tasks of several types, statuses and update times
    And the task type filter is <config>
    When the Buyer Agent queries the task list
    Then the response is compliant with the list_tasks spec
    And the result should be <outcome>
    # enums/task-type.json runs create_media_buy .. sync_reporting_receipts; log_event is not
    # the last member.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/task-type.json

    Examples: Boundary values
      | boundary_point                                                    | config                             | outcome                                                      |
      | create_media_buy (first enum value)                               | create_media_buy                   | tasks with task_type create_media_buy only                   |
      | sync_reporting_receipts (last enum value)                         | sync_reporting_receipts            | tasks with task_type sync_reporting_receipts only            |
      | ["create_media_buy", "update_media_buy"] (multi-type array)       | create_media_buy+update_media_buy  | tasks with task_type create_media_buy, update_media_buy only |
      | Not provided (no task type filtering)                             | omitted                            | tasks of every task_type                                     |
      | delete_media_buy (not in enum)                                    | delete_media_buy                   | error INVALID_REQUEST correctable on filters.task_type       |
      | [] (empty array, violates minItems)                               | empty array                        | error INVALID_REQUEST correctable on filters.task_types      |

  @T-UC-002-nfr-001 @nfr @nfr-001
  Scenario: Security hardening -- request validation and rate limiting
    Given a valid create_media_buy request
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the system should validate authentication before any business logic
    And the system should enforce rate limiting on the endpoint
    And the system should validate payload size limits

  @T-UC-002-nfr-003 @nfr @nfr-003
  Scenario: Audit logging -- all steps are logged
    Given a valid create_media_buy request
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the system should log the protocol audit entry
    And the approval decision should be logged
    And the adapter execution should be logged

  @T-UC-002-nfr-004 @nfr @nfr-004
  Scenario: Response latency -- within SLA
    Given a valid create_media_buy request
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the response should be returned within 15 seconds (p95)

  @T-UC-002-nfr-006 @nfr @nfr-006
  Scenario: Minimum order size enforcement
    Given a valid create_media_buy request
    And the account exists and is active
    And the tenant has minimum order size requirements
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the system should validate budget against minimum order requirements

  @T-UC-002-sandbox-happy @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account creates simulated media buy with sandbox flag
    Given a valid create_media_buy request with packages
    And the request targets a sandbox account
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response status should be "completed"
    And the response should include sandbox equals true
    And no real billing records should have been created
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-3: real billing suppressed
    #
    # INV-2 ("real ad platform calls suppressed") is NOT graded here, and the line that
    # claimed to grade it is gone. "And no real ad platform orders should have been
    # created" bound then_no_real_orders_created, which asserted require_payload +
    # sandbox is True -- character for character the same as then_sandbox_true, whose
    # sentence sits on the line above. Grading INV-2 needs an instrument outside a Then
    # (a structural guard over what a sandbox-serving tool imports, or an egress
    # observable the live server also writes); a duplicate sandbox read was not one.
    # BR-RULE-209 INV-4: response includes sandbox: true

  @T-UC-002-sandbox-no-live-ad-server @invariant @br-rule-209 @sandbox @live-ad-server
  Scenario: Sandbox account on a live-ad-server tenant is never routed to the live ad server
    Given a valid create_media_buy request with packages
    And the tenant runs the "google_ad_manager" ad server
    And the request targets a sandbox account
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response status should be "completed"
    # BR-RULE-209 INV-2: real ad platform calls suppressed.
    #
    # THIS is the grading the sibling @T-UC-002-sandbox-happy says it cannot do:
    # "Grading INV-2 needs an instrument outside a Then ... a duplicate sandbox read
    # was not one." The instrument is the TENANT. The Given gives it a real
    # google_ad_manager configuration with a network code and no credential, and
    # stops the harness faking the adapter decision — so the request is answered by
    # whatever production selection actually picks. A selection that reaches GAM
    # cannot answer at all from a deployment holding no GAM credential:
    # GoogleAdManager.__init__ raises AdCPConfigurationError(field="authentication")
    # at google_ad_manager.py:176, which is BEFORE the client manager is built and
    # therefore well before createOrders. So "completed" here is strictly stronger
    # than "createOrders was not called" — it says the GAM adapter was never
    # CONSTRUCTED for this request. Pinned AdCP 3.1.1
    # docs/media-buy/advanced-topics/sandbox.mdx:229 "MUST NOT make real ad platform
    # API calls (no real orders, line items, etc.)" and :11 "Once a request
    # references a sandbox account, the entire request is treated as sandbox".
    #
    # Deliberately asserts NOTHING about a sandbox echo on the response: that is the
    # separate, still-open gap @T-UC-002-sandbox-happy is xfailed for
    # (CreateMediaBuyResult declares no sandbox field), and binding the two together
    # would make this isolation grade wait on it.

  @T-UC-002-live-ad-server-reached @invariant @br-rule-209 @sandbox @live-ad-server
  Scenario: Production account on the same tenant is routed to the live ad server
    Given a valid create_media_buy request with packages
    And the tenant runs the "google_ad_manager" ad server
    And the request targets a production account
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the error code should be "CONFIGURATION_ERROR"
    # The INSTRUMENT CHECK for the scenario above, and the reason its green mark
    # means something. Byte-identical Givens except the account's sandbox flag, so
    # the only difference between the two outcomes is the flag. If the adapter_config
    # row were never written, or the harness were still faking the adapter decision,
    # or the account never reached the identity, THIS scenario would complete like
    # any other create — and the pair could not both be green. Neither scenario is
    # evidence alone; the pair is.
    #
    # CONFIGURATION_ERROR is the honest answer here, not a contrivance: a tenant
    # configured for an ad server whose credentials this deployment does not hold is
    # precisely what AdCPConfigurationError documents ("a missing API key"), and the
    # pinned table classifies the code terminal — the buyer has no lever over the
    # seller's ad-server credentials.

  @T-UC-002-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account media buy response does not include sandbox flag
    Given a valid create_media_buy request with packages
    And the request targets a production account
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response status should be "completed"
    And the response sandbox field should be absent or false
    # BR-RULE-209 INV-5: production account -> sandbox absent
    # LOCAL CORRECTION: the Then required sandbox to be absent. The pin describes only
    # sandbox: true ("When true, this response contains simulated data") and sandbox.mdx says
    # sellers "SHOULD include sandbox: true" for a sandbox account, so a production response
    # carrying sandbox: false is conformant. The Then grades absent or false on the wire.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-response.json
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/advanced-topics/sandbox.mdx

  @T-UC-002-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid budget returns real validation error
    Given a valid create_media_buy request with total budget 0
    And the request targets a sandbox account
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

  @T-UC-002-sandbox-natural-key @invariant @br-rule-209 @sandbox @account
  Scenario: v3.1 natural-key reference with sandbox true resolves to the sandbox account without prior provisioning
    Given a valid create_media_buy request with packages
    And the request uses a natural-key account reference with brand and operator and sandbox true
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the reference should resolve to the sandbox account for that brand and operator
    And the response should include sandbox equals true
    # BR-RULE-209 INV-8 + BR-RULE-080 INV-10: natural-key (brand+operator+sandbox:true) resolves to sandbox account without prior sync_accounts provisioning
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-idempotency-replay @v31 @idempotency-key @post-s1 @ext-w @happy-path
  Scenario: v3.1 idempotency_key replay returns existing media buy without re-execution
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request with idempotency_key "abc123-replay-xyz-9876"
    And the account "acc-001" exists and is active
    And a media buy was already created for the same seller with that idempotency_key
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    And the response status should be "completed"
    And the response should include the previously created "media_buy_id"
    And no new ad platform order should have been created
    # v3.1: idempotency_key uniquely identifies (seller, request) pair
    # POST-S5: Buyer receives an unambiguous success confirmation
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-idempotency-missing @v31 @idempotency-key @validation @post-f2 @ext-w
  Scenario: v3.1 idempotency_key missing fails request validation
    Given a create_media_buy request with the idempotency_key field omitted
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the error code should be "INVALID_REQUEST"
    And the error should reference the missing "idempotency_key" field
    And the error should include "suggestion" field
    # v3.1: idempotency_key is required on create-media-buy-request
    #
    # NAMES the code instead of saying "a validation error" and letting a shared step
    # pick one. The step it used to route to hardcoded VALIDATION_ERROR; the pinned split
    # puts a MISSING REQUIRED FIELD on the other side, verbatim: INVALID_REQUEST is
    # "malformed, MISSING REQUIRED FIELDS, or violates schema constraints", while
    # VALIDATION_ERROR is "invalid field values or violates business rules beyond schema
    # validation". An omitted required idempotency_key is the first, and all four
    # transports (impl/mcp/a2a/rest and e2e_rest) already emit INVALID_REQUEST in
    # agreement -- the scenario was the only thing asking for the other code.
    # The code enum and its descriptions live in core/error.json.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-idempotency-pattern-invalid @v31 @idempotency-key @validation @post-f2 @ext-w
  Scenario Outline: v3.1 idempotency_key violates length/pattern constraints
    Given a create_media_buy request with idempotency_key "<value>"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the error code should be "INVALID_REQUEST"
    And the error should name idempotency_key as rejected by schema keyword "<keyword>"
    And the error should include "suggestion" field
    # v3.1: idempotency_key pattern ^[A-Za-z0-9_.:-]{16,255}$
    # Corrected to the pin: a malformed key is a schema rejection, "A malformed request returns
    # `INVALID_REQUEST` without ever touching the cache" (security.mdx Idempotency rule 1), so
    # the code is INVALID_REQUEST, not VALIDATION_ERROR. The constraint is graded through the
    # pin's structured channel, error.issues[].keyword ("Schema keyword that rejected the
    # payload ... e.g. 'pattern', 'minimum', 'maxLength'"), never through message text.
    # The maxLength row used to carry 247 characters, which is a VALID key; it carries 256.
    # @source repo=adcp ref=v3.2.7 path=docs/building/by-layer/L1/security.mdx (L414: Idempotency rule 1)
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/error.json pointer=/properties/issues/items/properties/keyword
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/properties/idempotency_key

    Examples:
      | value                                                | keyword   |
      | short                                                | minLength |
      | key with spaces in it that is long enough           | pattern   |
      | key/with/slashes/that/is/also/long/enough           | pattern   |
      | AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA | maxLength |

  @T-UC-002-v31-idempotency-in-flight @v31 @idempotency-key @error-details @post-f2 @post-f3 @ext-w
  Scenario: v3.1 idempotency_key matching an in-flight request rejects with IDEMPOTENCY_IN_FLIGHT
    Given a create_media_buy request with idempotency_key "buy-2026-q1-inflight-001"
    And a prior request for the same (seller, account, idempotency_key) pair is still in flight
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the response should indicate a transient failure
    And the error code should be "IDEMPOTENCY_IN_FLIGHT"
    And the error should include "retry_after" field
    And the error should include "suggestion" field
    And no new media buy should have been created
    # BR-RULE-211 INV-4: in-flight key MAY reject with IDEMPOTENCY_IN_FLIGHT; buyer MUST NOT mint a fresh key
    # Corrected to the pin: the recovery is transient, not terminal. error-code.json
    # enumMetadata IDEMPOTENCY_IN_FLIGHT: "recovery": "transient"; security.mdx rule 9
    # (reject-and-redirect): "the seller returns `IDEMPOTENCY_IN_FLIGHT` immediately, with
    # top-level `error.retry_after` (seconds) populated".
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumMetadata/IDEMPOTENCY_IN_FLIGHT
    # @source repo=adcp ref=v3.2.7 path=docs/building/by-layer/L1/security.mdx (L440-445: Idempotency rule 9)

  @T-UC-002-v31-idempotency-expired @v31 @idempotency-key @error-details @post-f2 @post-f3 @ext-w
  Scenario: v3.1 idempotency_key whose cached response has expired rejects with IDEMPOTENCY_EXPIRED
    Given a create_media_buy request with idempotency_key "buy-2026-q1-expired-001"
    And the (seller, account, idempotency_key) pair was recorded but its cached response expired past replay_ttl_seconds
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the response should indicate a correctable failure
    And the error code should be "IDEMPOTENCY_EXPIRED"
    And the error should include "suggestion" field
    And no new media buy should have been created
    # BR-RULE-211 INV-5: expired cached response -> IDEMPOTENCY_EXPIRED; buyer MUST perform natural-key existence check before minting fresh key
    # security.mdx rule 6: "A request arriving after eviction with a key the seller has seen
    # SHOULD be rejected with `IDEMPOTENCY_EXPIRED` rather than silently treated as new".
    # @source repo=adcp ref=v3.2.7 path=docs/building/by-layer/L1/security.mdx (L428: Idempotency rule 6)
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumMetadata/IDEMPOTENCY_EXPIRED

  @T-UC-002-v31-idempotency-canonical-comparison @v31 @idempotency-key @post-s1 @ext-w @happy-path
  Scenario: v3.1 idempotency replay with reordered fields is treated as identical
    Given a media buy was already created for the same seller with idempotency_key "buy-2026-q1-canon-001"
    And a create_media_buy request with idempotency_key "buy-2026-q1-canon-001" whose payload differs only in field ordering
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    And the response should include the previously created "media_buy_id"
    And no new ad platform order should have been created
    # BR-RULE-211 INV-6: canonical comparison is semantic; field ordering MUST NOT affect outcome
    # security.mdx "Payload equivalence": "The canonical form is RFC 8785 JSON Canonicalization
    # Scheme (JCS) — number serialization, key ordering, and escaping all follow JCS §3".
    # "insignificant whitespace" was dropped from this scenario: whitespace exists only in the
    # serialized bytes each transport writes, so no transport-independent Given can vary it.
    # --- v3.1: submitted task envelope discrimination ---
    # @source repo=adcp ref=v3.2.7 path=docs/building/by-layer/L1/security.mdx (L472: Payload equivalence)

  @T-UC-002-v31-submitted-envelope-shape @v31 @submitted-envelope @post-s7 @post-s9
  Scenario: v3.1 submitted envelope carries task_id but not media_buy_id
    Given the tenant has "human_review_required" set to true
    And a valid create_media_buy request with account "acc-001"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy submitted spec
    And the response status should be "submitted"
    And the response should include a "task_id"
    And the response should not include a media_buy_id at the envelope level
    And the response should not include a packages array at the envelope level
    # v3.1: media_buy_id and packages land on the task's completion artifact, not the envelope
    # The pin's submitted branch: "not": {"anyOf": [required media_buy_id, required packages,
    # required warnings]} (sandbox is not in that list at the pin).
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-response.json pointer=/oneOf/2/not

  @T-UC-002-v31-submitted-envelope-message @v31 @submitted-envelope @prompt-injection
  Scenario: v3.1 submitted envelope message is plain text within the schema bound
    Given the tenant requires manual approval
    And a valid create_media_buy request with account "acc-001"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy submitted spec
    And the response status should be "submitted"
    And the submitted envelope carries a plain-text message of at most 2000 characters
    # v3.1: message field is bounded to maxLength 2000, plain text only
    # v3.1: message MUST be treated as untrusted seller input (prompt-injection defense)
    # Corrected to the pin. The three "the buyer SHOULD ..." Thens are deleted: they are
    # obligations on the BUYER ("Buyers MUST treat this as untrusted seller input: escape
    # before rendering ..."), and this suite grades the seller. The seller's half of the same
    # property is what remains: the message is "Plain text only" and "maxLength": 2000. The
    # When "the seller emits a submitted envelope with a human-readable message" described the
    # response rather than an action, and is folded into the Then.
    # --- v3.1: synchronous success response fields ---
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-response.json pointer=/oneOf/2/properties/message

  @T-UC-002-v31-success-revision-and-actions @v31 @sync-success @post-s1 @post-s3
  Scenario: v3.1 sync success response carries revision, confirmed_at, valid_actions
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    And the response status should be "completed"
    And the response should include a "media_buy_id"
    And the response should include "confirmed_at" as an ISO 8601 timestamp
    And the response should include "revision" with an integer value of at least 1
    And the response should include a "valid_actions" array
    And every value in valid_actions should be a member of the media-buy-valid-action enum
    # v3.1: confirmed_at — order confirmation timestamp
    # v3.1: revision — initial value >= 1 for optimistic concurrency on update_media_buy
    # v3.1: valid_actions — subset of [pause, resume, cancel, update_budget, update_dates, update_packages, add_packages, sync_creatives]
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-success-status-values @v31 @sync-success @media-buy-status
  Scenario Outline: v3.1 sync success status is a MediaBuyStatus enum value, never "submitted"
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request producing the "<initial_state>" condition
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    And the response status should be "completed"
    And the wire media_buy_status should be "<media_buy_status>"
    And the wire status should be "completed"
    # v3.1: synchronous-success status field carries MediaBuyStatus, not task-level "submitted"
    # v3.1: "submitted" is a task-level literal, exclusive to the submitted envelope shape
    # Corrected to the pin. The lifecycle value lives in `media_buy_status`, not `status`:
    # create_media_buy.mdx "The top-level `status` is the envelope task-status (TaskStatus) —
    # `completed` on synchronous success. The body-level `media_buy_status` carries the buy's
    # lifecycle state". The old Then asked `status` to equal a MediaBuyStatus value while the
    # line above it asked `status` to equal "completed"; and "status should not equal
    # submitted" is subsumed by the wire status pinned to "completed".
    # --- v3.1: governance plan_id ---
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/create_media_buy.mdx (L1523)
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-response.json pointer=/oneOf/0/properties/media_buy_status

    Examples:
      | initial_state                       | media_buy_status   |
      | creatives missing for all packages  | pending_creatives  |
      | creatives present, future flight    | pending_start      |
      | creatives present, asap flight      | active             |

  @T-UC-002-v31-plan-id-required-when-governance @v31 @governance @plan-id @validation @ext-x
  Scenario: governance_context required when account has governance_agents configured
    Given a create_media_buy request with the governance_context field omitted
    And the account "acc-001" exists and is active
    And the account has governance_agents configured
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the error code should be "PERMISSION_DENIED"
    And the response should indicate a correctable failure
    # LOCAL CORRECTION: this scenario required plan_id and answered INVALID_REQUEST naming
    # "plan_id". At the pin plan_id is "DEPRECATED on seller-facing requests ... the seller
    # forwards that opaque context and does not need the plan identifier", and the request
    # field governance applies through is governance_context: "Required when governance
    # applies to the resolved account". The campaign-governance spec (Seller enforcement):
    # "A service receiving a governed commitment MUST require a valid, in-date intent-phase
    # governance_context token ... A request without a token ... MUST be rejected with
    # PERMISSION_DENIED"; "a registered governance agent means the applicable commitment
    # requires a token". enumMetadata gives PERMISSION_DENIED recovery correctable ("call
    # check_governance to mint a valid token"). The tag keeps its old name so the routing
    # that names it still finds it.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/properties/governance_context
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/properties/plan_id
    # @source repo=adcp ref=v3.2.7 path=docs/governance/campaign/specification.mdx (Seller enforcement, L178-182)
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumMetadata/PERMISSION_DENIED

  @T-UC-002-v31-plan-id-resolves @v31 @governance @plan-id @ext-x @happy-path
  Scenario: v3.1 deprecated plan_id beside an approved governance_context -- the seller relies on the governance_context and proceeds
    Given a valid create_media_buy request with plan_id "plan-001"
    And the account "acc-001" exists and is active
    And the account has governance_agents configured
    And the buyer's governance agent has returned verdict "approved" for the exact proposed buy
    And the buyer attaches the governance_context from that verdict to the create_media_buy request
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the request should proceed past the governance gate
    And the seller's execution check should carry the request's governance_context verbatim
    # Corrected to the pin at 3.2.7. The scenario used to have the seller resolve plan_id
    # itself and forward it to check_governance. At the pin plan_id is DEPRECATED on
    # seller-facing requests: the seller "forwards that opaque context and does not need
    # the plan identifier" (create-media-buy-request.json /properties/plan_id), services
    # "MUST treat governance_context as authoritative and MUST NOT require a buyer to
    # disclose plan_id", and they pass governance_context "verbatim on execution and
    # lifecycle checks" (check-governance-request.json /properties/plan_id,
    # /properties/governance_context). The seller holds no plans to resolve; the issuing
    # governance agent recovers the plan from the token (campaign/specification.mdx).
    # Forwarding plan_id is permitted, not required, so it is no longer asserted.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/properties/plan_id
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/governance/check-governance-request.json pointer=/properties/governance_context
    # @source repo=adcp ref=v3.2.7 path=docs/governance/campaign/specification.mdx (L180, L967-977)

  # DELETED: @T-UC-002-v31-plan-id-not-found. It required create_media_buy to refuse an
  # unresolvable plan_id with PLAN_NOT_FOUND. The pin puts no plan_id obligation on this
  # task: plan_id is "DEPRECATED on seller-facing requests ... the seller forwards that
  # opaque context and does not need the plan identifier. If both are present, the
  # governance agent MUST reject a mismatch" -- the governance agent, not the seller.
  # PLAN_NOT_FOUND is a check_governance answer; the governed-commitment refusal the seller
  # owns is stated by @T-UC-002-v31-plan-id-required-when-governance above.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/properties/plan_id

  @T-UC-002-v31-plan-id-optional-without-governance @v31 @governance @plan-id @ext-x @happy-path
  Scenario: v3.1 plan_id optional when account has no governance_agents configured
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request with the plan_id field omitted
    And the account has no governance_agents configured
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    # BR-RULE-212 INV-4: no governance agents -> plan_id optional, governance skipped
    # CORRECTED against the pin. governance/campaign/specification.mdx: "When
    # `governance_agents` is absent, the seller processes media buy requests normally ...
    # there is no seller-side verification", and "Sellers MUST NOT require governance checks
    # as a prerequisite for all accounts". plan_id itself is DEPRECATED on the request. So the
    # one observable is a buy created normally: the success arm, on an auto-approved tenant.
    # "proceed past the governance gate" and "the governance path should be skipped" named
    # no observable beyond that (with no agent registered there is no call to watch), and
    # the "acc-001" line seeded an account the request does not name; the account graded is
    # the one the request names.
    # --- v3.1: invoice_recipient override ---
    # @source repo=adcp ref=v3.2.7 path=docs/governance/campaign/specification.mdx
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/properties/plan_id

  @T-UC-002-v31-invoice-recipient-echoed @v31 @invoice-recipient @post-s5
  Scenario: v3.1 invoice_recipient overrides default billing and is echoed in response
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request with an invoice_recipient override
    And the invoice_recipient is authorized for the account
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    And the response status should be "completed"
    And the response should include an "invoice_recipient" matching the request
    And the response invoice_recipient should not include any bank-detail fields
    # v3.1: per-buy billing override; seller MUST validate invoice_recipient is authorized for the account
    # v3.1: bank details are write-only and omitted from the response
    # --- v3.1: io_acceptance for proposal IO signing ---
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-io-acceptance-required @v31 @io-acceptance @proposal-based @error-details @post-f2 @post-f3 @ext-x
  Scenario: v3.1 proposal requires IO signature but io_acceptance is missing -- rejected with IO_REQUIRED
    Given a valid create_media_buy request with proposal_id "prop-abc-2026"
    And the proposal's insertion_order requires_signature is true
    And the request does not include an io_acceptance field
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the response should indicate a correctable failure
    And the error code should be "IO_REQUIRED"
    And the error should include "suggestion" field
    # BR-RULE-213 INV-1: requires_signature true + io_acceptance absent -> IO_REQUIRED (correctable)
    # v3.1 supersession: BR-RULE-213 mandates IO_REQUIRED reject-and-resubmit (replaces the earlier task-envelope-for-IO-signing modeling)
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-io-acceptance-provided @v31 @io-acceptance @proposal-based @post-s11 @ext-x @happy-path
  Scenario: v3.1 io_acceptance with matching io_id and signatory completes proposal execution synchronously
    Given a valid create_media_buy request with proposal_id "prop-abc-2026"
    And the proposal's insertion_order requires_signature is true
    And the request includes io_acceptance with:
    | field         | value                                |
    | io_id         | prop-abc-2026.io.v1                  |
    | accepted_at   | 2026-04-01T12:00:00Z                 |
    | signatory     | buyer-agent-12345                    |
    And the io_acceptance.io_id matches the proposal's insertion_order.io_id
    And the account "acc-001" exists and is active
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    And the response status should be "completed"
    And the response should include a "media_buy_id"
    # v3.1: io_acceptance carries the signing metadata; signature_id is optional
    # BR-RULE-213 INV-3: complete io_acceptance -> gate satisfied, proceeds
    # POST-S11: Buyer knows the proposal was successfully executed with their total budget
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-io-acceptance-incomplete @v31 @io-acceptance @proposal-based @validation @post-f2 @ext-x
  Scenario Outline: v3.1 io_acceptance missing a required member rejects with INVALID_REQUEST
    Given a create_media_buy request with proposal_id "prop-abc-2026" and an io_acceptance missing "<missing_member>"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the error code should be "INVALID_REQUEST"
    And the error should reference the missing "<missing_member>" member
    And the error should include "suggestion" field
    # BR-RULE-213 INV-2: io_acceptance present but missing io_id/accepted_at/signatory -> INVALID_REQUEST naming member(s)
    # CORRECTED against the pin. io_acceptance declares "required": ["io_id", "accepted_at",
    # "signatory"] on the object itself, so the refusal does not depend on the proposal: the
    # "requires_signature is true" precondition is dropped (it decides only whether
    # io_acceptance must be PRESENT). The "validation error" Then is dropped too: it asserted
    # VALIDATION_ERROR, and a missing required member is INVALID_REQUEST ("missing required
    # fields", enums/error-code.json; L1/security.mdx "Schema validation runs first").
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json

    Examples:
      | missing_member |
      | io_id          |
      | accepted_at    |
      | signatory      |

  @T-UC-002-v31-io-acceptance-not-required @v31 @io-acceptance @proposal-based @ext-x @happy-path
  Scenario: v3.1 io_acceptance optional when proposal does not require signature
    Given a valid create_media_buy request with proposal_id "prop-no-sig-2026" and the io_acceptance field omitted
    And the proposal's insertion_order requires_signature is false
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the request should proceed to further validation
    And the io_acceptance field should not affect processing
    # BR-RULE-213 INV-4: requires_signature false/absent -> io_acceptance optional, no effect
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-io-acceptance-signature-id @v31 @io-acceptance @proposal-based @ext-x @happy-path
  Scenario: v3.1 optional signature_id is recorded without altering the acceptance gate
    Given a valid create_media_buy request with proposal_id "prop-abc-2026"
    And the proposal's insertion_order requires_signature is true
    And the request includes io_acceptance with io_id, accepted_at, signatory, and signature_id "sig-998"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the IO acceptance gate should be satisfied
    And the signature_id should be recorded as a signing-service reference
    # BR-RULE-213 INV-5: optional signature_id recorded as reference, does not alter gate outcome
    # --- v3.1: advertiser_industry per buy ---
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-advertiser-industry-per-buy @v31 @advertiser-industry @post-s1
  Scenario: v3.1 advertiser_industry on the request targets a single industry per buy
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request with advertiser_industry "healthcare.wellness"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    And the response status should be "completed"
    # v3.1: a brand may operate across multiple industries; each media buy targets exactly one
    # Corrected to the pin. Two lines are deleted because nothing in this scenario can
    # establish or observe them:
    # - 'the brand "acme.com" operates across industries [...]'. The request field's own
    #   description consults the brand manifest only when advertiser_industry is OMITTED, and
    #   then as a MAY ("When omitted, sellers may infer from the brand manifest's industries
    #   field"). This request supplies it, so the brand's industries take no part, and this
    #   seller reads no buyer brand.json for a Given to seed.
    # - 'the seller should map advertiser_industry to its platform-native industry code'.
    #   "Sellers map this to platform-native codes" describes the seller's side and carries
    #   no MUST; no create_media_buy or get_media_buys response field reports the mapped code
    #   (get-media-buys-response.json carries advertiser_industry only inside
    #   accepted_proposal.commercial_terms), so no transport can observe it.
    # What the pin does fix is that the field is single-valued (one enum value per buy) and
    # declared on the request, so a seller must take it rather than refuse it; the success
    # Thens above grade exactly that.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/properties/advertiser_industry
    # --- v3.1: package paused flag ---
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-package-paused-on-creation @v31 @paused @post-s1
  Scenario: v3.1 package created with paused=true is reported paused
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request with one package having paused set to true
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    And the response status should be "completed"
    And the paused package should be created in a paused state
    # v3.1: paused flag on package-request (default false); paused packages do not serve
    # Corrected to the pin. "the paused package should not deliver impressions until resumed"
    # is deleted: a create response carries no delivery, so nothing in this scenario can
    # observe it; delivery is graded by get_media_buy_delivery (UC-004). What the create
    # does carry is the package state: core/package.json `paused` "Whether this package is
    # paused by the buyer. Paused packages do not deliver impressions."
    # --- v3.1: committed_metrics negotiation ---
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json pointer=/properties/paused
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/package.json pointer=/properties/paused

  @T-UC-002-v31-committed-metrics-standard-accepted @v31 @committed-metrics @post-s3
  Scenario: v3.1 committed_metrics standard scope -- seller accepts and stamps committed_at
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request
    And the request package includes committed_metrics with one entry of scope "standard"
    And the proposed standard metric_id is present in the product's available_metrics
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    And the response status should be "completed"
    And the response package committed_metrics should echo the standard metric_id
    And the response package committed_metrics entry should include a "committed_at" timestamp
    # v3.1: committed_at is stamped by the seller on accept; request-side entries do not carry it
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-committed-metrics-rejected @v31 @committed-metrics @validation @post-f2
  Scenario: v3.1 committed_metrics references metric not in product available_metrics -- TERMS_REJECTED
    Given a valid create_media_buy request
    And the request package includes committed_metrics with standard metric_id not in the product's available_metrics
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "TERMS_REJECTED"
    And the error should reference the offending committed_metrics entry
    # v3.1: seller SHOULD reject with TERMS_REJECTED when the proposal exceeds product capability
    # media-buy/package-request.json committed_metrics: "each `scope: standard` entry's
    # `metric_id` MUST be in the product's `available_metrics` ... sellers SHOULD reject with
    # `TERMS_REJECTED` and reference the offending entry when the proposal exceeds product
    # capability."
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/package-request.json
    # --- v3.1: planned_delivery transparency ---

  @T-UC-002-v31-planned-delivery-with-governance @v31 @planned-delivery @governance @post-s3
  Scenario: v3.1 planned_delivery present in response when account has governance agents
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request with plan_id "plan-2026-q1"
    And the account "acc-001" has governance_agents configured
    And the buyer's governance agent has returned verdict "approved" for the exact proposed buy
    And the buyer attaches the governance_context from that verdict to the create_media_buy request
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    And the response status should be "completed"
    And the response should include a "planned_delivery" block
    # v3.1: planned_delivery describes what the seller will actually run (geo, channels, flight, freq caps, budget)
    # v3.1: present when account has governance_agents OR seller chooses to provide delivery transparency
    # Corrected to the pin at 3.2.7: the request now carries the buyer's approved
    # governance_context. On an account with a registered governance agent the field is
    # "Required when governance applies to the resolved account", and "A request without a
    # token ... MUST be rejected with PERMISSION_DENIED" -- so the success this scenario
    # asserts is reachable only with the token. plan_id stays as the deprecated legacy
    # field; it is not what authorizes the buy.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-response.json pointer=/oneOf/0/properties/planned_delivery
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/properties/governance_context
    # @source repo=adcp ref=v3.2.7 path=docs/governance/campaign/specification.mdx (L180, L770-792)
    # --- v3.1: artifact_webhook for governance content delivery ---

  @T-UC-002-v31-artifact-webhook-config @v31 @artifact-webhook @governance
  Scenario: v3.1 artifact_webhook configured for content delivery to governance agent
    Given a valid create_media_buy request
    And the request includes an artifact_webhook with delivery_mode "realtime"
    And the artifact_webhook authentication scheme is "Bearer"
    And the account "acc-001" exists and is active
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    And the response status should be "completed"
    And the seller should accept the artifact_webhook configuration for governance content delivery
    # v3.1: artifact_webhook enables governance content-adjacency validation
    # v3.1: same authentication structure as reporting_webhook (Bearer or HMAC-SHA256, both deprecated; RFC 9421 preferred)
    # --- v3.1: agency_estimate_number ---
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-agency-estimate-number-buy-level @v31 @agency-estimate-number @broadcast
  Scenario: v3.1 agency_estimate_number at media-buy level travels with the order
    Given the tenant is configured for auto-approval
    And a valid create_media_buy request with agency_estimate_number "AE-2026-Q1-12345"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    And the response status should be "completed"
    And the media buy should persist agency_estimate_number "AE-2026-Q1-12345"
    # v3.1: primary financial reference for broadcast buys; maxLength 100
    # v3.1: per-package agency_estimate_number MAY override the buy-level value
    # The value is named in the Then so the assertion compares the stored record against the
    # scenario's own literal. create-media-buy-request.json: "Travels with the order and
    # creative traffic identifiers through the transaction lifecycle"; neither
    # create-media-buy-response.json nor get-media-buys-response.json carries the buy-level
    # field, so the stored record is where the seller can be seen to have kept it.
    # --- v3.1 wave A: BUDGET_TOO_LOW error-details routing ---
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/properties/agency_estimate_number

  @T-UC-002-v31-error-budget-too-low-details @v31 @error-details @budget-too-low @post-f2 @post-f3
  Scenario: v3.1 BUDGET_TOO_LOW error carries minimum_budget and currency in details
    Given a valid create_media_buy request with total budget 50
    And the product minimum spend is 500 USD
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the response should indicate a correctable failure
    And the error code should be "BUDGET_TOO_LOW"
    And the error details should include minimum_budget 500
    And the error "details" object should include "currency" with value "USD"
    # v3.1: error-details/budget-too-low.json — recommended details for BUDGET_TOO_LOW
    # v3.1: recovery classification correctable — buyer adjusts and retries
    # LOCAL CORRECTION: the recovery line asserted a terminal failure, which contradicted the
    # comment above it and the pin: enums/error-code.json gives BUDGET_TOO_LOW "Recovery:
    # correctable (increase budget or check capabilities.media_buy.limits)".
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumDescriptions/BUDGET_TOO_LOW
    # --- v3.1 wave A: IDEMPOTENCY_CONFLICT error-details routing (idempotency revision mismatch) ---
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-error-conflict-details @v31 @error-details @conflict @idempotency-key @post-f2 @ext-w
  Scenario: v3.1 IDEMPOTENCY_CONFLICT on idempotency-key reuse with a divergent payload exposes only the code
    Given a media buy was already created for the same seller with idempotency_key "buy-2026-q1-conflict-001"
    And the new request payload diverges from the prior request payload
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the response should indicate a correctable failure
    And the error code should be "IDEMPOTENCY_CONFLICT"
    And the error should carry no details, issues or field pointer
    And the error should include "suggestion" field
    # v3.1: recovery correctable — resend the exact original payload, or mint a fresh idempotency_key for the changed order
    # Corrected to the pin. The resource_id / current_version Thens are deleted because the
    # pin forbids them: security.mdx "IDEMPOTENCY_CONFLICT response shape" — the error body
    # "MUST NOT include the cached response, the original payload, a canonical-form diff, or
    # any fingerprint derived from them", a `field` hint "Sellers MUST NOT emit", and "The
    # error body exposes only the code." The prior buy is a REAL first create under the key,
    # not a seeded "mb-789" row, so the conflict is computed against a hash production stored.
    # --- v3.1 wave A: POLICY_VIOLATION error-details routing ---
    # @source repo=adcp ref=v3.2.7 path=docs/building/by-layer/L1/security.mdx (L535-556: IDEMPOTENCY_CONFLICT response shape)
    # @source repo=adcp ref=v3.2.7 path=docs/building/by-layer/L1/security.mdx (L427: Idempotency rule 5)

  @T-UC-002-v31-error-policy-violation-details @v31 @error-details @policy-violation @post-f2 @post-f3 @ext-y
  Scenario: v3.1 POLICY_VIOLATION on a create_media_buy that violates the seller's policy
    Given a create_media_buy request that violates the seller's editorial policy
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the response should indicate a correctable failure
    And the error code should be "POLICY_VIOLATION"
    And the error should include "suggestion" field
    # v3.1: error-details/policy-violation.json — recommended details for POLICY_VIOLATION
    # Corrected to the pin. Recovery is correctable (error-code.json enumMetadata
    # POLICY_VIOLATION), not terminal. The policy_id / violated_rules Thens are deleted: the
    # details shape is "Recommended", every field optional, `origin` is "Optional so
    # undisclosed seller policies can return only the typed POLICY_VIOLATION code", and
    # policy_id is a shared-registry id a seller policy uses seller_policy_ref instead of.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json pointer=/enumMetadata/POLICY_VIOLATION
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/error-details/policy-violation.json

  # DELETED: the six billing-gate scenarios that stood here
  # (@T-UC-002-v31-error-billing-not-supported-{capability,account,unauth-omits-scope},
  # @T-UC-002-v31-error-billing-not-permitted-for-agent-{suggested,terminal},
  # @T-UC-002-v31-billing-eligible-proceeds). Each sent "a create_media_buy request with billing
  # value X", and create_media_buy has no `billing` field: create-media-buy-request.json
  # declares account, advertiser_industry, agency_estimate_number, artifact_webhook, ... and no
  # billing (its additionalProperties does not widen this seller's DTO). The pin puts the
  # billing gate on sync_accounts: sync_accounts.mdx "`billing` ... Who the seller invoices for
  # this buyer-storefront account relationship"; conformance.mdx `billing_gate_dispatch`:
  # "Two-gate dispatch on `sync_accounts.billing` rejection". That gate is graded in
  # BR-UC-011-manage-accounts.feature (@T-UC-011-ext-c-rejected, @T-UC-011-billing-agent-gate-*).
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/properties
  # @source repo=adcp ref=v3.2.7 path=docs/accounts/tasks/sync_accounts.mdx (L107: billing)
  # @source repo=adcp ref=v3.2.7 path=docs/building/verification/conformance.mdx (L182: billing_gate_dispatch)

  @T-UC-002-inv-214-8 @invariant @BR-RULE-214 @billing @v31 @open-question
  Scenario: INV-8 -- unauthorized invoice_recipient override rejects the media buy
    Given a create_media_buy request that carries an invoice_recipient overriding the account default billing entity
    And the account exists and is active
    But the invoice_recipient is not authorized for the account
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And no new media buy should have been created
    # BR-RULE-214 INV-8 (v3.1): entity-level gate orthogonal to capability/per-account/per-agent gates; error code is an open question
    # Corrected to the pin. The three Thens that stood here asserted no wire value ("should
    # validate", "rejected at the gate", "no dedicated code is defined"). The pin's obligation
    # is the rejection itself: create-media-buy-request.json invoice_recipient "The seller MUST
    # validate the invoice recipient is authorized for this account", and
    # provisioning-walkthrough.mdx "If the seller does not authorize `invoice_recipient` ...
    # the seller rejects the media buy". The pin names no code for it, so none is asserted.
    # --- v3.1 wave A: offering / offering-asset-group lifecycle ---
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/properties/invoice_recipient
    # @source repo=adcp ref=v3.2.7 path=docs/accounts/provisioning-walkthrough.mdx (L383)

  @T-UC-002-v31-offering-referenced-via-package-catalog @v31 @offering @offering-asset-group @catalog-driven @post-s3
  Scenario: v3.1 media buy with catalog-driven package references brand offering with typed asset groups
    Given a valid create_media_buy request
    And a package targets a catalog whose items are brand offerings
    And the referenced offering "summer-sale" has structured asset groups including "headlines" and "images_landscape"
    And the account "acc-001" exists and is active
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    And the response status should be "completed"
    And the response package should echo the offering catalog carrying "summer-sale" with asset groups "headlines" and "images_landscape"
    # v3.1: core/offering.json — promotable brand offering with assets[] of core/offering-asset-group.json
    # v3.1: asset_group_id values come from per-format offering_asset_constraints (discoverable via list_creative_formats)
    # CORRECTED: "bound to the offering's asset groups for creative assembly" names seller-internal
    # state with no wire surface. What the pin puts on the wire is the echo, core/package.json
    # catalogs: "Catalogs this package promotes. ... Echoed from the create_media_buy request."
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/package.json
    # --- v3.1 wave A: catchment in targeting.store_catchments ---

  @T-UC-002-v31-catchment-isochrone @v31 @catchment @store-catchments @targeting @post-s1
  Scenario: v3.1 targeting.store_catchments selects catchment zones of a synced store catalog -- refused by a seller with no store catalogs
    Given a valid create_media_buy request
    And the targeting includes a store_catchments entry for catalog "retail-locations" narrowed to catchment "drive"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "field" field with value "packages[0].targeting_overlay.store_catchments"
    # CORRECTED against the pin. A targeting.store_catchments entry does not carry a
    # catchment definition: core/targeting.json declares its items as {catalog_id (required),
    # store_ids, catchment_ids} -- "Target users within store catchment areas from a synced
    # store catalog". The travel_time / transport_mode / radius / geometry methods of
    # core/catchment.json live on core/store-item.json, i.e. on the catalog synced through
    # sync_catalogs, and the targeting entry selects them by catchment_ids.
    # This seller declares catalog_management false and ships no sync_catalogs, so no store
    # catalog can exist for the reference to resolve against. core/targeting.json: "A fresh
    # create/update request MUST be applied exactly or rejected"; the targeting reference:
    # "The seller must declare support for store catchment targeting in get_adcp_capabilities".
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/targeting.json

  @T-UC-002-v31-catchment-radius @v31 @catchment @store-catchments @targeting @post-s1
  Scenario: v3.1 targeting.store_catchments narrowed to specific stores of a synced store catalog -- refused by a seller with no store catalogs
    Given a valid create_media_buy request
    And the targeting includes a store_catchments entry for catalog "retail-locations" narrowed to store "store_nyc_001"
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "field" field with value "packages[0].targeting_overlay.store_catchments"
    # CORRECTED against the pin, for the reason the isochrone scenario above records: a
    # radius is a core/catchment.json method on a synced store item, not a member of a
    # targeting.store_catchments entry. store_ids is the entry's other narrowing field
    # ("Filter to specific stores within the catalog").
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/targeting.json

  @T-UC-002-v31-catchment-xor-violation @v31 @catchment @store-catchments @validation @post-f2
  Scenario: v3.1 a geo_proximity entry with both radius and geometry violates oneOf and is rejected
    Given a create_media_buy request
    And the targeting includes a geo_proximity entry providing BOTH radius and geometry
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "field" field with value "packages[0].targeting_overlay.geo_proximity[0]"
    # CORRECTED against the pin. The buyer-supplied one-method-per-area oneOf (travel_time +
    # transport_mode XOR radius XOR geometry) is the oneOf on core/targeting.json geo_proximity
    # items; a store_catchments entry declares none of the three. A oneOf violation is a schema violation, so the code is
    # INVALID_REQUEST ("violates schema constraints", enums/error-code.json), not
    # VALIDATION_ERROR.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/targeting.json
    # --- v3.1 wave A: price (catalog/proposal pricing surface) ---

  # DELETED: @T-UC-002-v31-price-shape-on-proposal-item. It seeded "a price {amount,
  # currency, period}" on a proposal's product allocation item, a field the pin does not
  # declare there: core/product-allocation.json lists product_id, allocation_percentage,
  # min_spend_target_percentage, max_spend_percentage, pacing, pricing_option_id, rationale,
  # sequence, tags, start_time, end_time, daypart_targets, forecast and ext, and a committed
  # allocation names its commercial terms by pricing_option_id ("Required when the containing
  # proposal is committed so create_media_buy executes the exact disclosed commercial
  # terms"). core/price.json is the catalog-item price ("Used across catalog item types for
  # product prices, nightly hotel rates, salary ranges, rental costs"), not a proposal field.
  # Its Thens graded only a successful create, so nothing of the price shape it was named
  # for was ever asserted.
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/product-allocation.json
  # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/price.json
    # --- v3.1 wave A: frequency_cap scope is POSITIONAL, not a property ---
    # CORRECTED against the pin, which binds the scope vocabulary to no wire field:
    #   * core/frequency-cap.json declares five properties -- suppress, suppress_minutes,
    #     max_impressions, per, window -- and no "scope".
    #   * Its own max_impressions description states the mechanism: "campaign applies across
    #     the owning field's full flight: the package flight FOR A TARGETING OVERLAY, or the
    #     MediaBuy flight FOR A ROOT CAP". So the level is decided by WHERE the cap hangs.
    #   * The two places are different fields of different types: TargetingOverlay[Input]
    #     .frequency_cap is core/frequency-cap.json, and CreateMediaBuyRequest.frequency_cap
    #     is core/media-buy-frequency-cap.json (that schema plus a required max_impressions
    #     and no suppress).
    #   * enums/frequency-cap-scope.json DOES exist, its sole member is "package", and it is
    #     referenced by no schema in the bundle except the registry index.
    # The two scenarios this replaces asserted a "frequency_cap.scope" property on the
    # overlay cap. One of them ("accepts the registered value") asked this seller to accept a
    # field the pin declares nowhere -- CLAUDE.md pattern #7's documented case: the DTO is the
    # accepted shape, and a field we should NOT accept means the scenario is wrong. The other
    # demanded an enum refusal naming a field, which cannot be produced for a key that is not
    # part of the accepted shape at all.
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-frequency-cap-scope-package @v31 @frequency-cap-scope @targeting @post-s1
  Scenario: v3.1 an overlay frequency_cap is package-scoped with no scope field
    Given a valid create_media_buy request
    And the package targeting_overlay carries a frequency_cap with no scope field
    And the account "acc-001" exists and is active
    And the tenant is configured for auto-approval
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the response should succeed
    And the package frequency_cap should be echoed and recorded on the package, not at the media buy root
    # v3.1: core/frequency-cap.json — the level follows the owning field, not a property
    # core/package.json targeting_overlay: "Sellers MUST echo an applied package frequency_cap
    # independently from any MediaBuy root cap"; the record is read too, since an echo of the
    # request alone passes a seller that stores nothing.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/package.json pointer=/properties/targeting_overlay
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

  @T-UC-002-v31-frequency-cap-scope-unregistered @v31 @frequency-cap-scope @targeting @validation @post-f2
  Scenario: v3.1 a frequency_cap scope property is not part of the accepted shape
    Given a create_media_buy request
    And the package targeting_overlay carries a frequency_cap with a scope property
    And the account "acc-001" exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "field" field with value "packages[0].targeting_overlay.frequency_cap.scope"
    # v3.1: an undeclared key inside frequency_cap is refused in dev and dropped in production
    # (critical pattern #7); cross-package and cross-buy scoping are not in this release
    # CORRECTED: the refusal of an undeclared key is a SCHEMA refusal, so the code is
    # INVALID_REQUEST ("Request is malformed, missing required fields, or violates schema
    # constraints"), not VALIDATION_ERROR ("violates business rules beyond schema
    # validation"), per enums/error-code.json. Same code as @T-UC-002-ext-f-managed.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/frequency-cap.json

  @T-UC-002-boundary-sandbox-response @boundary @sandbox @br-rule-209
  Scenario Outline: v3.1 sandbox response-semantics boundary validation - <boundary_point>
    Given a create_media_buy request whose account resolution matches "<boundary_point>"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # Corrected to the pin. sandbox.mdx: sellers "SHOULD include `sandbox: true` in success
    # responses when processing a sandbox account request", and "Sandbox errors are real
    # validation errors". The schema field only ever confirms sandbox mode ("When true, this
    # response contains simulated data"), so a production account is answered with sandbox
    # absent OR false; the two rows that demanded one spelling each ("omitted", "false") for
    # the same production account are one outcome. "explicit production" is the natural key
    # with sandbox false: sandbox.mdx "adding a sandbox boolean to { "account_id": "..." } is
    # invalid". The error branch of create-media-buy-response.json forbids `sandbox`
    # (`not: anyOf ... required: [sandbox]`).
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/advanced-topics/sandbox.mdx (L14, L236-238, L253)
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-response.json pointer=/oneOf/1/not

    Examples: Boundary values
      | boundary_point                                              | outcome                                |
      | sandbox: true in response (sandbox account)                 | success with sandbox echoed true       |
      | sandbox absent in response (production account)             | success with sandbox absent or false   |
      | sandbox: false in the natural key (explicit production)     | success with sandbox absent or false   |
      | sandbox account with invalid budget (real validation error) | error BUDGET_TOO_LOW with suggestion   |
      | sandbox: true on create_media_buy synchronous success shape | success with sandbox echoed true       |
      | sandbox present on create_media_buy terminal-failure (errors) shape | error BUDGET_TOO_LOW without a sandbox field |

  # DELETED: @T-UC-002-boundary-billing-eligibility. Five of its six rows sent a `billing`
  # value on create_media_buy, which the request schema does not declare (see the note where
  # the six billing-gate scenarios stood). The sixth, an unauthorized invoice_recipient, is
  # the same obligation as @T-UC-002-inv-214-8 and is graded there.

  @T-UC-002-boundary-plan-id-governance @boundary @governance @plan-id @br-rule-212
  Scenario Outline: v3.1 governance plan_id boundary validation - <boundary_point>
    Given a create_media_buy request and account governance state matching "<boundary_point>"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # CORRECTED against the pin. The three "governance agents configured" rows keyed the
    # seller's governance gate on plan_id: absent -> INVALID_REQUEST, unresolvable ->
    # PLAN_NOT_FOUND, resolvable -> proceeds. At the pin the seller never reads plan_id.
    # create-media-buy-request.json plan_id: "DEPRECATED on seller-facing requests. New
    # buyers send the approved governance_context on the protocol envelope; the seller
    # forwards that opaque context and does not need the plan identifier."
    # governance/campaign/specification.mdx: "The service does not need the buyer's
    # `plan_id` ... A request without a token, with a token that fails verification, or with
    # a token issued for a different audience or phase MUST be rejected with
    # `PERMISSION_DENIED`." PLAN_NOT_FOUND is the governance agent's answer to sync_plans /
    # check_governance, not the seller's. The governed-account gate (governance_context
    # verification and the seller's check_governance call) is graded by BR-UC-030, so those
    # rows are deleted here rather than restated.
    # The two rows kept are the pin's: "When `governance_agents` is absent, the seller
    # processes media buy requests normally", and "Sellers MUST NOT require governance
    # checks as a prerequisite for all accounts" -- with or without the deprecated plan_id.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/properties/plan_id
    # @source repo=adcp ref=v3.2.7 path=docs/governance/campaign/specification.mdx

    Examples: Boundary values
      | boundary_point                                                  | outcome                               |
      | no governance agents + plan_id absent                           | proceeds, governance skipped          |
      | no governance agents + plan_id present                          | proceeds, governance skipped          |

  @T-UC-002-boundary-io-acceptance @boundary @io-acceptance @br-rule-213
  Scenario Outline: v3.1 io_acceptance gate boundary validation - <boundary_point>
    Given a proposal-based create_media_buy request matching "<boundary_point>"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json
    # CORRECTED against the pin: the three "io_acceptance present but <member> missing" rows
    # are deleted. io_acceptance declares "required": ["io_id", "accepted_at", "signatory"] on
    # the object itself, so that refusal is a schema refusal that does not depend on the
    # proposal's requires_signature, and @T-UC-002-v31-io-acceptance-incomplete grades exactly
    # it, per member, with the error located at io_acceptance.<member>. The rows left here are
    # the ones whose outcome does depend on the committed proposal's insertion_order.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/properties/io_acceptance/required

    Examples: Boundary values
      | boundary_point                                                                 | outcome                               |
      | requires_signature false, io_acceptance absent (no gate)                       | proceeds, no IO gate                  |
      | requires_signature true, io_acceptance present with all required members       | proceeds, gate satisfied              |
      | requires_signature true, io_acceptance present with signature_id also supplied | proceeds, gate satisfied              |
      | requires_signature false, io_acceptance present (no-op)                        | proceeds, io_acceptance ignored       |
      | requires_signature true, io_acceptance absent (gate blocks)                    | error IO_REQUIRED with suggestion     |

  @T-UC-002-boundary-creative-asset-discriminator @boundary @creative-asset @br-rule-015
  Scenario Outline: v3.1 creative asset discriminator boundary validation - <boundary_point>
    Given a create_media_buy request with an inline creative matching "<boundary_point>"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # @source repo=adcp ref=v3.2.3 commit=467fd93d7 path=static/schemas/source/media-buy/create-media-buy-request.json

    Examples: Boundary values
      | boundary_point                                          | outcome                               |
      | asset slot value with missing asset_type discriminator  | error INVALID_REQUEST with suggestion |
      | asset slot value with unknown asset_type value          | error INVALID_REQUEST with suggestion |

  @T-UC-002-boundary-targeting-collection-list @boundary @targeting-overlay @collection-list @br-rule-014
  Scenario Outline: v3.1 targeting_overlay collection-list boundary validation - <boundary_point>
    Given a create_media_buy request with package targeting matching "<boundary_point>"
    And the account exists and is active
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy spec
    And the result should be <outcome>
    # Corrected to the pin. The rows said "targeting validation passes", but both dimensions
    # say "Seller must declare support in get_adcp_capabilities", and this seller declares
    # neither (no media_buy.execution.targeting.collection_list / collection_list_exclude, no
    # Product.overlay_support). core/targeting.json: "A fresh create/update request MUST be
    # applied exactly or rejected", so an undeclared dimension is refused UNSUPPORTED_FEATURE,
    # never accepted and dropped.
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/core/targeting.json pointer=/properties/collection_list
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/protocol/get-adcp-capabilities-response.json pointer=/properties/media_buy/properties/execution/properties/targeting/properties/collection_list

    Examples: Boundary values
      | boundary_point                                           | outcome                                                                                            |
      | collection_list with valid agent_url and list_id         | error UNSUPPORTED_FEATURE on packages[0].targeting_overlay.collection_list with suggestion         |
      | collection_list_exclude with valid agent_url and list_id | error UNSUPPORTED_FEATURE on packages[0].targeting_overlay.collection_list_exclude with suggestion |
      | both collection_list and collection_list_exclude set     | error UNSUPPORTED_FEATURE with suggestion                                                          |

  @T-UC-002-storyboard-async-submitted-envelope-task-id-roundtrip @schema-v3.1 @v3-1 @submitted-envelope @async @task-id-roundtrip
  Scenario: Async submitted envelope -- task_id matches deterministic value registered via comply_test_controller
    Given a comply_test_controller directive registered force_create_media_buy_arm with branch "submitted" and task_id "task_async_signed_io_q2"
    And the directive is keyed to the caller's authenticated sandbox account
    When the Buyer Agent sends create_media_buy under the registered sandbox account
    Then the response is compliant with the create_media_buy submitted spec
    And the response should carry status "submitted"
    And the response should carry task_id "task_async_signed_io_q2"
    And the response should NOT carry media_buy_id on the submitted envelope
    And the response should NOT carry packages on the submitted envelope
    And the task_id on the response should match the value registered by the controller directive
    # create_media_buy_async storyboard: a controller directive
    # (force_create_media_buy_arm with task_id=X) registers a deterministic task_id
    # against the caller's sandbox account. The subsequent create_media_buy MUST
    # honor that directive and return status=submitted with task_id=X. Sellers
    # that fabricate a fresh task_id break the buyer's polling contract -- buyers
    # cannot tell whether the buy is queued or confirmed.
    # create_media_buy_async: deterministic task_id roundtrips through registered controller directive
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/create_media_buy_async.yaml phase=submitted_arm_response step=create_media_buy_submitted

  @T-UC-002-storyboard-governance-approved @schema-v3.1 @v3-1 @governance @governance-decision
  Scenario: Governance approved -- seller verifies the approved governance_context and commits the buy
    Given the account has governance_agents configured
    And the buyer's governance agent has returned verdict "approved" for the exact proposed buy
    And the buyer attaches the governance_context from that verdict to the create_media_buy request
    And the buyer sends create_media_buy without inline creatives
    When the Buyer Agent sends create_media_buy with the governance_context
    Then the response is compliant with the create_media_buy success spec
    And the wire media_buy_status should be "pending_creatives"
    And the response should carry the media_buy_id
    And a get_media_buys readback of the buy should return the authorized total_budget and currency
    # governance_approved storyboard: the buyer's intent check returns verdict
    # "approved" with a signed governance_context; the buyer sends that token on
    # create_media_buy; the seller verifies it before committing, and the commit is
    # proven by reading the buy back (media_buy_id, total_budget, currency).
    # Corrected to the pin at 3.2.7. The scenario used to send a "governance_decision
    # payload" with decision "APPROVED" and require the seller to echo it. Neither is
    # the pin's: the request field is governance_context (an opaque signed token), the
    # verdict values are lowercase ("approved" / "denied" / "conditions"), and the
    # storyboard says the response "need not echo the authorization context" and that
    # it "never treats a seller-to-buyer governance_context echo as evidence of
    # authorization" -- the readback is the evidence it grades instead.
    # The scenario also used to assert status "active" or "pending_start". The
    # storyboard's create_media_buy sample_request carries packages with only
    # product_id, budget and pricing_option_id -- no creatives -- and the pin names that
    # buy pending_creatives ("approved by the seller and has no creatives assigned").
    # The lifecycle value is the wire media_buy_status; the wire status is the envelope
    # task status. The Given now states the creative-less shape the storyboard sends.
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/governance_approved.yaml phase=buy_approved step=create_media_buy
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/governance_approved.yaml phase=committed_state_and_evidence step=get_media_buys_readback
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/media-buy-status.json pointer=/enumDescriptions/pending_creatives

  @T-UC-002-storyboard-governance-with-conditions @schema-v3.1 @v3-1 @governance @governance-decision @conditions
  Scenario: Governance conditions -- the buyer resolves them before the seller commits, and the seller commits only on an approved execution check
    Given the account has governance_agents configured
    And the buyer's governance agent returned verdict "conditions" and then verdict "approved" for the adjusted proposed buy
    And the buyer attaches the governance_context from that verdict to the create_media_buy request
    When the Buyer Agent sends create_media_buy with the governance_context
    Then the response is compliant with the create_media_buy success spec
    And the response should carry the media_buy_id
    And the seller's execution check should have returned verdict "approved" before the buy was committed
    And a get_media_buys readback of the buy should return the authorized total_budget and currency
    # governance_conditions storyboard: "conditions" is a non-authorizing INTENT
    # counterproposal. The buyer applies the condition and repeats the intent check;
    # only the later "approved" verdict returns the governance_context it sends to the
    # seller. The seller's own execution check is binary ("approved" or "denied"; a
    # "conditions" execution verdict is invalid and never authorizes a commit), and the
    # storyboard observes it in the governance audit log, then reads the buy back.
    # Corrected to the pin at 3.2.7. The scenario used to have the seller receive an
    # "APPROVED_WITH_CONDITIONS" governance_decision and echo the decision and its
    # conditions array on the response. The pin has no such verdict or request field,
    # the seller never receives a conditions verdict, and "The create response need not
    # echo governance conditions or governance_context".
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/governance_conditions.yaml phase=governed_execution step=create_media_buy_after_approval
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/governance_conditions.yaml phase=committed_state_and_outcome step=get_plan_audit_logs_execution

  @T-UC-002-storyboard-governance-denied @schema-v3.1 @v3-1 @governance @governance-decision @rejection
  Scenario: Governance denied -- the seller's execution check is denied, so it rejects the buy with GOVERNANCE_DENIED and propagates the findings
    Given the account has governance_agents configured
    And the buyer's governance agent has returned verdict "approved" for the exact proposed buy
    And the buyer attaches the governance_context from that verdict to the create_media_buy request
    And the plan owner then reduces the plan budget below the proposed buy
    When the Buyer Agent sends create_media_buy with the governance_context
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "GOVERNANCE_DENIED"
    And the error should carry the findings of the seller's denied execution check
    And no media buy record should be persisted
    # governance_denied storyboard: the buyer's intent check approves a $25K buy, then the
    # plan owner amends the plan to $10K. The seller receives a valid in-date token and
    # must still run its own execution check, which the governance agent denies against
    # the amended plan. The seller does not commit the buy and returns GOVERNANCE_DENIED
    # in errors[] and adcp_error (create_media_buy has no rejection arm), propagating the
    # governance findings ("sellers SHOULD propagate governance findings verbatim").
    # Corrected to the pin at 3.2.7. The scenario used to have the buyer attach a DENIED
    # "governance_decision payload". At the pin a denied intent yields no
    # governance_context to attach, and a request without one is PERMISSION_DENIED
    # "without consulting governance" -- so GOVERNANCE_DENIED is reachable only through
    # the seller's own execution check, which is what this scenario now drives.
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/governance_denied.yaml phase=buy_denied step=create_media_buy_denied
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/enums/error-code.json (enumDescriptions.GOVERNANCE_DENIED)

  # STORYBOARD CLAIM WITHDRAWN at the pin — RESTORABLE BY DECLARING.
    # The pinned governance_denied_recovery storyboard is gated by `requires_capability
    # adcp.governance_enforcement.tasks` containing {task: create_media_buy, modes:
    # [signed_context, online_execution_check]} ("Other sellers grade not_applicable").
    # This seller declares no governance_enforcement, so the claim that an AdCP storyboard
    # grades this scenario is FALSE at the pin, and feeding a guard a false claim is the
    # guard grading a fiction. Declaring it restores the claim; declaring a capability is a
    # get_adcp_capabilities behaviour change and this repo declares a posture only where
    # the implementation backs it.
    # Every Given, When and Then below still runs on every transport.
  @T-UC-002-storyboard-governance-denied-recovery @schema-v3.1 @v3-1 @governance @recovery
  Scenario: Governance denied recovery -- buyer shrinks the buy to within plan limits and retries successfully
    Given a previous create_media_buy attempt failed with error code "GOVERNANCE_DENIED"
    And the buyer reduces the proposed buy to within the amended governance plan
    And the buyer obtains a fresh approved intent governance_context for the corrected buy
    When the Buyer Agent sends the corrected create_media_buy carrying that governance_context and a fresh idempotency_key
    Then the response is compliant with the create_media_buy success spec
    And the response should carry the media_buy_id
    # governance_denied_recovery storyboard: the prior attempt carried a valid intent
    # token and the seller's own execution check denied it (GOVERNANCE_DENIED). The buyer
    # shrinks the buy to fit the amended plan, obtains a fresh intent approval, and
    # retries with a fresh idempotency_key; the seller accepts it and returns a
    # media_buy_id. "Every create_media_buy carries an approved intent token. A request
    # without governance_context is rejected with PERMISSION_DENIED before any governance
    # consultation, so it cannot exercise denial or recovery."
    # LOCAL CORRECTION: the request carried a "governance_decision" payload, which
    # create-media-buy-request.json does not declare; the approval travels as the opaque
    # governance_context. The Then `the response should carry status "active" or
    # "pending_start"` is removed: the top-level status is the task status ("completed" on
    # synchronous success), and the storyboard grades only media_buy_id on the retry.
    # @source repo=adcp ref=v3.2.7 path=dist/compliance/3.2.7/protocols/media-buy/scenarios/governance_denied_recovery.yaml phase=buy_retried step=create_media_buy_retry
    # @source repo=adcp ref=v3.2.7 path=static/schemas/source/media-buy/create-media-buy-request.json pointer=/properties/governance_context
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/task-reference/create_media_buy.mdx (L1523)

  # STORYBOARD CLAIM WITHDRAWN at the AdCP 3.2.1 pin — RESTORABLE BY DECLARING.
    # Gated upstream by `requires_capability media_buy.execution.targeting.property_list`, which this seller does not declare, so the claim that an AdCP storyboard grades this scenario is FALSE at 3.2.1
    # and feeding a guard a false claim is the guard grading a fiction. Declaring it
    # restores the claim and the coverage with it; that decision is salesagent-o1wxm,
    # because declaring a capability is a get_adcp_capabilities behaviour change and this
    # repo declares a posture only where the implementation backs it.
    # The `@storyboard-v3.1` TAG is gone and nothing else is: every Given, When and Then
    # below still runs on every transport.
  @T-UC-002-storyboard-inventory-list-no-match @v3-1 @inventory-list @no-match
  Scenario: Inventory list references that resolve to zero inventory -- zero-forecast or informative error, never silent success
    Given the buyer references a property_list whose entries do not match any seller inventory
    And the buyer references a collection_list whose entries do not match any seller inventory
    When the Buyer Agent sends create_media_buy with the no-match list references in targeting_overlay
    Then the response is compliant with the create_media_buy spec
    And the response should NOT be a silent success with normal forecast numbers
    And one of the following two outcomes should be observed:
    | outcome              | required behavior                                                                       |
    | zero_forecast_accept | buy accepted with packages reporting zero deliverable inventory and a mismatch message  |
    | informative_error    | error code "PRODUCT_UNAVAILABLE" or "INVALID_REQUEST" with findings identifying lists |
    # inventory_list_no_match storyboard: the buyer references a PropertyListReference
    # and CollectionListReference that resolve to nothing in the seller's catalog.
    # The seller MUST either (a) accept the buy with zero-forecast reporting OR
    # (b) reject with PRODUCT_UNAVAILABLE/INVALID_REQUEST carrying findings
    # identifying which list matched nothing. Silently-successful buys with normal
    # forecast numbers, crashes, or non-AdCP error shapes are compliance failures.
    # inventory_list_no_match: empty intersection MUST be surfaced truthfully
    # @source repo=adcp ref=v3.2.3 path=static/compliance/source/protocols/media-buy/scenarios/inventory_list_no_match.yaml phase=no_match_attempt step=create_buy_no_match

  # STORYBOARD CLAIM WITHDRAWN at the AdCP 3.2.1 pin — RESTORABLE BY DECLARING.
    # Gated upstream by `requires_capability media_buy.execution.targeting.property_list`, which this seller does not declare, so the claim that an AdCP storyboard grades this scenario is FALSE at 3.2.1
    # and feeding a guard a false claim is the guard grading a fiction. Declaring it
    # restores the claim and the coverage with it; that decision is salesagent-o1wxm,
    # because declaring a capability is a get_adcp_capabilities behaviour change and this
    # repo declares a posture only where the implementation backs it.
    # The `@storyboard-v3.1` TAG is gone and nothing else is: every Given, When and Then
    # below still runs on every transport.
  @T-UC-002-storyboard-inventory-list-targeting-parity @v3-1 @inventory-list @property-list @collection-list
  Scenario: PropertyListReference and CollectionListReference honored in package targeting on create_media_buy
    Given the buyer holds a property_list (agent_url, list_id) that matches seller inventory
    And the buyer holds a collection_list (agent_url, list_id) that matches seller inventory
    When the Buyer Agent sends create_media_buy with property_list and collection_list in package targeting_overlay
    Then the response is compliant with the create_media_buy success spec
    And the response should carry the media_buy_id
    And the persisted package targeting should reflect the property_list and collection_list references
    # inventory_list_targeting storyboard: the seller MUST accept
    # PropertyListReference (agent_url+list_id) and CollectionListReference
    # (agent_url+list_id) in package targeting_overlay on create_media_buy. The
    # storyboard also asserts parity with update_media_buy (covered in UC-003)
    # so the buyer can manage list-bound buys post-creation.
    # inventory_list_targeting: list-based targeting honored on create
    # @source repo=adcp ref=v3.2.3 path=static/compliance/source/protocols/media-buy/scenarios/inventory_list_targeting.yaml phase=create_with_both_lists step=create_buy_with_lists

  @T-UC-002-storyboard-measurement-terms-rejected @storyboard-v3.1 @v3-1 @measurement-terms @rejection
  Scenario: Measurement terms unworkable for the seller -- TERMS_REJECTED with terms identified in error details
    Given the buyer attaches measurement_terms that the seller will not accept
    When the Buyer Agent sends create_media_buy with the unworkable measurement_terms
    Then the response is compliant with the create_media_buy error spec
    And the operation should fail
    And the error code should be "TERMS_REJECTED"
    And the error details should identify which measurement_terms are unworkable
    # measurement_terms_rejected storyboard: the buyer proposes measurement_terms
    # the seller will not accept. Seller MUST reject with TERMS_REJECTED, identifying
    # the unworkable terms so the buyer can retry with seller-compatible terms.
    # Distinct from the existing UC-002 TERMS_REJECTED scenario which targets
    # committed_metrics references not in product available_metrics; this storyboard
    # tests the measurement_terms field specifically.
    # measurement_terms_rejected: seller refuses unworkable measurement_terms with structured error
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/measurement_terms_rejected.yaml phase=reject_terms step=create_media_buy_aggressive_terms

  @T-UC-002-storyboard-pending-creatives-state-transition @schema-v3.1 @v3-1 @lifecycle @pending-creatives @pending-start
  Scenario: Media buy created without creatives sits in pending_creatives until sync_creatives completes, then transitions to pending_start
    Given the tenant is configured for auto-approval
    And the tenant creative approval mode is configured as auto-approve
    And the buyer sends create_media_buy without inline creatives
    When the Buyer Agent sends the create_media_buy request
    Then the response is compliant with the create_media_buy success spec
    And the wire status should be "completed"
    And the wire media_buy_status should be "pending_creatives"
    When the buyer syncs a creative and assigns it to the package through update_media_buy
    Then the buy's media_buy_status should transition to "pending_start"
    # pending_creatives_to_start storyboard: when a buy is created without inline
    # creatives, the seller persists it in pending_creatives. Once the buyer calls
    # sync_creatives and all required creatives reach an acceptable status, the
    # buy transitions to pending_start. The state transition is observable via
    # get_media_buys (covered in UC-019) but the anchor is the buy state on UC-002.
    # pending_creatives_to_start: creative sync unblocks pending_creatives -> pending_start
    # CORRECTED against the storyboard. create_buy_no_creatives grades TWO fields:
    # media_buy_status == "pending_creatives" and the envelope status == "completed"
    # ("Envelope task-status is completed on synchronous success"); "status pending_creatives"
    # conflated them. The unblock is sync_creatives AND then update_media_buy with
    # creative_assignments (step assign_creative_to_package), not sync_creatives alone. The
    # storyboard gates on media_buy.creative_approval_mode == auto_approve, which is the
    # tenant's creative approval mode (the creative Given); the media-buy auto-approval Given
    # keeps the create synchronous. The buyer's one update carries the creative inline, which
    # package-update.json `creatives` defines as entering the library and assigning to the
    # package. The flight is in the future, so the transition lands on pending_start.
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/pending_creatives_to_start.yaml phase=create_without_creatives step=create_buy_no_creatives
    # @source repo=adcp ref=v3.2.7 path=static/compliance/source/protocols/media-buy/scenarios/pending_creatives_to_start.yaml phase=supply_creatives step=assign_creative_to_package
    # @source repo=adcp ref=v3.2.7 path=docs/media-buy/specification.mdx section="Media Buy State Transitions"
