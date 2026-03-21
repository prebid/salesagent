# Generated from adcp-req @ 8a219ece2b54628c33f1075d386b73082a0f4832 on 2026-03-20T12:00:24Z
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py

Feature: BR-UC-014 Sponsored Intelligence Session
  As a Buyer
  I want to check offering availability, initiate conversational sessions with brand agents, exchange messages, and terminate sessions
  So that I can engage with sponsored brand experiences inside AI assistants without leaving the conversation context

  # Postconditions verified:
  #   POST-S1: Buyer knows the offering availability status and can view offering details and matching products
  #   POST-S2: Buyer has an active conversational session with a unique session_id and negotiated capabilities
  #   POST-S3: Buyer has received a conversational response from the brand agent
  #   POST-S4: Buyer knows the current session status after each message exchange
  #   POST-S5: Buyer has successfully terminated the session and received confirmation
  #   POST-S6: When session terminates with handoff_transaction, Buyer has ACP handoff data for checkout
  #   POST-S7: Buyer has received an offering_token for session continuity correlation
  #   POST-S8: Capability negotiation produces the intersection of host and brand capabilities
  #   POST-F1: System state is unchanged on failure
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: When a session is not found, the error references the provided session_id
  #   POST-F4: When an offering is unavailable, the error includes the reason and may suggest alternatives
  #
  # Rules: BR-RULE-095..104 (10 rules, 37 invariants)
  # Extensions: A (initiate session), B (send message), C (terminate session),
  #   D (SESSION_NOT_FOUND), E (OFFER_UNAVAILABLE), F (CAPABILITY_UNSUPPORTED),
  #   G (RATE_LIMITED), H (SESSION_ALREADY_TERMINATED)
  # Error codes: session_not_found, offer_unavailable, capability_unsupported, rate_limited,
  #   session_already_terminated, CONTEXT_PII_DETECTED, CONSENT_GRANTED_REQUIRED,
  #   CONSENT_SCOPE_REQUIRED, IDENTITY_CONSENT_CONFLICT, CAPABILITY_UNSUPPORTED,
  #   SESSION_STATUS_INVALID, HANDOFF_REQUIRED, SESSION_ALREADY_TERMINATED,
  #   MESSAGE_CONTENT_REQUIRED, SESSION_ID_REQUIRED, TERMINATION_REASON_INVALID,
  #   TERMINATION_REASON_REQUIRED, ACP_HANDOFF_REQUIRED, PRODUCT_LIMIT_TOO_LOW,
  #   PRODUCT_LIMIT_TOO_HIGH, UI_ELEMENT_TYPE_INVALID, UI_ELEMENT_DATA_REQUIRED,
  #   UI_ELEMENT_TYPE_REQUIRED, RANGE_ERROR, SESSION_ID_PREDICTABLE, TYPE_ERROR

  Background:
    Given a Seller Agent is operational and accepting requests
    And the Seller Agent has SI protocol support enabled


  @T-UC-014-001 @main-flow @get-offering @happy-path @post-s1 @post-s7
  Scenario Outline: Get offering via <transport> -- available offering with products
    Given a valid offering "delta-flights-summer" exists in the brand catalog
    And the offering is active and available
    And the Buyer Agent has an authenticated connection via <transport>
    When the Buyer Agent sends si_get_offering with offering_id "delta-flights-summer" and include_products true
    Then the response contains available true
    And the response contains offering details with offering_id, title, summary, and tagline
    And the response contains an offering_token for session continuity
    And the response contains ttl_seconds indicating validity duration
    And the response contains checked_at timestamp
    And the response contains matching_products array with total_matching count
    # POST-S1: Buyer knows offering availability and can view details and products
    # POST-S7: Buyer received offering_token for session continuity

    Examples:
      | transport |
      | MCP       |
      | REST      |

  @T-UC-014-002 @main-flow @get-offering @happy-path @post-s1
  Scenario: Get offering -- available offering without products
    Given a valid offering "nike-sneakers" exists in the brand catalog
    And the offering is active and available
    When the Buyer Agent sends si_get_offering with offering_id "nike-sneakers" and include_products false
    Then the response contains available true
    And the response contains offering details
    And the response does not contain matching_products array
    # POST-S1: Buyer knows offering is available

  @T-UC-014-003 @main-flow @get-offering @post-s1
  Scenario: Get offering -- unavailable offering returns reason and alternatives
    Given a valid offering "expired-promo" exists in the brand catalog
    And the offering is expired
    When the Buyer Agent sends si_get_offering with offering_id "expired-promo"
    Then the response contains available false
    And the response contains unavailable_reason explaining expiration
    And the response may contain alternative_offering_ids array
    # POST-S1: Buyer knows offering is unavailable with reason

  @T-UC-014-004 @main-flow @get-offering @post-s1
  Scenario: Get offering -- anonymous context with no PII is accepted
    Given a valid offering "shoes-fall" exists and is available
    When the Buyer Agent sends si_get_offering with offering_id "shoes-fall" and context "mens size 14 near Cincinnati"
    Then the response contains available true
    And the context was processed without PII violation
    # POST-S1: Anonymous offering check succeeds

  @T-UC-014-005 @ext-a @initiate-session @happy-path @post-s2 @post-s8
  Scenario: Initiate session -- consent granted with full capabilities
    Given a valid offering has been checked and an offering_token received
    And the host supports conversational, voice, and product_card components
    When the Buyer Agent sends si_initiate_session with context "I want to explore flight options" and identity with consent_granted true and consent_scope ["name", "email"] and supported_capabilities
    Then the response contains a unique session_id
    And the response contains negotiated_capabilities with the intersection of host and brand capabilities
    And negotiated_capabilities includes conversational modality as true
    And the response contains an initial brand response with message text
    # POST-S2: Active session with unique ID and negotiated capabilities
    # POST-S8: Capabilities are intersection of host and brand

  @T-UC-014-006 @ext-a @initiate-session @happy-path @post-s2 @post-s8
  Scenario: Initiate session -- consent denied with anonymous session
    Given the Buyer is authenticated with a valid principal_id
    When the Buyer Agent sends si_initiate_session with context "explore shoes" and identity with consent_granted false and anonymous_session_id "anon-abc123"
    Then the response contains a unique session_id
    And the session is created anonymously without user data
    And negotiated_capabilities includes conversational modality as true
    # POST-S2: Anonymous session created
    # POST-S8: At least conversational baseline negotiated

  @T-UC-014-007 @ext-a @initiate-session @happy-path @post-s8
  Scenario: Initiate session -- no supported_capabilities yields conversational baseline only
    When the Buyer Agent sends si_initiate_session with context "browse products" and identity with consent_granted false without supported_capabilities
    Then the response contains a unique session_id
    And negotiated_capabilities contains only conversational modality
    And negotiated_capabilities does not include voice, video, or avatar
    # POST-S8: Only conversational baseline when host omits capabilities

  @T-UC-014-008 @ext-a @initiate-session @happy-path @post-s2
  Scenario: Initiate session -- with valid offering_token recalls prior context
    Given the Buyer Agent previously received offering_token "opq-xyz" from si_get_offering within TTL
    When the Buyer Agent sends si_initiate_session with offering_token "opq-xyz"
    Then the session is created with offering context recalled
    And the brand agent can reference prior product listings
    # POST-S2: Session with offering context

  @T-UC-014-009 @ext-a @initiate-session @happy-path @post-s2 @degradation
  Scenario: Initiate session -- expired offering_token degrades gracefully
    Given the Buyer Agent has an expired offering_token "opq-expired"
    When the Buyer Agent sends si_initiate_session with offering_token "opq-expired"
    Then the session is created successfully without offering context
    And no error is returned for the expired token
    # POST-S2: Session proceeds without offering context (graceful degradation)

  @T-UC-014-010 @send-message @happy-path @post-s3 @post-s4
  Scenario: Send message -- text message with active session
    Given an active SI session exists with session_id "sess-abc123"
    When the Buyer Agent sends si_send_message with session_id "sess-abc123" and message "tell me about sizes"
    Then the response contains a conversational message from the brand agent
    And the response contains session_status "active"
    And the response contains session_id "sess-abc123"
    # POST-S3: Buyer received brand response
    # POST-S4: Session status is active

  @T-UC-014-011 @send-message @happy-path @post-s3
  Scenario: Send message -- action_response from UI button click
    Given an active SI session exists with session_id "sess-abc123"
    When the Buyer Agent sends si_send_message with session_id "sess-abc123" and action_response with action "select_product" and payload
    Then the response contains a conversational message from the brand agent
    And the response contains session_status
    # POST-S3: Brand responds to action

  @T-UC-014-012 @send-message @happy-path @post-s3
  Scenario: Send message -- both message and action_response provided
    Given an active SI session exists with session_id "sess-abc123"
    When the Buyer Agent sends si_send_message with session_id "sess-abc123" and message "I want this one" and action_response with action "select_product"
    Then the response contains a conversational message from the brand agent
    # POST-S3: Both inputs processed

  @T-UC-014-013 @send-message @happy-path @post-s4 @post-s6
  Scenario: Send message -- session status transitions to pending_handoff with handoff object
    Given an active SI session exists with session_id "sess-abc123"
    And the brand agent determines a commerce handoff is appropriate
    When the Buyer Agent sends si_send_message with session_id "sess-abc123" and message "I want to buy this"
    Then the response contains session_status "pending_handoff"
    And the response contains a handoff object with type "transaction"
    And the handoff object includes intent with action and product details
    # POST-S4: Session status is pending_handoff
    # BR-RULE-098 INV-2: pending_handoff requires handoff object

  @T-UC-014-014 @send-message @happy-path @post-s4
  Scenario: Send message -- session status transitions to complete
    Given an active SI session exists with session_id "sess-abc123"
    And the brand agent determines the conversation has naturally concluded
    When the Buyer Agent sends si_send_message with session_id "sess-abc123" and message "thanks, that answers my question"
    Then the response contains session_status "complete"
    # POST-S4: Session status is complete

  @T-UC-014-015 @send-message @happy-path @post-s3
  Scenario: Send message -- response includes standard UI elements
    Given an active SI session exists with session_id "sess-abc123"
    When the Buyer Agent sends si_send_message with session_id "sess-abc123" and message "show me products"
    Then the response contains ui_elements array
    And each ui_element has a type from the standard set
    And standard components include text, link, image, product_card, carousel, or action_button
    # POST-S3: Brand response includes renderable UI components

  @T-UC-014-016 @terminate-session @happy-path @post-s5 @post-s6
  Scenario: Terminate session -- handoff_transaction returns ACP handoff data
    Given an active SI session exists with session_id "sess-abc123"
    When the Buyer Agent sends si_terminate_session with session_id "sess-abc123" and reason "handoff_transaction"
    Then the response contains terminated true
    And the response contains acp_handoff with checkout_url and checkout_token
    And the response may contain follow_up suggestions
    # POST-S5: Session terminated successfully
    # POST-S6: ACP handoff data for checkout
    # BR-RULE-100 INV-1: handoff_transaction requires acp_handoff

  @T-UC-014-017 @terminate-session @happy-path @post-s5
  Scenario: Terminate session -- user_exit without ACP handoff
    Given an active SI session exists with session_id "sess-abc123"
    When the Buyer Agent sends si_terminate_session with session_id "sess-abc123" and reason "user_exit"
    Then the response contains terminated true
    And the response does not contain acp_handoff
    And the response may contain follow_up suggestions
    # POST-S5: Session terminated (no commerce handoff)
    # BR-RULE-100 INV-2: non-transaction reason has no ACP data

  @T-UC-014-018 @terminate-session @happy-path @post-s5
  Scenario: Terminate session -- handoff_complete without ACP handoff
    Given an active SI session exists with session_id "sess-abc123"
    When the Buyer Agent sends si_terminate_session with session_id "sess-abc123" and reason "handoff_complete"
    Then the response contains terminated true
    And the response does not contain acp_handoff
    # POST-S5: Session terminated with handoff_complete

  @T-UC-014-019 @terminate-session @happy-path @post-s5
  Scenario: Terminate session -- session_timeout
    Given an active SI session exists with session_id "sess-abc123"
    When the Buyer Agent sends si_terminate_session with session_id "sess-abc123" and reason "session_timeout"
    Then the response contains terminated true
    # POST-S5: Session terminated due to timeout

  @T-UC-014-020 @terminate-session @happy-path @post-s5
  Scenario: Terminate session -- host_terminated with cause
    Given an active SI session exists with session_id "sess-abc123"
    When the Buyer Agent sends si_terminate_session with session_id "sess-abc123" and reason "host_terminated" and termination_context with cause "policy_violation"
    Then the response contains terminated true
    # POST-S5: Session terminated by host

  @T-UC-014-021 @extension @ext-d @error @post-f1 @post-f2 @post-f3
  Scenario: Send message to nonexistent session -- SESSION_NOT_FOUND
    Given no session exists with session_id "nonexistent-sess"
    When the Buyer Agent sends si_send_message with session_id "nonexistent-sess" and message "hello"
    Then the operation should fail
    And the error code should be "session_not_found"
    And the error message should contain "session"
    And the error should include "suggestion" field
    And the suggestion should contain "session_id"
    And the error references session_id "nonexistent-sess"
    # POST-F1: System state unchanged
    # POST-F2: Error code session_not_found
    # POST-F3: Error references the provided session_id

  @T-UC-014-022 @extension @ext-d @error @post-f1 @post-f2 @post-f3
  Scenario: Terminate nonexistent session -- SESSION_NOT_FOUND
    Given no session exists with session_id "ghost-session"
    When the Buyer Agent sends si_terminate_session with session_id "ghost-session" and reason "user_exit"
    Then the operation should fail
    And the error code should be "session_not_found"
    And the error message should contain "session"
    And the error should include "suggestion" field
    And the suggestion should contain "session_id"
    And the error references session_id "ghost-session"
    # POST-F1: System state unchanged
    # POST-F2: Error code session_not_found
    # POST-F3: Error references the provided session_id
    # --- Extension E: OFFER_UNAVAILABLE ---

  @T-UC-014-023 @extension @ext-e @error @post-f1 @post-f2 @post-f4
  Scenario: Get offering for nonexistent offering -- OFFER_UNAVAILABLE
    Given no offering exists with offering_id "nonexistent-offer"
    When the Buyer Agent sends si_get_offering with offering_id "nonexistent-offer"
    Then the operation should fail
    And the error code should be "offer_unavailable"
    And the error message should contain "offering"
    And the error should include "suggestion" field
    And the suggestion should contain "offering"
    And the response may contain alternative_offering_ids
    # POST-F1: System state unchanged
    # POST-F2: Error code offer_unavailable
    # POST-F4: Error includes reason and may suggest alternatives

  @T-UC-014-024 @get-offering @post-f2 @post-f4
  Scenario: Get offering for sold-out offering -- OFFER_UNAVAILABLE with reason
    Given an offering "sold-out-promo" exists but is sold out
    When the Buyer Agent sends si_get_offering with offering_id "sold-out-promo"
    Then the response contains available false
    And the response contains unavailable_reason "sold_out"
    And the response may contain alternative_offering_ids
    # POST-F2: Buyer knows why the offering is unavailable
    # POST-F4: Reason provided with alternatives
    # --- Extension F: CAPABILITY_UNSUPPORTED ---

  @T-UC-014-025 @extension @ext-f @error @post-f1 @post-f2
  Scenario: Initiate session with unsupported required capability -- CAPABILITY_UNSUPPORTED
    Given the brand agent does not support video modality
    When the Buyer Agent sends si_initiate_session requiring video modality as essential
    Then the operation should fail
    And the error code should be "capability_unsupported"
    And the error message should contain "capability"
    And the error should include "suggestion" field
    And the suggestion should contain "conversational"
    # POST-F1: No session created when required capability is missing
    # POST-F2: Error identifies the unsupported capability
    # --- Extension G: RATE_LIMITED ---

  @T-UC-014-026 @extension @ext-g @error @post-f1 @post-f2
  Scenario: Rate limited on get_offering -- RATE_LIMITED with retry_after
    Given the Buyer Agent has exceeded the rate limit for si_get_offering
    When the Buyer Agent sends si_get_offering with offering_id "any-offer"
    Then the operation should fail
    And the error code should be "rate_limited"
    And the error message should contain "rate"
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    And the error includes retry_after seconds
    # POST-F1: Request not processed
    # POST-F2: Error code rate_limited with retry guidance

  @T-UC-014-027 @extension @ext-g @error @post-f1 @post-f2
  Scenario: Rate limited on send_message -- RATE_LIMITED with retry_after
    Given an active SI session exists with session_id "sess-abc123"
    And the Buyer Agent has exceeded the rate limit for si_send_message
    When the Buyer Agent sends si_send_message with session_id "sess-abc123" and message "hello"
    Then the operation should fail
    And the error code should be "rate_limited"
    And the error message should contain "rate"
    And the error should include "suggestion" field
    And the suggestion should contain "retry"
    # POST-F1: Request not processed
    # POST-F2: Error code rate_limited
    # --- Extension H: SESSION_ALREADY_TERMINATED ---

  @T-UC-014-028 @extension @ext-h @error @post-f1 @post-f2
  Scenario: Send message to terminated session -- SESSION_ALREADY_TERMINATED
    Given a session exists with session_id "sess-done" in terminated state
    When the Buyer Agent sends si_send_message with session_id "sess-done" and message "hello again"
    Then the operation should fail
    And the error code should be "session_already_terminated"
    And the error message should contain "terminated"
    And the error should include "suggestion" field
    And the suggestion should contain "new session"
    And the response contains session_status "complete"
    # POST-F1: System state unchanged
    # POST-F2: Error code session_already_terminated
    # BR-RULE-098 INV-4: Message to terminal session rejected

  @T-UC-014-029 @extension @ext-h @error @post-f1 @post-f2
  Scenario: Send message to complete session -- SESSION_ALREADY_TERMINATED
    Given a session exists with session_id "sess-complete" in complete state
    When the Buyer Agent sends si_send_message with session_id "sess-complete" and message "one more question"
    Then the operation should fail
    And the error code should be "session_already_terminated"
    And the error message should contain "ended"
    And the error should include "suggestion" field
    And the suggestion should contain "si_initiate_session"
    # POST-F1: System state unchanged
    # POST-F2: Complete session also rejects messages

  @T-UC-014-part-offering-pii @partition @offering-pii @br-rule-095
  Scenario Outline: Offering PII partition validation - <partition>
    Given a valid offering exists and is available
    When the Buyer Agent sends si_get_offering with offering_id "test-offer" and context <context_value>
    Then <outcome>

    Examples: Valid partitions
      | partition         | context_value                        | outcome                                                 |
      | context_absent    |                                      | the offering lookup succeeds                            |
      | anonymous_context | "mens size 14 near Cincinnati"       | the offering lookup succeeds                            |
      | location_only     | "shoes available in Ohio"             | the offering lookup succeeds                            |

    Examples: Invalid partitions
      | partition                  | context_value                          | outcome                                                                          |
      | context_with_email         | "find shoes for john@example.com"      | error "CONTEXT_PII_DETECTED" with suggestion "Remove PII from the context"       |
      | context_with_phone         | "order for 555-123-4567"               | error "CONTEXT_PII_DETECTED" with suggestion "Remove PII from the context"       |
      | context_with_name_and_address | "deliver to John Smith at 123 Main St" | error "CONTEXT_PII_DETECTED" with suggestion "Remove PII from the context"    |

  @T-UC-014-bound-offering-pii @boundary @offering-pii @br-rule-095
  Scenario Outline: Offering PII boundary validation - <boundary_point>
    Given a valid offering exists and is available
    When the Buyer Agent sends si_get_offering with offering_id "test-offer" and context <context_value>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                               | context_value                          | outcome                                                                    |
      | context is null/absent                       |                                        | the offering lookup succeeds                                               |
      | context is empty string                      | ""                                     | the offering lookup succeeds                                               |
      | context with generic intent (no PII)         | "mens size 14 near Cincinnati"         | the offering lookup succeeds                                               |
      | context containing email pattern             | "find shoes for john@example.com"      | error "CONTEXT_PII_DETECTED" with suggestion "Remove PII from the context" |
      | context containing phone pattern             | "order for 555-123-4567"               | error "CONTEXT_PII_DETECTED" with suggestion "Remove PII from the context" |
      | context containing full name + street address | "deliver to John Smith at 123 Main St" | error "CONTEXT_PII_DETECTED" with suggestion "Remove PII from the context" |

  @T-UC-014-part-identity-consent @partition @identity-consent @br-rule-096
  Scenario Outline: Identity consent partition validation - <partition>
    When the Buyer Agent sends si_initiate_session with context "explore products" and identity matching <partition>
    Then <outcome>

    Examples: Valid partitions
      | partition              | outcome                                                         |
      | consent_granted_full   | session created with identity and user data per scope            |
      | consent_granted_partial | session created with limited scope data                         |
      | consent_denied_anonymous | session created anonymously via anonymous_session_id            |

    Examples: Invalid partitions
      | partition                    | outcome                                                                                          |
      | consent_missing              | error "CONSENT_GRANTED_REQUIRED" with suggestion "Provide a boolean consent_granted field"        |
      | consent_true_no_scope        | error "CONSENT_SCOPE_REQUIRED" with suggestion "Provide consent_scope array"                     |
      | consent_false_with_user_data | error "IDENTITY_CONSENT_CONFLICT" with suggestion "Remove user data from the identity object"    |

  @T-UC-014-bound-identity-consent @boundary @identity-consent @br-rule-096
  Scenario Outline: Identity consent boundary validation - <boundary_point>
    When the Buyer Agent sends si_initiate_session with context "explore products" and identity at boundary <boundary_point>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                        | outcome                                                                                       |
      | consent_granted = true with full scope and user data  | session created with identity                                                                 |
      | consent_granted = true with empty consent_scope array | session created with limited scope                                                            |
      | consent_granted = false with anonymous_session_id     | session created anonymously                                                                   |
      | consent_granted = false without anonymous_session_id  | session created anonymously                                                                   |
      | consent_granted missing from identity object          | error "CONSENT_GRANTED_REQUIRED" with suggestion "Provide a boolean consent_granted field"     |
      | consent_granted = true but no consent_scope           | error "CONSENT_SCOPE_REQUIRED" with suggestion "Provide consent_scope array"                   |
      | consent_granted = false but user object is populated  | error "IDENTITY_CONSENT_CONFLICT" with suggestion "Remove user data from the identity object"  |

  @T-UC-014-part-capability @partition @capability-negotiation @br-rule-097
  Scenario Outline: Capability negotiation partition validation - <partition>
    When the Buyer Agent sends si_initiate_session with supported_capabilities matching <partition>
    Then <outcome>

    Examples: Valid partitions
      | partition          | outcome                                                                   |
      | full_negotiation   | negotiated_capabilities is intersection of host and brand capabilities     |
      | baseline_only      | negotiated_capabilities contains only conversational modality              |
      | commerce_capable   | negotiated_capabilities includes acp_checkout true                         |

    Examples: Invalid partitions
      | partition                  | outcome                                                                                                         |
      | conversational_disabled    | error "CAPABILITY_UNSUPPORTED" with suggestion "Ensure conversational modality is always enabled"                |
      | unknown_standard_component | error "CAPABILITY_UNSUPPORTED" with suggestion "Use only standard component types"                               |

  @T-UC-014-bound-capability @boundary @capability-negotiation @br-rule-097
  Scenario Outline: Capability negotiation boundary validation - <boundary_point>
    When the Buyer Agent sends si_initiate_session with capabilities at boundary <boundary_point>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                    | outcome                                                                                         |
      | supported_capabilities absent (baseline only)     | negotiated_capabilities contains only conversational modality                                    |
      | all modalities supported by both sides            | negotiated_capabilities includes all matching modalities                                         |
      | host supports voice but brand does not            | negotiated_capabilities excludes voice (intersection excludes it)                                |
      | host supports acp_checkout and brand does too     | negotiated_capabilities includes acp_checkout true                                               |
      | conversational explicitly set to false            | error "CAPABILITY_UNSUPPORTED" with suggestion "Ensure conversational modality is always enabled" |
      | unknown component type in standard array          | error "CAPABILITY_UNSUPPORTED" with suggestion "Use only standard component types"                |

  @T-UC-014-part-session-lifecycle @partition @session-lifecycle @br-rule-098
  Scenario Outline: Session lifecycle partition validation - <partition>
    Given an SI session exists in the appropriate state for <partition>
    When the system determines session status or a message is sent
    Then <outcome>

    Examples: Valid partitions
      | partition                    | outcome                                                            |
      | active_session               | conversation continues and more messages are accepted               |
      | pending_handoff_transaction  | session_status is pending_handoff with handoff type transaction     |
      | pending_handoff_complete     | session_status is pending_handoff with handoff type complete        |
      | complete_session             | conversation naturally concluded and no more messages accepted      |

    Examples: Invalid partitions
      | partition                           | outcome                                                                                                |
      | unknown_status                      | error "SESSION_STATUS_INVALID" with suggestion "Use only the enumerated session status values"          |
      | pending_handoff_no_handoff_object   | error "HANDOFF_REQUIRED" with suggestion "Include a handoff object"                                    |
      | message_to_complete_session         | error "SESSION_ALREADY_TERMINATED" with suggestion "Start a new session with si_initiate_session"      |

  @T-UC-014-bound-session-lifecycle @boundary @session-lifecycle @br-rule-098
  Scenario Outline: Session lifecycle boundary validation - <boundary_point>
    Given an SI session at lifecycle boundary <boundary_point>
    When the relevant SI operation is performed
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                         | outcome                                                                                           |
      | session_status = 'active'                              | conversation continues                                                                            |
      | session_status = 'pending_handoff' with handoff object | pending_handoff accepted with handoff data                                                        |
      | session_status = 'complete'                            | no more messages accepted                                                                         |
      | session_status = unknown value                         | error "SESSION_STATUS_INVALID" with suggestion "Use only the enumerated session status values"     |
      | session_status = 'pending_handoff' without handoff object | error "HANDOFF_REQUIRED" with suggestion "Include a handoff object"                             |
      | send_message to complete session                       | error "SESSION_ALREADY_TERMINATED" with suggestion "Start a new session with si_initiate_session"  |

  @T-UC-014-part-message-content @partition @message-content @br-rule-099
  Scenario Outline: Message content partition validation - <partition>
    Given an active SI session exists with session_id "sess-test"
    When the Buyer Agent sends si_send_message with session_id "sess-test" and content matching <partition>
    Then <outcome>

    Examples: Valid partitions
      | partition            | outcome                                                 |
      | message_only         | the brand agent responds with a message                  |
      | action_response_only | the brand agent responds to the action                   |
      | both_present         | the brand agent processes both message and action         |

    Examples: Invalid partitions
      | partition           | outcome                                                                                              |
      | neither_present     | error "MESSAGE_CONTENT_REQUIRED" with suggestion "Include a text message or an action_response"       |
      | session_id_missing  | error "SESSION_ID_REQUIRED" with suggestion "Provide the session_id from si_initiate_session"         |

  @T-UC-014-bound-message-content @boundary @message-content @br-rule-099
  Scenario Outline: Message content boundary validation - <boundary_point>
    Given an active SI session exists with session_id "sess-test"
    When the Buyer Agent sends si_send_message at boundary <boundary_point>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                              | outcome                                                                                          |
      | message only (non-empty string)             | the brand agent responds                                                                         |
      | action_response only (with action field)    | the brand agent responds to action                                                               |
      | both message and action_response present    | both inputs processed                                                                            |
      | neither message nor action_response         | error "MESSAGE_CONTENT_REQUIRED" with suggestion "Include a text message or an action_response"   |
      | message is empty string, no action_response | error "MESSAGE_CONTENT_REQUIRED" with suggestion "Include a text message or an action_response"   |
      | session_id missing                          | error "SESSION_ID_REQUIRED" with suggestion "Provide the session_id from si_initiate_session"     |

  @T-UC-014-part-termination @partition @termination-handoff @br-rule-100
  Scenario Outline: Termination reason partition validation - <partition>
    Given an active SI session exists with session_id "sess-term"
    When the Buyer Agent sends si_terminate_session with session_id "sess-term" and reason matching <partition>
    Then <outcome>

    Examples: Valid partitions
      | partition                      | outcome                                                       |
      | handoff_transaction_with_acp   | session terminated with acp_handoff containing checkout data   |
      | handoff_complete               | session terminated without ACP handoff                         |
      | user_exit                      | session terminated without ACP handoff                         |
      | session_timeout                | session terminated without ACP handoff                         |
      | host_terminated                | session terminated without ACP handoff                         |

    Examples: Invalid partitions
      | partition                   | outcome                                                                                                    |
      | unknown_reason              | error "TERMINATION_REASON_INVALID" with suggestion "Use one of the enumerated termination reasons"          |
      | reason_missing              | error "TERMINATION_REASON_REQUIRED" with suggestion "Provide a termination reason"                          |
      | handoff_transaction_no_acp  | error "ACP_HANDOFF_REQUIRED" with suggestion "Ensure the brand agent generates ACP checkout data"           |

  @T-UC-014-bound-termination @boundary @termination-handoff @br-rule-100
  Scenario Outline: Termination reason boundary validation - <boundary_point>
    Given an active SI session exists with session_id "sess-term"
    When the Buyer Agent sends si_terminate_session at boundary <boundary_point>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                          | outcome                                                                                               |
      | reason = handoff_transaction with acp_handoff in response | session terminated with ACP handoff data                                                            |
      | reason = handoff_complete                               | session terminated without ACP handoff                                                                |
      | reason = user_exit                                      | session terminated without ACP handoff                                                                |
      | reason = session_timeout                                | session terminated without ACP handoff                                                                |
      | reason = host_terminated with cause                     | session terminated without ACP handoff                                                                |
      | reason = unknown value                                  | error "TERMINATION_REASON_INVALID" with suggestion "Use one of the enumerated termination reasons"     |
      | reason missing                                          | error "TERMINATION_REASON_REQUIRED" with suggestion "Provide a termination reason"                     |
      | reason = handoff_transaction but no acp_handoff         | error "ACP_HANDOFF_REQUIRED" with suggestion "Ensure the brand agent generates ACP checkout data"      |

  @T-UC-014-part-token-ttl @partition @offering-token-ttl @br-rule-101
  Scenario Outline: Offering token TTL partition validation - <partition>
    When the relevant SI operation involves an offering token matching <partition>
    Then <outcome>

    Examples: Valid partitions
      | partition              | outcome                                                           |
      | token_with_ttl         | offering_token and ttl_seconds returned for available offering     |
      | token_used_in_session  | session created with offering context recalled                     |
      | expired_token_graceful | session created without offering context (graceful degradation)    |
      | no_token               | session created without offering context (direct start)            |

    Examples: Invalid partitions
      | partition      | outcome                                                        |
      | ttl_negative   | error "RANGE_ERROR" with suggestion "Provide a non-negative integer for ttl_seconds" |

  @T-UC-014-bound-token-ttl @boundary @offering-token-ttl @br-rule-101
  Scenario Outline: Offering token TTL boundary validation - <boundary_point>
    When the offering token TTL is at boundary <boundary_point>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                               | outcome                                                                           |
      | ttl_seconds = 0 (immediate expiry)           | offering_token returned with ttl_seconds 0                                         |
      | ttl_seconds = 1 (minimal validity)           | offering_token returned with ttl_seconds 1                                         |
      | ttl_seconds = -1                             | error "RANGE_ERROR" with suggestion "Provide a non-negative integer for ttl_seconds" |
      | offering_token present, used within TTL      | session created with offering context recalled                                      |
      | offering_token present, used after TTL       | session created without offering context (graceful degradation)                     |
      | offering_token absent in initiate_session    | session created without offering context                                            |

  @T-UC-014-part-session-id @partition @session-id @br-rule-102
  Scenario Outline: Session ID partition validation - <partition>
    When the SI operation involves a session_id matching <partition>
    Then <outcome>

    Examples: Valid partitions
      | partition           | outcome                                                  |
      | valid_session_id    | unique unpredictable session_id returned at initiation    |
      | session_id_in_send  | message routed to the correct active session              |

    Examples: Invalid partitions
      | partition                | outcome                                                                                                   |
      | empty_session_id         | error "SESSION_ID_REQUIRED" with suggestion "Use the session_id returned from si_initiate_session"         |
      | nonexistent_session_id   | error "SESSION_NOT_FOUND" with suggestion "Verify the session_id matches an active session"                |
      | predictable_id           | error "SESSION_ID_PREDICTABLE" with suggestion "Use cryptographically random session ID generation"        |

  @T-UC-014-bound-session-id @boundary @session-id @br-rule-102
  Scenario Outline: Session ID boundary validation - <boundary_point>
    When the SI operation involves a session_id at boundary <boundary_point>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                     | outcome                                                                                               |
      | newly generated session_id in initiate response    | unique unpredictable session_id generated                                                              |
      | session_id used in send_message for active session | message routed to the correct session                                                                  |
      | empty string session_id                            | error "SESSION_ID_REQUIRED" with suggestion "Use the session_id returned from si_initiate_session"      |
      | session_id not found in store                      | error "SESSION_NOT_FOUND" with suggestion "Verify the session_id matches an active session"             |
      | sequential integer as session_id                   | error "SESSION_ID_PREDICTABLE" with suggestion "Use cryptographically random session ID generation"     |

  @T-UC-014-part-ui-components @partition @ui-components @br-rule-103
  Scenario Outline: UI component partition validation - <partition>
    Given an active SI session
    When the brand agent returns a UI element matching <partition>
    Then <outcome>

    Examples: Valid partitions
      | partition              | outcome                                                   |
      | standard_text          | UI element rendered with type text and data.message        |
      | standard_link          | UI element rendered with type link and data.url and label  |
      | standard_product_card  | UI element rendered with type product_card and data.title  |
      | standard_carousel      | UI element rendered with type carousel and data.items      |
      | extension_component    | UI element accepted but host may ignore if unsupported     |

    Examples: Invalid partitions
      | partition               | outcome                                                                                               |
      | unknown_component_type  | error "UI_ELEMENT_TYPE_INVALID" with suggestion "Use a standard component type"                        |
      | missing_required_data   | error "UI_ELEMENT_DATA_REQUIRED" with suggestion "Provide all required data fields"                    |
      | type_missing            | error "UI_ELEMENT_TYPE_REQUIRED" with suggestion "Add a type field to the UI element"                  |

  @T-UC-014-bound-ui-components @boundary @ui-components @br-rule-103
  Scenario Outline: UI component boundary validation - <boundary_point>
    Given an active SI session
    When the brand agent returns a UI element at boundary <boundary_point>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                                       | outcome                                                                              |
      | type = 'text' with data.message                      | UI element rendered                                                                  |
      | type = 'link' with data.url and data.label           | UI element rendered                                                                  |
      | type = 'image' with data.url and data.alt            | UI element rendered                                                                  |
      | type = 'product_card' with data.title and data.price | UI element rendered                                                                  |
      | type = 'carousel' with data.items                    | UI element rendered                                                                  |
      | type = 'action_button' with data.label and data.action | UI element rendered                                                                |
      | type = 'app_handoff' (extension)                     | UI element accepted but host may ignore                                              |
      | type = 'integration_actions' (extension)             | UI element accepted but host may ignore                                              |
      | type = unknown value                                 | error "UI_ELEMENT_TYPE_INVALID" with suggestion "Use a standard component type"       |
      | type missing from element                            | error "UI_ELEMENT_TYPE_REQUIRED" with suggestion "Add a type field to the UI element" |
      | link element missing data.url                        | error "UI_ELEMENT_DATA_REQUIRED" with suggestion "Provide all required data fields"   |

  @T-UC-014-part-product-limits @partition @product-limits @br-rule-104
  Scenario Outline: Product limit partition validation - <partition>
    Given a valid offering with multiple matching products
    When the Buyer Agent sends si_get_offering with include_products true and product_limit matching <partition>
    Then <outcome>

    Examples: Valid partitions
      | partition       | outcome                                                 |
      | default_limit   | response contains up to 5 matching products (default)    |
      | minimum_limit   | response contains up to 1 matching product               |
      | maximum_limit   | response contains up to 50 matching products             |
      | typical_limit   | response contains up to the specified number of products |

    Examples: Invalid partitions
      | partition       | outcome                                                                                   |
      | below_minimum   | error "PRODUCT_LIMIT_TOO_LOW" with suggestion "Set product_limit to a value between 1 and 50"  |
      | above_maximum   | error "PRODUCT_LIMIT_TOO_HIGH" with suggestion "Set product_limit to a value between 1 and 50" |
      | wrong_type      | error "TYPE_ERROR" with suggestion "Provide an integer value for product_limit"                 |

  @T-UC-014-bound-product-limits @boundary @product-limits @br-rule-104
  Scenario Outline: Product limit boundary validation - <boundary_point>
    Given a valid offering with 60 matching products
    When the Buyer Agent sends si_get_offering with include_products true and product_limit at boundary <boundary_point>
    Then <outcome>

    Examples: Boundary values
      | boundary_point                          | outcome                                                                                   |
      | product_limit = 0 (below minimum)       | error "PRODUCT_LIMIT_TOO_LOW" with suggestion "Set product_limit to a value between 1 and 50"  |
      | product_limit = 1 (minimum)             | response contains up to 1 matching product                                                 |
      | product_limit = 2 (just above minimum)  | response contains up to 2 matching products                                                |
      | product_limit = 5 (default)             | response contains up to 5 matching products                                                |
      | product_limit = 49 (just below maximum) | response contains up to 49 matching products                                               |
      | product_limit = 50 (maximum)            | response contains up to 50 matching products                                               |
      | product_limit = 51 (above maximum)      | error "PRODUCT_LIMIT_TOO_HIGH" with suggestion "Set product_limit to a value between 1 and 50" |
      | product_limit absent (default applies)  | response contains up to 5 matching products (default)                                      |

  @T-UC-014-inv-095-1 @invariant @br-rule-095
  Scenario: BR-RULE-095 INV-1 holds -- context absent means no PII check needed
    Given a valid offering "shoes-fall" exists and is available
    When the Buyer Agent sends si_get_offering with offering_id "shoes-fall" without context
    Then the offering lookup succeeds without PII validation
    # BR-RULE-095 INV-1: context absent → no PII check

  @T-UC-014-inv-095-2 @invariant @br-rule-095
  Scenario: BR-RULE-095 INV-2 holds -- anonymous context without PII proceeds
    Given a valid offering "shoes-fall" exists and is available
    When the Buyer Agent sends si_get_offering with offering_id "shoes-fall" and context "mens running shoes size 10"
    Then the offering lookup succeeds
    # BR-RULE-095 INV-2: context without PII → proceeds

  @T-UC-014-inv-095-3 @invariant @br-rule-095 @error
  Scenario: BR-RULE-095 INV-3 violated -- context with PII rejected
    Given a valid offering "shoes-fall" exists and is available
    When the Buyer Agent sends si_get_offering with offering_id "shoes-fall" and context "shoes for john@example.com at 123 Main St"
    Then the operation should fail
    And the error code should be "CONTEXT_PII_DETECTED"
    And the error message should contain "personally identifiable information"
    And the error should include "suggestion" field
    And the suggestion should contain "Remove PII"
    # BR-RULE-095 INV-3: PII in context → rejected

  @T-UC-014-inv-096-1 @invariant @br-rule-096
  Scenario: BR-RULE-096 INV-1 holds -- consent granted with scope transmits user data
    When the Buyer Agent sends si_initiate_session with identity consent_granted true and consent_scope ["name", "email"] and user data
    Then the session is created with user identity
    And user data is transmitted per consent_scope
    # BR-RULE-096 INV-1: consent true + scope → data shared

  @T-UC-014-inv-096-2 @invariant @br-rule-096
  Scenario: BR-RULE-096 INV-2 holds -- consent denied creates anonymous session
    When the Buyer Agent sends si_initiate_session with identity consent_granted false and anonymous_session_id "anon-xyz"
    Then the session is created anonymously
    And no user data is transmitted
    # BR-RULE-096 INV-2: consent false → anonymous session

  @T-UC-014-inv-096-3 @invariant @br-rule-096 @error
  Scenario: BR-RULE-096 INV-3 violated -- consent_granted absent rejected
    When the Buyer Agent sends si_initiate_session with identity missing consent_granted field
    Then the operation should fail
    And the error code should be "CONSENT_GRANTED_REQUIRED"
    And the error message should contain "consent_granted"
    And the error should include "suggestion" field
    And the suggestion should contain "boolean consent_granted"
    # BR-RULE-096 INV-3: missing consent_granted → rejected

  @T-UC-014-inv-096-4 @invariant @br-rule-096 @error
  Scenario: BR-RULE-096 INV-4 violated -- consent true without scope rejected
    When the Buyer Agent sends si_initiate_session with identity consent_granted true but no consent_scope
    Then the operation should fail
    And the error code should be "CONSENT_SCOPE_REQUIRED"
    And the error message should contain "consent_scope"
    And the error should include "suggestion" field
    And the suggestion should contain "consent_scope array"
    # BR-RULE-096 INV-4: consent true but no scope → rejected

  @T-UC-014-inv-096-5 @invariant @br-rule-096 @error
  Scenario: BR-RULE-096 INV-5 violated -- consent false with user data rejected
    When the Buyer Agent sends si_initiate_session with identity consent_granted false but user object with email "j@x.com"
    Then the operation should fail
    And the error code should be "IDENTITY_CONSENT_CONFLICT"
    And the error message should contain "user data"
    And the error should include "suggestion" field
    And the suggestion should contain "Remove user data"
    # BR-RULE-096 INV-5: consent false + user data → conflict

  @T-UC-014-inv-097-1 @invariant @br-rule-097
  Scenario: BR-RULE-097 INV-1 holds -- host provides capabilities, intersection negotiated
    Given the host supports conversational, voice, and product_card
    And the brand supports conversational, voice, and carousel
    When the Buyer Agent sends si_initiate_session with those supported_capabilities
    Then negotiated_capabilities includes conversational and voice
    And negotiated_capabilities does not include product_card or carousel only from one side
    # BR-RULE-097 INV-1: intersection of host and brand

  @T-UC-014-inv-097-2 @invariant @br-rule-097
  Scenario: BR-RULE-097 INV-2 holds -- host omits capabilities, only conversational
    When the Buyer Agent sends si_initiate_session without supported_capabilities
    Then negotiated_capabilities contains only conversational modality
    # BR-RULE-097 INV-2: no host capabilities → baseline only

  @T-UC-014-inv-097-3 @invariant @br-rule-097
  Scenario: BR-RULE-097 INV-3 holds -- conversational always true in any session
    Given any combination of host and brand capabilities
    When the Buyer Agent sends si_initiate_session
    Then negotiated_capabilities.modalities.conversational is always true
    # BR-RULE-097 INV-3: conversational always true

  @T-UC-014-inv-097-4 @invariant @br-rule-097
  Scenario: BR-RULE-097 INV-4 holds -- unsupported modality excluded from negotiation
    Given the host supports voice modality
    And the brand does not support voice modality
    When the Buyer Agent sends si_initiate_session with voice in supported_capabilities
    Then negotiated_capabilities does not include voice
    # BR-RULE-097 INV-4: unsupported modality excluded

  @T-UC-014-inv-098-1 @invariant @br-rule-098
  Scenario: BR-RULE-098 INV-1 holds -- active status allows more messages
    Given an active SI session with session_id "sess-active"
    When the Buyer Agent sends si_send_message and session_status is "active"
    Then further messages are accepted for the session
    # BR-RULE-098 INV-1: active → conversation continues

  @T-UC-014-inv-098-2 @invariant @br-rule-098
  Scenario: BR-RULE-098 INV-2 holds -- pending_handoff includes handoff object
    Given an active SI session with session_id "sess-handoff"
    When the brand agent responds with session_status "pending_handoff"
    Then the response includes a handoff object with type "transaction" or "complete"
    # BR-RULE-098 INV-2: pending_handoff → handoff object present

  @T-UC-014-inv-098-3 @invariant @br-rule-098
  Scenario: BR-RULE-098 INV-3 holds -- complete status means no more messages
    Given an SI session with session_id "sess-done" in complete state
    Then no more messages are accepted for session "sess-done"
    # BR-RULE-098 INV-3: complete → no more messages

  @T-UC-014-inv-098-4 @invariant @br-rule-098 @error
  Scenario: BR-RULE-098 INV-4 violated -- message to terminal session rejected
    Given an SI session with session_id "sess-ended" in terminated state
    When the Buyer Agent sends si_send_message with session_id "sess-ended" and message "hello"
    Then the operation should fail
    And the error code should be "SESSION_ALREADY_TERMINATED"
    And the error message should contain "terminated"
    And the error should include "suggestion" field
    And the suggestion should contain "new session"
    # BR-RULE-098 INV-4: terminal session rejects messages

  @T-UC-014-inv-099-1 @invariant @br-rule-099
  Scenario: BR-RULE-099 INV-1 holds -- message present makes request valid
    Given an active SI session with session_id "sess-msg"
    When the Buyer Agent sends si_send_message with session_id "sess-msg" and message "what sizes do you have"
    Then the request is valid and the brand agent responds
    # BR-RULE-099 INV-1: message present → valid

  @T-UC-014-inv-099-2 @invariant @br-rule-099
  Scenario: BR-RULE-099 INV-2 holds -- action_response present makes request valid
    Given an active SI session with session_id "sess-msg"
    When the Buyer Agent sends si_send_message with session_id "sess-msg" and action_response with action "add_to_cart"
    Then the request is valid and the brand agent responds
    # BR-RULE-099 INV-2: action_response present → valid

  @T-UC-014-inv-099-3 @invariant @br-rule-099 @error
  Scenario: BR-RULE-099 INV-3 violated -- neither message nor action_response rejected
    Given an active SI session with session_id "sess-msg"
    When the Buyer Agent sends si_send_message with session_id "sess-msg" and no message and no action_response
    Then the operation should fail
    And the error code should be "MESSAGE_CONTENT_REQUIRED"
    And the error message should contain "message or action_response"
    And the error should include "suggestion" field
    And the suggestion should contain "text message or an action_response"
    # BR-RULE-099 INV-3: neither present → rejected

  @T-UC-014-inv-100-1 @invariant @br-rule-100
  Scenario: BR-RULE-100 INV-1 holds -- handoff_transaction returns ACP data
    Given an active SI session with session_id "sess-checkout"
    When the Buyer Agent sends si_terminate_session with session_id "sess-checkout" and reason "handoff_transaction"
    Then the response includes acp_handoff with checkout_url and checkout_token
    # BR-RULE-100 INV-1: handoff_transaction → ACP data required

  @T-UC-014-inv-100-2 @invariant @br-rule-100
  Scenario: BR-RULE-100 INV-2 holds -- non-transaction reason has no ACP data
    Given an active SI session with session_id "sess-exit"
    When the Buyer Agent sends si_terminate_session with session_id "sess-exit" and reason "user_exit"
    Then the response does not include acp_handoff
    # BR-RULE-100 INV-2: user_exit → no ACP data

  @T-UC-014-inv-100-3 @invariant @br-rule-100 @error
  Scenario: BR-RULE-100 INV-3 violated -- invalid termination reason rejected
    Given an active SI session with session_id "sess-bad"
    When the Buyer Agent sends si_terminate_session with session_id "sess-bad" and reason "cancelled"
    Then the operation should fail
    And the error code should be "TERMINATION_REASON_INVALID"
    And the error message should contain "reason"
    And the error should include "suggestion" field
    And the suggestion should contain "enumerated termination reasons"
    # BR-RULE-100 INV-3: invalid reason → rejected

  @T-UC-014-inv-100-4 @invariant @br-rule-100 @error
  Scenario: BR-RULE-100 INV-4 violated -- missing termination reason rejected
    Given an active SI session with session_id "sess-nope"
    When the Buyer Agent sends si_terminate_session with session_id "sess-nope" without a reason
    Then the operation should fail
    And the error code should be "TERMINATION_REASON_REQUIRED"
    And the error message should contain "reason"
    And the error should include "suggestion" field
    And the suggestion should contain "termination reason"
    # BR-RULE-100 INV-4: absent reason → rejected

  @T-UC-014-inv-101-1 @invariant @br-rule-101
  Scenario: BR-RULE-101 INV-1 holds -- offering_token is opaque to host
    Given a valid offering is available
    When the Buyer Agent receives si_get_offering response with offering_token
    Then the offering_token is opaque and the host must not parse or modify it
    # BR-RULE-101 INV-1: token opaque to host

  @T-UC-014-inv-101-2 @invariant @br-rule-101
  Scenario: BR-RULE-101 INV-2 holds -- stale token after TTL expiry
    Given the Buyer Agent received offering_token with ttl_seconds 60 at time T
    And ttl_seconds have elapsed since checked_at
    Then the host should re-fetch offering data
    # BR-RULE-101 INV-2: TTL expired → should re-fetch

  @T-UC-014-inv-101-3 @invariant @br-rule-101
  Scenario: BR-RULE-101 INV-3 holds -- valid token recalls prior context
    Given the Buyer Agent received offering_token "opq-valid" within TTL
    When the Buyer Agent sends si_initiate_session with offering_token "opq-valid"
    Then the brand recalls prior query context for the session
    # BR-RULE-101 INV-3: valid token → context recalled

  @T-UC-014-inv-101-4 @invariant @br-rule-101
  Scenario: BR-RULE-101 INV-4 holds -- expired token degrades gracefully
    Given the Buyer Agent has an expired offering_token "opq-stale"
    When the Buyer Agent sends si_initiate_session with offering_token "opq-stale"
    Then the session is created without offering context
    And no error is returned for the expired token
    # BR-RULE-101 INV-4: expired token → graceful degradation

  @T-UC-014-inv-102-1 @invariant @br-rule-102
  Scenario: BR-RULE-102 INV-1 holds -- initiate session returns unique unpredictable ID
    When the Buyer Agent sends si_initiate_session with valid context and identity
    Then the response contains a session_id that is unique and unpredictable
    # BR-RULE-102 INV-1: unique unpredictable session_id

  @T-UC-014-inv-102-2 @invariant @br-rule-102
  Scenario: BR-RULE-102 INV-2 holds -- valid session_id routes to correct session
    Given an active SI session with session_id "sess-valid"
    When the Buyer Agent sends si_send_message with session_id "sess-valid"
    Then the message is routed to the correct session
    # BR-RULE-102 INV-2: valid ID → correct routing

  @T-UC-014-inv-102-3 @invariant @br-rule-102 @error
  Scenario: BR-RULE-102 INV-3 violated -- nonexistent session_id rejected
    When the Buyer Agent sends si_send_message with session_id "no-such-session"
    Then the operation should fail
    And the error code should be "SESSION_NOT_FOUND"
    And the error message should contain "session"
    And the error should include "suggestion" field
    And the suggestion should contain "active session"
    # BR-RULE-102 INV-3: nonexistent ID → SESSION_NOT_FOUND

  @T-UC-014-inv-103-1 @invariant @br-rule-103
  Scenario: BR-RULE-103 INV-1 holds -- standard component type is renderable
    Given an active SI session
    When the brand agent returns a UI element with type "product_card"
    Then all compliant hosts must render the product_card
    # BR-RULE-103 INV-1: standard type → renderable

  @T-UC-014-inv-103-2 @invariant @br-rule-103
  Scenario: BR-RULE-103 INV-2 holds -- extension component may be ignored
    Given an active SI session
    When the brand agent returns a UI element with type "app_handoff"
    Then the host may ignore the element if unsupported
    And the brand must not rely on it for core functionality
    # BR-RULE-103 INV-2: extension type → optional, not for core flow

  @T-UC-014-inv-103-3 @invariant @br-rule-103 @error
  Scenario: BR-RULE-103 INV-3 violated -- UI element missing type is invalid
    Given an active SI session
    When the brand agent returns a UI element without a type field
    Then the UI element is invalid
    And the error code should be "UI_ELEMENT_TYPE_REQUIRED"
    And the error should include "suggestion" field
    And the suggestion should contain "type field"
    # BR-RULE-103 INV-3: missing type → invalid

  @T-UC-014-inv-103-4 @invariant @br-rule-103 @error
  Scenario: BR-RULE-103 INV-4 violated -- unknown UI element type rejected
    Given an active SI session
    When the brand agent returns a UI element with type "video_player"
    Then the UI element is rejected or ignored
    And the error code should be "UI_ELEMENT_TYPE_INVALID"
    And the error should include "suggestion" field
    And the suggestion should contain "standard component type"
    # BR-RULE-103 INV-4: unknown type → rejected

  @T-UC-014-inv-104-1 @invariant @br-rule-104
  Scenario: BR-RULE-104 INV-1 holds -- product_limit in range proceeds
    Given a valid offering with 20 matching products
    When the Buyer Agent sends si_get_offering with include_products true and product_limit 10
    Then the response contains up to 10 matching products
    And total_matching may indicate more products available
    # BR-RULE-104 INV-1: 1-50 → proceeds with bounded results

  @T-UC-014-inv-104-2 @invariant @br-rule-104
  Scenario: BR-RULE-104 INV-2 holds -- absent product_limit defaults to 5
    Given a valid offering with 20 matching products
    When the Buyer Agent sends si_get_offering with include_products true and no product_limit
    Then the response contains up to 5 matching products
    # BR-RULE-104 INV-2: absent → default 5

  @T-UC-014-inv-104-3 @invariant @br-rule-104 @error
  Scenario: BR-RULE-104 INV-3 violated -- product_limit below 1 rejected
    When the Buyer Agent sends si_get_offering with include_products true and product_limit 0
    Then the operation should fail
    And the error code should be "PRODUCT_LIMIT_TOO_LOW"
    And the error message should contain "product_limit"
    And the error should include "suggestion" field
    And the suggestion should contain "between 1 and 50"
    # BR-RULE-104 INV-3: < 1 → PRODUCT_LIMIT_TOO_LOW

  @T-UC-014-inv-104-4 @invariant @br-rule-104 @error
  Scenario: BR-RULE-104 INV-4 violated -- product_limit above 50 rejected
    When the Buyer Agent sends si_get_offering with include_products true and product_limit 51
    Then the operation should fail
    And the error code should be "PRODUCT_LIMIT_TOO_HIGH"
    And the error message should contain "product_limit"
    And the error should include "suggestion" field
    And the suggestion should contain "between 1 and 50"
    # BR-RULE-104 INV-4: > 50 → PRODUCT_LIMIT_TOO_HIGH

  @T-UC-014-part-reason @partition @reason @br-rule-100
  Scenario Outline: Termination reason partition validation - <partition>
    Given an active SI session with session_id "sess-reason"
    When the Buyer Agent sends si_terminate_session with session_id "sess-reason" and reason "<reason_value>"
    Then <outcome>

    Examples: Valid partitions
      | partition             | reason_value         | outcome                               |
      | handoff_transaction   | handoff_transaction  | session terminated with ACP handoff    |
      | handoff_complete      | handoff_complete     | session terminated normally             |
      | user_exit             | user_exit            | session terminated normally             |
      | session_timeout       | session_timeout      | session terminated normally             |
      | host_terminated       | host_terminated      | session terminated normally             |

    Examples: Invalid partitions
      | partition       | reason_value | outcome                                                                                               |
      | unknown_value   | cancelled    | error "TERMINATION_REASON_INVALID" with suggestion "Use one of the enumerated termination reasons"     |
      | empty_string    |              | error "TERMINATION_REASON_REQUIRED" with suggestion "Provide a termination reason"                     |

  @T-UC-014-bound-reason @boundary @reason @br-rule-100
  Scenario Outline: Termination reason boundary validation - <boundary_point>
    Given an active SI session with session_id "sess-reason"
    When the Buyer Agent sends si_terminate_session with reason at boundary "<boundary_point>"
    Then <outcome>
    # @bva boundary: reason empty string covered by reason_missing partition (empty and absent are equivalent for required enum)

    Examples: Boundary values
      | boundary_point       | outcome                                                                                               |
      | handoff_transaction  | session terminated with ACP handoff                                                                    |
      | handoff_complete     | session terminated normally                                                                            |
      | user_exit            | session terminated normally                                                                            |
      | session_timeout      | session terminated normally                                                                            |
      | host_terminated      | session terminated normally                                                                            |
      | cancelled            | error "TERMINATION_REASON_INVALID" with suggestion "Use one of the enumerated termination reasons"     |
      |                      | error "TERMINATION_REASON_REQUIRED" with suggestion "Provide a termination reason"                     |

  @T-UC-014-bound-reason-empty @boundary @reason @br-rule-100
  Scenario: Termination reason boundary -- empty string reason rejected
    Given an active SI session with session_id "sess-empty-reason"
    When the Buyer Agent sends si_terminate_session with session_id "sess-empty-reason" and reason ""
    Then the operation should fail
    And the error code should be "TERMINATION_REASON_REQUIRED"
    And the error message should contain "reason"
    And the error should include "suggestion" field
    And the suggestion should contain "termination reason"
    # BVA: empty string for required enum field

  @T-UC-014-part-txn-action @partition @transaction-action @br-rule-100
  Scenario Outline: Transaction intent action partition validation - <partition>
    Given an active SI session with session_id "sess-txn"
    When the Buyer Agent sends si_terminate_session with reason "handoff_transaction" and transaction_intent action "<action_value>"
    Then <outcome>

    Examples: Valid partitions
      | partition      | action_value | outcome                                                   |
      | purchase       | purchase     | session terminated with ACP handoff for purchase           |
      | subscribe      | subscribe    | session terminated with ACP handoff for subscription       |
      | not_provided   |              | session terminated with ACP handoff (action not specified)  |

    Examples: Invalid partitions
      | partition      | action_value | outcome                                                |
      | unknown_value  | refund       | error "TRANSACTION_ACTION_INVALID" with suggestion "Use purchase or subscribe" |

  @T-UC-014-bound-txn-action @boundary @transaction-action @br-rule-100
  Scenario Outline: Transaction intent action boundary validation - <boundary_point>
    Given an active SI session with session_id "sess-txn"
    When the Buyer Agent sends si_terminate_session with transaction_intent action at boundary "<boundary_point>"
    Then <outcome>
    # @bva boundary: transaction_intent.action empty string covered by empty_string partition

    Examples: Boundary values
      | boundary_point | outcome                                                                        |
      | purchase       | session terminated with ACP handoff for purchase                                |
      | subscribe      | session terminated with ACP handoff for subscription                            |
      | Not provided   | session terminated with ACP handoff (action not specified)                       |
      | refund         | error "TRANSACTION_ACTION_INVALID" with suggestion "Use purchase or subscribe"  |
      |                | error "TRANSACTION_ACTION_INVALID" with suggestion "Use purchase or subscribe"  |

  @T-UC-014-bound-txn-empty @boundary @transaction-action @br-rule-100
  Scenario: Transaction intent action boundary -- empty string action rejected
    Given an active SI session with session_id "sess-empty-action"
    When the Buyer Agent sends si_terminate_session with reason "handoff_transaction" and transaction_intent action ""
    Then the operation should fail
    And the error code should be "TRANSACTION_ACTION_INVALID"
    And the error message should contain "action"
    And the error should include "suggestion" field
    And the suggestion should contain "purchase or subscribe"
    # BVA: empty string for transaction_intent.action enum

  @T-UC-014-030 @extension @error @precondition @post-f1 @post-f2
  Scenario: Get offering with missing offering_id -- validation error
    When the Buyer Agent sends si_get_offering without an offering_id
    Then the operation should fail
    And the error code should be "OFFERING_ID_REQUIRED"
    And the error message should contain "offering_id"
    And the error should include "suggestion" field
    And the suggestion should contain "offering_id"
    # POST-F1: System state unchanged
    # POST-F2: Error explains missing required field
    # PRE-BIZ1: offering_id is required

  @T-UC-014-031 @extension @ext-a @error @precondition @post-f1 @post-f2
  Scenario: Initiate session with missing context -- validation error
    When the Buyer Agent sends si_initiate_session without context and with valid identity
    Then the operation should fail
    And the error code should be "CONTEXT_REQUIRED"
    And the error message should contain "context"
    And the error should include "suggestion" field
    And the suggestion should contain "context"
    # POST-F1: System state unchanged
    # POST-F2: Error explains missing required field
    # PRE-BIZ2: context is required for initiate_session

  @T-UC-014-032 @extension @ext-a @error @precondition @post-f1 @post-f2
  Scenario: Initiate session with missing identity -- validation error
    When the Buyer Agent sends si_initiate_session with context "explore shoes" but no identity object
    Then the operation should fail
    And the error code should be "IDENTITY_REQUIRED"
    And the error message should contain "identity"
    And the error should include "suggestion" field
    And the suggestion should contain "identity"
    # POST-F1: System state unchanged
    # POST-F2: Error explains missing required field
    # PRE-BIZ2: identity is required for initiate_session

  @T-UC-014-033 @extension @ext-b @error @br-rule-098
  Scenario: Send message response with pending_handoff but no handoff object -- HANDOFF_REQUIRED
    Given an active SI session with session_id "sess-broken"
    When the brand agent responds with session_status "pending_handoff" but omits the handoff object
    Then the response is invalid
    And the error code should be "HANDOFF_REQUIRED"
    And the error message should contain "handoff"
    And the error should include "suggestion" field
    And the suggestion should contain "handoff object"
    # BR-RULE-098 INV-2 violated: pending_handoff without handoff object

  @T-UC-014-034 @extension @ext-b @error @br-rule-103
  Scenario: UI element with missing required data fields -- UI_ELEMENT_DATA_REQUIRED
    Given an active SI session
    When the brand agent returns a UI element with type "link" but data object missing url field
    Then the UI element is invalid
    And the error code should be "UI_ELEMENT_DATA_REQUIRED"
    And the error message should contain "data fields"
    And the error should include "suggestion" field
    And the suggestion should contain "required data fields"
    # BR-RULE-103: Standard component missing required data

  @T-UC-014-035 @extension @ext-c @error @br-rule-100
  Scenario: Terminate with handoff_transaction but ACP generation fails -- ACP_HANDOFF_REQUIRED
    Given an active SI session with session_id "sess-acp-fail"
    When the Buyer Agent sends si_terminate_session with session_id "sess-acp-fail" and reason "handoff_transaction" but ACP generation fails
    Then the operation should fail
    And the error code should be "ACP_HANDOFF_REQUIRED"
    And the error message should contain "acp_handoff"
    And the error should include "suggestion" field
    And the suggestion should contain "ACP checkout data"
    # BR-RULE-100: handoff_transaction requires ACP data

  @T-UC-014-036 @extension @ext-b @error @br-rule-099
  Scenario: Send message with missing session_id -- SESSION_ID_REQUIRED
    When the Buyer Agent sends si_send_message with message "hello" but no session_id
    Then the operation should fail
    And the error code should be "SESSION_ID_REQUIRED"
    And the error message should contain "session_id"
    And the error should include "suggestion" field
    And the suggestion should contain "session_id from si_initiate_session"
    # BR-RULE-099: session_id is co-required with content

  @T-UC-014-037 @terminate-session @happy-path @post-s5
  Scenario Outline: Terminate session follow-up action -- <follow_up_action>
    Given an active SI session with session_id "sess-followup"
    When the Buyer Agent sends si_terminate_session with session_id "sess-followup" and reason "user_exit"
    Then the response contains terminated true
    And the response contains follow_up with action "<follow_up_action>"

    Examples:
      | follow_up_action    |
      | save_for_later      |
      | set_reminder        |
      | subscribe_updates   |
      | none                |

