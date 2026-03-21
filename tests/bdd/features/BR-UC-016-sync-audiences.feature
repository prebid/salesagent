# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-016 Sync Audiences
  As a Buyer
  I want to manage first-party CRM audiences on a seller account
  So that I can upload, update, discover, and delete audience segments for targeting campaigns

  # Postconditions verified:
  #   POST-S1: Buyer has synced audiences and received per-audience action results
  #   POST-S2: Buyer can discover all audiences on an account without modification
  #   POST-S3: Buyer knows the matching status and seller_id for each audience
  #   POST-S4: Buyer knows uploaded and matched member counts
  #   POST-S5: Buyer has deleted a specific audience and received confirmation
  #   POST-S6: Buyer has purged buyer-managed audiences not in the request
  #   POST-S7: Application context from the request is echoed unchanged
  #   POST-S8: When remove and add contain the same identifier, remove takes precedence
  #   POST-F1: System state is unchanged on complete operation failure
  #   POST-F2: Buyer knows what failed and the specific error code with recovery
  #   POST-F3: Application context is still echoed when possible
  #   POST-F4: Individual audience failures do not prevent other audiences from being processed
  #
  # Rules: BR-RULE-113..132 (20 rules, ~80 invariants)
  # Extensions: A (sync/upsert), B (delete specific), C (bulk delete missing),
  #   D (ACCOUNT_NOT_FOUND), E (INVALID_REQUEST), F (AUDIENCE_TOO_SMALL),
  #   G (UNSUPPORTED_FEATURE), H (AUTH_REQUIRED), I (RATE_LIMITED), J (SERVICE_UNAVAILABLE)
  # Error codes: AUTH_REQUIRED, ACCOUNT_NOT_FOUND, INVALID_REQUEST, AUDIENCE_TOO_SMALL,
  #   UNSUPPORTED_FEATURE, RATE_LIMITED, SERVICE_UNAVAILABLE

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context
    And the Buyer is authenticated with a valid principal_id
    And the seller has declared audience_targeting capability as true
    And the account reference resolves to a valid account


  @T-UC-016-main @main-flow @discovery @post-s2 @post-s7
  Scenario Outline: Discovery-only mode via <transport> -- list all audiences on account
    Given the account has 3 synced audiences with various statuses
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends a sync_audiences request via <transport> with no audiences array
    Then the response is a SyncAudiencesSuccess with an audiences array
    And the response contains 3 audience results
    And each audience result has action "unchanged"
    And each audience result has uploaded_count 0
    And the request context is echoed in the response
    # POST-S2: Buyer discovers all audiences without modification
    # POST-S7: Application context echoed unchanged

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-016-discovery-empty @main-flow @discovery @post-s2 @boundary
  Scenario: Discovery-only mode -- empty account returns empty audiences array
    Given the account has 0 audiences
    When the Buyer Agent sends a sync_audiences request with no audiences array
    Then the response is a SyncAudiencesSuccess with an audiences array
    And the audiences array is empty
    # BR-RULE-125 INV-4: no audiences exist -> empty array returned

  @T-UC-016-discovery-mode-valid @main-flow @partition @boundary @discovery-mode
  Scenario Outline: Discovery mode valid -- <partition>
    When the Buyer Agent sends a sync_audiences request with <setup>
    Then <outcome>
    # BR-RULE-125 INV-1..4

    Examples:
      | partition                | boundary_point                              | setup                                       | outcome                                                  |
      | discovery_with_audiences | audiences omitted, account has audiences    | audiences omitted, account has 3 audiences  | all 3 audiences returned, action=unchanged each          |
      | discovery_empty_account  | audiences omitted, account has no audiences | audiences omitted, account has 0 audiences  | empty audiences array returned                           |
      | sync_mode                | audiences provided                          | audiences array provided with 1 audience    | sync processing (not discovery-only)                     |

  @T-UC-016-upsert-valid @post-s1 @post-s3 @partition @boundary @upsert-semantics
  Scenario Outline: Upsert semantics valid -- <partition>
    Given the account has audience "aud_existing" with name "Old Segment"
    When the Buyer Agent syncs audiences with <setup>
    Then the audience result for <audience_id> has action "<expected_action>"
    # POST-S1: Buyer receives per-audience action results

    Examples:
      | partition        | boundary_point                           | audience_id  | setup                                              | expected_action |
      | create_new       | new audience_id on account               | aud_new_001  | new audience_id "aud_new_001" with name and members | created         |
      | update_existing  | existing audience_id on account          | aud_existing | existing "aud_existing" with new add members        | updated         |
      | unchanged        | existing audience_id with no changes     | aud_existing | existing "aud_existing" with no changes             | unchanged       |

  @T-UC-016-upsert-invalid @ext-a @error @partition @boundary @upsert-semantics @post-f2
  Scenario Outline: Upsert semantics invalid -- <partition>
    When the Buyer Agent syncs audiences with <setup>
    Then the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-114 INV: missing audience_id

    Examples:
      | partition              | boundary_point                      | setup                                 |
      | missing_audience_id    | audience object missing audience_id | audience object without audience_id   |

  @T-UC-016-partial-success @post-f4
  Scenario: Partial success -- one audience fails, others succeed
    Given the account exists
    When the Buyer Agent syncs 3 audiences where audience "aud_bad" triggers a platform error
    Then the audience result for "aud_good_1" has action "created"
    And the audience result for "aud_good_2" has action "created"
    And the audience result for "aud_bad" has action "failed"
    And the "aud_bad" result includes per-audience errors
    # POST-F4: Individual failures do not prevent other audiences from being processed
    # BR-RULE-114 INV-5
    # --- Member Delta Semantics (BR-RULE-115) ---

  @T-UC-016-member-delta-valid @partition @boundary @member-delta
  Scenario Outline: Member delta valid -- <partition>
    Given the account has an existing audience "aud_delta"
    When the Buyer Agent syncs audience "aud_delta" with <setup>
    Then <outcome>
    # BR-RULE-115 INV-1..4

    Examples:
      | partition       | boundary_point                  | setup                                        | outcome                                              |
      | add_only        | add with 1 member               | add array with 5 members                     | audience is updated with 5 new members               |
      | remove_only     | remove with 1 member            | remove array with 2 members                  | 2 members are dropped from the audience              |
      | add_and_remove  | both add and remove provided    | add array with 3 members and remove with 1   | both operations applied atomically                   |
      | neither         | no add or remove                | only metadata name change, no add or remove   | metadata updated, action is "updated"                |

  @T-UC-016-member-delta-invalid @ext-a @error @partition @boundary @member-delta @post-f2
  Scenario Outline: Member delta invalid -- <partition>
    Given the account has an existing audience "aud_delta"
    When the Buyer Agent syncs audience "aud_delta" with <setup>
    Then the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-115 INV-5
    # --- Remove Precedence (BR-RULE-116) ---

    Examples:
      | partition       | boundary_point                   | setup                                        |
      | empty_add       | add present but empty array      | add array present but empty                  |
      | empty_remove    | remove present but empty array   | remove array present but empty               |

  @T-UC-016-remove-precedence @post-s8 @partition @boundary @remove-precedence
  Scenario Outline: Remove precedence over add -- <partition>
    Given the account has an existing audience "aud_conflict"
    When the Buyer Agent syncs audience "aud_conflict" with <setup>
    Then <outcome>
    # POST-S8: Remove takes precedence over add for same identifier
    # BR-RULE-116 INV-1..3
    # --- Member Identity Requirements (BR-RULE-117) ---

    Examples:
      | partition             | boundary_point                              | setup                                                                    | outcome                                                |
      | no_overlap            | disjoint add and remove sets                | add member "u1" and remove member "u2" (disjoint sets)                   | both operations applied normally                       |
      | overlap_remove_wins   | same external_id in both add and remove     | add member "u1" and remove member "u1" (same external_id in both)        | member "u1" is removed, not added                      |

  @T-UC-016-member-identity-valid @partition @boundary @member-identity
  Scenario Outline: Member identity valid -- <partition>
    When the Buyer Agent syncs an audience with a member having <setup>
    Then <outcome>
    # BR-RULE-117 INV-1,4

    Examples:
      | partition                | boundary_point                             | setup                                                    | outcome                           |
      | email_only               | external_id + one matchable identifier     | external_id "crm_001" and hashed_email                  | member accepted for processing    |
      | phone_only               | external_id + one matchable identifier     | external_id "crm_001" and hashed_phone                  | member accepted for processing    |
      | uid_only                 | external_id + one matchable identifier     | external_id "crm_001" and uids array with uid2           | member accepted for processing    |
      | multiple_identifiers     | external_id + all matchable identifiers    | external_id "crm_001" with hashed_email and hashed_phone | member accepted for processing    |

  @T-UC-016-member-identity-invalid @ext-a @error @partition @boundary @member-identity @post-f2
  Scenario Outline: Member identity invalid -- <partition>
    When the Buyer Agent syncs an audience with a member having <setup>
    Then the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-117 INV-2,3
    # --- Hashed Identifier Format (BR-RULE-118) ---

    Examples:
      | partition                | boundary_point                             | setup                                                    |
      | missing_external_id      | missing external_id                        | hashed_email only, no external_id                       |
      | no_matchable_identifier  | external_id but no matchable identifiers   | external_id "crm_001" only, no email/phone/uids         |

  @T-UC-016-hashed-id-valid @partition @boundary @hashed-identifier
  Scenario Outline: Hashed identifier valid -- <partition>
    When the Buyer Agent syncs an audience with a member having hashed_email "<hash_value>"
    Then the identifier is accepted for platform matching
    # BR-RULE-118 INV-1,2

    Examples:
      | partition              | boundary_point                      | hash_value                                                        |
      | valid_sha256_email     | exactly 64 lowercase hex characters | a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2 |
      | valid_sha256_phone     | exactly 64 lowercase hex characters | f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5 |

  @T-UC-016-hashed-id-invalid @ext-a @error @partition @boundary @hashed-identifier @post-f2
  Scenario Outline: Hashed identifier invalid -- <partition>
    When the Buyer Agent syncs an audience with a member having hashed_email "<hash_value>"
    Then the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-118 INV-3,4

    Examples:
      | partition              | boundary_point                      | hash_value                                                        |
      | wrong_length           | 63 characters                       | a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b |
      | uppercase_hex          | 64 characters with uppercase        | A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2 |
      | non_hex_chars          | 64 characters with non-hex          | g1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2 |
      | unhashed_email         | plaintext email                     | user@example.com                                                  |

  @T-UC-016-hashed-id-boundary-65 @ext-a @error @boundary @hashed-identifier @post-f2
  Scenario: Hashed identifier boundary -- 65 characters rejected
    When the Buyer Agent syncs an audience with a member having hashed_email "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2f"
    Then the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-118: 65 characters boundary
    # --- Audience Type Classification (BR-RULE-119) ---

  @partition @boundary @audience-type
  Scenario Outline: Audience type valid -- <partition>
    When the Buyer Agent syncs an audience with audience_type <type_value>
    Then <outcome>
    # BR-RULE-119 INV-1..4

    Examples:
      | partition        | boundary_point   | type_value       | outcome                                              |
      | crm              | crm              | "crm"            | audience used for positive targeting                 |
      | suppression      | suppression      | "suppression"    | audience used for negative targeting (exclusion)     |
      | lookalike_seed   | lookalike_seed   | "lookalike_seed" | audience used as seed for lookalike modeling          |
      | omitted          | field omitted    | (absent)         | seller applies default handling                      |

  @T-UC-016-audience-type-invalid @ext-a @error @partition @boundary @audience-type @post-f2
  Scenario Outline: Audience type invalid -- <partition>
    When the Buyer Agent syncs an audience with audience_type <type_value>
    Then the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-119 INV-5
    # --- Audience Tags (BR-RULE-120) ---

    Examples:
      | partition        | boundary_point       | type_value      |
      | invalid_enum     | unknown enum value   | "retargeting"   |

  @T-UC-016-audience-tags-valid @partition @boundary @audience-tags
  Scenario Outline: Audience tags valid -- <partition>
    When the Buyer Agent syncs an audience with tags <tags_value>
    Then <outcome>
    # BR-RULE-120 INV-1,2

    Examples:
      | partition        | boundary_point        | tags_value                              | outcome                         |
      | single_tag       | one valid tag         | ["holiday_2026"]                        | tags stored by seller            |
      | multiple_tags    | multiple unique tags  | ["holiday_2026", "high_ltv", "us_east"] | tags stored by seller            |
      | omitted          | tags omitted          | (absent)                                | no tags stored; previous unaffected |

  @T-UC-016-audience-tags-invalid @ext-a @error @partition @boundary @audience-tags @post-f2
  Scenario Outline: Audience tags invalid -- <partition>
    When the Buyer Agent syncs an audience with tags <tags_value>
    Then the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-120 INV-3,4
    # --- Consent Basis (BR-RULE-121) ---

    Examples:
      | partition          | boundary_point   | tags_value                |
      | empty_string_tag   | empty string tag | [""]                      |
      | duplicate_tags     | duplicate tags   | ["high_ltv", "high_ltv"]  |

  @T-UC-016-consent-basis-valid @partition @boundary @consent-basis
  Scenario Outline: Consent basis valid -- <partition>
    When the Buyer Agent syncs an audience with consent_basis <consent_value>
    Then <outcome>
    # BR-RULE-121 INV-1..3: informational, not validated

    Examples:
      | partition              | boundary_point   | consent_value          | outcome                                |
      | consent                | valid enum value | "consent"              | value stored, does not affect processing |
      | legitimate_interest    | valid enum value | "legitimate_interest"  | value stored, does not affect processing |
      | contract               | valid enum value | "contract"             | value stored, does not affect processing |
      | legal_obligation       | valid enum value | "legal_obligation"     | value stored, does not affect processing |
      | omitted                | field omitted    | (absent)               | buyer asserts implicit basis             |

  @T-UC-016-consent-basis-invalid @ext-a @error @partition @boundary @consent-basis @post-f2
  Scenario Outline: Consent basis invalid -- <partition>
    When the Buyer Agent syncs an audience with consent_basis <consent_value>
    Then the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-121 INV-4
    # --- Status, Matched Count, Minimum Size (BR-RULE-129, 130, 131) ---

    Examples:
      | partition              | boundary_point       | consent_value   |
      | invalid_enum           | unknown enum value   | "performance"   |

  @T-UC-016-status-presence @post-s3 @post-s4 @partition @boundary @status-presence
  Scenario Outline: Status presence and conditional fields -- <partition>
    Given the account has an audience that was synced with result action "<action>" and matching status "<status>"
    Then <outcome>
    # BR-RULE-129 INV-1..5, BR-RULE-130, BR-RULE-131
    # POST-S3: Buyer knows matching status
    # POST-S4: Buyer knows uploaded and matched counts

    Examples:
      | partition                      | boundary_point                         | action    | status     | outcome                                                              |
      | processing_with_sync_action    | action=created with status=ready       | created   | processing | status is present; matched_count may not be populated                |
      | ready_with_sync_action         | action=created with status=ready       | updated   | ready      | status is present; matched_count is populated (integer >= 0)         |
      | too_small_with_sync_action     | action=created with status=ready       | created   | too_small  | status is present; minimum_size is populated (integer >= 1)          |
      | absent_for_deleted             | action=deleted with no status          | deleted   | (absent)   | no status field; no matched_count or minimum_size                    |
      | absent_for_failed              | action=failed with no status           | failed    | (absent)   | no status field; errors field present instead                        |

  @T-UC-016-status-boundary @boundary @status-presence
  Scenario Outline: Status and count boundaries -- <boundary_point>
    Given an audience sync result with the given conditions
    Then <outcome>
    # --- Matched Count (BR-RULE-130) ---

    Examples:
      | boundary_point                         | outcome                                                      |
      | status=ready, matched_count=0          | valid: audience exists but zero reach                        |
      | status=ready, matched_count=100000     | valid: high match count                                      |
      | status=processing                      | matched_count may not be populated                           |
      | status=too_small, minimum_size=1       | minimum boundary for minimum_size                            |
      | status=too_small, minimum_size=1000    | typical platform minimum                                     |
      | status=ready                           | minimum_size not expected                                    |

  @T-UC-016-matched-count @partition @matched-count
  Scenario Outline: Matched count population -- <partition>
    Given an audience sync result with status "<status>"
    Then <outcome>
    # BR-RULE-130 INV-1..4
    # --- Minimum Size (BR-RULE-131) ---

    Examples:
      | partition             | status     | outcome                                                        |
      | ready_with_count      | ready      | matched_count is populated (integer >= 0, cumulative)          |
      | processing_no_count   | processing | matched_count may not be populated (matching in progress)      |
      | too_small_no_count    | too_small  | matched_count may or may not be present                        |

  @T-UC-016-minimum-size @partition @minimum-size
  Scenario Outline: Minimum size population -- <partition>
    Given an audience sync result with status "<status>"
    Then <outcome>
    # BR-RULE-131 INV-1..3
    # --- Member Count Recommendation (BR-RULE-126) ---

    Examples:
      | partition               | status     | outcome                                                          |
      | too_small_with_minimum  | too_small  | minimum_size is populated (integer >= 1)                         |
      | ready_no_minimum        | ready      | minimum_size not expected (audience meets threshold)             |

  @T-UC-016-member-count @partition @boundary @member-count
  Scenario Outline: Member count recommendation -- <partition>
    When the Buyer Agent syncs an audience with <member_count> members in the add array
    Then <outcome>
    # BR-RULE-126 INV-1..3: advisory 100,000 limit
    # --- Response Structure (BR-RULE-127) ---

    Examples:
      | partition      | boundary_point      | member_count | outcome                                                    |
      | under_limit    | 1 member            | 50000        | request processed normally                                 |
      | at_limit       | 100,000 members     | 100000       | request processed normally                                 |
      | over_limit     | 100,001 members     | 200000       | request processed (protocol recommends against but accepts) |

  @T-UC-016-response-exclusivity-valid @partition @boundary @response-exclusivity
  Scenario Outline: Response structure exclusivity valid -- <partition>
    When a sync_audiences operation completes with <operation_result>
    Then the response <response_check>
    # BR-RULE-127 INV-1,2: oneOf success XOR error

    Examples:
      | partition   | boundary_point                  | operation_result       | response_check                                          |
      | success     | audiences present, no errors    | at least partial success | has audiences array and no top-level errors field       |
      | error       | errors present, no audiences    | complete failure         | has errors array and no audiences or sandbox fields     |

  @T-UC-016-response-exclusivity-invalid @ext-a @error @partition @boundary @response-exclusivity @post-f2
  Scenario Outline: Response structure exclusivity invalid -- <partition>
    When a sync_audiences response has <response_shape>
    Then the response should be invalid (violates oneOf constraint)
    And the error should include "suggestion" field
    # BR-RULE-127 INV-3,4
    # --- Action Vocabulary (BR-RULE-128) ---

    Examples:
      | partition         | boundary_point                  | response_shape             |
      | both_present      | both audiences and errors       | both arrays present        |
      | neither_present   | neither audiences nor errors    | neither array present      |

  @T-UC-016-action-vocab-valid @partition @boundary @action-vocabulary
  Scenario Outline: Per-audience action vocabulary valid -- <partition>
    Given a sync operation produces a result with action "<action>"
    Then <outcome>
    # BR-RULE-128 INV-1..3: required action field

    Examples:
      | partition   | boundary_point           | action    | outcome                                                    |
      | created     | each valid enum value    | created   | status may be present; errors absent                       |
      | updated     | each valid enum value    | updated   | status may be present; errors absent                       |
      | unchanged   | each valid enum value    | unchanged | status may be present; errors absent                       |
      | deleted     | each valid enum value    | deleted   | status absent; errors absent                               |
      | failed      | each valid enum value    | failed    | errors present with per-audience error details             |

  @T-UC-016-action-vocab-invalid @ext-a @error @partition @boundary @action-vocabulary @post-f2
  Scenario Outline: Per-audience action vocabulary invalid -- <partition>
    When an audience result has action <action>
    Then the result should be invalid
    And the error should include "suggestion" field
    # BR-RULE-128 INV-4

    Examples:
      | partition       | boundary_point       | action     |
      | missing_action  | action field missing | (absent)   |
      | invalid_enum    | unknown action value | "removed"  |

  @T-UC-016-delete-valid @ext-b @post-s5 @post-s7 @partition @boundary @delete-semantics
  Scenario Outline: Delete specific audience -- <partition>
    Given the account has audiences including "aud_target"
    When the Buyer Agent syncs with <setup>
    Then <outcome>
    # POST-S5: Buyer deletes specific audience
    # BR-RULE-122 INV-1..4

    Examples:
      | partition          | boundary_point                     | setup                                                     | outcome                                              |
      | delete_true        | delete=true, audience exists       | audience "aud_target" with delete=true                    | audience result action=deleted, no status field      |
      | delete_false       | delete=false                       | audience "aud_target" with delete=false                   | normal sync processing (create/update)               |
      | delete_not_found   | delete=true, audience not found    | audience "aud_nonexistent" with delete=true               | audience result action=failed with error             |

  @T-UC-016-delete-boundary @ext-b @boundary @delete-semantics
  Scenario: Delete semantics boundary -- delete omitted defaults to false
    Given the account has audience "aud_1"
    When the Buyer Agent syncs audience "aud_1" without the delete field
    Then normal sync processing occurs (not deletion)
    # BR-RULE-122 INV-4: delete omitted = normal sync

  @T-UC-016-delete-ignores-fields @ext-b @invariant @br-rule-122
  Scenario: Delete ignores other fields -- delete=true with name, add, and tags present
    Given the account has audience "aud_deleteme"
    When the Buyer Agent syncs audience "aud_deleteme" with delete=true and name "New Name" and add members and tags ["test"]
    Then the audience result has action "deleted"
    And the name, add, and tags fields were ignored
    And the audience is removed from the account
    # BR-RULE-122 INV-2: Other fields ignored when delete=true

  @T-UC-016-delete-mixed @ext-b @happy-path @post-f4
  Scenario: Delete combined with sync -- delete and create in same request
    When the Buyer Agent syncs with audience "aud_old" delete=true and audience "aud_new" with members
    Then the audience result for "aud_old" has action "deleted"
    And the audience result for "aud_new" has action "created"
    # BR-RULE-122: delete and sync in same batch
    # POST-F4: Independent processing

  @T-UC-016-delete-missing-cond-valid @partition @boundary @delete-missing-conditional
  Scenario Outline: Delete missing conditional valid -- <partition>
    When the Buyer Agent sends a sync_audiences request with <setup>
    Then <outcome>
    # BR-RULE-123 INV-1,3

    Examples:
      | partition                        | boundary_point                              | setup                                                        | outcome                                         |
      | delete_missing_with_audiences    | delete_missing=true with audiences present  | delete_missing=true and audiences array with "aud_keep"      | accepted; missing buyer-managed audiences purged |
      | delete_missing_false             | delete_missing=false without audiences      | delete_missing=false, no audiences                           | discovery-only mode                              |
      | discovery_only                   | delete_missing omitted without audiences    | no delete_missing, no audiences                              | discovery-only mode                              |

  @T-UC-016-delete-missing-cond-invalid @ext-c @error @partition @boundary @delete-missing-conditional @post-f2
  Scenario Outline: Delete missing conditional invalid -- <partition>
    When the Buyer Agent sends a sync_audiences request with <setup>
    Then the error code should be "INVALID_REQUEST"
    And the error should include "suggestion" field
    # BR-RULE-123 INV-2: safety guardrail

    Examples:
      | partition                        | boundary_point                              | setup                                             |
      | delete_missing_no_audiences      | delete_missing=true without audiences       | delete_missing=true but audiences array omitted   |

  @T-UC-016-delete-missing-scope @post-s6 @partition @boundary @delete-missing-scope
  Scenario Outline: Delete missing scope -- <partition>
    Given the account has buyer-managed audiences "aud_buyer_1", "aud_buyer_2" and seller-managed audience "platform_seg_1"
    When the Buyer Agent sends sync_audiences with delete_missing=true and audiences array containing only "aud_buyer_1"
    Then <outcome>
    # POST-S6: Buyer-managed audiences not in request are purged
    # BR-RULE-124 INV-1..3

    Examples:
      | partition              | boundary_point                                                    | outcome                                                         |
      | buyer_managed_purged   | delete_missing=true, buyer-managed audience absent from request   | "aud_buyer_2" is deleted (buyer-managed, not in request)        |
      | seller_managed_safe    | delete_missing=true, seller-managed audience absent from request  | "platform_seg_1" is preserved (seller-managed, never affected)  |
      | buyer_managed_kept     | delete_missing=true, buyer-managed audience present in request    | "aud_buyer_1" is processed normally (in request, not purged)    |

  @T-UC-016-cap-gate-valid @partition @boundary @cap-gate
  Scenario: Capability gate valid -- capability_true boundary capability=true
    Given the seller's capabilities response has audience_targeting set to true
    When the Buyer Agent sends a sync_audiences request
    Then the request proceeds to audience processing
    # BR-RULE-132 INV-1: capability=true -> task available

  @T-UC-016-cap-gate-invalid @ext-g @error @partition @boundary @cap-gate @post-f2
  Scenario Outline: Capability gate invalid -- <partition>
    Given the seller's capabilities response has audience_targeting set to <capability>
    When the Buyer Agent sends a sync_audiences request
    Then the error code should be "UNSUPPORTED_FEATURE"
    And the error should include "suggestion" field
    And the suggestion should contain "get_adcp_capabilities"
    # BR-RULE-132 INV-2,3

    Examples:
      | partition           | boundary_point     | capability |
      | capability_false    | capability=false   | false      |
      | capability_absent   | capability absent  | (absent)   |

  @T-UC-016-ext-d @extension @ext-d @error @post-f1 @post-f2 @post-f3
  Scenario Outline: ACCOUNT_NOT_FOUND via <transport>
    Given the account reference does not match any account on the seller platform
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends a sync_audiences request
    Then the operation should fail
    And the error code should be "ACCOUNT_NOT_FOUND"
    And the error recovery should be "terminal"
    And the error should include "suggestion" field
    And the suggestion should contain "verify account"
    And the request context is echoed in the error response
    # POST-F1: System state unchanged
    # POST-F2: Error code with terminal recovery
    # POST-F3: Context echoed
    # --- Extension E: INVALID_REQUEST ---

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-016-ext-e @extension @ext-e @error @post-f1 @post-f2 @post-f3
  Scenario Outline: INVALID_REQUEST via <transport> -- <trigger>
    Given the Buyer Agent has an authenticated connection
    When the Buyer Agent sends a sync_audiences request via <transport> with <invalid_input>
    Then the operation should fail
    And the error code should be "INVALID_REQUEST"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the error should include "field" path if applicable
    And the request context is echoed in the error response
    # POST-F1: System state unchanged
    # POST-F2: Error with correctable recovery
    # POST-F3: Context echoed
    # --- Extension F: AUDIENCE_TOO_SMALL ---

    Examples:
      | transport | trigger                         | invalid_input                                    |
      | MCP       | missing account field           | no account reference                             |
      | REST      | missing account field           | no account reference                             |
      | MCP       | empty audiences array           | audiences array present but empty (minItems: 1)  |
      | MCP       | member missing external_id      | member with hashed_email but no external_id      |
      | MCP       | member no matchable identifier  | member with external_id only                     |
      | MCP       | invalid hashed_email format     | hashed_email with 32 chars instead of 64         |
      | MCP       | delete_missing=true no audiences | delete_missing=true without audiences array      |

  @T-UC-016-ext-f @extension @ext-f @post-s3 @post-f4
  Scenario: AUDIENCE_TOO_SMALL -- per-audience status, not operation-level error
    When the Buyer Agent syncs an audience with 10 members on a platform requiring minimum 1000
    Then the audience result has action "created" or "updated"
    And the audience status is "too_small"
    And the audience result includes minimum_size of 1000
    And the error should include "suggestion" field
    And other audiences in the same request are unaffected
    And the request context is echoed in the response
    # POST-S3: Buyer knows status=too_small and the minimum_size threshold
    # POST-F4: Other audiences unaffected
    # --- Extension G: UNSUPPORTED_FEATURE ---

  @T-UC-016-ext-g-error @extension @ext-g @error @post-f1 @post-f2 @post-f3
  Scenario: UNSUPPORTED_FEATURE -- audience_targeting not declared
    Given the seller has NOT declared audience_targeting capability
    When the Buyer Agent sends a sync_audiences request
    Then the operation should fail
    And the error code should be "UNSUPPORTED_FEATURE"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "get_adcp_capabilities"
    And the request context is echoed in the error response
    # POST-F1: System state unchanged
    # POST-F2: Error explains feature not supported
    # POST-F3: Context echoed with suggestion
    # --- Extension H: AUTH_REQUIRED ---

  @T-UC-016-ext-h-valid @partition @boundary @auth
  Scenario: Authentication valid -- valid_token (valid token present)
    Given the Buyer Agent sends a sync_audiences request with a valid non-expired bearer token
    When the system validates authentication
    Then the request proceeds to account resolution
    # BR-RULE-113 INV-1: valid token present

  @T-UC-016-ext-h @extension @ext-h @error @post-f1 @post-f2 @post-f3 @partition @boundary @auth
  Scenario Outline: AUTH_REQUIRED -- <partition>
    Given the Buyer Agent sends a sync_audiences request with <auth_setup>
    When the system validates authentication
    Then the operation should fail
    And the error code should be "AUTH_REQUIRED"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field
    And the suggestion should contain "valid bearer token"
    And the request context is echoed in the error response when possible
    # POST-F1: System state unchanged
    # POST-F2: Error with correctable recovery
    # POST-F3: Context echoed when possible
    # --- Extension I: RATE_LIMITED ---

    Examples:
      | partition         | boundary_point    | auth_setup                                      |
      | missing_token     | no token          | no authentication token present                 |
      | expired_token     | expired token     | an expired authentication token                 |
      | malformed_token   | malformed token   | a structurally invalid token "not-a-jwt"        |

  @T-UC-016-ext-i @extension @ext-i @error @post-f1 @post-f2 @post-f3
  Scenario Outline: RATE_LIMITED via <transport>
    Given the request rate for this tenant has been exceeded
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends a sync_audiences request
    Then the operation should fail
    And the error code should be "RATE_LIMITED"
    And the error recovery should be "transient"
    And the error should include "retry_after" field
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    And the request context is echoed in the error response
    # POST-F1: System state unchanged
    # POST-F2: Error with transient recovery and retry_after
    # POST-F3: Context echoed
    # --- Extension J: SERVICE_UNAVAILABLE ---

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-016-ext-j @extension @ext-j @error @post-f1 @post-f2 @post-f3
  Scenario Outline: SERVICE_UNAVAILABLE via <transport>
    Given the ad platform is temporarily unreachable
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends a sync_audiences request
    Then the operation should fail
    And the error code should be "SERVICE_UNAVAILABLE"
    And the error recovery should be "transient"
    And the error should include "retry_after" field
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    And the request context is echoed in the error response
    # POST-F1: System state unchanged (no partial writes)
    # POST-F2: Error with transient recovery
    # POST-F3: Context echoed

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-016-context-echo @cross-cutting @post-s7
  Scenario: Context echo -- request context echoed in success response
    Given the Buyer Agent includes application context {"campaign_id": "c123"} in the request
    When the Buyer Agent sends a sync_audiences request with audiences
    Then the response includes context {"campaign_id": "c123"} unchanged
    # POST-S7: Application context echoed unchanged

  @T-UC-016-context-echo-error @cross-cutting @post-f3
  Scenario: Context echo -- request context echoed in error response
    Given the Buyer Agent includes application context {"session": "s456"} in the request
    And the account reference does not match any account
    When the Buyer Agent sends a sync_audiences request
    Then the error response includes context {"session": "s456"} when possible
    # POST-F3: Application context echoed in error response

  @T-UC-016-sandbox-happy @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account sync_audiences produces simulated results with sandbox flag
    Given the Buyer is authenticated with a valid principal_id
    And the request targets a sandbox account
    When the Buyer Agent sends a sync_audiences request with audiences
    Then the response is a success variant with audiences array
    And the response should include sandbox equals true
    And no real audience platform syncs should have been triggered
    And no real billing records should have been created
    # BR-RULE-209 INV-1: inputs validated same as production
    # BR-RULE-209 INV-2: real ad platform calls suppressed
    # BR-RULE-209 INV-3: real billing suppressed
    # BR-RULE-209 INV-4: response includes sandbox: true

  @T-UC-016-sandbox-production @invariant @br-rule-209 @sandbox
  Scenario: Production account sync_audiences response does not include sandbox flag
    Given the Buyer is authenticated with a valid principal_id
    And the request targets a production account
    When the Buyer Agent sends a sync_audiences request with audiences
    Then the response is a success variant with audiences array
    And the response should not include a sandbox field
    # BR-RULE-209 INV-5: production account -> sandbox absent

  @T-UC-016-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account with invalid audience returns real validation error
    Given the Buyer is authenticated with a valid principal_id
    And the request targets a sandbox account
    When the Buyer Agent sends a sync_audiences request with invalid member identifiers
    Then the response should indicate a validation error
    And the error should be a real validation error, not simulated
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

