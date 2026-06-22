# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

Feature: BR-UC-023 Sync Product Catalogs
  As a Buyer
  I want to manage product catalog feeds on a seller account
  So that I can upload, update, discover, and delete catalogs for catalog-based advertising campaigns

  # Postconditions verified:
  #   POST-S1: Buyer has synced catalogs and received per-catalog action results (created, updated, unchanged, failed, deleted)
  #   POST-S2: Buyer can discover all catalogs on an account without modification (discovery-only mode)
  #   POST-S3: Buyer knows the platform-assigned ID, item counts, and item review status for each catalog
  #   POST-S4: Buyer knows about per-catalog warnings, errors, and item-level issues
  #   POST-S5: Buyer has previewed what changes would be made without applying them (dry-run mode)
  #   POST-S6: Buyer has purged buyer-managed catalogs not in the request when delete_missing is true
  #   POST-S7: Application context from the request is echoed unchanged in the response
  #   POST-S8: For async operations, Buyer receives progress updates and knows when input is required
  #   POST-S9: For queued operations, Buyer receives a submitted task envelope (status=submitted, task_id) resolvable via tasks/get or webhook
  #   POST-S10: A retried sync with the same idempotency_key does not double-apply changes or re-fire side effects (at-most-once)
  #   POST-F1: System state is unchanged on complete operation failure
  #   POST-F2: Buyer knows what failed and the specific error code with recovery classification
  #   POST-F3: Application context is still echoed when possible
  #   POST-F4: Individual catalog failures do not prevent other catalogs from being processed (partial success)
  #
  # Rules: BR-RULE-043 (context echo), BR-RULE-132 (capability gate), BR-RULE-172 (upsert semantics),
  #   BR-RULE-173 (catalog validation), BR-RULE-174 (delete missing), BR-RULE-175 (feed management),
  #   BR-RULE-176 (selectors/attribution), BR-RULE-177 (response structure), BR-RULE-178 (async lifecycle),
  #   BR-RULE-211 (idempotency at-most-once / replay)
  # Extensions: A (discovery), B (dry run), C (delete missing), D (ACCOUNT_NOT_FOUND),
  #   E (INVALID_REQUEST), F (UNSUPPORTED_FEATURE), G (AUTH_REQUIRED), H (RATE_LIMITED),
  #   I (SERVICE_UNAVAILABLE), J (async input required), K (submitted async task), L (idempotent replay)
  # Error codes: AUTH_REQUIRED, ACCOUNT_NOT_FOUND, INVALID_REQUEST, UNSUPPORTED_FEATURE,
  #   RATE_LIMITED, SERVICE_UNAVAILABLE

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Buyer is authenticated with a valid principal_id
    And the seller has declared catalog_management capability as true
    And the account reference resolves to a valid account


  @T-UC-023-main @main-flow @post-s1 @post-s3 @post-s7
  Scenario Outline: Sync catalogs via <transport> -- new catalog created with upsert semantics
    Given the account has no catalog with catalog_id "feed-001"
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends a sync_catalogs request via <transport> with catalogs:
    | catalog_id | type    | url                            | feed_format           |
    | feed-001   | product | https://feeds.example.com/prod | google_merchant_center |
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "feed-001" has action "created"
    And the catalog result for "feed-001" includes a platform_id
    And the catalog result for "feed-001" includes item_count
    And the request context is echoed in the response
    # POST-S1: Per-catalog action report (created)
    # POST-S3: Platform-assigned ID and item counts
    # POST-S7: Application context echoed unchanged
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-023-main-update @main-flow @post-s1 @post-s3 @post-s4
  Scenario: Sync catalogs -- existing catalog updated with changes list
    Given the account has a catalog with catalog_id "feed-001" and name "Old Name"
    When the Buyer Agent sends a sync_catalogs request with catalogs:
    | catalog_id | type    | name     |
    | feed-001   | product | New Name |
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "feed-001" has action "updated"
    And the catalog result for "feed-001" has changes including "name"
    # POST-S1: Per-catalog action report (updated)
    # POST-S4: Changes list shows what was modified
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-main-mixed @main-flow @post-s1 @post-s3
  Scenario: Sync catalogs -- mixed upsert (create and update in one request)
    Given the account has a catalog with catalog_id "existing-feed" and type "product"
    And the account has no catalog with catalog_id "new-feed"
    When the Buyer Agent sends a sync_catalogs request with catalogs:
    | catalog_id    | type    |
    | existing-feed | product |
    | new-feed      | store   |
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "existing-feed" has action "updated" or "unchanged"
    And the catalog result for "new-feed" has action "created"
    # POST-S1: Per-catalog action reports for both catalogs
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-main-item-review @main-flow @post-s3 @post-s4
  Scenario: Sync catalogs -- platform performs item-level review
    Given the account has no catalog with catalog_id "reviewed-feed"
    When the Buyer Agent sends a sync_catalogs request with a product catalog "reviewed-feed" containing 100 inline items
    Then the response is a SyncCatalogsSuccess
    And the catalog result for "reviewed-feed" includes item_count of 100
    And the catalog result for "reviewed-feed" includes items_approved count
    And the catalog result for "reviewed-feed" includes items_pending count
    And the catalog result for "reviewed-feed" includes items_rejected count
    And the catalog result for "reviewed-feed" may include item_issues array
    # POST-S3: Platform-assigned ID, item counts, review status
    # POST-S4: Item-level issues reported when present

  @T-UC-023-main-partial @main-flow @post-s1 @post-f4
  Scenario: Sync catalogs -- partial success with lenient validation mode
    Given the account has no existing catalogs
    When the Buyer Agent sends a sync_catalogs request with validation_mode "lenient" and catalogs:
    | catalog_id | type    |
    | good-feed  | product |
    | bad-feed   | INVALID |
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "good-feed" has action "created"
    And the catalog result for "bad-feed" has action "failed"
    And the catalog result for "bad-feed" includes per-catalog errors
    # POST-S1: Per-catalog action results
    # POST-F4: Individual failure does not prevent other catalogs from processing
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-main-scoped @main-flow @post-s1
  Scenario: Sync catalogs -- catalog_ids filter limits sync scope
    Given the account has catalogs "feed-A", "feed-B", and "feed-C"
    When the Buyer Agent sends a sync_catalogs request with catalog_ids ["feed-A"] and catalogs:
    | catalog_id | type    | name         |
    | feed-A     | product | Updated Feed |
    Then the response includes result for "feed-A" only
    And catalogs "feed-B" and "feed-C" are unaffected
    # BR-RULE-172 INV-7: catalog_ids scopes sync to specified IDs

  @T-UC-023-ext-a @extension @ext-a @happy-path @post-s2 @post-s7
  Scenario Outline: Discovery-only mode via <transport> -- list all catalogs on account
    Given the account has 3 synced catalogs with various types
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends a sync_catalogs request via <transport> with no catalogs array
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the response contains 3 catalog results
    And each catalog result has action "unchanged"
    And each catalog result includes catalog_id and platform_id
    And the request context is echoed in the response
    # POST-S2: Buyer discovers all catalogs without modification
    # POST-S7: Application context echoed unchanged

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-023-ext-a-empty @extension @ext-a @post-s2 @boundary
  Scenario: Discovery-only mode -- empty account returns empty catalogs array
    Given the account has 0 catalogs
    When the Buyer Agent sends a sync_catalogs request with no catalogs array
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalogs array is empty
    # BR-RULE-172 INV-3: catalogs omitted -> all account catalogs returned (empty)

  @T-UC-023-ext-b @extension @ext-b @happy-path @post-s5 @post-f1 @post-s7
  Scenario: Dry run mode -- preview sync changes without applying
    Given the account has a catalog with catalog_id "existing-feed"
    And the account has no catalog with catalog_id "new-feed"
    When the Buyer Agent sends a sync_catalogs request with dry_run true and catalogs:
    | catalog_id    | type    |
    | existing-feed | product |
    | new-feed      | store   |
    Then the response is a SyncCatalogsSuccess with dry_run true
    And the catalog result for "existing-feed" shows projected action "updated" or "unchanged"
    And the catalog result for "new-feed" shows projected action "created"
    And no state changes are applied to the account
    And the request context is echoed in the response
    # POST-S5: Buyer previews changes without applying
    # POST-F1: System state unchanged (dry-run)
    # POST-S7: Application context echoed
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-ext-b-validation @extension @ext-b @happy-path @post-s5
  Scenario: Dry run mode -- validation errors reported without state change
    When the Buyer Agent sends a sync_catalogs request with dry_run true and catalogs:
    | catalog_id | type    | url                       | items            |
    | bad-feed   | product | https://example.com/feed  | [{"id": "item1"}] |
    Then the response shows "bad-feed" with projected validation errors
    And no state changes are applied to the account
    # POST-S5: Validation errors visible in dry run preview

  @T-UC-023-ext-c @extension @ext-c @happy-path @post-s1 @post-s6 @post-s7
  Scenario: Delete missing -- purge buyer-managed catalogs not in request
    Given the account has buyer-managed catalogs "keep-feed" and "remove-feed"
    And the account has seller-managed catalog "seller-feed"
    When the Buyer Agent sends a sync_catalogs request with delete_missing true and catalogs:
    | catalog_id | type    |
    | keep-feed  | product |
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "keep-feed" has action "updated" or "unchanged"
    And the catalog result for "remove-feed" has action "deleted"
    And the seller-managed catalog "seller-feed" is not affected
    And the request context is echoed in the response
    # POST-S1: Per-catalog action reports include deleted catalogs
    # POST-S6: Buyer-managed catalogs not in request are purged
    # POST-S7: Context echoed
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-ext-c-seller-safe @extension @ext-c @invariant @BR-RULE-174
  Scenario: Delete missing -- seller-managed catalogs are never deleted
    Given the account has only seller-managed catalogs "seller-A" and "seller-B"
    And the account has no buyer-managed catalogs
    When the Buyer Agent sends a sync_catalogs request with delete_missing true and catalogs:
    | catalog_id  | type    |
    | new-catalog | product |
    Then the catalog result for "new-catalog" has action "created"
    And seller-managed catalogs "seller-A" and "seller-B" remain unchanged
    # BR-RULE-174 INV-3: Seller-managed catalogs are never deleted
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-ext-c-false @extension @ext-c @invariant @BR-RULE-174
  Scenario: Delete missing false -- no catalogs deleted
    Given the account has buyer-managed catalogs "feed-A" and "feed-B"
    When the Buyer Agent sends a sync_catalogs request with delete_missing false and catalogs:
    | catalog_id | type    |
    | feed-A     | product |
    Then the catalog result for "feed-A" has action "updated" or "unchanged"
    And catalog "feed-B" remains on the account (not deleted)
    # BR-RULE-174 INV-4: delete_missing=false means no deletions

  @T-UC-023-ext-d @extension @ext-d @error @post-f1 @post-f2 @post-f3
  Scenario: Account not found -- account reference cannot be resolved
    Given the account reference "nonexistent_acct" does not match any account
    When the Buyer Agent sends a sync_catalogs request with account_id "nonexistent_acct"
    Then the operation should fail
    And the error code should be "ACCOUNT_NOT_FOUND"
    And the error recovery should be "terminal"
    And the error should include "suggestion" field
    And the suggestion should contain "verify account reference"
    And the request context is echoed in the response
    # POST-F1: System state unchanged
    # POST-F2: Error code ACCOUNT_NOT_FOUND with terminal recovery
    # POST-F3: Context echoed

  @T-UC-023-ext-d-brand @extension @ext-d @error @post-f2
  Scenario: Account not found -- brand+operator natural key does not match
    Given no account matches brand "UnknownBrand" and operator "UnknownOp"
    When the Buyer Agent sends a sync_catalogs request with brand "UnknownBrand" and operator "UnknownOp"
    Then the operation should fail
    And the error code should be "ACCOUNT_NOT_FOUND"
    And the error recovery should be "terminal"
    And the error should include "suggestion" field
    And the suggestion should contain "account"
    # POST-F2: Account not found via natural key

  @T-UC-023-ext-e-catalogs-overflow @extension @ext-e @error @post-f1 @post-f2 @post-f3
  Scenario: Invalid request -- catalogs array exceeds maximum 50 items
    When the Buyer Agent sends a sync_catalogs request with 51 catalogs
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "catalogs"
    And the request context is echoed in the response
    # POST-F1: System state unchanged
    # POST-F2: Error identifies catalogs array size constraint
    # POST-F3: Context echoed

  @T-UC-023-ext-e-catalogs-empty @extension @ext-e @error @post-f2
  Scenario: Invalid request -- catalogs array is empty
    When the Buyer Agent sends a sync_catalogs request with an empty catalogs array
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "catalogs"
    # POST-F2: catalogs minItems=1 constraint violated

  @T-UC-023-ext-e-delete-no-catalogs @extension @ext-e @error @post-f2
  Scenario: Invalid request -- delete_missing true without catalogs array
    When the Buyer Agent sends a sync_catalogs request with delete_missing true and no catalogs array
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "catalogs"
    # POST-F2: Schema conditional violation -- delete_missing requires catalogs
    # BR-RULE-174 INV-2

  @T-UC-023-ext-e-url-items-both @extension @ext-e @error @post-f2
  Scenario: Invalid request -- catalog has both url and items (mutually exclusive)
    When the Buyer Agent sends a sync_catalogs request with catalogs:
    | catalog_id | type    | url                      | items              |
    | bad-feed   | product | https://example.com/feed | [{"id": "item1"}]  |
    Then the operation should fail with per-catalog error for "bad-feed"
    And the per-catalog error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "url" or "items"
    # POST-F2: URL/items mutual exclusivity violated
    # BR-RULE-173 INV-3
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-ext-e-invalid-type @extension @ext-e @error @post-f2
  Scenario: Invalid request -- catalog type not in enum
    When the Buyer Agent sends a sync_catalogs request with catalogs:
    | catalog_id | type           |
    | bad-feed   | unknown_type   |
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "type"
    # POST-F2: Catalog type not in 13-value enum
    # BR-RULE-173 INV-2
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-ext-e-mapping-xor @extension @ext-e @error @post-f2
  Scenario: Invalid request -- feed field mapping has both feed_field and value
    When the Buyer Agent sends a sync_catalogs request with a catalog having feed_field_mappings:
    | feed_field | value     | catalog_field |
    | name       | override  | name          |
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "feed_field" or "value"
    # POST-F2: Mapping feed_field XOR value constraint violated
    # BR-RULE-175 INV-2
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-ext-e-target-xor @extension @ext-e @error @post-f2
  Scenario: Invalid request -- feed field mapping has both catalog_field and asset_group_id
    When the Buyer Agent sends a sync_catalogs request with a catalog having feed_field_mappings:
    | feed_field | catalog_field | asset_group_id    |
    | photo      | image_url     | images_landscape  |
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "catalog_field" or "asset_group_id"
    # POST-F2: Mapping catalog_field XOR asset_group_id constraint violated
    # BR-RULE-175 INV-3
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-ext-e-gtin @extension @ext-e @error @post-f2
  Scenario: Invalid request -- GTIN with invalid format
    When the Buyer Agent sends a sync_catalogs request with a catalog having selectors:
    | gtins         |
    | ABC12345      |
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "GTIN" or "numeric"
    # POST-F2: GTIN pattern violated (must be 8-14 numeric digits)
    # BR-RULE-176 INV-3
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-ext-e-strict @extension @ext-e @error @post-f2
  Scenario: Invalid request -- strict validation mode fails entire sync on any error
    When the Buyer Agent sends a sync_catalogs request with validation_mode "strict" and catalogs:
    | catalog_id | type    |
    | good-feed  | product |
    | bad-feed   | INVALID |
    Then the operation should fail completely
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "validation"
    # POST-F2: Strict mode -- single catalog error fails entire operation
    # BR-RULE-172 INV-5
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-ext-e-idempotency-missing @extension @ext-e @error @post-f1 @post-f2
  Scenario: Invalid request -- idempotency_key is missing
    When the Buyer Agent sends a sync_catalogs request without an idempotency_key
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "idempotency_key"
    # POST-F1: system state unchanged (idempotency_key is a required top-level field in v3.1)
    # POST-F2: idempotency_key missing -> INVALID_REQUEST
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-ext-e-idempotency-too-short @extension @ext-e @error @post-f2
  Scenario: Invalid request -- idempotency_key shorter than minimum length
    When the Buyer Agent sends a sync_catalogs request with idempotency_key "short-key"
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "idempotency_key"
    # POST-F2: idempotency_key minLength=16 (pattern ^[A-Za-z0-9_.:-]{16,255}$) violated

  @T-UC-023-ext-f @extension @ext-f @error @post-f1 @post-f2 @post-f3
  Scenario: Unsupported feature -- catalog_management capability not declared
    Given the seller has NOT declared catalog_management capability
    When the Buyer Agent sends a sync_catalogs request with catalogs:
    | catalog_id | type    |
    | feed-001   | product |
    Then the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "get_adcp_capabilities"
    And the request context is echoed in the response
    # POST-F1: System state unchanged
    # POST-F2: Error explains feature not supported
    # POST-F3: Context echoed
    # BR-RULE-132 INV-2: capability absent -> UNSUPPORTED_FEATURE

  @T-UC-023-ext-g @extension @ext-g @error @post-f1 @post-f2 @post-f3
  Scenario: Auth required -- authentication missing
    Given the Buyer has no authentication credentials
    When the Buyer Agent sends a sync_catalogs request
    Then the operation should fail
    And the error code should be "AUTH_REQUIRED"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "authentication"
    And the request context is echoed in the response
    # POST-F1: System state unchanged
    # POST-F2: Error explains auth is required
    # POST-F3: Context echoed

  @T-UC-023-ext-g-expired @extension @ext-g @error @post-f2
  Scenario: Auth required -- token expired
    Given the Buyer Agent has an expired authentication token
    When the Buyer Agent sends a sync_catalogs request
    Then the operation should fail
    And the error code should be "AUTH_REQUIRED"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "authentication" or "token"
    # POST-F2: Expired token treated as auth failure

  @T-UC-023-ext-h @extension @ext-h @error @post-f1 @post-f2 @post-f3
  Scenario: Rate limited -- request rate exceeded
    Given the Buyer Agent has exceeded the request rate for sync_catalogs
    When the Buyer Agent sends a sync_catalogs request
    Then the operation should fail
    And the error code should be "RATE_LIMITED"
    And the error recovery should be "transient"
    And the error should include retry_after interval
    And the error should include "suggestion" field
    And the suggestion should contain "retry" or "rate"
    And the request context is echoed in the response
    # POST-F1: System state unchanged
    # POST-F2: Rate limit exceeded with retry_after hint
    # POST-F3: Context echoed

  @T-UC-023-ext-i @extension @ext-i @error @post-f1 @post-f2 @post-f3
  Scenario: Service unavailable -- seller service temporarily down
    Given the seller service is experiencing a transient failure
    When the Buyer Agent sends a sync_catalogs request
    Then the operation should fail
    And the error code should be "SERVICE_UNAVAILABLE"
    And the error recovery should be "transient"
    And the error should include retry_after interval
    And the error should include "suggestion" field
    And the suggestion should contain "retry" or "unavailable"
    And the request context is echoed in the response
    # POST-F1: System state unchanged
    # POST-F2: Service unavailable with retry_after
    # POST-F3: Context echoed

  @T-UC-023-ext-j-submitted @extension @ext-j @async @post-s8
  Scenario: Async lifecycle -- submitted acknowledgment for large sync
    Given the sync_catalogs operation requires async processing
    When the Buyer Agent sends a sync_catalogs request with 50 catalogs
    Then the response is a SyncCatalogsAsyncResponseSubmitted
    And the submitted response includes the request context
    And the submitted response has status "submitted"
    And the submitted response includes a task_id
    # POST-S8: Buyer receives submitted acknowledgment
    # BR-RULE-178 INV-1: sync operation queued -> status submitted + required task_id
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-ext-j-working @extension @ext-j @async @post-s8
  Scenario: Async lifecycle -- working progress with catalog and item counts
    Given the sync_catalogs operation is in progress
    When the Buyer Agent receives an async progress update
    Then the response is a SyncCatalogsAsyncResponseWorking
    And the working response includes percentage between 0 and 100
    And the working response may include catalogs_processed and catalogs_total
    And the working response may include items_processed and items_total
    And the working response may include current_step description
    # POST-S8: Buyer receives progress updates
    # BR-RULE-178 INV-2: working state with progress fields

  @T-UC-023-ext-j-input @extension @ext-j @async @post-s8
  Scenario Outline: Async lifecycle -- input required with reason <reason>
    Given the sync_catalogs operation is paused waiting for buyer input
    When the Buyer Agent receives an input-required notification with reason "<reason>"
    Then the response is a SyncCatalogsAsyncResponseInputRequired
    And the input-required response has reason "<reason>"
    And the response includes the request context
    # POST-S8: Buyer knows operation is paused and why
    # BR-RULE-178 INV-3, INV-5: input-required with valid reason code

    Examples:
      | reason             |
      | APPROVAL_REQUIRED  |
      | FEED_VALIDATION    |
      | ITEM_REVIEW        |
      | FEED_ACCESS        |

  @T-UC-023-ext-j-webhook @extension @ext-j @async @post-s8
  Scenario: Async lifecycle -- push notification config enables webhook delivery
    Given the Buyer Agent provides a push_notification_config with webhook URL
    When the Buyer Agent sends a sync_catalogs request that requires async processing
    Then the system sends async updates via the configured webhook URL
    And the webhook notification includes the task status
    # BR-RULE-178 INV-6: webhook sent on completion or input-required

  @T-UC-023-ext-k @extension @ext-k @async @post-s9
  Scenario: Submitted async task -- queued sync returns task handle with results deferred
    Given the sync_catalogs operation must be processed asynchronously
    When the Buyer Agent sends a sync_catalogs request that is queued for batch ingestion
    Then the response is a SyncCatalogsSubmitted with status "submitted"
    And the submitted response includes a task_id
    And the submitted response does not contain a catalogs array
    And the request context is echoed in the response
    And the Buyer Agent can resolve final per-catalog results via tasks/get with the task_id
    # POST-S9: submitted task envelope; per-catalog results resolved via tasks/get or webhook
    # BR-RULE-177 INV-7: queued -> status submitted + task_id, per-result data deferred

  @T-UC-023-ext-l @extension @ext-l @idempotency @happy-path @post-s10 @post-s1
  Scenario: Idempotent replay -- duplicate idempotency_key returns original result without re-applying
    Given the Buyer Agent previously synced a catalog "feed-100" with idempotency_key "idem-abc-00000001"
    When the Buyer Agent re-sends the same sync_catalogs request with idempotency_key "idem-abc-00000001"
    Then the response is the original recorded SyncCatalogsSuccess result
    And the catalog "feed-100" is not upserted a second time
    And no additional audit events are emitted for the retry
    And no additional platform review is triggered for the retry
    # POST-S10: at-most-once -- retry does not double-apply or re-fire side effects
    # POST-S1: replayed response reports the same per-catalog actions as the original
    # BR-RULE-211 INV-2: matching key + identical payload -> original response, no new state
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-ext-l-different-account @extension @ext-l @idempotency @happy-path @post-s1
  Scenario: Idempotent replay -- same idempotency_key on a different account is a new execution
    Given the Buyer Agent previously synced a catalog with idempotency_key "idem-xyz-00000001" on account "acct-A"
    When the Buyer Agent sends a sync_catalogs request with idempotency_key "idem-xyz-00000001" on account "acct-B"
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog is processed as a new execution
    # BR-RULE-211 INV-1: dedup key scoped to (seller, account, idempotency_key); different account -> new execution

  @T-UC-023-partition-upsert @partition @upsert-semantics
  Scenario Outline: Upsert semantics partition validation -- <partition>
    Given <setup>
    When the Buyer Agent sends a sync_catalogs request with <input>
    Then <outcome>

    Examples: Valid partitions
      | partition              | setup                                                | input                                                                                                  | outcome                                                  |
      | sync_new_catalogs      | the account has no existing catalogs                 | catalogs [{"catalog_id": "new-feed", "type": "product"}]                                               | catalog "new-feed" action is "created"                   |
      | sync_existing_catalogs | the account has catalog "existing-feed"               | catalogs [{"catalog_id": "existing-feed", "type": "product", "name": "Updated"}]                       | catalog "existing-feed" action is "updated"              |
      | sync_mixed             | the account has catalog "existing" but not "new"      | catalogs [{"catalog_id": "existing", "type": "product"}, {"catalog_id": "new", "type": "store"}]       | "existing" updated and "new" created                     |
      | discovery_only         | the account has 2 catalogs                           | no catalogs array                                                                                      | all 2 catalogs returned as unchanged                     |
      | dry_run_preview        | the account has catalog "feed"                        | dry_run true and catalogs [{"catalog_id": "feed", "type": "product"}]                                  | response has dry_run true, no state change               |
      | scoped_by_catalog_ids  | the account has catalogs "A" and "B"                  | catalog_ids ["A"] and catalogs [{"catalog_id": "A", "type": "product"}]                                | only "A" affected, "B" unaffected                        |
      | lenient_mode           | the account has no catalogs                          | validation_mode "lenient" and catalogs with 1 valid + 1 invalid                                        | valid catalog processed, invalid fails independently     |
      | max_catalogs           | the account has no catalogs                          | catalogs array with exactly 50 items                                                                   | all 50 catalogs processed successfully                   |

    Examples: Invalid partitions
      | partition              | setup                                                | input                                                                                                  | outcome                                                                          |
      | catalogs_empty_array   | the account has catalogs                             | empty catalogs array []                                                                                | error "INVALID_REQUEST" with suggestion                                          |
      | catalogs_exceed_max    | the account has catalogs                             | catalogs array with 51 items                                                                           | error "INVALID_REQUEST" with suggestion                                          |
      | catalog_ids_empty_array| the account has catalogs                             | catalog_ids [] with catalogs                                                                           | error "INVALID_REQUEST" with suggestion                                          |
      | catalog_ids_exceed_max | the account has catalogs                             | catalog_ids with 51 IDs                                                                                | error "INVALID_REQUEST" with suggestion                                          |
      | strict_mode_any_error  | the account has no catalogs                          | validation_mode "strict" and catalogs with 1 valid + 1 invalid type                                    | error "INVALID_REQUEST" with suggestion -- entire sync fails                     |

  @T-UC-023-boundary-upsert @boundary @upsert-semantics
  Scenario Outline: Upsert semantics boundary validation -- <boundary_point>
    Given <setup>
    When the Buyer Agent sends a sync_catalogs request with <input>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                   | setup                              | input                                | outcome                                                  |
      | catalogs array with 1 item (minimum)             | the account has no catalogs        | 1 catalog in array                   | success, catalog processed                               |
      | catalogs array with 50 items (maximum)           | the account has no catalogs        | 50 catalogs in array                 | success, all 50 processed                                |
      | catalogs array with 0 items (below minimum)      | the account has catalogs           | empty catalogs array                 | error "INVALID_REQUEST" with suggestion                  |
      | catalogs array with 51 items (above maximum)     | the account has catalogs           | 51 catalogs in array                 | error "INVALID_REQUEST" with suggestion                  |
      | catalogs omitted entirely                        | the account has 2 catalogs         | no catalogs array                    | discovery mode, 2 catalogs returned                      |
      | catalog_ids with 1 item (minimum)                | the account has catalog "A"        | catalog_ids ["A"]                    | success, scoped to "A"                                   |
      | catalog_ids with 50 items (maximum)              | the account has 50 catalogs        | catalog_ids with 50 IDs              | success, all scoped                                      |
      | catalog_ids with 0 items (below minimum)         | the account has catalogs           | catalog_ids []                       | error "INVALID_REQUEST" with suggestion                  |
      | catalog_ids with 51 items (above maximum)        | the account has catalogs           | catalog_ids with 51 IDs              | error "INVALID_REQUEST" with suggestion                  |
      | dry_run=true                                     | the account has catalog "feed"     | dry_run true with catalogs           | success, dry_run true in response                        |
      | dry_run=false (default)                          | the account has no catalogs        | dry_run false with catalogs          | success, normal processing                               |
      | validation_mode=strict (default)                 | the account has no catalogs        | validation_mode "strict"             | success if all catalogs valid                             |
      | validation_mode=lenient                          | the account has no catalogs        | validation_mode "lenient"            | success, partial failures reported                       |
      | strict mode + one invalid catalog                | the account has no catalogs        | strict + 1 valid + 1 invalid catalog | error "INVALID_REQUEST" with suggestion                  |

  @T-UC-023-partition-catalog-validation @partition @catalog-validation
  Scenario Outline: Catalog validation partition -- <partition>
    When the Buyer Agent sends a sync_catalogs request with a catalog: <input>
    Then <outcome>

    Examples: Valid partitions
      | partition                  | input                                                                                    | outcome                                                  |
      | structural_type_offering   | type "offering" with inline items                                                        | catalog accepted for processing                          |
      | structural_type_product    | type "product" with url and feed_format "google_merchant_center"                         | catalog accepted for processing                          |
      | vertical_type_hotel        | type "hotel" with url and feed_format "custom"                                           | catalog accepted for processing                          |
      | url_sourced                | type "product" with url "https://example.com/feed" and feed_format "custom"              | feed fetched from external URL                           |
      | items_sourced              | type "offering" with items [{"offering_id": "o1"}]                                       | inline items processed directly                          |
      | neither_url_nor_items      | catalog_id "existing" with type "product" (no url or items)                              | catalog reference accepted (platform uses synced copy)   |

    Examples: Invalid partitions
      | partition                  | input                                                                                    | outcome                                                              |
      | missing_type               | catalog_id "feed" with url but no type                                                   | error "INVALID_REQUEST" with suggestion                              |
      | unknown_type               | type "unknown_vertical" with items                                                       | error "INVALID_REQUEST" with suggestion                              |
      | url_and_items_both         | type "product" with both url and items                                                   | error "INVALID_REQUEST" with suggestion                              |
      | items_empty_array          | type "offering" with items []                                                            | error "INVALID_REQUEST" with suggestion                              |
      | url_invalid_format         | type "product" with url "not-a-url" and feed_format "custom"                             | error "INVALID_REQUEST" with suggestion                              |

  @T-UC-023-boundary-catalog-validation @boundary @catalog-validation
  Scenario Outline: Catalog validation boundary -- <boundary_point>
    When the Buyer Agent sends a sync_catalogs request with a catalog: <input>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                        | input                                                                    | outcome                                              |
      | type=offering (first structural type) | type "offering" with inline items                                        | catalog accepted                                     |
      | type=app (last vertical type)         | type "app" with url and feed_format "custom"                             | catalog accepted                                     |
      | type=product (structural)             | type "product" with url and feed_format "google_merchant_center"         | catalog accepted                                     |
      | type omitted                          | catalog with no type field                                               | error "INVALID_REQUEST" with suggestion               |
      | type=unknown_value                    | type "nonexistent_type"                                                  | error "INVALID_REQUEST" with suggestion               |
      | url only (no items)                   | type "product" with url only                                             | catalog accepted, feed fetched                       |
      | items only (no url)                   | type "offering" with items only                                          | catalog accepted, inline processing                  |
      | neither url nor items                 | type "product" with catalog_id only                                      | catalog reference accepted                           |
      | both url and items present            | type "product" with both url and items                                   | error "INVALID_REQUEST" with suggestion               |
      | items with 1 element (minimum)        | type "offering" with items [1 item]                                      | catalog accepted                                     |
      | items with 0 elements                 | type "offering" with items []                                            | error "INVALID_REQUEST" with suggestion               |

  @T-UC-023-partition-delete-missing @partition @delete-missing
  Scenario Outline: Delete missing partition validation -- <partition>
    Given <setup>
    When the Buyer Agent sends a sync_catalogs request with <input>
    Then <outcome>

    Examples: Valid partitions
      | partition              | setup                                                  | input                                          | outcome                                                  |
      | delete_with_catalogs   | the account has buyer-managed catalogs "A" and "B"     | delete_missing true and catalogs [A only]       | "A" kept, "B" deleted                                    |
      | delete_false_default   | the account has buyer-managed catalogs                 | catalogs provided, no delete_missing            | no catalogs deleted (default false)                      |
      | delete_false_explicit  | the account has buyer-managed catalogs                 | delete_missing false with catalogs              | no catalogs deleted                                      |

    Examples: Invalid partitions
      | partition                | setup                                                | input                                          | outcome                                                  |
      | delete_without_catalogs  | the account has buyer-managed catalogs               | delete_missing true with no catalogs array      | error "INVALID_REQUEST" with suggestion                  |

  @T-UC-023-boundary-delete-missing @boundary @delete-missing
  Scenario Outline: Delete missing boundary validation -- <boundary_point>
    Given <setup>
    When the Buyer Agent sends a sync_catalogs request with <input>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                      | setup                                              | input                                              | outcome                                              |
      | delete_missing=true with catalogs array present     | the account has buyer-managed catalogs "A" and "B" | delete_missing true with catalogs [A]               | "A" kept, "B" deleted                                |
      | delete_missing=true without catalogs array          | the account has buyer-managed catalogs             | delete_missing true with no catalogs array          | error "INVALID_REQUEST" with suggestion              |
      | delete_missing=false (explicit)                     | the account has buyer-managed catalogs             | delete_missing false with catalogs                  | no deletions                                         |
      | delete_missing omitted (default false)              | the account has buyer-managed catalogs             | catalogs provided, delete_missing omitted           | no deletions (default false)                         |
      | delete_missing=true with empty catalogs array       | the account has buyer-managed catalogs             | delete_missing true with catalogs []                | error "INVALID_REQUEST" with suggestion              |

  @T-UC-023-partition-feed-management @partition @feed-management
  Scenario Outline: Feed management partition validation -- <partition>
    When the Buyer Agent sends a sync_catalogs request with a catalog: <input>
    Then <outcome>

    Examples: Valid partitions
      | partition                  | input                                                                                                  | outcome                                          |
      | url_with_format            | type "product" url "https://example.com/feed.xml" feed_format "google_merchant_center"                 | catalog accepted with feed format                |
      | url_offering_no_format     | type "offering" url "https://example.com/offerings.json" (no feed_format)                              | catalog accepted (offering type needs no format) |
      | url_with_frequency         | type "product" url "https://example.com/feed" feed_format "custom" update_frequency "daily"            | catalog accepted with frequency hint             |
      | mapping_field_rename       | feed_field_mappings: feed_field "hotel_name" -> catalog_field "name"                                   | mapping accepted, field renamed                  |
      | mapping_date_transform     | feed_field_mappings: feed_field "avail_date" -> catalog_field "valid_from" transform "date"             | date transform applied                           |
      | mapping_divide_transform   | feed_field_mappings: feed_field "price_cents" -> catalog_field "price.amount" transform "divide" by 100 | divide transform applied                         |
      | mapping_literal_injection  | feed_field_mappings: value "USD" -> catalog_field "price.currency"                                      | static value injected                            |
      | mapping_asset_group        | feed_field_mappings: feed_field "photo_url" -> asset_group_id "images_landscape"                       | image routed to asset pool                       |
      | mapping_split_transform    | feed_field_mappings: feed_field "tags" -> catalog_field "tags" transform "split" separator ","          | split transform applied                          |
      | mapping_with_default       | feed_field_mappings: feed_field "is_available" -> catalog_field "available" transform "boolean" default false | default used when field absent              |

    Examples: Invalid partitions
      | partition                       | input                                                                                      | outcome                                                  |
      | both_feed_field_and_value       | mapping: feed_field "name" AND value "override" -> catalog_field "name"                    | error "INVALID_REQUEST" with suggestion                  |
      | both_catalog_field_and_asset_group | mapping: feed_field "photo" -> catalog_field "image_url" AND asset_group_id "images"    | error "INVALID_REQUEST" with suggestion                  |
      | unknown_feed_format             | type "product" url "https://example.com/feed" feed_format "unknown_format"                 | error "INVALID_REQUEST" with suggestion                  |
      | unknown_transform               | mapping: feed_field "x" -> catalog_field "y" transform "uppercase"                         | error "INVALID_REQUEST" with suggestion                  |
      | divide_by_zero_or_negative      | mapping: feed_field "price" -> catalog_field "amount" transform "divide" by 0              | error "INVALID_REQUEST" with suggestion                  |
      | mappings_empty_array            | feed_field_mappings: []                                                                    | error "INVALID_REQUEST" with suggestion                  |

  @T-UC-023-boundary-feed-management @boundary @feed-management
  Scenario Outline: Feed management boundary validation -- <boundary_point>
    When the Buyer Agent sends a sync_catalogs request with a catalog: <input>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                 | input                                                                                          | outcome                                              |
      | feed_format=google_merchant_center             | url with feed_format "google_merchant_center"                                                  | catalog accepted                                     |
      | feed_format=custom (last enum value)           | url with feed_format "custom"                                                                  | catalog accepted                                     |
      | feed_format=unknown_value                      | url with feed_format "nonexistent"                                                             | error "INVALID_REQUEST" with suggestion               |
      | update_frequency=realtime                      | url with update_frequency "realtime"                                                           | catalog accepted with frequency hint                 |
      | update_frequency=weekly (last)                 | url with update_frequency "weekly"                                                             | catalog accepted with frequency hint                 |
      | feed_field_mappings with 1 mapping (minimum)   | 1 mapping in feed_field_mappings                                                               | mappings accepted                                    |
      | feed_field_mappings empty array                | feed_field_mappings []                                                                         | error "INVALID_REQUEST" with suggestion               |
      | mapping with feed_field only (no value)        | mapping: feed_field "name" -> catalog_field "title"                                            | valid input source                                   |
      | mapping with value only (no feed_field)        | mapping: value "USD" -> catalog_field "currency"                                               | valid literal injection                              |
      | mapping with both feed_field and value         | mapping: feed_field "name" AND value "override" -> catalog_field "title"                       | error "INVALID_REQUEST" with suggestion               |
      | mapping with catalog_field only                | mapping: feed_field "name" -> catalog_field "title"                                            | valid output target                                  |
      | mapping with asset_group_id only               | mapping: feed_field "photo" -> asset_group_id "images"                                         | valid asset routing                                  |
      | mapping with both catalog_field and asset_group_id | mapping: feed_field "photo" -> catalog_field "img" AND asset_group_id "images"             | error "INVALID_REQUEST" with suggestion               |
      | divide transform with by=0.01 (positive)       | mapping with transform "divide" by 0.01                                                       | valid division                                       |
      | divide transform with by=0 (zero)              | mapping with transform "divide" by 0                                                          | error "INVALID_REQUEST" with suggestion               |

  @T-UC-023-partition-selectors @partition @selectors-attribution
  Scenario Outline: Selectors and attribution partition validation -- <partition>
    When the Buyer Agent sends a sync_catalogs request with a catalog having selectors: <input>
    Then <outcome>

    Examples: Valid partitions
      | partition              | input                                                               | outcome                                              |
      | ids_selector           | ids ["SKU-123", "SKU-456"]                                          | items filtered by specific IDs                       |
      | gtins_selector         | gtins ["00013000006040", "5901234123457"]                           | items filtered by GTIN identifiers                   |
      | tags_or_logic          | tags ["summer", "clearance"]                                        | items matching ANY tag included (OR logic)           |
      | category_filter        | category "beverages/soft-drinks"                                    | items filtered by category path                      |
      | query_filter           | query "pasta sauces under $5"                                       | items filtered by natural language query             |
      | attribution_declared   | conversion_events ["purchase", "add_to_cart"] content_id_type "gtin" | attribution configured for catalog                  |
      | gtin_8_digits          | gtins ["12345678"]                                                  | GTIN-8 accepted                                      |
      | gtin_14_digits         | gtins ["12345678901234"]                                            | GTIN-14 accepted                                     |

    Examples: Invalid partitions
      | partition                    | input                                                         | outcome                                                  |
      | gtin_too_short               | gtins ["1234567"]                                             | error "INVALID_REQUEST" with suggestion                  |
      | gtin_too_long                | gtins ["123456789012345"]                                     | error "INVALID_REQUEST" with suggestion                  |
      | gtin_non_numeric             | gtins ["0001300ABC040"]                                       | error "INVALID_REQUEST" with suggestion                  |
      | ids_empty_array              | ids []                                                        | error "INVALID_REQUEST" with suggestion                  |
      | tags_empty_array             | tags []                                                       | error "INVALID_REQUEST" with suggestion                  |
      | conversion_events_empty      | conversion_events []                                          | error "INVALID_REQUEST" with suggestion                  |
      | conversion_events_duplicate  | conversion_events ["purchase", "purchase"]                    | error "INVALID_REQUEST" with suggestion                  |
      | unknown_content_id_type      | content_id_type "unknown_type"                                | error "INVALID_REQUEST" with suggestion                  |

  @T-UC-023-boundary-selectors @boundary @selectors-attribution
  Scenario Outline: Selectors and attribution boundary validation -- <boundary_point>
    When the Buyer Agent sends a sync_catalogs request with a catalog having selectors: <input>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                             | input                                              | outcome                                              |
      | GTIN with 8 digits (minimum)               | gtins ["12345678"]                                 | GTIN-8 accepted                                      |
      | GTIN with 14 digits (maximum)              | gtins ["12345678901234"]                           | GTIN-14 accepted                                     |
      | GTIN with 7 digits (below minimum)         | gtins ["1234567"]                                  | error "INVALID_REQUEST" with suggestion               |
      | GTIN with 15 digits (above maximum)        | gtins ["123456789012345"]                          | error "INVALID_REQUEST" with suggestion               |
      | GTIN with letters                          | gtins ["0001300ABC040"]                            | error "INVALID_REQUEST" with suggestion               |
      | ids with 1 item (minimum)                  | ids ["SKU-1"]                                      | IDs accepted                                         |
      | ids empty array                            | ids []                                             | error "INVALID_REQUEST" with suggestion               |
      | tags with 1 item (minimum)                 | tags ["summer"]                                    | tags accepted                                        |
      | tags empty array                           | tags []                                            | error "INVALID_REQUEST" with suggestion               |
      | conversion_events with 1 unique event      | conversion_events ["purchase"]                     | attribution accepted                                 |
      | conversion_events with duplicate           | conversion_events ["purchase", "purchase"]         | error "INVALID_REQUEST" with suggestion               |
      | content_id_type=sku (first enum)           | content_id_type "sku"                              | content ID type accepted                             |
      | content_id_type=app_id (last enum)         | content_id_type "app_id"                           | content ID type accepted                             |
      | content_id_type=unknown                    | content_id_type "nonexistent"                      | error "INVALID_REQUEST" with suggestion               |

  @T-UC-023-partition-response @partition @sync-response
  Scenario Outline: Sync response structure partition validation -- <partition>
    Given <setup>
    When the Buyer Agent sends a sync_catalogs request
    Then <outcome>

    Examples: Valid partitions
      | partition                | setup                                                   | outcome                                                          |
      | success_all_created      | all catalogs are new                                    | response has catalogs array, all action "created"                |
      | success_all_updated      | all catalogs exist                                      | response has catalogs array, all action "updated"                |
      | success_mixed_actions    | some catalogs exist, some new                           | response has mixed actions (created, updated, unchanged)         |
      | partial_success          | lenient mode with 1 valid + 1 invalid catalog           | response has catalogs with action "created" and "failed"         |
      | success_with_item_review | catalogs submitted for platform review                  | response includes item review counts per catalog                 |
      | success_with_deletions   | delete_missing true with some catalogs omitted          | response includes action "deleted" for purged catalogs           |
      | success_dry_run          | dry_run true with catalogs                              | response has dry_run true, projected actions shown               |
      | error_response           | account not found                                       | response has errors array, no catalogs field                     |
      | discovery_response       | catalogs omitted (discovery mode)                       | response has catalogs with action "unchanged"                    |

    Examples: Invalid partitions (structural invariants -- response schema enforcement)
      | partition                | setup                                                   | outcome                                                                          |
      | both_catalogs_and_errors | N/A (oneOf structural invariant)                        | never produced -- schema prevents both catalogs and errors in same response       |
      | errors_empty             | N/A (minItems=1 on errors array)                        | never produced -- error branch requires at least 1 error                         |
      | missing_catalog_id       | N/A (required field on per-catalog result)              | never produced -- catalog_id is required on every result                         |
      | missing_action           | N/A (required field on per-catalog result)              | never produced -- action is required on every result                             |
      | unknown_action           | N/A (enum constraint on action)                         | never produced -- action must be one of 5 enum values                            |

  @T-UC-023-boundary-response @boundary @sync-response
  Scenario Outline: Sync response structure boundary validation -- <boundary_point>
    Given <setup>
    When <action>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                 | setup                                              | action                                               | outcome                                              |
      | Response with catalogs only (success branch)   | valid sync request                                 | Buyer Agent sends sync_catalogs                      | response has catalogs array, no errors               |
      | Response with errors only (error branch)       | invalid account reference                          | Buyer Agent sends sync_catalogs                      | response has errors array, no catalogs               |
      | Response with both catalogs and errors         | N/A (structural invariant)                         | system constructs response                           | never occurs (oneOf prevents both)                   |
      | Error branch with 1 error (minimum)            | account not found                                  | Buyer Agent sends sync_catalogs                      | errors array with 1 error object                     |
      | Error branch with 0 errors                     | N/A (structural invariant)                         | system constructs response                           | never occurs (minItems=1)                            |
      | Per-catalog action=created                     | new catalog synced                                 | Buyer Agent sends sync_catalogs with new catalog     | catalog result has action "created"                  |
      | Per-catalog action=deleted                     | delete_missing purges catalog                      | Buyer Agent sends sync_catalogs with delete_missing  | catalog result has action "deleted"                  |
      | Per-catalog action=failed (partial success)    | lenient mode with invalid catalog                  | Buyer Agent sends sync_catalogs                      | failed catalog alongside successful ones             |
      | Per-catalog action=unknown                     | N/A (structural invariant)                         | system constructs response                           | never occurs (enum constraint)                       |
      | dry_run=true in success response               | dry_run request                                    | Buyer Agent sends dry_run sync                       | response has dry_run true                            |
      | item_count=0 (minimum)                         | catalog with no items ingested                     | Buyer Agent syncs empty catalog                      | item_count 0 is valid                                |
      | Catalog result with item_issues array          | platform flags items                               | Buyer Agent syncs catalog with reviewed items        | item_issues array present with per-item details      |
      | Response with status=submitted + task_id (submitted branch) | sync queued for async ingestion       | Buyer Agent sends sync_catalogs                      | response has status submitted and task_id, no catalogs |
      | Submitted branch with task_id omitted          | N/A (structural invariant)                         | system constructs response                           | never occurs (task_id required when status=submitted) |
      | Submitted branch carrying catalogs             | N/A (structural invariant)                         | system constructs response                           | never occurs (oneOf forbids catalogs on submitted)  |

  @T-UC-023-partition-async @partition @async-lifecycle
  Scenario Outline: Async lifecycle partition validation -- <partition>
    Given <setup>
    When <action>
    Then <outcome>

    Examples: Valid partitions
      | partition                       | setup                                                    | action                                                              | outcome                                              |
      | submitted_ack                   | async processing required                                | Buyer Agent sends sync_catalogs with large batch                    | submitted acknowledgment returned with context       |
      | working_with_percentage         | sync operation in progress                               | Buyer Agent receives progress update                                | percentage, current_step, catalog counts reported    |
      | working_with_steps              | sync operation in progress                               | Buyer Agent receives step-tracking update                           | step_number, total_steps, current_step reported      |
      | working_with_item_counts        | sync operation processing items                          | Buyer Agent receives item progress                                  | items_processed, items_total reported                |
      | input_required_approval         | platform requires catalog approval                       | system sends input-required notification                            | reason "APPROVAL_REQUIRED"                           |
      | input_required_feed_validation  | feed URL returned unexpected format                      | system sends input-required notification                            | reason "FEED_VALIDATION"                             |
      | input_required_item_review      | platform flagged items for review                        | system sends input-required notification                            | reason "ITEM_REVIEW"                                 |
      | input_required_feed_access      | platform cannot access feed URL                          | system sends input-required notification                            | reason "FEED_ACCESS"                                 |

    Examples: Invalid partitions
      | partition                  | setup                                                        | action                                                              | outcome                                              |
      | percentage_below_zero      | sync in progress                                            | system reports percentage -1                                        | invalid (minimum=0)                                  |
      | percentage_above_100       | sync in progress                                            | system reports percentage 101                                       | invalid (maximum=100)                                |
      | total_steps_zero           | sync in progress                                            | system reports total_steps 0                                        | invalid (minimum=1)                                  |
      | unknown_reason             | input required with unknown reason                          | system sends reason "UNKNOWN_REASON"                                | invalid (not in 4-value enum)                        |

  @T-UC-023-boundary-async @boundary @async-lifecycle
  Scenario Outline: Async lifecycle boundary validation -- <boundary_point>
    Given <setup>
    When <action>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                              | setup                                | action                                                   | outcome                                              |
      | percentage=0 (minimum, just started)        | sync just started                    | system reports percentage 0                              | valid progress report                                |
      | percentage=100 (maximum, about to complete) | sync nearly done                     | system reports percentage 100                            | valid progress report                                |
      | percentage=-1 (below minimum)               | sync in progress                     | system reports percentage -1                             | invalid (below minimum)                              |
      | percentage=101 (above maximum)              | sync in progress                     | system reports percentage 101                            | invalid (above maximum)                              |
      | total_steps=1 (minimum)                     | single-step sync                     | system reports total_steps 1                             | valid step count                                     |
      | total_steps=0 (below minimum)               | sync in progress                     | system reports total_steps 0                             | invalid (below minimum)                              |
      | step_number=1 (minimum)                     | first step of sync                   | system reports step_number 1                             | valid step number                                    |
      | catalogs_processed=0 (none yet)             | sync just started                    | system reports catalogs_processed 0                      | valid progress                                       |
      | reason=APPROVAL_REQUIRED                    | platform requires approval           | system sends input-required with APPROVAL_REQUIRED       | valid reason code                                    |
      | reason=FEED_ACCESS (last enum value)        | platform cannot access feed          | system sends input-required with FEED_ACCESS             | valid reason code                                    |
      | reason=UNKNOWN_REASON                       | N/A                                  | system sends input-required with UNKNOWN_REASON          | invalid (not in enum)                                |
      | submitted state (minimal payload)           | async processing queued              | Buyer Agent receives submitted acknowledgment            | valid submitted response with context                |
      | submitted state (status + required task_id) | async processing queued              | Buyer Agent receives submitted acknowledgment            | valid submitted response with status and task_id     |
      | submitted state missing task_id             | N/A (structural invariant)           | system constructs submitted envelope                     | invalid (task_id required when status=submitted)     |

  @T-UC-023-inv-172-1 @invariant @BR-RULE-172
  Scenario: BR-RULE-172 INV-1 holds -- existing catalog matched by catalog_id is updated
    Given the account has a catalog with catalog_id "feed-001"
    When the Buyer Agent sends a sync_catalogs request with catalog_id "feed-001"
    Then the catalog result for "feed-001" has action "updated"
    # BR-RULE-172 INV-1: matching catalog_id -> update

  @T-UC-023-inv-172-2 @invariant @BR-RULE-172
  Scenario: BR-RULE-172 INV-2 holds -- unmatched catalog_id creates new catalog
    Given the account has no catalog with catalog_id "new-feed"
    When the Buyer Agent sends a sync_catalogs request with catalog_id "new-feed"
    Then the catalog result for "new-feed" has action "created"
    # BR-RULE-172 INV-2: no match -> create

  @T-UC-023-inv-172-5 @invariant @BR-RULE-172 @error
  Scenario: BR-RULE-172 INV-5 violated -- strict mode fails entire sync on any error
    When the Buyer Agent sends a sync_catalogs request with validation_mode "strict" and one invalid catalog
    Then the entire sync operation fails
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "validation"
    # BR-RULE-172 INV-5: strict + any error -> entire sync fails

  @T-UC-023-inv-172-6 @invariant @BR-RULE-172
  Scenario: BR-RULE-172 INV-6 holds -- lenient mode processes valid catalogs despite errors
    When the Buyer Agent sends a sync_catalogs request with validation_mode "lenient" and catalogs:
    | catalog_id | type    |
    | valid-feed | product |
    | bad-feed   | INVALID |
    Then the catalog result for "valid-feed" has action "created"
    And the catalog result for "bad-feed" has action "failed"
    # BR-RULE-172 INV-6: lenient + errors -> valid catalogs still processed

  @T-UC-023-inv-173-3 @invariant @BR-RULE-173 @error
  Scenario: BR-RULE-173 INV-3 violated -- catalog has both url and items
    When the Buyer Agent syncs a catalog with both url "https://example.com/feed" and items [{"id":"1"}]
    Then the catalog is rejected
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "url" or "items"
    # BR-RULE-173 INV-3: url + items -> rejected

  @T-UC-023-inv-173-4 @invariant @BR-RULE-173
  Scenario: BR-RULE-173 INV-4 holds -- catalog with url only fetches from external feed
    When the Buyer Agent syncs a catalog with type "product" and url "https://feeds.example.com/products.xml" and feed_format "google_merchant_center"
    Then the catalog is accepted for processing
    And the feed is fetched from the external URL
    # BR-RULE-173 INV-4: url only -> feed fetched

  @T-UC-023-inv-173-5 @invariant @BR-RULE-173
  Scenario: BR-RULE-173 INV-5 holds -- catalog with items only processes inline data
    When the Buyer Agent syncs a catalog with type "offering" and items [{"offering_id": "o1"}]
    Then the catalog is accepted for processing
    And inline items are processed directly
    # BR-RULE-173 INV-5: items only -> inline processing

  @T-UC-023-inv-173-6 @invariant @BR-RULE-173
  Scenario: BR-RULE-173 INV-6 holds -- catalog with neither url nor items is a reference
    When the Buyer Agent syncs a catalog with catalog_id "existing" and type "product" but no url or items
    Then the catalog is accepted as a reference
    And the platform uses the existing synced copy
    # BR-RULE-173 INV-6: neither url nor items -> catalog reference

  @T-UC-023-inv-174-2 @invariant @BR-RULE-174 @error
  Scenario: BR-RULE-174 INV-2 violated -- delete_missing true without catalogs
    When the Buyer Agent sends a sync_catalogs request with delete_missing true and no catalogs array
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "catalogs"
    # BR-RULE-174 INV-2: delete_missing=true + catalogs omitted -> rejected

  @T-UC-023-inv-175-2 @invariant @BR-RULE-175 @error
  Scenario: BR-RULE-175 INV-2 violated -- mapping has both feed_field and value
    When the Buyer Agent syncs a catalog with a mapping having feed_field "name" and value "override"
    Then the mapping is rejected
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "feed_field" or "value"
    # BR-RULE-175 INV-2: feed_field + value -> rejected

  @T-UC-023-inv-175-3 @invariant @BR-RULE-175 @error
  Scenario: BR-RULE-175 INV-3 violated -- mapping has both catalog_field and asset_group_id
    When the Buyer Agent syncs a catalog with a mapping having catalog_field "img" and asset_group_id "images"
    Then the mapping is rejected
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "catalog_field" or "asset_group_id"
    # BR-RULE-175 INV-3: catalog_field + asset_group_id -> rejected

  @T-UC-023-inv-175-4 @invariant @BR-RULE-175 @error
  Scenario: BR-RULE-175 INV-4 violated -- divide transform with by <= 0
    When the Buyer Agent syncs a catalog with a mapping having transform "divide" and by 0
    Then the mapping is rejected
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "divide" or "positive"
    # BR-RULE-175 INV-4: divide by <= 0 -> rejected

  @T-UC-023-inv-175-5 @invariant @BR-RULE-175 @error
  Scenario: BR-RULE-175 INV-5 violated -- feed_field_mappings present but empty
    When the Buyer Agent syncs a catalog with feed_field_mappings []
    Then the request is rejected
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "mappings"
    # BR-RULE-175 INV-5: empty mappings array -> rejected

  @T-UC-023-inv-176-2 @invariant @BR-RULE-176 @error
  Scenario: BR-RULE-176 INV-2 violated -- GTIN fewer than 8 digits
    When the Buyer Agent syncs a catalog with gtins ["1234567"]
    Then the selector is rejected
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "GTIN" or "digits"
    # BR-RULE-176 INV-2: GTIN < 8 digits -> rejected

  @T-UC-023-inv-176-3 @invariant @BR-RULE-176 @error
  Scenario: BR-RULE-176 INV-3 violated -- GTIN contains non-numeric characters
    When the Buyer Agent syncs a catalog with gtins ["0001300ABC040"]
    Then the selector is rejected
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "GTIN" or "numeric"
    # BR-RULE-176 INV-3: non-numeric GTIN -> rejected

  @T-UC-023-inv-176-5 @invariant @BR-RULE-176 @error
  Scenario: BR-RULE-176 INV-5 violated -- conversion_events has duplicate event types
    When the Buyer Agent syncs a catalog with conversion_events ["purchase", "purchase"]
    Then the request is rejected
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "unique" or "duplicate"
    # BR-RULE-176 INV-5: duplicate events -> rejected (uniqueItems)

  @T-UC-023-inv-177-1 @invariant @BR-RULE-177
  Scenario: BR-RULE-177 INV-1 holds -- success response has catalogs, no errors
    When the Buyer Agent sends a valid sync_catalogs request
    Then the response contains a catalogs array
    And the response does not contain an errors field
    # BR-RULE-177 INV-1: success -> catalogs present, errors absent

  @T-UC-023-inv-177-2 @invariant @BR-RULE-177
  Scenario: BR-RULE-177 INV-2 holds -- error response has errors, no catalogs
    Given the account reference does not match any account
    When the Buyer Agent sends a sync_catalogs request
    Then the response contains an errors array
    And the response does not contain catalogs, dry_run, or sandbox fields
    # BR-RULE-177 INV-2: error -> errors present, catalogs/dry_run/sandbox absent

  @T-UC-023-inv-177-3 @invariant @BR-RULE-177
  Scenario: BR-RULE-177 INV-3 holds -- individual catalog failure in partial success
    Given validation_mode is "lenient"
    When the Buyer Agent syncs 2 catalogs where one has an invalid type
    Then the valid catalog has action "created"
    And the invalid catalog has action "failed" with per-catalog errors
    And the response is a SyncCatalogsSuccess (not an error response)
    # BR-RULE-177 INV-3: individual catalog fails while others succeed

  @T-UC-023-inv-177-4 @invariant @BR-RULE-177
  Scenario: BR-RULE-177 INV-4 holds -- per-catalog result has catalog_id and action
    When the Buyer Agent sends a valid sync_catalogs request
    Then every catalog result includes catalog_id
    And every catalog result includes action from enum [created, updated, unchanged, failed, deleted]
    # BR-RULE-177 INV-4: required fields present

  @T-UC-023-inv-177-6 @invariant @BR-RULE-177
  Scenario: BR-RULE-177 INV-6 holds -- updated catalog includes changes array
    Given the account has a catalog with catalog_id "feed-001" and name "Old"
    When the Buyer Agent syncs catalog "feed-001" with name "New"
    Then the catalog result for "feed-001" has action "updated"
    And the catalog result for "feed-001" has changes array listing modified fields
    # BR-RULE-177 INV-6: action=updated -> changes array present

  @T-UC-023-inv-177-7 @invariant @BR-RULE-177
  Scenario: BR-RULE-177 INV-7 holds -- queued operation returns submitted envelope with task_id
    Given the sync_catalogs operation is queued for async processing
    When the Buyer Agent sends a valid sync_catalogs request
    Then the response has status "submitted"
    And the response includes a task_id
    And the response does not contain a catalogs array
    # BR-RULE-177 INV-7: queued -> status submitted + task_id; per-result data deferred to completion artifact

  @T-UC-023-inv-178-4 @invariant @BR-RULE-178
  Scenario: BR-RULE-178 INV-4 holds -- working percentage between 0 and 100
    Given a sync_catalogs operation is in async working state
    When the system reports progress
    Then the percentage is between 0 and 100 inclusive
    # BR-RULE-178 INV-4: percentage range validated

  @T-UC-023-inv-178-5 @invariant @BR-RULE-178
  Scenario: BR-RULE-178 INV-5 holds -- input-required reason is valid enum value
    Given a sync_catalogs operation requires buyer input
    When the system sends an input-required notification
    Then the reason is one of APPROVAL_REQUIRED, FEED_VALIDATION, ITEM_REVIEW, FEED_ACCESS
    # BR-RULE-178 INV-5: reason from closed 4-value enum

  @T-UC-023-inv-043-1 @invariant @BR-RULE-043
  Scenario: BR-RULE-043 INV-1 holds -- context echoed on success
    Given the Buyer Agent includes context {"request_id": "req-123", "trace_id": "t-456"}
    When the Buyer Agent sends a sync_catalogs request
    Then the response includes context {"request_id": "req-123", "trace_id": "t-456"}
    # BR-RULE-043 INV-1: request context -> response context identical

  @T-UC-023-inv-043-2 @invariant @BR-RULE-043
  Scenario: BR-RULE-043 INV-2 holds -- context omitted when not provided
    Given the Buyer Agent does not include context in the request
    When the Buyer Agent sends a sync_catalogs request
    Then the response does not include a context field
    # BR-RULE-043 INV-2: no request context -> no response context

  @T-UC-023-inv-043-error @invariant @BR-RULE-043 @error
  Scenario: BR-RULE-043 INV-1 holds on error -- context echoed in error response
    Given the Buyer Agent includes context {"request_id": "req-789"}
    And the account reference does not match any account
    When the Buyer Agent sends a sync_catalogs request
    Then the error response includes context {"request_id": "req-789"}
    And the error should include "suggestion" field
    And the suggestion should contain "account"
    # BR-RULE-043 INV-1: context echoed even on error path (POST-F3)

  @T-UC-023-inv-132-1 @invariant @BR-RULE-132
  Scenario: BR-RULE-132 INV-1 holds -- catalog_management true allows sync_catalogs
    Given the seller has declared catalog_management capability as true
    When the Buyer Agent sends a valid sync_catalogs request
    Then the request is accepted for processing
    # BR-RULE-132 INV-1: capability true -> task available

  @T-UC-023-inv-132-2 @invariant @BR-RULE-132 @error
  Scenario: BR-RULE-132 INV-2 violated -- catalog_management false returns UNSUPPORTED_FEATURE
    Given the seller has declared catalog_management capability as false
    When the Buyer Agent sends a sync_catalogs request
    Then the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "capabilities"
    # BR-RULE-132 INV-2: capability false -> UNSUPPORTED_FEATURE
    # BR-RULE-132 INV-3: recovery is correctable

  @T-UC-023-sandbox-happy @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account sync_catalogs produces simulated results with sandbox flag
    Given the Buyer is authenticated with a valid principal_id
    And the seller has declared catalog_management capability as true
    And the request targets a sandbox account
    When the Buyer Agent sends a sync_catalogs request with catalogs
    Then the response is a success variant with catalogs array
    And the response should include sandbox equals true
    And no real catalog platform syncs should have been triggered
    And no real billing records should have been created
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-3: real billing suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true

  @T-UC-023-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account sync_catalogs response does not include sandbox flag
    Given the Buyer is authenticated with a valid principal_id
    And the seller has declared catalog_management capability as true
    And the request targets a production account
    When the Buyer Agent sends a sync_catalogs request with catalogs
    Then the response is a success variant with catalogs array
    And the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent

  @T-UC-023-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid catalog returns real validation error
    Given the Buyer is authenticated with a valid principal_id
    And the seller has declared catalog_management capability as true
    And the request targets a sandbox account
    When the Buyer Agent sends a sync_catalogs request with invalid catalog type
    Then the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

  @T-UC-023-type-app @typed-catalog @app-item @post-s1
  Scenario: Sync app-type catalog with inline app items
    Given the account has no catalog with catalog_id "app-feed-001"
    When the Buyer Agent syncs a catalog with type "app" and items [{"app_id":"puzzlequest-ios","name":"Puzzle Quest: Match 3","platform":"ios","bundle_id":"com.acmegames.puzzlequest","apple_id":"1234567890","price":{"amount":0,"currency":"USD"}}]
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "app-feed-001" has action "created"
    And the catalog result for "app-feed-001" includes a platform_id
    And the catalog result for "app-feed-001" includes item_count
    # POST-S1: typed app catalog upsert returns created action
    # POST-S3: platform_id and item_count present on typed catalog
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-app-invalid-platform @typed-catalog @app-item @error
  Scenario: App item with platform outside enum is rejected
    When the Buyer Agent syncs a catalog with type "app" and items [{"app_id":"x","name":"X","platform":"windows"}]
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "platform"
    # BR-RULE-173: typed item field-enum validation (app platform: ios|android)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-destination @typed-catalog @destination-item @post-s1
  Scenario: Sync destination-type catalog with inline destinations
    Given the account has no catalog with catalog_id "dest-feed-001"
    When the Buyer Agent syncs a catalog with type "destination" and items [{"destination_id":"barcelona","name":"Barcelona","country":"ES","location":{"lat":41.3874,"lng":2.1686},"destination_type":"urban","price":{"amount":399,"currency":"EUR"}}]
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "dest-feed-001" has action "created"
    And the catalog result for "dest-feed-001" includes item_count
    # POST-S1: typed destination catalog upsert returns created action
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-destination-invalid-country @typed-catalog @destination-item @error
  Scenario: Destination item with non ISO-3166-1 alpha-2 country is rejected
    When the Buyer Agent syncs a catalog with type "destination" and items [{"destination_id":"x","name":"X","country":"ESP"}]
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "country"
    # BR-RULE-173: destination country must match ^[A-Z]{2}$
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-education @typed-catalog @education-item @post-s1
  Scenario: Sync education-type catalog with inline programs
    Given the account has no catalog with catalog_id "edu-feed-001"
    When the Buyer Agent syncs a catalog with type "education" and items [{"program_id":"uva-msc-cs-2025","name":"MSc Computer Science","school":"University of Amsterdam","degree_type":"master","modality":"in_person","price":{"amount":2314,"currency":"EUR","period":"year"}}]
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "edu-feed-001" has action "created"
    And the catalog result for "edu-feed-001" includes item_count
    # POST-S1: typed education catalog upsert returns created action
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-education-invalid-degree @typed-catalog @education-item @error
  Scenario: Education item with degree_type outside enum is rejected
    When the Buyer Agent syncs a catalog with type "education" and items [{"program_id":"x","name":"X","school":"Y","degree_type":"phd"}]
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "degree_type"
    # BR-RULE-173: education degree_type enum (certificate|associate|bachelor|master|doctorate|professional|bootcamp)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-flight @typed-catalog @flight-item @post-s1
  Scenario: Sync flight-type catalog with inline routes
    Given the account has no catalog with catalog_id "flight-feed-001"
    When the Buyer Agent syncs a catalog with type "flight" and items [{"flight_id":"ams-jfk-summer","origin":{"airport_code":"AMS","city":"Amsterdam"},"destination":{"airport_code":"JFK","city":"New York"},"airline":"KLM","price":{"amount":449,"currency":"EUR"}}]
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "flight-feed-001" has action "created"
    And the catalog result for "flight-feed-001" includes item_count
    # POST-S1: typed flight catalog upsert returns created action
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-flight-invalid-airport @typed-catalog @flight-item @error
  Scenario: Flight item with non-IATA airport_code is rejected
    When the Buyer Agent syncs a catalog with type "flight" and items [{"flight_id":"x","origin":{"airport_code":"AMST"},"destination":{"airport_code":"JFK"}}]
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "airport_code"
    # BR-RULE-173: flight airport_code must match IATA pattern ^[A-Z]{3}$
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-hotel @typed-catalog @hotel-item @post-s1
  Scenario: Sync hotel-type catalog with inline properties
    Given the account has no catalog with catalog_id "hotel-feed-001"
    When the Buyer Agent syncs a catalog with type "hotel" and items [{"hotel_id":"grand-amsterdam","name":"Grand Hotel Amsterdam","location":{"lat":52.3676,"lng":4.9041},"star_rating":5,"price":{"amount":289,"currency":"EUR","period":"night"},"check_in_time":"15:00","check_out_time":"11:00"}]
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "hotel-feed-001" has action "created"
    And the catalog result for "hotel-feed-001" includes item_count
    # POST-S1: typed hotel catalog upsert returns created action
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-hotel-invalid-star-rating @typed-catalog @hotel-item @error
  Scenario: Hotel item with star_rating outside 1-5 range is rejected
    When the Buyer Agent syncs a catalog with type "hotel" and items [{"hotel_id":"x","name":"X","location":{"lat":0,"lng":0},"star_rating":6}]
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "star_rating"
    # BR-RULE-173: hotel star_rating bounded integer 1-5
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-job @typed-catalog @job-item @post-s1
  Scenario: Sync job-type catalog with inline postings
    Given the account has no catalog with catalog_id "job-feed-001"
    When the Buyer Agent syncs a catalog with type "job" and items [{"job_id":"eng-sr-2025-042","title":"Senior Software Engineer","company_name":"Acme Corp","description":"Lead our platform team.","location":"Amsterdam, NL","employment_type":"full_time","experience_level":"senior","salary":{"min":80000,"max":110000,"currency":"EUR","period":"year"}}]
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "job-feed-001" has action "created"
    And the catalog result for "job-feed-001" includes item_count
    # POST-S1: typed job catalog upsert returns created action
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-job-invalid-employment-type @typed-catalog @job-item @error
  Scenario: Job item with employment_type outside enum is rejected
    When the Buyer Agent syncs a catalog with type "job" and items [{"job_id":"x","title":"X","company_name":"Y","description":"Z","employment_type":"volunteer"}]
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "employment_type"
    # BR-RULE-173: job employment_type enum (full_time|part_time|contract|temporary|internship|freelance)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-real-estate @typed-catalog @real-estate-item @post-s1
  Scenario: Sync real-estate-type catalog with inline listings
    Given the account has no catalog with catalog_id "re-feed-001"
    When the Buyer Agent syncs a catalog with type "real_estate" and items [{"listing_id":"ams-jordaan-3br","title":"Spacious 3BR Apartment in Jordaan","address":{"city":"Amsterdam","country":"NL"},"price":{"amount":650000,"currency":"EUR"},"property_type":"apartment","listing_type":"for_sale","bedrooms":3,"bathrooms":1,"area":{"value":95,"unit":"sqm"}}]
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "re-feed-001" has action "created"
    And the catalog result for "re-feed-001" includes item_count
    # POST-S1: typed real-estate catalog upsert returns created action
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-real-estate-invalid-listing-type @typed-catalog @real-estate-item @error
  Scenario: Real estate item with listing_type outside enum is rejected
    When the Buyer Agent syncs a catalog with type "real_estate" and items [{"listing_id":"x","title":"X","address":{"city":"Y"},"listing_type":"for_lease"}]
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "listing_type"
    # BR-RULE-173: real_estate listing_type enum (for_sale|for_rent)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-store @typed-catalog @store-item @post-s1
  Scenario: Sync store-type catalog with inline locations and catchments
    Given the account has no catalog with catalog_id "store-feed-001"
    When the Buyer Agent syncs a catalog with type "store" and items [{"store_id":"amsterdam-flagship","name":"Amsterdam Flagship","location":{"lat":52.3676,"lng":4.9041},"catchments":[{"catchment_id":"walk","travel_time":{"value":10,"unit":"min"},"transport_mode":"walking"},{"catchment_id":"drive","travel_time":{"value":15,"unit":"min"},"transport_mode":"driving"}]}]
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "store-feed-001" has action "created"
    And the catalog result for "store-feed-001" includes item_count
    # POST-S1: typed store catalog upsert returns created action
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-store-radius-catchment @typed-catalog @store-item @post-s1
  Scenario: Sync store catalog with radius-based catchment
    Given the account has no catalog with catalog_id "store-feed-radius"
    When the Buyer Agent syncs a catalog with type "store" and items [{"store_id":"warehouse-east","name":"East Warehouse","location":{"lat":52.2942,"lng":4.9581},"catchments":[{"catchment_id":"local","radius":{"value":10,"unit":"km"}}]}]
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "store-feed-radius" has action "created"
    # Catchment radius mode (catchment.json oneOf branch 2)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-store-geojson-catchment @typed-catalog @store-item @post-s1
  Scenario: Sync store catalog with pre-computed GeoJSON catchment
    Given the account has no catalog with catalog_id "store-feed-geojson"
    When the Buyer Agent syncs a catalog with type "store" and items [{"store_id":"brooklyn-heights","name":"Brooklyn Heights","location":{"lat":40.6960,"lng":-73.9936},"catchments":[{"catchment_id":"trade-area","geometry":{"type":"Polygon","coordinates":[[[-74.01,40.68],[-73.97,40.68],[-73.97,40.71],[-74.01,40.71],[-74.01,40.68]]]}}]}]
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "store-feed-geojson" has action "created"
    # Catchment GeoJSON mode (catchment.json oneOf branch 3)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-store-catchment-conflict @typed-catalog @store-item @error
  Scenario: Store catchment with both radius and travel_time is rejected
    When the Buyer Agent syncs a catalog with type "store" and items [{"store_id":"x","name":"X","location":{"lat":0,"lng":0},"catchments":[{"catchment_id":"bad","radius":{"value":5,"unit":"km"},"travel_time":{"value":10,"unit":"min"},"transport_mode":"walking"}]}]
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "catchment"
    # BR-RULE-173: catchment.json oneOf -- exactly one of travel_time+transport_mode, radius, geometry
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-vehicle @typed-catalog @vehicle-item @post-s1
  Scenario: Sync vehicle-type catalog with inline inventory
    Given the account has no catalog with catalog_id "vehicle-feed-001"
    When the Buyer Agent syncs a catalog with type "vehicle" and items [{"vehicle_id":"dlr-2024-civic-001","title":"2024 Honda Civic EX Sedan","make":"Honda","model":"Civic","year":2024,"trim":"EX","price":{"amount":28500,"currency":"USD"},"condition":"new","body_style":"sedan","transmission":"cvt","fuel_type":"gasoline"}]
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the catalog result for "vehicle-feed-001" has action "created"
    And the catalog result for "vehicle-feed-001" includes item_count
    # POST-S1: typed vehicle catalog upsert returns created action
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-vehicle-invalid-condition @typed-catalog @vehicle-item @error
  Scenario: Vehicle item with condition outside enum is rejected
    When the Buyer Agent syncs a catalog with type "vehicle" and items [{"vehicle_id":"x","title":"X","make":"Y","model":"Z","year":2024,"condition":"refurbished"}]
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "condition"
    # BR-RULE-173: vehicle condition enum (new|used|certified_pre_owned)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-price-invalid-currency @typed-catalog @price @error
  Scenario: Typed item with non ISO-4217 currency on price is rejected
    When the Buyer Agent syncs a catalog with type "hotel" and items [{"hotel_id":"x","name":"X","location":{"lat":0,"lng":0},"price":{"amount":100,"currency":"euros"}}]
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "currency"
    # BR-RULE-173: price.currency must match ^[A-Z]{3}$ across all typed items (S36)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-price-negative-amount @typed-catalog @price @error
  Scenario: Typed item with negative price amount is rejected
    When the Buyer Agent syncs a catalog with type "vehicle" and items [{"vehicle_id":"x","title":"X","make":"Y","model":"Z","year":2024,"price":{"amount":-100,"currency":"USD"}}]
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    And the suggestion should contain "amount"
    # BR-RULE-173: price.amount minimum 0 (S36)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-catalogs-request.json

  @T-UC-023-type-mixed-verticals @typed-catalog @post-s1
  Scenario: Sync multiple typed catalogs of different verticals in one request
    Given the account has no catalog with catalog_id "hotel-mix"
    And the account has no catalog with catalog_id "flight-mix"
    And the account has no catalog with catalog_id "vehicle-mix"
    When the Buyer Agent syncs catalogs of mixed types:
    | catalog_id    | type    |
    | hotel-mix     | hotel   |
    | flight-mix    | flight  |
    | vehicle-mix   | vehicle |
    Then the response is a SyncCatalogsSuccess with a catalogs array
    And the response contains 3 catalog results
    And every catalog result includes action from enum [created, updated, unchanged, failed, deleted]
    # POST-S1: independent typed catalogs processed together
    # BR-RULE-177: catalogs array variant retains per-catalog action enum across types

  @T-UC-023-boundary-typed-item @boundary @typed-catalog
  Scenario Outline: Typed catalog item boundary validation -- <boundary_point>
    When the Buyer Agent syncs a catalog with a typed item where <input>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                              | input                                                       | outcome                                                  |
      | price.amount = 0 (minimum)                  | price.amount is 0 with currency "USD"                       | catalog accepted                                         |
      | price.amount = -0.01                        | price.amount is -0.01 with currency "USD"                   | error "INVALID_REQUEST" with suggestion                  |
      | currency = 'USD'                            | price.currency is "USD" (3 uppercase, ISO 4217)             | catalog accepted                                         |
      | currency = 'usd' (lowercase)                | price.currency is "usd"                                     | error "INVALID_REQUEST" with suggestion                  |
      | vehicle.year = 1900 (minimum)               | vehicle.year is 1900                                        | catalog accepted                                         |
      | vehicle.year = 1899                         | vehicle.year is 1899                                        | error "INVALID_REQUEST" with suggestion                  |
      | flight airport_code = 'AMS' (3 uppercase)   | flight origin airport_code is "AMS"                         | catalog accepted                                         |
      | flight airport_code = 'ams' (lowercase)     | flight origin airport_code is "ams"                         | error "INVALID_REQUEST" with suggestion                  |
      | catchment with exactly one method (radius)  | store catchment supplies only a radius                      | catalog accepted                                         |
      | catchment with two methods                  | store catchment supplies both radius and geometry           | error "INVALID_REQUEST" with suggestion                  |
      | catchment with zero methods                 | store catchment supplies none of travel_time/radius/geometry | error "INVALID_REQUEST" with suggestion                 |
      | travel_time present, transport_mode absent  | store catchment supplies travel_time without transport_mode | error "INVALID_REQUEST" with suggestion                  |

  @T-UC-023-boundary-idempotency-replay @boundary @idempotency
  Scenario Outline: Idempotency replay boundary validation -- <boundary_point>
    Given <setup>
    When the Buyer Agent sends a sync_catalogs request with that idempotency_key
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                                                | setup                                                                    | outcome                                                                  |
      | key absent (field omitted)                                                    | the request omits the required idempotency_key                           | error "INVALID_REQUEST" with suggestion                                  |
      | key present, no prior record for (seller, account, key)                       | no prior record exists for this (seller, account, key)                   | request processed normally as a first execution                          |
      | key present, prior record exists, payload byte-identical                      | a prior completed sync exists with a byte-identical canonical payload     | the cached response is returned unchanged                                |
      | sync_catalogs: key present, prior feed sync exists, payload identical (retry after timeout) | a prior feed sync exists with an identical payload, retried after a timeout | prior sync outcome returned, no audit events re-emitted, no platform review re-triggered |
      | key present, prior record exists, payload has one field changed               | a prior record exists but one payload field differs                       | error "IDEMPOTENCY_CONFLICT" with suggestion                             |
      | key present, prior record exists, payload has all fields changed              | a prior record exists but every payload field differs                     | error "IDEMPOTENCY_CONFLICT" with suggestion                             |
      | key present, prior request still in flight (not yet committed)                | the first request under this key is still being processed                 | error "IDEMPOTENCY_IN_FLIGHT" with suggestion                            |
      | key present, prior record exists, replay arrives exactly at replay_ttl_seconds boundary | a prior record exists and the replay arrives exactly at the replay_ttl_seconds boundary | error "IDEMPOTENCY_EXPIRED" with suggestion                  |
