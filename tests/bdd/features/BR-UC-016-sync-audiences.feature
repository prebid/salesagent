# Generated from adcp-req @ a14db6e5894e781a8b2c577e86e1b136876e4915 on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

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
  #   POST-S9: Async/governance-gated uploads surface a SyncAudiencesSubmitted envelope (status=submitted + task_id)
  #   POST-S10: Buyer can read a per-identifier-type match_breakdown for each audience whose seller reports per-type matching
  #   POST-S11: Replaying a sync with a previously-seen idempotency_key returns the original result (at-most-once)
  #   POST-F1: System state is unchanged on complete operation failure
  #   POST-F2: Buyer knows what failed and the specific error code with recovery
  #   POST-F3: Application context is still echoed when possible
  #   POST-F4: Individual audience failures do not prevent other audiences from being processed
  #
  # Rules: BR-RULE-113..132, 209, 211 (idempotency), 230 (match_breakdown/effective_match_rate), 231 (CONFLICT)
  # Extensions: A (sync/upsert), B (delete specific), C (bulk delete missing),
  #   D (ACCOUNT_NOT_FOUND), E (INVALID_REQUEST), F (AUDIENCE_TOO_SMALL),
  #   G (UNSUPPORTED_FEATURE), H (AUTH_REQUIRED), I (RATE_LIMITED), J (SERVICE_UNAVAILABLE),
  #   K (Idempotent Replay, v3.1), L (CONFLICT, v3.1)
  # Error codes: AUTH_REQUIRED, ACCOUNT_NOT_FOUND, INVALID_REQUEST, AUDIENCE_TOO_SMALL,
  #   UNSUPPORTED_FEATURE, RATE_LIMITED, SERVICE_UNAVAILABLE, CONFLICT

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
      | invalid_enum           | unknown enum value   | "marketing"     |

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

  @T-UC-016-effective-match-rate @v31 @effective-match-rate @partition @boundary @post-s4
  Scenario Outline: Deduplicated effective_match_rate population -- <partition>
    Given an audience sync result with status "<status>"
    Then <outcome>
    # BR-RULE-230 INV-1 (ready -> populated [0,1]), INV-2 (processing/too_small -> not expected)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

    Examples:
      | partition         | status     | outcome                                                                       |
      | ready_rate        | ready      | effective_match_rate is populated, a number in [0,1], deduplicated across id types |
      | ready_zero        | ready      | effective_match_rate is 0 (no members matched)                                |
      | processing_absent | processing | effective_match_rate is not expected (reach not yet determined)               |
      | too_small_absent  | too_small  | effective_match_rate is not expected (reach not yet determined)               |

  @T-UC-016-effective-match-rate-boundary @v31 @effective-match-rate @boundary
  Scenario Outline: effective_match_rate range boundaries -- <boundary_point>
    Given an audience sync result with status "ready" and effective_match_rate <value>
    Then the result is <expected>
    # range [0,1] inclusive
    # --- Match Breakdown (BR-RULE-230, v3.1, per-identifier-type) ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

    Examples:
      | boundary_point                          | value   | expected |
      | effective_match_rate=0 (status=ready)   | 0       | valid    |
      | effective_match_rate=1 (status=ready, full match) | 1       | valid    |
      | effective_match_rate=1.0001             | 1.0001  | invalid  |
      | effective_match_rate=-0.0001            | -0.0001 | invalid  |

  @T-UC-016-match-breakdown @v31 @match-breakdown @partition @post-s10
  Scenario Outline: Per-identifier-type match_breakdown reporting -- <partition>
    Given an audience sync result with status "ready" whose match_breakdown is "<partition>"
    Then <outcome>
    # BR-RULE-230 INV-3 (array minItems1, item shape), INV-4 (omitted is valid)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

    Examples:
      | partition   | outcome                                                                                              |
      | single_type | match_breakdown is an array of length 1; the item carries id_type, submitted>=0, matched>=0, match_rate in [0,1] |
      | multi_type  | match_breakdown lists one item per identifier type, each with id_type, submitted, matched, match_rate |
      | omitted     | match_breakdown is absent; its absence is valid and is not an error (aggregate-only seller)           |

  @T-UC-016-match-breakdown-item-validity @v31 @match-breakdown @partition @boundary
  Scenario Outline: match_breakdown item validity -- <partition>
    Given an audience sync result whose match_breakdown is <example>
    Then the breakdown is <expected>
    # BR-RULE-230 INV-3 minItems1 + required item fields + id_type enum
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

    Examples:
      | partition                   | example                                                                  | expected |
      | single_type                 | one item with id_type "hashed_email", submitted 1000, matched 620, match_rate 0.62 | valid    |
      | empty_array                 | an empty array                                                           | invalid  |
      | missing_required_item_field | an item missing the submitted field                                      | invalid  |
      | bad_id_type                 | an item whose id_type is "ssn" (not a match-id-type enum member)         | invalid  |

  @T-UC-016-match-breakdown-authoritative @v31 @match-breakdown @semantics @post-s10
  Scenario: match_breakdown match_rate is server-authoritative and must not be conflated with effective_match_rate
    Given an audience sync result with status "ready"
    And effective_match_rate of 0.62
    And a match_breakdown listing id_type "hashed_email" with matched 620 and id_type "maid" with matched 300
    Then each match_breakdown item match_rate is server-authoritative and the consumer prefers it over a self-computed matched/submitted
    And the sum of matched across breakdown items double-counts multi-identifier members
    And that sum does not reconstruct effective_match_rate or matched_count
    # BR-RULE-230 INV-5 (server-authoritative), INV-6 (dedup vs sum MUST NOT conflate)
    # --- Member Count Recommendation (BR-RULE-126) ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

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
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

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
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

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
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

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

  @T-UC-016-submitted-envelope @v31 @submitted-envelope @post-s9 @async
  Scenario: Submitted envelope -- seller queues batch ingestion and returns task_id
    Given the seller pipeline batches audience ingestion and cannot return per-audience results synchronously
    When the Buyer Agent sends a sync_audiences request with audiences
    Then the response is a SyncAudiencesSubmitted envelope
    And the response has status "submitted"
    And the response has a task_id
    And the response does not have an audiences array
    And the response may include an optional message field
    # POST-S9: buyer polls tasks/get or awaits webhook for completion artifact
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

  @T-UC-016-submitted-governance-gated @v31 @submitted-envelope @post-s9 @async @governance
  Scenario: Submitted envelope -- governance review gates the upload before matching starts
    Given the seller routes audience uploads through governance review before matching can start
    When the Buyer Agent sends a sync_audiences request with audiences
    Then the response is a SyncAudiencesSubmitted envelope
    And the response has status "submitted"
    And the response has a task_id
    And the final per-audience results land on the task completion artifact
    # POST-S9: governance-gated ingestion uses task envelope, not synchronous success
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

  @T-UC-016-submitted-advisory-errors @v31 @submitted-envelope @post-s9 @async @advisory-errors
  Scenario: Submitted envelope -- advisory errors permitted for non-blocking warnings
    Given the seller queues the sync operation with non-blocking warnings
    When the Buyer Agent sends a sync_audiences request with audiences
    Then the response is a SyncAudiencesSubmitted envelope
    And the response has status "submitted"
    And the response has a task_id
    And the response may include an advisory errors array with non-blocking warnings
    And terminal failures are not present in the submitted envelope (those use SyncAudiencesError instead)
    # BR-23: advisory errors allowed; terminal failures excluded
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

  @T-UC-016-submitted-mutual-exclusion @v31 @submitted-envelope @boundary @response-exclusivity
  Scenario Outline: Submitted envelope mutual exclusion -- <partition>
    When a sync_audiences response is shaped as <response_shape>
    Then the response <response_check>
    # BR-15: three-shape oneOf; triple-not guard ensures shapes are unambiguous
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

    Examples:
      | partition               | response_shape                                      | response_check                                                          |
      | submitted_no_audiences  | SyncAudiencesSubmitted with task_id only            | is a valid submitted envelope without audiences array                   |
      | submitted_plus_audiences | submitted with status="submitted" AND audiences    | should be invalid (submitted envelope must not carry audiences)         |
      | success_plus_task_id    | success audiences array AND a top-level task_id    | should be invalid (synchronous success must not carry task_id)          |
      | error_plus_status_submitted | errors array AND status="submitted"            | should be invalid (error branch must not carry submitted status)        |

  @T-UC-016-submitted-vs-per-item-processing @v31 @submitted-envelope @per-audience-async
  Scenario: Per-audience status=processing belongs on synchronous success, not submitted envelope
    Given the seller resolves the sync synchronously but one audience is still matching
    When the Buyer Agent sends a sync_audiences request with audiences
    Then the response is a SyncAudiencesSuccess with an audiences array
    And the audience still matching has audience-status "processing"
    And the response does not use the SyncAudiencesSubmitted envelope
    # POST-S9 boundary: operation-level async vs per-audience async are distinct
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

  @T-UC-016-submitted-message-injection-guard @v31 @submitted-envelope @security @prompt-injection
  Scenario: Submitted envelope message field is untrusted seller input -- buyer must sanitize
    Given the seller returns a SyncAudiencesSubmitted envelope with a hostile message field
    When the Buyer Agent renders or forwards the response
    Then the message field is treated as untrusted seller input
    And the buyer escapes the message before rendering to any HTML UI
    And the buyer sanitizes or isolates the message before passing to an LLM prompt context
    And the message length does not exceed 2000 characters
    # BR-24: prompt-injection guard on message field
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

  @T-UC-016-too-small-details-shape @v31 @audience-too-small @error-details @schema @br-25
  Scenario: too_small per-audience result carries audience-too-small details (minimum_size, current_size)
    Given the Buyer Agent submits a sync_audiences request with a CRM list whose matched count falls below the seller's minimum
    When the Seller Agent returns a SyncAudiencesSuccess with that audience's status set to "too_small"
    Then the per-audience error.details object conforms to /schemas/error-details/audience-too-small.json
    And error.details.minimum_size is a number greater than 0
    And error.details.current_size is a number less than error.details.minimum_size
    # Buyer broadens targeting using the threshold gap; BR-RULE-131 INV-4 details shape
    # --- CONFLICT (BR-UC-016-ext-l, BR-RULE-231) ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

  @T-UC-016-conflict-details-on-concurrent-sync @v31 @conflict @error-details @schema @ext-l @post-f2
  Scenario: Concurrent sync on the same audience_id marks that audience CONFLICT with conflict details
    Given a sync_audiences request whose audiences array includes audience_id "aud_loyalty_2026"
    And a concurrent in-flight sync is already mutating audience_id "aud_loyalty_2026"
    When the Seller Agent returns a SyncAudiencesSuccess response
    Then that audience result has action "failed" with an errors entry whose code is "CONFLICT"
    And that errors entry's error.details object conforms to /schemas/error-details/conflict.json
    And error.details.resource_id equals "aud_loyalty_2026"
    And error.details.expected_version is present
    And error.details.current_version reflects the version applied by the winning sync
    And the errors entry should include a "suggestion" field for reconciliation
    # BR-RULE-231 INV-1 (concurrent same audience_id), INV-3 (conflict.json details)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

  @T-UC-016-conflict-partial-success @v31 @conflict @ext-l @partial-success @post-f4
  Scenario: A per-audience CONFLICT does not abort the request -- other audiences process normally
    Given a sync_audiences request whose audiences array contains one audience that collides with a concurrent sync and two valid audiences
    When the Seller Agent processes the request
    Then the response is a SyncAudiencesSuccess
    And the conflicted audience result has action "failed" with an errors entry whose code is "CONFLICT"
    And the two valid audiences are processed normally
    And the errors entry should include a "suggestion" field for reconciliation
    # BR-RULE-231 INV-4 (partial success; one CONFLICT MUST NOT abort the whole request)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

  @T-UC-016-conflict-seller-managed-collision @v31 @conflict @ext-l @post-f2
  Scenario: Buyer audience_id colliding with a seller-managed segment fails CONFLICT without overwrite
    Given a sync_audiences request whose audience_id collides with an existing seller-managed segment the buyer may not overwrite
    When the Seller Agent processes that audience
    Then that audience result has action "failed" with an errors entry whose code is "CONFLICT"
    And the seller-managed segment is not overwritten
    And the errors entry should include a "suggestion" field for reconciliation
    # BR-RULE-231 INV-2 (seller-managed segment collision)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

  @T-UC-016-conflict-not-cleared-by-replay @v31 @conflict @ext-l @idempotency-key @semantics
  Scenario: A resource-collision CONFLICT is not resolved by replaying under the same idempotency_key
    Given an audience previously failed with CONFLICT due to a resource collision
    When the Buyer Agent replays the sync_audiences request under the same idempotency_key
    Then the audience still fails with CONFLICT
    And recovery requires reconciling the resource by re-reading it or choosing a different audience_id
    And the errors entry should include a "suggestion" field for reconciliation
    # BR-RULE-231 INV-5 (distinct from BR-RULE-211 idempotency replay and BR-RULE-215 revision token)
    # --- Idempotent Replay (BR-UC-016-ext-k, BR-RULE-211) ---
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

  @T-UC-016-idempotent-replay @v31 @idempotency-key @ext-k @post-s11 @happy-path
  Scenario: Replaying sync_audiences with a previously-seen idempotency_key returns the original result
    Given a sync_audiences request with idempotency_key "aud-sync-2026-q1-replay-001"
    And a prior sync_audiences request for the same (seller, account, idempotency_key) completed with byte-identical payload
    When the Buyer Agent resends the sync_audiences request
    Then the Seller Agent returns the original response unchanged
    And no new audience side effects are produced
    And no audit events or downstream refreshes are re-fired
    And the request context is echoed unchanged
    # BR-RULE-211 INV-2 (identical replay -> cached response, no new state); POST-S11 at-most-once; POST-S7 context echo (ext-k step 3)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

  @T-UC-016-idempotent-new-key @v31 @idempotency-key @ext-k @post-s11 @happy-path
  Scenario: First sync_audiences request with a new idempotency_key executes normally
    Given a sync_audiences request with idempotency_key "aud-sync-2026-q1-new-key-001"
    And no prior record exists for the same (seller, account, idempotency_key)
    When the Buyer Agent sends the sync_audiences request
    Then the request is processed as a new execution
    And the idempotency_key is stored with the canonical payload and response
    # BR-RULE-211 INV-1 (new key -> processed normally)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

  @T-UC-016-idempotency-key-required @v31 @idempotency-key @validation @partition @boundary @post-f2
  Scenario Outline: idempotency_key presence and format on sync_audiences -- <partition>
    Given a sync_audiences request with idempotency_key "<value>"
    When the Buyer Agent sends the sync_audiences request
    Then the request is <expected>
    # idempotency_key.yaml: v3.1 REQUIRED, 16-255, pattern ^[A-Za-z0-9_.:-]{16,255}$

    Examples:
      | partition       | value                                  | expected                                |
      | absent_required | (field omitted)                        | rejected with error code "INVALID_REQUEST" |
      | empty_string    | (empty string)                         | rejected with error code "INVALID_REQUEST" |
      | too_short       | abc1234                                | rejected with error code "INVALID_REQUEST" |
      | boundary_min    | abcd0123_efgh.456                      | accepted (exactly 16 characters)        |
      | typical_valid   | aud-sync-2026-q1-0001                   | accepted                                |
      | boundary_max    | a 255-character key                    | accepted (exactly 255 characters)       |
      | too_long        | a 256-character key                    | rejected with error code "INVALID_REQUEST" |
      | bad_pattern     | abcd 0123 efgh 4567                    | rejected with error code "INVALID_REQUEST" |

  @T-UC-016-sandbox-response-bva @invariant @br-rule-209 @sandbox @boundary @partition
  Scenario Outline: sync_audiences sandbox response boundary -- <boundary>
    Given the Buyer is authenticated with a valid principal_id
    And the request targets a <account_kind> account
    When the Buyer Agent sends a sync_audiences request with audiences
    Then the response is a success variant with audiences array
    And the sandbox field is <sandbox_field_state>
    And the returned audience data is <data_kind>
    # BR-RULE-209 INV-4 (sandbox account -> sandbox: true, simulated), INV-5 (production -> sandbox absent, real)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

    Examples:
      | boundary                                          | account_kind | sandbox_field_state         | data_kind |
      | sandbox: true in response (sandbox account)       | sandbox      | present and equal to true   | simulated |
      | sandbox absent in response (production account)   | production   | absent                      | real      |
      | sandbox: false in response (explicit production)  | production   | present and equal to false  | real      |

  @T-UC-016-idempotency-key-format-bva @v31 @idempotency-key @validation @boundary @partition @post-f2
  Scenario Outline: idempotency_key format rejection boundary -- <boundary>
    Given a sync_audiences request whose idempotency_key is "<key_condition>"
    When the Buyer Agent sends the sync_audiences request
    Then the request is rejected with error code "INVALID_REQUEST"
    And the error code should be "INVALID_REQUEST"
    And the error should include a "suggestion" field for how to fix the idempotency_key
    # idempotency_key.yaml BR-RULE-081: v3.1 REQUIRED, 16-255, pattern ^[A-Za-z0-9_.:-]{16,255}$
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

    Examples:
      | boundary                                         | key_condition               |
      | absent (field not provided)                      | absent (field not provided) |
      | valid length, disallowed character (e.g. space)  | abcd 0123 efgh 4567         |

  @T-UC-016-replay-policy-valid-bva @v31 @idempotency-key @ext-k @boundary @partition @post-s11
  Scenario Outline: sync_audiences idempotency replay accept boundary -- <boundary>
    Given the replay condition is <boundary>
    When the Buyer Agent sends a sync_audiences request under idempotency_key "aud-sync-2026-q1-replay-200"
    Then the request outcome is <outcome>
    # BR-RULE-211 INV-1 (new key -> processed normally), INV-2 (identical replay -> cached response, no new state)
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/media-buy/sync-audiences-request.json

    Examples:
      | boundary                                                  | outcome                                         |
      | key present, no prior record for (seller, account, key)   | processed as a new execution                    |
      | key present, prior record exists, payload byte-identical  | the original cached response returned unchanged |

  @T-UC-016-replay-policy-invalid-bva @v31 @idempotency-key @ext-k @boundary @partition @post-f2
  Scenario Outline: sync_audiences idempotency replay rejection boundary -- <boundary>
    Given a prior sync_audiences request under idempotency_key "aud-sync-2026-q1-replay-100" for this (seller, account)
    And the replay condition is <boundary>
    When the Buyer Agent resends a sync_audiences request under the same idempotency_key
    Then the request is rejected with error code "<error_code>"
    And the error code should be "<error_code>"
    And the error should include a "suggestion" field for recovery
    # BR-RULE-211: divergent payload -> IDEMPOTENCY_CONFLICT; in-flight -> IDEMPOTENCY_IN_FLIGHT; TTL expiry -> IDEMPOTENCY_EXPIRED; key absent -> schema INVALID_REQUEST

    Examples:
      | boundary                                                                                 | error_code            |
      | key absent (field omitted)                                                               | INVALID_REQUEST       |
      | key present, prior record exists, payload has one field changed                          | IDEMPOTENCY_CONFLICT  |
      | key present, prior record exists, payload has all fields changed                         | IDEMPOTENCY_CONFLICT  |
      | key present, prior request still in flight (not yet committed)                           | IDEMPOTENCY_IN_FLIGHT |
      | key present, prior record exists, replay arrives exactly at replay_ttl_seconds boundary  | IDEMPOTENCY_EXPIRED   |
