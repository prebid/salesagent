# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

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
  #   POST-F1: System state is unchanged on complete operation failure
  #   POST-F2: Buyer knows what failed and the specific error code with recovery classification
  #   POST-F3: Application context is still echoed when possible
  #   POST-F4: Individual catalog failures do not prevent other catalogs from being processed (partial success)
  #
  # Rules: BR-RULE-043 (context echo), BR-RULE-132 (capability gate), BR-RULE-172 (upsert semantics),
  #   BR-RULE-173 (catalog validation), BR-RULE-174 (delete missing), BR-RULE-175 (feed management),
  #   BR-RULE-176 (selectors/attribution), BR-RULE-177 (response structure), BR-RULE-178 (async lifecycle)
  # Extensions: A (discovery), B (dry run), C (delete missing), D (ACCOUNT_NOT_FOUND),
  #   E (INVALID_REQUEST), F (UNSUPPORTED_FEATURE), G (AUTH_REQUIRED), H (RATE_LIMITED),
  #   I (SERVICE_UNAVAILABLE), J (async input required)
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
    # POST-S8: Buyer receives submitted acknowledgment
    # BR-RULE-178 INV-1: sync operation queued

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

