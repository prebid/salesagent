# Generated from adcp-req @ cac2015cd7436b762053f469b952f94f262cf02f on 2026-08-20T12:03:24Z (merge mode)

Feature: BR-UC-011 Manage Accounts
  As a Buyer
  I want to query and provision billing accounts with the Seller Agent
  So that I can manage advertiser relationships and billing arrangements

  # Postconditions verified:
  #   POST-S1: Buyer knows which billing accounts are accessible to them
  #   POST-S2: Buyer knows each account's status
  #   POST-S3: Buyer knows the advertiser, billing proxy, rate card, and payment terms
  #   POST-S4: Buyer can paginate through accounts
  #   POST-S5: Buyer knows the seller-assigned account_id
  #   POST-S6: Buyer knows the action taken per account
  #   POST-S7: Buyer knows billing model for each account
  #   POST-S8: Buyer receives setup information for pending accounts
  #   POST-S9: Buyer knows which accounts were deactivated
  #   POST-S10: Buyer receives dry-run preview
  #   POST-F1: System state unchanged on failure
  #   POST-F2: Buyer knows what failed and the specific error code
  #   POST-F3: Application context echoed when possible

  Background:
    Given a Seller Agent is operational and accepting requests
    And a tenant is resolvable from the request context

  @T-UC-011-list-main @list @happy-path @post-s1 @post-s2 @post-s3 @partition @boundary
  Scenario: List accounts (authenticated_with_accounts)
    Given the Buyer is authenticated
    And the agent has 3 accessible accounts with statuses "active", "pending_approval", "suspended"
    When the Buyer Agent sends a list_accounts request
    Then the response is compliant with the list_accounts spec
    And the response contains an accounts array with 3 items
    And each account includes account_id, name, status, advertiser, rate_card, and payment_terms
    And the accounts are only those accessible to the authenticated agent
    # @bva accounts (response): multiple accounts visible
    # @bva authentication (account operations): valid token on list
    # POST-S1: Buyer knows which accounts are accessible
    # POST-S2: Buyer knows each account's status
    # POST-S3: Buyer knows advertiser, billing, rate card, payment terms

  @T-UC-011-list-status-filter @list @status-filter @partition @boundary
  Scenario Outline: List accounts filtered by status <status> (status_filter_match)
    Given the Buyer is authenticated
    And the agent has accounts with statuses "active", "pending_approval", "suspended", "closed"
    When the Buyer Agent sends a list_accounts request with status filter "<status>"
    Then the response is compliant with the list_accounts spec
    And the response contains only accounts with status "<status>"
    And accounts with other statuses are excluded
    # @bva status: active (first enum value), closed (last enum value)
    # @bva accounts (response): status filter = specific value with matches
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/account-status.json pointer=/enum
    # rejected row: the Given seeds no rejected account, so this row grades enum
    # acceptance (no validation error) — the matching-data case is
    # @T-UC-011-list-status-rejected below.

    Examples:
      | status             |
      | active             |
      | pending_approval   |
      | rejected           |
      | payment_required   |
      | suspended          |
      | closed             |

  @T-UC-011-list-status-rejected @list @status-filter @partition @boundary
  Scenario: List accounts filtered by status rejected returns only rejected accounts
    Given the Buyer is authenticated
    And the agent has accounts with statuses "rejected", "active", "active"
    When the Buyer Agent sends a list_accounts request with status filter "rejected"
    Then the response is compliant with the list_accounts spec
    And the response contains an accounts array with 1 items
    And every returned account has status "rejected"
    # @bva status: rejected — the one account-status enum value the filter outline never seeded
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/list-accounts-request.json pointer=/properties/status/enum

  @T-UC-011-list-no-accounts @list @empty-result @partition @boundary
  Scenario: List accounts returns empty when authenticated agent has no accounts (0 accounts visible)
    Given the Buyer is authenticated
    And the agent has no accessible accounts
    When the Buyer Agent sends a list_accounts request
    Then the response is compliant with the list_accounts spec
    And the response contains an empty accounts array
    And the response is not an error
    # @bva accounts (response): 0 accounts visible

  @T-UC-011-list-unauth @list @auth @partition @boundary
  Scenario: List accounts without authentication returns auth error (no token on list)
    Given the Buyer Agent has an unauthenticated connection
    When the Buyer Agent sends a list_accounts request without an authentication token
    Then the error is compliant with the AdCP error spec
    And the response is an error variant with no accounts array
    And the error code is "AUTH_MISSING"
    # @bva authentication (account operations): no token on list

  @T-UC-011-ext-c-a2a @list @extension @ext-c @error @a2a @post-f1 @post-f2 @post-f3
  Scenario: A2A list_accounts request with invalid auth token — error returned
    Given a tenant is resolvable from the request context
    And the Buyer has an invalid authentication token
    When the Buyer Agent sends a list_accounts skill request via A2A with the token
    Then the error is compliant with the AdCP error spec
    And the response arrives
    And the response contains error code AUTH_INVALID
    # Coverage gap alongside salesagent-7moz (BR-UC-010 @T-UC-010-ext-c-a2a): A2A
    # always validates a presented token regardless of the requested DISCOVERY_SKILLS
    # member (get_adcp_capabilities and list_accounts share the same boundary code path).
    # POST-F2: Buyer knows what failed and the error code
    # POST-F1: No state change (read-only)
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumDescriptions/AUTH_INVALID

  @T-UC-011-list-pagination @list @pagination @post-s4
  Scenario: List accounts with pagination
    Given the Buyer is authenticated
    And the agent has 120 accessible accounts
    When the Buyer Agent sends a list_accounts request with max_results 50
    Then the response is compliant with the list_accounts spec
    And the response contains 50 accounts
    And the response includes pagination metadata with has_more true and a cursor
    When the Buyer Agent sends a list_accounts request with the returned cursor
    Then the response contains 50 more accounts
    And the response includes pagination metadata with has_more true
    # POST-S4: Buyer can paginate through accounts

  @T-UC-011-list-pagination-terminal @list @pagination @post-s4 @partition @boundary
  Scenario: Terminal page omits the cursor when has_more is false
    Given the Buyer is authenticated
    And the agent has 20 accessible accounts
    When the Buyer Agent sends a list_accounts request with max_results 50
    Then the response is compliant with the list_accounts spec
    And the response contains 20 accounts
    And the response pagination has has_more false and no cursor
    # GRADED GREEN (salesagent-9if1): _apply_pagination emits cursor=None when has_more=false and
    # every transport pre-serializes via model_dump(mode="json") (exclude_none), so the cursor is
    # omitted (not null) on the wire — the invariant now holds on a2a/mcp/rest.
    # cursor is "Only present when has_more is true" — has_more=false MUST NOT carry a cursor
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/pagination-response.json pointer=/properties/cursor/description
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/universal/pagination-integrity-list-accounts.yaml pointer=phases/pagination_walk

  @T-UC-011-list-account-filter @list @account-filter @partition @boundary
  Scenario Outline: List accounts with exact account filter -- <key_shape>
    Given the Buyer is authenticated
    And accessible accounts exist for brand domains "acme-corp.com" and "nova-brands.com"
    When the Buyer Agent sends a list_accounts request with an account filter keyed by <key_shape> for brand domain "acme-corp.com"
    Then the response is compliant with the list_accounts spec
    And the response contains an accounts array with 1 items
    And the returned account has brand domain "acme-corp.com" and operator "acme-corp.com"
    # Graduated: _apply_list_account_filters honors req.account (AccountReference oneOf, both
    # account_id and natural-key branches), forwarded by all 3 transports.
    # AccountRef oneOf: account_id XOR natural key (brand + operator, optionally sandbox)
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/list-accounts-request.json pointer=/properties/account
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/account-ref.json pointer=/oneOf

    Examples:
      | key_shape             |
      | account_id            |
      | brand and operator    |

  @T-UC-011-list-authorization @list @authorization @partition
  Scenario: Per-account authorization block carries allowed_tasks when scope introspection is supported
    Given the Buyer is authenticated
    And the seller supports scope introspection for the authenticated agent
    And the agent has 1 accessible accounts
    When the Buyer Agent sends a list_accounts request
    Then the response is compliant with the list_accounts spec
    And each returned account includes an authorization object with required key "allowed_tasks"
    And each allowed_tasks array is a non-empty list of unique snake_case task names
    # XFAIL-EXPECTED: production gap — GH #1615 (account-with-authorization item shape is NEW in
    # 3.1.1; production Account schema has no authorization field, list items are bare — out of
    # #1592 A3 core scope, tracked separately as #1615)
    # allowed_tasks is the only required field of account-authorization; absence of the whole
    # object means "no introspection", NOT denial — callers MUST NOT infer access from absence
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/list-accounts-response.json pointer=/properties/accounts/items
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/account-authorization.json pointer=/required

  # DELETED: "list_accounts tolerates the 3.1 every-request envelope (idempotency_key + ext)".
  # READS TAKE NO IDEMPOTENCY KEY. A read is idempotent by construction, so there is no
  # at-most-once guarantee for a key to carry, and account/list-accounts-request.json does not
  # declare one. The scenario asserted that this seller ACCEPTS an undeclared field because the
  # pinned schema says additionalProperties: true -- but the DTO is the accepted shape here and
  # a pin permitting a sender to add fields is not an instruction to carry them inward
  # (CLAUDE.md critical pattern #7). ListAccountsRequest.idempotency_key was declared once to
  # satisfy exactly this obligation and removed again as an invented spec field; the scenario
  # outlived the reasoning that retired it. Its storyboard citation
  # (read-tool-idempotency.yaml) stays ledgered in tests/storyboard/known_failures.txt, which
  # is the honest record of a conformance duty this seller declines.

  @T-UC-011-list-status-filter-no-match @list @status-filter @empty-result @partition @boundary
  Scenario: List accounts with status filter returns empty when no matches (status filter = specific value with no matches)
    Given the Buyer is authenticated
    And the agent has accounts with statuses "active", "active", "active"
    When the Buyer Agent sends a list_accounts request with status filter "suspended"
    Then the response is compliant with the list_accounts spec
    And the response contains an empty accounts array
    And the response is not an error
    # @bva accounts (response): status filter = specific value with no matches

  @T-UC-011-list-invalid-status @list @validation @partition @boundary
  Scenario: List accounts with unknown status value not in enum
    Given the Buyer is authenticated
    When the Buyer Agent sends a list_accounts request with status filter "unknown_status"
    # A value outside the pinned status enum is a SCHEMA constraint violation, so the
    # seller answers INVALID_REQUEST ("violates schema constraints"), not
    # VALIDATION_ERROR ("business rules BEYOND schema validation"). The step asserted
    # VALIDATION_ERROR while the request was built in the test process and never
    # dispatched, so the code was never checked against the seller (salesagent-prkv.65).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json
    #   pointer=/INVALID_REQUEST
    Then the error is compliant with the AdCP error spec
    And the response contains a validation error
    And the error indicates the status value is not recognized
    # @bva status: Unknown string not in enum

  @T-UC-011-list-pagination-bva @list @pagination @bva @partition @boundary
  Scenario Outline: List accounts pagination boundary - max_results <value>
    Given the Buyer is authenticated
    And the agent has 200 accessible accounts
    When the Buyer Agent sends a list_accounts request with max_results <value>
    Then the response is compliant with the list_accounts spec
    And the response has outcome "<outcome>"

    Examples:
      | value | outcome                         |
      | 0     | validation error                |
      | 1     | success with 1 account          |
      | 50    | success with 50 accounts        |
      | 100   | success with 100 accounts       |
      | 101   | validation error                |

  @T-UC-011-list-status-all @list @status-filter @partition @boundary
  Scenario: List accounts with no status filter returns all statuses (status filter = 'all')
    Given the Buyer is authenticated
    And the agent has accounts with statuses "active", "pending_approval", "suspended", "closed"
    When the Buyer Agent sends a list_accounts request without a status filter
    Then the response is compliant with the list_accounts spec
    And the response contains accounts with all statuses
    And the result set is identical to requesting without any filter
    # @bva accounts (response): status filter = 'all'

  @T-UC-011-sync-create @sync @happy-path @post-s5 @post-s6 @partition @boundary
  Scenario: Sync new account -- single_brand_domain, all_created (1 account, all same action)
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator        | billing  |
    | acme-corp.com   | acme-corp.com   | operator |
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "created"
    And the account has a seller-assigned account_id
    And the account has status "active"
    And the response includes brand domain "acme-corp.com" echoed from request
    # @bva authentication (account operations): valid token on sync
    # @bva accounts (sync operation): 1 account (minimum)
    # @bva accounts (sync operation): all same action
    # @bva brand (brand-ref): single_brand_domain -- brand with domain only (single brand)
    # POST-S5: Buyer knows the seller-assigned account_id
    # POST-S6: Buyer knows the action taken per account

  @T-UC-011-sync-schema-valid @sync @schema @v3-1 @envelope
  Scenario: A sync_accounts response validates against its pinned AdCP schema
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator        | billing  |
    | acme-corp.com   | acme-corp.com   | operator |
    Then the response is compliant with the sync_accounts success spec
    And the response envelope carries status completed
    # core/protocol-envelope.json marks `status` REQUIRED on every task response
    # envelope, and sync-accounts-response.json composes that branch through a
    # top-level allOf -- so the requirement reaches this response by composition
    # rather than by being spelled out on it. That is how it went missing in a
    # real implementation: the response type declared no status at all, and no
    # transport noticed, because nothing graded the composed branch.
    # @source repo=adcp ref=v3.1-04f59d2d5 commit=04f59d2d5 path=static/schemas/source/account/list-accounts-request.json

  @T-UC-011-sync-multi-brand @sync @brand-identity @partition @boundary
  Scenario: Sync multi_brand_domain with brand_id and operator (brand with domain + brand_id)
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | brand.brand_id | operator        | billing  |
    | nova-brands.com | spark          | pinnacle-media.com | operator |
    | nova-brands.com | glow           | pinnacle-media.com | agent    |
    Then the response is compliant with the sync_accounts success spec
    And the response contains 2 account results
    And the account for brand domain "nova-brands.com" brand_id "spark" has action "created"
    And the account for brand domain "nova-brands.com" brand_id "glow" has action "created"
    And each account echoes brand domain and brand_id from the request
    # @bva brand (brand-ref): multi_brand_domain -- brand with domain + brand_id (multi brand)
    # @bva brand (brand-ref): brand with domain + brand_id + operator

  @T-UC-011-sync-brand-direct @sync @brand-identity @partition @boundary
  Scenario: Sync brand_direct -- brand operating own seat (operator is brand's domain)
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator        | billing  |
    | acme-corp.com   | acme-corp.com   | operator |
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "created"
    And the account operator is "acme-corp.com"
    And the account billing is "operator"
    # @bva brand (brand-ref): brand_direct -- brand operating own seat

  @T-UC-011-sync-update @sync @upsert @partition
  Scenario: Sync updates existing account -- all_updated
    Given the Buyer is authenticated
    And an account for brand domain "acme-corp.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing |
    | acme-corp.com   | acme-corp.com | agent   |
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "updated"
    And the account billing is "agent"

  @T-UC-011-sync-unchanged @sync @upsert @partition
  Scenario: Sync unchanged account is idempotent -- all_unchanged
    Given the Buyer is authenticated
    And an account for brand domain "acme-corp.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "unchanged"

  @T-UC-011-sync-idempotency-envelope @sync @idempotency @v3-1 @partition
  Scenario: sync_accounts accepts the buyer's client-generated idempotency_key
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request carrying idempotency_key "buyer-sync-key-000001" and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "created"
    # sync-accounts-request.json 3.1.1 lists idempotency_key in /required and describes it
    # as "Client-generated" — the buyer mints it, the seller accepts it. A transport that
    # rejects the field as an unknown input (REST body model, MCP tool signature) or drops
    # it (A2A skill handler) is non-conformant on a field the spec makes mandatory.
    # Storyboard: dist/compliance/3.1.1 has no account domain — this obligation is UNGRADED
    # by the conformance storyboard; the schema is the sole authority.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/required
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/idempotency_key

  @T-UC-011-sync-idempotency-malformed @sync @idempotency @validation @v3-1 @partition @boundary
  Scenario Outline: sync_accounts rejects a malformed idempotency_key -- <partition_name>
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request carrying idempotency_key "<key>" and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts error spec
    And the operation should fail
    And the error code should be "INVALID_REQUEST"
    # The value production validates MUST be the buyer's. A seller that substitutes a
    # server-minted uuid4 (or drops the field) would let both rows below succeed, so this
    # outline is what distinguishes "threads the buyer's key" from "fabricates its own".
    # @bva idempotency_key: 15 chars (one below minLength 16); disallowed charset
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/idempotency_key

    Examples:
      | key                  | partition_name  | boundary_point                    |
      | fifteen-chars-x      | below_min_length | length = 15 (minLength 16 - 1)   |
      | buyer sync key 0001! | bad_charset      | space and ! outside allowed set  |

  @T-UC-011-sync-idempotency-omitted @sync @idempotency @validation @v3-1 @partition @boundary
  Scenario: sync_accounts rejects a request that omits idempotency_key
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with no idempotency_key and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts error spec
    And the response arrives
    And the response contains error code INVALID_REQUEST
    And the response error field is idempotency_key
    # The sibling of the malformed outline above, and the one that proves the field is
    # REQUIRED rather than merely constrained: those rows send a key the pattern rejects,
    # this one sends none at all. Without it, a seller that made idempotency_key optional
    # would pass every idempotency scenario in this file -- which is exactly the state this
    # agent was in until salesagent-prkv.86, with the deviation recorded in a test docstring
    # and graded by nothing.
    # ABSENCE IS STATED, not implied: the harness defaults a conformant key into every
    # sync_accounts payload (a scenario grading `operator` must not be refused for a missing
    # key first), so this scenario names OMIT_IDEMPOTENCY_KEY to send none.
    # Storyboard: dist/compliance/3.1.1 has no account domain -- UNGRADED by the conformance
    # storyboard, so the schema is the sole authority, same as the accept scenario above.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/required

  @T-UC-011-sync-billing-enum @sync @billing @post-s7 @partition @boundary
  Scenario Outline: Sync with billing model <billing> -- <partition_name>
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing   |
    | acme-corp.com   | acme-corp.com | <billing> |
    Then the response is compliant with the sync_accounts success spec
    And the account billing is "<billing>"
    # @bva billing: operator (first enum value), advertiser (last enum value)
    # POST-S7: Buyer knows billing model for each account
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/billing-party.json pointer=/enum

    Examples:
      | billing    | partition_name     | boundary_point                |
      | operator   | operator_honored   | billing = operator            |
      | agent      | agent_honored      | billing = agent               |
      | advertiser | advertiser_honored | billing = advertiser          |

  @T-UC-011-sync-billing-advertiser @sync @billing @post-s7 @partition @boundary
  Scenario: Sync with billing "advertiser" is accepted per the billing-party enum
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator           | billing    |
    | acme-corp.com   | pinnacle-media.com | advertiser |
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "created"
    And the account billing is "advertiser"
    And the account has a seller-assigned account_id
    And the account has status "active"
    # billing-party enum = ["operator", "agent", "advertiser"]; the seller invoices the
    # advertiser directly even when a different operator places orders on their behalf.
    # status is in the per-item required set, and the spec example for an advertiser-billed
    # created account pins status "active" — an unpinned status let a rejected/pending result pass.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/billing-party.json pointer=/enum
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/properties/billing
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-response.json pointer=/oneOf/0/properties/accounts/items/required
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-response.json pointer=/examples/3

  @T-UC-011-sync-mixed @sync @upsert @partition
  Scenario: Sync mixed_results -- created and updated in same request (all different actions)
    Given the Buyer is authenticated
    And an account for brand domain "existing-brand.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain        | operator            | billing  |
    | new-brand.com       | new-brand.com       | operator |
    | existing-brand.com  | existing-brand.com  | agent    |
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "new-brand.com" has action "created"
    And the account for brand domain "existing-brand.com" has action "updated"

  @T-UC-011-sync-brand-echo @sync @invariant @partition
  Scenario: Sync echoes brand from request in per-account result (brand echo)
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | brand.brand_id | operator        | billing  |
    | nova-brands.com | spark          | pinnacle-media.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And the per-account result echoes brand domain "nova-brands.com" and brand_id "spark"
    # BR-RULE-056 INV-4: Request includes brand for an account -> response echoes same brand value
    # BR-RULE-058 INV-3: Account is processed -> response echoes brand (brand-ref) from request

  @T-UC-011-sync-shortest-domain @sync @brand-identity @partition @boundary
  Scenario: Sync with shortest valid domain (e.g., 'a.b')
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain | operator | billing  |
    | a.b          | a.b      | operator |
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "a.b" has action "created"
    # @bva brand (brand-ref): shortest valid domain (e.g., 'a.b')

  @T-UC-011-sync-natural-key-acceptance @sync @invariant @partition
  Scenario: Seller keeps accepting the natural-key AccountRef after echoing account_id
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And the account has a seller-assigned account_id
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the account for brand domain "acme-corp.com" has action "unchanged"
    And the echoed account_id equals the account_id from the first response
    # Sellers MAY echo a seller-assigned account_id, but MUST continue accepting the
    # natural-key AccountRef for every account provisioned in provisioning mode —
    # the second natural-key call must upsert to the SAME handle, never demand account_id
    # and never mint a different account_id (which would pass the bare-presence check while
    # breaking the stability invariant this scenario exists to grade).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-response.json pointer=/oneOf/0/properties/accounts/items/properties/account_id/description
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/description

  @T-UC-011-sync-settings-update @sync @settings-update @partition @boundary
  Scenario: Settings-update mode targets an existing account by account_id without provisioning
    Given the Buyer is authenticated
    And an account for brand domain "acme-corp.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with a settings-update entry keyed by the existing account's account_id setting payment_terms "net_45"
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "updated"
    And the account payment_terms is "net_45"
    When the Buyer Agent sends a list_accounts request
    Then the response contains an accounts array with 1 items
    # "no new account was created" is only observable on the account census, not on the seller's
    # internal state: a settings-update MUST NOT provision, so the post-call list stays at the one
    # pre-existing account. A second (provisioned) entry would surface with action "created".
    # Graduated: settings-update (AccountReference) mode implemented via
    # _process_settings_update_entry (both AccountReference1/account_id and
    # AccountReference2/natural-key branches), mode-exclusivity enforced in _impl before dispatch.
    # Settings-update entry carries `account` (AccountRef); trio fields MUST be absent;
    # the seller updates settable state with no provisioning side effects
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/properties/account
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/oneOf/1

  @T-UC-011-sync-settings-update-no-provision @sync @settings-update @error @post-f1 @post-f2 @partition @boundary
  Scenario: Settings-update entry never provisions -- unknown account rejected with UNSUPPORTED_PROVISIONING
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with a settings-update entry keyed by unknown account_id "acc_does_not_exist"
    Then the response is compliant with the sync_accounts success spec
    And the settings-update entry has action "failed"
    And the accounts entry carries error code "UNSUPPORTED_PROVISIONING"
    And the per-account error recovery is "correctable"
    And the per-account error with code "UNSUPPORTED_PROVISIONING" carries a non-empty suggestion
    When the Buyer Agent sends a list_accounts request
    Then the response contains an empty accounts array
    # Graduated: unmatched settings-update references are rejected with UNSUPPORTED_PROVISIONING.
    # "When `account` is present, the seller MUST NOT create a new account — entries that
    # would otherwise trigger provisioning are rejected with UNSUPPORTED_PROVISIONING"
    # (recovery "correctable" per the enum metadata). "No accounts were modified" is graded on the
    # census: nothing was seeded, so the post-call list stays empty.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/properties/account/description
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumMetadata/UNSUPPORTED_PROVISIONING
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/error.json pointer=/properties/recovery/enum

  @T-UC-011-delete-missing-brandless @sync @delete-missing @error @partition @boundary
  Scenario: delete_missing over a persisted brand-less account returns a typed error, never a crash
    Given the Buyer is authenticated
    And the agent previously synced accounts for brand domain "acme-corp.com" and "old-brand.com"
    And the previously synced account for brand domain "old-brand.com" has no brand recorded
    When the Buyer Agent sends a sync_accounts request with delete_missing true and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts error spec
    And the response arrives
    And the response contains error code CONFIGURATION_ERROR
    # The DEACTIVATION branch reads brand off every account the request did not mention,
    # to report it as closed. That read has the same seller-side-inconsistency
    # exposure as the settings-update echo -- accounts.brand is NULLABLE -- and it is
    # a SEPARATE code path: the settings-update scenario never reaches it, because a
    # settings-update entry names its target and delete_missing walks the ones it
    # does not. Both were bare `assert ... "should be unreachable"` before #1721,
    # i.e. an AssertionError and an untyped 500 with no code, recovery or suggestion.
    # NOT-IN-SPEC (code choice, same reasoning as the settings-update sibling): the
    # pinned 3.1.1 enum defines no INTERNAL_ERROR and no "the seller's own stored
    # state is inconsistent" code; CONFIGURATION_ERROR's enumMetadata describes
    # exactly this posture (seller-side fault, buyer cannot resolve, MUST NOT
    # auto-retry), hence recovery "terminal".
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-response.json pointer=/oneOf/1
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumMetadata/CONFIGURATION_ERROR

  @T-UC-011-sync-settings-update-brandless @sync @settings-update @error @partition @boundary
  Scenario: Settings-update against a persisted brand-less account returns a typed error, never a crash
    Given the Buyer is authenticated
    And an account for brand domain "acme-corp.com" already exists with billing "operator"
    And the persisted account has no brand recorded
    When the Buyer Agent sends a sync_accounts request with a settings-update entry keyed by the existing account's account_id setting payment_terms "net_45"
    Then the response is compliant with the sync_accounts error spec
    And the response arrives
    And the response contains error code CONFIGURATION_ERROR
    # accounts.brand is a NULLABLE column, so a brand-less persisted row is a state the
    # seller's own storage permits. The settings-update branch reads that row's brand to echo it
    # back, and today it guards the read with a bare `assert ... "should be unreachable"`
    # (src/core/tools/accounts.py) — an AssertionError, which is not an AdCP error at all:
    # the buyer gets an untyped 500 through the generic transport lane, with no code, no
    # recovery, and no suggestion. Whatever the seller does about its own corrupt row, the
    # response MUST still be a conformant error object: sync-accounts-response.json's error
    # variant (oneOf/1) with a canonical code, and core/error.json makes `recovery` the
    # normative carrier of retry semantics.
    # NOT-IN-SPEC (the code choice, stated so it is not read as a spec quotation): the pinned
    # 3.1.1 enum defines no INTERNAL_ERROR, and none of its codes name "the seller's own
    # persisted state is inconsistent". CONFIGURATION_ERROR is the one pinned code whose
    # enumMetadata describes exactly this posture — a seller-side fault the buyer cannot
    # resolve and MUST NOT auto-retry ("surface to a human at the seller") — hence recovery
    # "terminal". A transient/correctable code would be a lie: retrying, or editing the
    # request, cannot make a brand appear on the stored row.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-response.json pointer=/oneOf/1
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumMetadata/CONFIGURATION_ERROR
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/error.json pointer=/properties/recovery

  @T-UC-011-sync-mode-exclusive @sync @settings-update @validation @partition @boundary
  Scenario: Entry carrying both an account reference and the provisioning trio is rejected
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with an entry carrying both an account reference and the provisioning trio
    Then the response is compliant with the sync_accounts error spec
    And the request is rejected at the operation level with error code "VALIDATION_ERROR" naming field "accounts[0]"
    # Graduated: mode-exclusivity enforced in _impl before dispatch (VALIDATION_ERROR naming accounts[i]).
    # An entry satisfying BOTH branches violates the item oneOf (exactly one), which is a structural
    # request-schema violation — graded as an operation-level error variant (top-level errors[],
    # response oneOf branch 1) carrying VALIDATION_ERROR (recovery "correctable" per the enum) with
    # field pointing at the offending entry — NOT a per-account "failed" result (branch 0), which is
    # for semantically-valid entries that fail a business rule. Production currently validates the
    # both-shapes entry as the ProvisioningMode branch and silently ignores the extra `account`, so it
    # provisions instead of rejecting.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/oneOf
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-response.json pointer=/oneOf/1
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumMetadata/VALIDATION_ERROR
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/error.json pointer=/properties/field

  @T-UC-011-ext-a-no-token @sync @ext-a @auth @error @post-f1 @post-f2 @partition @boundary
  Scenario: Sync without authentication -- sync_no_token returns error_auth (no token on sync)
    Given the Buyer Agent has an unauthenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts error spec
    And the response is an error variant with no accounts array
    And the error code is "AUTH_MISSING"
    And the error should include "suggestion" field with remediation guidance
    And no accounts were modified on the seller
    # @bva authentication (account operations): no token on sync
    # POST-F1: System state unchanged
    # POST-F2: Buyer knows what failed

  @T-UC-011-ext-a-expired @sync @ext-a @auth @error @partition @boundary
  Scenario: Sync with expired token -- sync_invalid_token returns AUTH_INVALID (invalid token on sync)
    # Pairs with @T-UC-011-ext-a-no-token above: the two scenarios are the two
    # sides of the v3.1.1 auth split, and the axis is CREDENTIAL PRESENCE.
    # No token -> AUTH_MISSING / correctable (retrying with credentials can
    # succeed); a token presented and rejected -> AUTH_INVALID / terminal (the
    # same credential retried unmodified is rejected again).
    Given the Buyer Agent has an A2A connection with an expired token
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts error spec
    And the response is an error variant
    And the response arrives
    And the response contains error code AUTH_INVALID
    And the error should include "suggestion" field with remediation guidance
    # @bva authentication (account operations): invalid token on sync
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumDescriptions/AUTH_INVALID

  @T-UC-011-ext-b-partial @sync @partial-failure @invariant @partition @boundary
  Scenario: Sync partial_failure -- success_partial_failure with action=failed (action=failed with errors)
    Given the Buyer is authenticated
    And the seller does not support "advertiser" billing
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain        | operator            | billing    |
    | acme-corp.com       | acme-corp.com       | operator   |
    | nova-motors.com     | nova-motors.com     | advertiser |
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "created"
    And the account for brand domain "nova-motors.com" has action "failed"
    And the failed account includes a per-account errors array
    And the response does not contain an operation-level errors array

  @T-UC-011-ext-c-rejected @sync @ext-c @billing @error @partition @boundary
  Scenario: Seller rejects unsupported billing -- billing_rejected (billing = unsupported value for seller)
    Given the Buyer is authenticated
    And the seller does not support "operator" billing
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "failed"
    And the account has status "rejected"
    And the accounts entry carries error code "BILLING_NOT_SUPPORTED"
    And the error message explains the billing model is not available
    And the error should include "suggestion" field with remediation guidance
    And the per-account error recovery is "correctable"
    And the per-account error details scope is "capability"
    And the per-account error details supported_billing echoes the seller's supported billing values
    # @bva billing: billing = unsupported value for seller
    # BR-RULE-059 INV-2: Request includes billing model the seller does not support -> action=failed, status=rejected, BILLING_NOT_SUPPORTED
    # POST-F2: Buyer knows what failed and the specific error code
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/universal/billing-gate-dispatch.yaml pointer=phases/capability_gate/steps/sync_accounts_unsupported_billing
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/error-details/billing-not-supported.json

  @T-UC-011-billing-gate-recover @sync @ext-c @billing @recovery @partition
  Scenario: Buyer recovers from BILLING_NOT_SUPPORTED by retrying with a supported value
    Given the Buyer is authenticated
    And the seller supports "agent" billing but not "operator" billing
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "failed"
    And the accounts entry carries error code "BILLING_NOT_SUPPORTED"
    When the Buyer Agent retries the sync_accounts request with billing "agent" and a fresh idempotency_key
    Then the account for brand domain "acme-corp.com" has action "created"
    And the account billing is "agent"
    # GRADED GREEN (salesagent-9jiu): the retry-with-supported-value recovery flow is now
    # wired and passes on a2a/mcp/rest — the rejected first leg is not persisted
    # (_check_billing_policy continues), so the keyless re-dispatch provisions a fresh account.
    # Recovery: pick a value from supported_billing and retry — a NEW request (the rejected
    # leg left no natural-key row, so no IDEMPOTENCY_CONFLICT). The retry carries a genuinely
    # fresh key: sync-accounts-request.json 3.1.1 lists idempotency_key in /required, so the
    # keyless re-dispatch this note used to describe would be refused at the boundary before
    # reaching the recovery. (It also claimed REST rejects the field as extra; SyncAccountsBody
    # derives from the DTO and declares it, so that was never true.)
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/universal/billing-gate-dispatch.yaml pointer=phases/per_agent_gate_recover (recover-leg mechanics; capability-gate recovery narrative in the storyboard header)

  @T-UC-011-billing-agent-gate-reject @sync @billing @per-agent-gate @error @partition
  Scenario: Passthrough-only buyer agent submitting billing "agent" is rejected with BILLING_NOT_PERMITTED_FOR_AGENT
    Given the Buyer is authenticated
    And the Buyer Agent is registered with the seller as passthrough-only
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator           | billing  |
    | acme-corp.com   | pinnacle-media.com | agent    |
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "failed"
    And the account has status "rejected"
    And the accounts entry carries error code "BILLING_NOT_PERMITTED_FOR_AGENT"
    And the per-account error recovery is "correctable"
    And the per-account error details rejected_billing is "agent"
    And the per-account error details suggested_billing is "operator"
    And the per-account error details do not include permitted_billing, rate_card, payment_terms, credit_limit, billing_entity, or account_id
    # XFAIL-EXPECTED: production gap — GH #1772 (no per-buyer-agent commercial gate exists in production)
    # Clamped details shape (additionalProperties: false) — the per-agent code MUST NOT act as
    # a commercial-state oracle; only rejected_billing + optional suggested_billing may appear.
    # Only emitted under ESTABLISHED agent identity — unidentified callers get BILLING_NOT_SUPPORTED.
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/universal/billing-gate-dispatch.yaml pointer=phases/per_agent_gate_reject/steps/sync_accounts_passthrough_rejects_agent
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/error-details/billing-not-permitted-for-agent.json

  @T-UC-011-billing-agent-gate-recover @sync @billing @per-agent-gate @recovery @partition
  Scenario: Passthrough-only buyer agent recovers autonomously via suggested_billing
    Given the Buyer is authenticated
    And the Buyer Agent is registered with the seller as passthrough-only
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator           | billing  |
    | acme-corp.com   | pinnacle-media.com | agent    |
    Then the response is compliant with the sync_accounts success spec
    And the accounts entry carries error code "BILLING_NOT_PERMITTED_FOR_AGENT"
    When the Buyer Agent retries the sync_accounts request with the seller's suggested_billing value and a fresh idempotency_key
    Then the account for brand domain "acme-corp.com" has action "created"
    And the account billing is "operator"
    # XFAIL-EXPECTED: production gap — GH #1772 (per-agent gate + autonomous recovery unimplemented)
    # The recover phase is NOT a replay: different payload requires a fresh idempotency_key
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/universal/billing-gate-dispatch.yaml pointer=phases/per_agent_gate_recover/steps/sync_accounts_recover_with_suggested

  @T-UC-011-ext-c-mixed @sync @ext-c @billing @partial-failure @partition
  Scenario: Billing rejection is per-account -- other accounts still succeed
    Given the Buyer is authenticated
    And the seller supports "agent" billing but not "operator" billing
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain      | operator          | billing  |
    | good-brand.com    | good-brand.com    | agent    |
    | bad-brand.com     | bad-brand.com     | operator |
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "good-brand.com" has action "created"
    And the account for brand domain "bad-brand.com" has action "failed"
    And the failed account has status "rejected" with BILLING_NOT_SUPPORTED error
    And the error should include "suggestion" field with remediation guidance
    # BR-RULE-059 INV-2 + BR-RULE-057 INV-1: rejected billing produces per-account failure within success variant

  @T-UC-011-ext-c-invalid-enum @sync @billing @validation @partition @boundary
  Scenario: Billing value not in enum -- invalid_billing_value (billing = invalid string)
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | prepaid  |
    Then the response is compliant with the sync_accounts error spec
    And the account processing fails with a validation error for billing
    # @bva billing: billing = invalid string

  @T-UC-011-ext-d-pending-url @sync @approval @post-s8 @partition @boundary
  Scenario: Account pending_with_url -- setup with url + message + expires_at (status = pending_approval with setup)
    Given the Buyer is authenticated
    And the seller requires credit review for new accounts
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And the account has status "pending_approval"
    And the account has action "created"
    And the account includes a setup object
    And the setup object includes a message describing the required action
    And the setup object includes a URL for the human buyer
    And the setup object includes an expires_at timestamp
    # POST-S8: Buyer receives setup information

  @T-UC-011-ext-d-pending-message @sync @approval @partition @boundary
  Scenario: Account pending_message_only -- setup with message only
    Given the Buyer is authenticated
    And the seller requires legal review for new accounts
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And the account has status "pending_approval"
    And the setup object includes a message
    And the setup object does not include a URL

  @T-UC-011-ext-d-active @sync @approval @partition @boundary
  Scenario: Account immediately active -- active_no_setup (status = active (no setup))
    Given the Buyer is authenticated
    And the seller auto-approves new accounts
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And the account has status "active"
    And the account does not include a setup object

  @T-UC-011-ext-d-push @sync @push-notification @partition
  Scenario: Push notification for async status changes -- with_push_notification
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    And the request includes a push_notification_config with url "https://agent.com/webhooks"
    Then the response is compliant with the sync_accounts success spec
    And the system registers the webhook for async account status notifications
    And when the account transitions from "pending_approval" to "active"
    Then a push notification is sent to "https://agent.com/webhooks"

  @T-UC-011-notif-register-paused @sync @notification-configs @partition @boundary
  Scenario: Register a paused account-level notification subscriber and read it back
    Given the Buyer is authenticated
    And the Buyer Agent has published a signing key the seller can resolve
    And the Buyer Agent signs the request
    When the Buyer Agent sends a sync_accounts request provisioning brand domain "acme-corp.com" with a paused notification config subscriber "buyer-primary" for url "https://buyer.example/webhooks/adcp/creative", event_types "creative.status_changed, creative.purged", and legacy Bearer authentication
    Then the response is compliant with the sync_accounts success spec
    And the account notification_configs echo exactly 1 subscriber
    And the echoed subscriber "buyer-primary" has url "https://buyer.example/webhooks/adcp/creative" and active false
    And the echoed subscriber has event_types "creative.status_changed, creative.purged"
    And the echoed subscriber's authentication object omits "credentials"
    # The `authentication` block and its "omits credentials" Then were REMOVED under
    # #1291 D1 and are RESTORED here, SIGNED (salesagent-n78j0.1.3).
    # The removal itself was correct at the time: security.mdx @ v3.1.1 :1462-1465
    # ("Downgrade and injection resistance") makes a seller that SUPPORTS request signing
    # reject an UNSIGNED request carrying accounts[].notification_configs[].authentication
    # with `request_signature_required`, D1 made this deployment such a seller, and a BDD
    # scenario could only dispatch unsigned — so the scenario was asserting an outcome the
    # pin FORBIDS. What has changed is the second half of that reasoning, not the first:
    # the recorded rationale for not simply signing it ("signing would buy nothing in
    # exchange for the e2e_rest blast radius") stopped being true the moment
    # salesagent-n78j0.1.1 gave the cross-transport env a real signature on all four legs.
    # Signing now buys the thing that was actually missing: the write-only credential rule
    # is graded ON THE WIRE, on every transport, against a request that really registers a
    # credential — which the impl-level test below cannot do (it never crosses a boundary
    # where an echo could leak differently).
    # The two integration tests stay and are not duplicated by this: they grade the
    # ADJACENT obligations — that the impl scrubs credentials
    # (tests/integration/test_sync_accounts_credentials_are_write_only.py, including the
    # whole-document grep this scenario does not do), and that the UNSIGNED registration is
    # refused (tests/integration/test_request_signature_operations.py
    # ::TestWebhookCredentialsOutrankTheBucket::test_an_account_notification_config
    # _authentication_forces_a_signature). This scenario grades the third: the
    # SIGNED registration is accepted and echoes no credential.
    When the Buyer Agent sends a list_accounts request
    Then the listed account for brand domain "acme-corp.com" echoes subscriber "buyer-primary" with active false
    # Graduated (T2 increment F4a): accounts.notification_configs now persists as a
    # whole-array JSONType column and is echoed on sync_accounts + list_accounts with
    # authentication.credentials scrubbed.
    # Paused entries (active: false) may skip only the outbound proof challenge; the seller MUST
    # persist and echo applied state on sync_accounts AND list_accounts, credentials write-only
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/properties/notification_configs
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-response.json pointer=/oneOf/0/properties/accounts/items/properties/notification_configs
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/universal/notification-config-lifecycle.yaml pointer=phases/register_and_echo_paused_subscriber

  @T-UC-011-notif-replace-clear @sync @notification-configs @partition @boundary
  Scenario: Re-sending a subscriber_id replaces in place; an empty array clears the set
    Given the Buyer is authenticated
    And an account for brand domain "acme-corp.com" exists with a paused notification config subscriber "buyer-primary" for url "https://buyer.example/webhooks/adcp/creative"
    When the Buyer Agent sends a sync_accounts request re-sending subscriber "buyer-primary" as paused with url "https://buyer.example/webhooks/adcp/paused" and event_types "creative.purged"
    Then the response is compliant with the sync_accounts success spec
    And the account notification_configs echo exactly 1 subscriber
    And the echoed subscriber "buyer-primary" has url "https://buyer.example/webhooks/adcp/paused" and active false
    And the echoed subscriber has event_types "creative.purged"
    When the Buyer Agent sends a sync_accounts request with an empty notification_configs array for brand domain "acme-corp.com"
    Then the account notification_configs echo exactly 0 subscribers
    # Graduated (T2 increment F4a): declarative-replace semantics implemented (omit
    # preserves, [] clears, re-sent subscriber_id replaces in place).
    # Declarative replace: full desired array replaces the persisted set; re-sending an existing
    # subscriber_id replaces (never duplicates); [] removes all subscribers; seller MUST NOT merge
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/properties/notification_configs/description
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/universal/notification-config-lifecycle.yaml pointer=phases/replace_pause_and_clear

  @T-UC-011-notif-omit-preserves @sync @notification-configs @partition @boundary
  Scenario: Omitting notification_configs leaves persisted subscribers unchanged
    Given the Buyer is authenticated
    And an account for brand domain "acme-corp.com" exists with a paused notification config subscriber "buyer-primary" for url "https://buyer.example/webhooks/adcp/creative"
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And the account notification_configs echo exactly 1 subscriber
    And the echoed subscriber "buyer-primary" has url "https://buyer.example/webhooks/adcp/creative" and active false
    # Graduated (T2 increment F4a): omitting notification_configs leaves persisted
    # subscribers unchanged.
    # "Omit this field to leave existing subscribers unchanged" — omission is not clearance
    # The seed declares a PAUSED subscriber (active false) so the echoed active flag is a
    # declared input, not an undeclared fixture default (F19)
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/properties/notification_configs/description
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/notification-config.json pointer=/properties/active

  @T-UC-011-notif-event-scope-reject @sync @notification-configs @error @post-f1 @post-f2 @partition @boundary
  Scenario: Media-buy-anchored event type on the account surface is rejected per entry
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request provisioning brand domain "acme-corp.com" with a paused notification config subscriber "delivery-reports" for url "https://buyer.example/webhooks/adcp/account" and event_types "scheduled"
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "failed"
    And the account has status "rejected"
    And the accounts entry carries error code "VALIDATION_ERROR"
    And the per-account error field points at "notification_configs[0].event_types[0]"
    # Graduated (T2 increment F4b): _check_notification_configs rejects
    # media-buy-anchored event types on the account surface pre-persist.
    # Media-buy-anchored event types (scheduled, final, delayed, adjusted, impairment) do not
    # belong on the account surface; also no account-lifecycle event types exist here (no
    # account.status_changed — poll list_accounts or use push_notification_config instead)
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/properties/notification_configs/description
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/universal/notification-config-event-scope.yaml pointer=steps/sync_accounts_rejects_scheduled_account_notification

  @T-UC-011-notif-duplicate-subscriber @sync @notification-configs @error @post-f2 @partition @boundary
  Scenario: Duplicate subscriber_id values within one submitted array are rejected
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request provisioning brand domain "acme-corp.com" with two notification config entries both using subscriber "buyer-primary"
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "failed"
    And the account has status "rejected"
    And the accounts entry carries error code "VALIDATION_ERROR"
    And the per-account error field points at "notification_configs[1].subscriber_id"
    # Graduated (T2 increment F4b): _check_notification_configs rejects duplicate
    # subscriber_id values within one submitted array pre-persist.
    # "Duplicate subscriber_id values within one submitted array are rejected" — no
    # last-write-wins merging, no duplicate subscriptions
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/properties/notification_configs/description
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/universal/notification-config-rejections.yaml pointer=steps/sync_accounts_rejects_duplicate_subscriber_id

  @T-UC-011-notif-activation-proof-fail @sync @notification-configs @error @post-f1 @post-f2 @partition @boundary
  Scenario: Active subscriber whose proof-of-control challenge fails is rejected and prior state kept
    Given the Buyer is authenticated
    And an account for brand domain "acme-corp.com" exists with a paused notification config subscriber "buyer-primary" for url "https://buyer.example/webhooks/adcp/creative"
    And the webhook proof-of-control challenge for "https://buyer.example/webhooks/adcp/unreachable" fails
    When the Buyer Agent sends a sync_accounts request re-sending subscriber "buyer-primary" as active with url "https://buyer.example/webhooks/adcp/unreachable"
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "failed"
    And the accounts entry carries error code "VALIDATION_ERROR"
    And the per-account error field points at "notification_configs[0].url"
    And the account keeps its prior notification_configs set unchanged
    # Graduated (T2 increment F4c): NotificationProofService performs a bounded
    # proof-of-control challenge before the write transaction opens; a failed proof rejects
    # the entry with VALIDATION_ERROR at notification_configs[j].url and writes nothing.
    # Before activating a new/changed active subscriber the seller MUST complete the
    # proof-of-control challenge; on failure it rejects the entry (action=failed), leaves the
    # prior notification_configs[] set unchanged, and reports VALIDATION_ERROR at the url field
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/properties/notification_configs/description (Activation proof paragraph)

  # ══════════════════════════════════════════════════════════════════════════
  # Entry-field disposition (salesagent-gcze step 12)
  #
  # Every field a sync_accounts entry can carry has ONE declared disposition per
  # entry mode. The five scenarios below grade the dispositions that no scenario
  # graded before, so the single field-policy table that replaces the two
  # hand-maintained allowlists cannot be written to match the code instead of the
  # contract. Two of them (billing-forbidden, preferred_reporting_protocol) pin
  # behavior production ALREADY has: they are what EARNS those rows the
  # `spec_forbidden` / `ignored_by_design` label rather than "undecided debt".
  # ══════════════════════════════════════════════════════════════════════════

  # DELETED: "A provisioning re-sync that omits governance_agents preserves the binding".
  # Its own comment called governance_agents a LOCAL EXTENSION accepted "only via
  # additionalProperties" -- and this seller does not honour additionalProperties: the DTO is
  # the accepted shape (CLAUDE.md critical pattern #7). So the scenario could never dispatch
  # the field it depends on; it failed on all three transports with INVALID_REQUEST.
  #
  # NOT re-homed by declaring governance_agents on the sync DTO, because the pin does not
  # declare it on that request (account/sync-accounts-request.json items carry 9 properties and
  # this is not one) and the spec's designated surface is sync_governance.
  #
  # The INVARIANT it graded is real and is filed, not discarded: an omitting re-sync must not
  # clear the binding. Production already resolves it that way (_resolve_governance_agents,
  # omission-preserving, written after salesagent-gcze where a re-sync DID wipe it). What does
  # not exist is check_governance -- the consumer that would make a wipe a governance BYPASS is
  # referenced only in comments, so the gate is unbuilt and the scenario was anticipating it.

  @T-UC-011-settings-update-sandbox-reject @sync @list @settings-update @sandbox @error @post-f1 @post-f2 @partition @boundary
  Scenario: Settings-update entry carrying entry-root sandbox is rejected per account
    Given the Buyer is authenticated
    And an account for brand domain "acme-corp.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with a settings-update entry keyed by the existing account's account_id carrying entry-root sandbox true
    Then the response is compliant with the sync_accounts success spec
    And the settings-update entry has action "failed"
    And the accounts entry carries error code "UNSUPPORTED_FEATURE"
    And the per-account error recovery is "correctable"
    And the per-account error field points at "sandbox"
    When the Buyer Agent sends a list_accounts request
    Then the listed account for brand domain "acme-corp.com" has sandbox false
    # Schema-LEGAL on this branch (sandbox is absent from the settings-update `not:` list) but
    # scoped by its own description to provisioning mode, and today silently ignored.
    # It is the ONE mode-inapplicable field that escalates from declared no-op to explicit
    # rejection, because sandbox is part of the buyer-declared natural key: honoring it would
    # re-key the account and orphan it from every subsequent natural-key sync.
    # Per-account failure (action "failed"), NOT operation-level — the field is schema-legal,
    # so an operation-level raise would kill an otherwise valid batch.
    # UNSUPPORTED_FEATURE over UNSUPPORTED_PROVISIONING: the latter's suggestion is about entry
    # SHAPE ("re-issue with the entry shape the seller supports"), the former's is literally
    # "remove unsupported fields" — which is the buyer action here.
    # Storyboard: UNGRADED (no 3.1.1 storyboard sends entry-root sandbox in settings-update).
    # POST-F1: the account's persisted sandbox flag is unchanged, so it is still findable
    # under its original natural key.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/properties/sandbox/description
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/oneOf/1
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/account.json pointer=/properties/sandbox
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumMetadata/UNSUPPORTED_FEATURE

  @T-UC-011-billing-entity-roundtrip @sync @list @settings-update @billing-entity @partition @boundary
  Scenario: billing_entity is applied in both modes and echoed with bank details stripped
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request provisioning brand domain "acme-corp.com" with a billing_entity legal_name "Acme GmbH" and bank details
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "created"
    And the echoed billing_entity legal_name is "Acme GmbH"
    And the echoed billing_entity omits "bank"
    When the Buyer Agent sends a sync_accounts request with a settings-update entry keyed by the existing account's account_id refining billing_entity legal_name to "Acme Holdings GmbH"
    Then the settings-update entry has action "updated"
    And the echoed billing_entity legal_name is "Acme Holdings GmbH"
    When the Buyer Agent sends a list_accounts request
    Then the listed account for brand domain "acme-corp.com" echoes billing_entity legal_name "Acme Holdings GmbH"
    And the listed billing_entity omits "bank"
    # "Permitted in BOTH modes — sellers MAY accept refinements in settings-update mode
    # (e.g., updated bank details)", and the response account item carries it "echoed from
    # the request ... Bank details are omitted (write-only)". Rejecting is not an option:
    # the spec's own DACH B2B provisioning example sends billing_entity at provisioning time.
    # Today NEITHER branch applies it and no response model carries it — accepted on the wire,
    # then dropped, with a success response. The bank leg has teeth because the request
    # DECLARES bank details, so an echo that returns them is a real write-only leak.
    # Storyboard: graded only NEGATIVELY (billing_entity MUST NOT leak through error.details,
    # billing-gate-dispatch.yaml:350); persistence + echo are UNGRADED.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/properties/billing_entity/description
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-response.json pointer=/oneOf/0/properties/accounts/items/properties/billing_entity
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/account.json pointer=/properties/billing_entity
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/universal/billing-gate-dispatch.yaml pointer=phases/capability_gate (non-leak leg only)

  @T-UC-011-preferred-reporting-protocol-noop @sync @partition @boundary
  Scenario: preferred_reporting_protocol is accepted as a declared no-op, never a rejection
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request provisioning brand domain "acme-corp.com" with preferred_reporting_protocol "s3"
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "created"
    And the account has status "active"
    And the per-account result carries no errors
    And the response does not contain an operation-level errors array
    # REGRESSION LOCK, not a gap: this pins the status quo so the field-policy table cannot
    # quietly turn an advisory hint into a buyer-visible rejection.
    # The request description is HINT language — "The seller provisions the account
    # reporting_bucket using this protocol IF SUPPORTED ... When omitted, the seller chooses
    # from its supported offline_delivery_protocols" — and the response account item carries
    # neither preferred_reporting_protocol nor reporting_bucket, so there is no echo to
    # disagree with. The per-account errors array is documented "only present when action is
    # failed", so the protocol offers NO channel to advise on a SUCCESSFUL account: a protocol
    # that cannot advise on an unhonored optional hint is saying such hints are simply not
    # honored. Rejecting would fail a spec-legal provisioning request over an advisory hint.
    # Non-support stays DISCOVERABLE via get_adcp_capabilities: offline_delivery_protocols /
    # reporting_delivery_methods are declared unbacked (#1291), which is what discharges the
    # no-quiet-failure rule here.
    # Storyboard: UNGRADED (zero occurrences of preferred_reporting_protocol in dist/compliance/3.1.1).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/properties/preferred_reporting_protocol/description
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/core/account.json pointer=/properties/reporting_bucket
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-response.json pointer=/oneOf/0/properties/accounts/items/properties/errors/description

  @T-UC-011-settings-update-billing-forbidden @sync @list @settings-update @validation @error @post-f1 @post-f2 @partition @boundary
  Scenario: Settings-update entry carrying billing alone is rejected at the operation level
    Given the Buyer is authenticated
    And an account for brand domain "acme-corp.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with a settings-update entry keyed by the existing account's account_id carrying billing "agent"
    Then the response is compliant with the sync_accounts error spec
    And the request is rejected at the operation level with error code "VALIDATION_ERROR" naming field "accounts[0]"
    When the Buyer Agent sends a list_accounts request
    Then the listed account for brand domain "acme-corp.com" has billing "operator"
    # REGRESSION LOCK, not a gap. T-UC-011-sync-mode-exclusive already grades an entry
    # carrying `account` AND the FULL trio; this grades `account` + `billing` ALONE, which
    # is the shape the item oneOf forbids field-by-field
    # (SettingsUpdateMode allOf: not-required brand, not-required operator, not-required billing)
    # and the one the field-policy table calls `spec_forbidden` for settings-update.
    # Without it, that row would rest on an AST assertion that the mode-exclusivity `if`
    # exists — move the dispatch above the guard and the assertion stays green while billing
    # is silently dropped again. This scenario is what makes the row a behavioral claim.
    # POST-F1: the pre-existing account keeps billing "operator" — an operation-level
    # rejection writes nothing.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/oneOf/1/allOf/2
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/properties/billing/description
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumMetadata/VALIDATION_ERROR

  @T-UC-011-ext-e-preview @sync @dry-run @post-s10 @partition @boundary
  Scenario: dry_run_true returns preview -- success_dry_run (dry_run = true)
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with dry_run true and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And the response includes dry_run true
    And the account for brand domain "acme-corp.com" has action "created"
    And no accounts were actually created or modified on the seller
    # POST-S10: Buyer receives dry-run preview

  @T-UC-011-ext-e-preview-settings-update @sync @dry-run @settings-update @partition @boundary
  Scenario: dry_run_true with a settings-update entry previews without persisting
    Given the Buyer is authenticated
    And an account for brand domain "acme-corp.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with dry_run true and a settings-update entry keyed by the existing account's account_id setting payment_terms "net_45"
    Then the response is compliant with the sync_accounts success spec
    And the response includes dry_run true
    And the account for brand domain "acme-corp.com" has action "updated"
    And the account payment_terms is "net_45"
    And the persisted account for brand domain "acme-corp.com" has no payment_terms set
    # Locally added (GH: settings-update entries ignored dry_run and persisted the write).
    # The provisioning-trio preview scenario above never reaches the settings-update
    # dispatch, which routes BEFORE any dry_run branch — this grades that branch.
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/dry_run
    # ("When true, preview what would change without applying. Returns what would be
    # created/updated/deactivated.") Conformance storyboard: UNGRADED (dry_run absent
    # from dist/compliance/3.1.1 at v3.1.1).

  @T-UC-011-ext-e-normal @sync @dry-run @partition @boundary
  Scenario: dry_run_false -- normal sync applies changes (dry_run = false)
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with dry_run false and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And the response does not include a dry_run field
    And the account was actually created on the seller

  @T-UC-011-ext-e-omitted @sync @dry-run @partition @boundary
  Scenario: dry_run_omitted -- default behavior applies changes (dry_run omitted)
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And the response does not include a dry_run field
    And the account was actually created on the seller

  @T-UC-011-ext-f-deactivate @sync @delete-missing @post-s9 @partition @boundary
  Scenario: delete_missing_true deactivates absent accounts (delete_missing = true with absent accounts)
    Given the Buyer is authenticated
    And the agent previously synced accounts for brand domain "acme-corp.com" and "old-brand.com"
    When the Buyer Agent sends a sync_accounts request with delete_missing true and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And the response includes a result for brand domain "old-brand.com" showing deactivation
    And the account for brand domain "acme-corp.com" has action "unchanged" or "updated"
    # POST-S9: Buyer knows which accounts were deactivated

  @T-UC-011-ext-f-settings-update @sync @delete-missing @settings-update @partition @boundary
  Scenario: delete_missing_true does not deactivate an account included via a settings-update entry
    Given the Buyer is authenticated
    And an account for brand domain "acme-corp.com" already exists with billing "operator"
    When the Buyer Agent sends a sync_accounts request with delete_missing true and a settings-update entry keyed by the existing account's account_id setting payment_terms "net_45"
    Then the response is compliant with the sync_accounts success spec
    And the response contains an accounts array with 1 items
    And the account payment_terms is "net_45"
    And brand domain "acme-corp.com" remains in its current state
    # Locally added (GH: seen_account_ids is only populated on the provisioning path,
    # so a settings-update entry's target counted as "missing" and was CLOSED by the
    # very request that successfully updated it — the response carried both the update
    # result and an action=updated/status=closed result for the same account).
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/delete_missing
    # ("accounts previously synced by this agent but not included in this request will
    # be deactivated" — the settings-update target IS included in this request.)
    # Conformance storyboard: UNGRADED (delete_missing absent from dist/compliance/3.1.1 at v3.1.1).

  @T-UC-011-ext-f-scoped @sync @delete-missing @agent-scoped
  Scenario: Delete missing scoped to authenticated agent only
    Given the Buyer is authenticated
    And agent A previously synced accounts for brand domain "brand-a.com"
    And agent B previously synced accounts for brand domain "brand-b.com"
    When agent A sends a sync_accounts request with delete_missing true and:
    | brand.domain    | operator      | billing  |
    | brand-a.com     | brand-a.com   | operator |
    Then the response is compliant with the sync_accounts success spec
    And agent B's account for brand domain "brand-b.com" is not affected
    And only agent A's absent accounts are deactivated

  @T-UC-011-ext-f-false @sync @delete-missing @partition @boundary
  Scenario: delete_missing_false preserves absent accounts (delete_missing = false with absent accounts)
    Given the Buyer is authenticated
    And the agent previously synced accounts for brand domain "acme-corp.com" and "old-brand.com"
    When the Buyer Agent sends a sync_accounts request with delete_missing false and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And brand domain "old-brand.com" remains in its current state
    And only the included accounts are processed

  @T-UC-011-ext-f-none-absent @sync @delete-missing @partition @boundary
  Scenario: delete_missing_none_absent -- true with no absent accounts (delete_missing = true with no absent accounts)
    Given the Buyer is authenticated
    And the agent previously synced accounts for brand domain "acme-corp.com" only
    When the Buyer Agent sends a sync_accounts request with delete_missing true and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And no accounts are deactivated
    And the account for brand domain "acme-corp.com" is processed normally

  @T-UC-011-ext-f-omitted @sync @delete-missing @partition @boundary
  Scenario: delete_missing_omitted -- default preserves accounts (delete_missing omitted)
    Given the Buyer is authenticated
    And the agent previously synced accounts for brand domain "acme-corp.com" and "old-brand.com"
    When the Buyer Agent sends a sync_accounts request without delete_missing and:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And brand domain "old-brand.com" remains in its current state
    And only the included accounts are processed

  @T-UC-011-ext-g-echo @context-echo @post-f3 @partition @boundary
  Scenario Outline: context_provided -- context echoed in <operation> response (context with properties)
    Given the Buyer is authenticated
    When the Buyer Agent sends a <operation> request with context {"session_id": "abc-123", "trace": "xyz-789"}
    Then the response is compliant with the <operation> spec
    And the response includes context {"session_id": "abc-123", "trace": "xyz-789"}
    And the context is identical to what was sent
    # POST-F3: Application context echoed when possible

    Examples:
      | operation      |
      | list_accounts  |
      | sync_accounts  |

  @T-UC-011-ext-g-echo-error @context-echo @error @post-f3
  Scenario: Context echoed in sync error response
    Given the Buyer Agent has an unauthenticated connection
    When the Buyer Agent sends a sync_accounts request with context {"trace": "err-001"}
    Then the response is compliant with the sync_accounts error spec
    And the response is an error variant with AUTH_MISSING
    And the response includes context {"trace": "err-001"}
    And the error should include "suggestion" field with remediation guidance
    # POST-F3: Context echoed even on error path

  @T-UC-011-ext-g-absent @context-echo @partition @boundary
  Scenario: context_absent -- context omitted from response (context absent)
    Given the Buyer is authenticated
    When the Buyer Agent sends a list_accounts request without a context object
    Then the response is compliant with the list_accounts spec
    And the response does not include a context field

  @T-UC-011-ext-g-empty @context-echo @partition @boundary
  Scenario: context_empty_object -- empty context echoed unchanged (context = {})
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with context {}
    Then the response is compliant with the sync_accounts success spec
    And the response includes context {}

  @T-UC-011-ext-g-nested @context-echo @partition @boundary
  Scenario: context_nested -- deeply nested context echoed unchanged (context with properties)
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with context {"deep": {"nested": {"level": 3}}, "array": [1, 2, 3]}
    Then the response is compliant with the sync_accounts success spec
    And the response includes context {"deep": {"nested": {"level": 3}}, "array": [1, 2, 3]}
    And the context is identical to what was sent

  @T-UC-011-sync-empty-accounts @sync @validation @partition @boundary
  Scenario: Sync with empty_accounts array rejected (0 accounts)
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with an empty accounts array
    Then the response is compliant with the sync_accounts error spec
    And the response is an error variant
    And the error indicates accounts array must not be empty

  # ── A missing required member makes the REQUEST malformed, not the ACCOUNT
  # unprovisionable. This distinction decides the shape of the two scenarios below,
  # and it was invisible while the harness built the request in the test process.
  #
  # sync-accounts-request declares accounts[] as an anyOf of TWO branches:
  #   provisioning     requires ['brand', 'operator', 'billing']
  #   settings-update  requires ['account']
  # An entry missing brand.domain / operator / billing satisfies NEITHER branch, so the
  # request never becomes a well-formed list of entries the seller could evaluate
  # one by one. It is rejected whole, with INVALID_REQUEST -- "Request is malformed,
  # missing required fields, or violates schema constraints".
  #
  # A per-account result carrying action: "failed" is the OTHER outcome: an entry
  # that IS well-formed but cannot be provisioned (e.g. UNSUPPORTED_PROVISIONING for
  # a settings-update entry naming an unknown account). Asking for action "failed"
  # here asked the seller to report per-entry on a request it could not parse into
  # entries.
  #
  # These two scenarios asserted the per-account shape and passed anyway, because
  # nothing was dispatched: pydantic raised in the test process and the step graded
  # its own exception (salesagent-prkv.65). Corrected to the request-level rejection
  # production actually returns, and which the spec requires.
  # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json
  #   pointer=/properties/accounts/items/anyOf
  # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json
  #   pointer=/INVALID_REQUEST

  @T-UC-011-sync-missing-brand @sync @validation @partition @boundary
  Scenario: Sync account with no_domain -- missing brand domain rejected
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with an account that has no brand domain field
    # Blames `brand`, not `brand.domain`, and that is the seller being precise: the
    # When step omits the `brand` member ENTIRELY (it sends only operator + billing),
    # so the missing member IS brand. The scenario's name and its @bva note say
    # "missing domain", which the payload does not actually exercise -- a pre-existing
    # mismatch between this scenario's title and its own When, surfaced once the
    # payload started reaching the seller. Asserting `brand` grades what is really
    # sent; making it genuinely test a missing DOMAIN means changing the When to send
    # `brand: {}`, which would be redefining the scenario rather than fixing it.
    Then the response is compliant with the sync_accounts error spec
    And the account processing fails with a validation error for brand
    # @bva brand (brand-ref): missing domain in brand-ref

  @T-UC-011-sync-missing-operator @sync @validation @partition @boundary
  Scenario: Sync account with missing operator -- operator is required
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with an account that has no operator field
    Then the response is compliant with the sync_accounts error spec
    And the account processing fails with a validation error for operator

  @T-UC-011-sync-missing-billing @sync @validation @partition @boundary
  Scenario: Sync account with missing billing -- billing is required
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with an account that has no billing field
    Then the response is compliant with the sync_accounts error spec
    And the account processing fails with a validation error for billing

  @T-UC-011-sync-invalid-patterns @sync @validation @patterns @partition @boundary
  Scenario Outline: Sync with invalid pattern -- <field> "<value>" (<partition_name>)
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with <field> set to "<value>"
    Then the response is compliant with the sync_accounts error spec
    And the account processing fails with a validation error for <field>
    # @bva brand (brand-ref): invalid patterns -- uppercase domain, invalid brand_id_pattern

    Examples:
      | field          | value         | partition_name             |
      | brand.domain   | ACME.COM      | invalid_domain_pattern     |
      | brand.domain   | acme corp.com | invalid_domain_pattern     |
      | brand.brand_id | Dove!         | invalid_brand_id_pattern   |
      | brand.brand_id | UPPERCASE     | invalid_brand_id_pattern   |
      | operator       | NOT A DOMAIN  | invalid_domain_pattern     |

  @T-UC-011-sync-accounts-bva @sync @validation @bva @partition @boundary
  Scenario Outline: Sync accounts array boundary -- <count> accounts (<boundary_desc>)
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with <count> accounts
    Then the response is compliant with the sync_accounts spec
    And the response has outcome "<outcome>"

    Examples:
      | count | outcome                              | boundary_desc                      |
      | 1     | success with per-account results      | 1 account (minimum)                |
      | 1000  | success with per-account results      | 1000 accounts (maximum)            |
      | 1001  | validation error for exceeding limit  | 1001 accounts (exceeds maxItems)   |

  @T-UC-011-atomic-success @sync @atomic @partition @boundary
  Scenario: success_all_ok -- accounts present, no operation-level errors (success with 0 per-account failures)
    Given the Buyer is authenticated
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response contains an accounts array
    And the response does not contain an operation-level errors array
    And the response is compliant with the sync_accounts success spec

  @T-UC-011-atomic-all-failed @sync @atomic @partition @boundary
  Scenario: success with all per-account failures -- still success variant (success with all per-account failures)
    Given the Buyer is authenticated
    And the seller does not support any of the requested billing models
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts success spec
    And all accounts have action "failed"
    And the response does not contain an operation-level errors array

  @T-UC-011-atomic-error @sync @atomic @error @partition @boundary
  Scenario: Error variant -- errors present, no accounts or dry_run (error with exactly 1 error)
    Given the Buyer Agent has an unauthenticated connection
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts error spec
    And the response contains an errors array with at least 1 error
    And the response does not contain an accounts array
    And the response does not contain a dry_run field
    And the response is an error variant with no accounts array
    And the error should include "suggestion" field with remediation guidance

  @T-UC-011-atomic-service-error @sync @atomic @error @partition @boundary
  Scenario: error_service -- service-level failure (error with multiple errors)
    Given the Buyer is authenticated
    And the seller system is experiencing an internal failure
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain    | operator      | billing  |
    | acme-corp.com   | acme-corp.com | operator |
    Then the response is compliant with the sync_accounts error spec
    And the response is an error variant
    And the errors array may contain multiple errors
    And each error includes code and message
    And the error should include "suggestion" field with remediation guidance

  @T-UC-011-sandbox-provision @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account provisioned via sync_accounts with sandbox flag
    Given the Buyer is authenticated
    And the seller declares account.sandbox equals true in capabilities
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain  | operator      | billing  | sandbox |
    | acme-corp.com | acme-corp.com | operator | true    |
    Then the response is compliant with the sync_accounts success spec
    And the provisioned account should have sandbox equals true
    And the account should have a seller-assigned account_id
    And no real ad platform account should have been created
    # BR-RULE-209 INV-6: seller with account.sandbox: true supports sandbox provisioning
    # 3.1.1: the sandbox capability lives at account.sandbox (media_buy.features has no sandbox flag)
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/account/properties/sandbox
    # BR-RULE-209 INV-2: real ad platform calls suppressed for sandbox account

  @T-UC-011-sandbox-list-filter @invariant @br-rule-209 @sandbox
  Scenario: List accounts with sandbox filter returns only sandbox accounts
    Given the Buyer is authenticated
    And both sandbox and production accounts exist for the Buyer
    When the Buyer Agent sends a list_accounts request with sandbox equals true
    Then the response is compliant with the list_accounts spec
    And the response contains an accounts array with 1 items
    And all returned accounts should have sandbox equals true
    And the response should not include production accounts
    # BR-RULE-209 INV-4: sandbox accounts identifiable via sandbox: true
    # Given seeds exactly one sandbox + one production account; the sandbox=true
    # filter returns only the sandbox account (count 1, sandbox id present, prod id absent)
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/list-accounts-request.json pointer=/properties/sandbox

  @T-UC-011-sandbox-validation @invariant @br-rule-209 @sandbox
  Scenario: Sandbox account provisioning with invalid billing returns real validation error
    Given the Buyer is authenticated
    And the seller declares account.sandbox equals true in capabilities
    When the Buyer Agent sends a sync_accounts request with:
    | brand.domain  | operator      | billing       | sandbox |
    | acme-corp.com | acme-corp.com | unsupported   | true    |
    # BR-RULE-209 INV-1 ("inputs validated same as production") graded DIRECTLY.
    # This asserted a pydantic.ValidationError OBJECT existed, which was a PROXY for
    # "validation really ran" -- and the proxy stopped being satisfiable once the
    # payload actually reached the seller and was rejected at its schema boundary,
    # even though INV-1 became MORE true. An issues[] entry carrying the JSON-Schema
    # keyword that failed plus a pointer at the offending member IS production's
    # validation output, and is stricter than the type check: a simulated or
    # hand-wrapped error carries no such structure, whereas any ValidationError --
    # including one synthesised in the test process -- satisfied an isinstance check.
    # An out-of-enum billing value is a schema violation, hence INVALID_REQUEST
    # ("violates schema constraints") rather than VALIDATION_ERROR ("business rules
    # BEYOND schema validation").
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json
    #   pointer=/INVALID_REQUEST
    # BR-RULE-209 INV-1: BR-UC-001-discover-available-inventory.feature:1420
    Then the response is compliant with the sync_accounts error spec
    And the response arrives
    And the response contains error code INVALID_REQUEST
    And the response error issues include keyword enum for field billing
    And the error should include a suggestion for how to fix the issue
    # BR-RULE-209 INV-1: sandbox inputs validated same as production
    # BR-RULE-209 INV-7: sandbox validation errors are real
    # POST-F3: suggestion field present

  @T-UC-011-sandbox-response-shape @sync @v3-1 @sandbox @invariant @partition @boundary
  Scenario Outline: Account response reflects sandbox type for a <request_item> request item
    Given the Buyer is authenticated
    And the seller declares account.sandbox equals true in capabilities
    When the Buyer Agent sends a sync_accounts request with idempotency_key "sandbox-shape-001" and a request item where sandbox is <request_item>
    Then the response is compliant with the sync_accounts success spec
    And the per-account result sandbox field is "<response_field>"
    # @bva sandbox: sandbox: true in response (sandbox account)
    # @bva sandbox: sandbox: false in response (explicit production)
    # @bva sandbox: sandbox absent in response (production account)
    # @bva sandbox: sandbox omitted on sync_accounts request item
    # BR-RULE-209 INV-4: sandbox accounts identifiable via sandbox: true; production accounts via false or absence
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/accounts/items/properties/sandbox
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-response.json pointer=/oneOf/0/properties/accounts/items/properties/sandbox

    Examples:
      | request_item | response_field |
      | true         | true           |
      | false        | false          |
      | omitted      | absent         |

  @T-UC-011-sandbox-capability-not-declared @sync @v3-1 @sandbox @error @post-f1 @post-f2 @partition @boundary
  Scenario: Sandbox provisioning requested when capability not declared is rejected
    Given the Buyer is authenticated
    And the seller does not declare account.sandbox in capabilities
    When the Buyer Agent sends a sync_accounts request with idempotency_key "sandbox-nocap-001" and:
    | brand.domain  | operator      | billing  | sandbox |
    | acme-corp.com | acme-corp.com | operator | true    |
    Then the response is compliant with the sync_accounts success spec
    And the account for brand domain "acme-corp.com" has action "failed"
    And the accounts entry carries error code "UNSUPPORTED_FEATURE"
    And the per-account error recovery is "correctable"
    And the per-account error field points at "sandbox"
    And the per-account error suggestion mentions "get_adcp_capabilities"
    # @bva sandbox: capability not declared, sandbox provisioning requested
    # BR-RULE-209 INV-6: only a seller with account.sandbox: true supports sandbox provisioning
    # UNSUPPORTED_FEATURE is the canonical fit (a requested feature the seller does not
    # support); recovery correctable = "check get_adcp_capabilities and remove unsupported fields"
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/protocol/get-adcp-capabilities-response.json pointer=/properties/account/properties/sandbox
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumMetadata/UNSUPPORTED_FEATURE
    # POST-F1: no real or sandbox account created on failure

  # DELETED: "ACCOUNT_SETUP_REQUIRED carries v3.1 details shape (setup_url + setup_steps)".
  # THE CODE DOES NOT BELONG ON sync_accounts, and the shape it demanded is a MAY.
  #
  # 1. WRONG TOOL. ACCOUNT_SETUP_REQUIRED answers an account REFERENCE that resolved to an
  #    account needing setup -- enums/error-code.json @3.1 enumDescriptions:
  #    "Natural key resolved but the account needs setup before use", and
  #    L2/accounts-and-agents.mdx § Error codes: "Natural key resolved but account needs
  #    setup | Check details.setup for URL/message". account/sync-accounts-request.json
  #    declares NO top-level `account` (its properties are idempotency_key, accounts,
  #    delete_missing, dry_run, push_notification_config, context, ext), so sync_accounts
  #    never resolves one and has no reference to refuse. Production agrees by
  #    construction: the raise lives in the account-reference resolver
  #    (src/core/database/repositories/account_lookup.py::_check_status, reached only from
  #    resolved_identity._load_account for a request's top-level account).
  # 2. sync_accounts ALREADY HAS a pinned carrier for "this account needs setup", and it is
  #    on the SUCCESS path: per-account `status: pending_approval` plus the `setup` object
  #    (core/account.json @3.1 /properties/setup -- "Present when status is
  #    'pending_approval'. Contains next steps"). Production implements it
  #    (accounts.py::_build_setup_for_approval) and @T-UC-011-ext-d-pending-url /
  #    @T-UC-011-ext-d-pending-message grade it live. An operation-level refusal would be
  #    the seller declining to provision on the ground that provisioning is needed.
  # 3. OVER-SPECIFIED even where the code does belong: error-details/
  #    account-setup-required.json @3.1 declares NO `required` array, so setup_url and
  #    setup_steps are both MAY. "should include setup_url matching a URI format" grades an
  #    obligation the pin does not impose.
  # 4. NOT LOST: the details-shape obligation is graded on a tool that DOES resolve an
  #    account reference -- BR-UC-002 @T-UC-002-ext-s ("Account requires setup before use",
  #    create_media_buy against a pending_approval account), live and passing.

  # DELETED: "CONFLICT on sync_accounts carries v3.1 details shape (resource_id +
  # expected/current version)".
  # AdCP 3.1 DEFINES NO VERSIONING FOR AN ACCOUNT, so the scenario's preconditions have no
  # wire carrier in either direction:
  #   - core/account.json declares no version and no etag field, so a seller has no
  #     "version 12" to report and a buyer has no version to have last read;
  #   - account/sync-accounts-request.json declares no if_match / expected_version, so the
  #     Given "the Buyer Agent's last-read version of acct-001 is 9" cannot be expressed as
  #     a request. (core/version-envelope.json, the one `version` this request composes, is
  #     PROTOCOL-version negotiation -- adcp_version / adcp_major_version -- not resource
  #     versioning.)
  # error-details/conflict.json is a "Recommended details shape for CONFLICT errors" for
  # sellers that HAVE versions; declaring the key names does not create an
  # optimistic-concurrency protocol on a surface with no version field.
  # And sync_accounts is upsert-by-natural-key by definition -- sync-accounts-request.json
  # describes the mode as "The seller provisions or links accounts via upsert" -- so a
  # concurrent writer on the same natural key is resolved to the winner, not refused
  # (accounts.py, the NaturalKeyConflict branch: "the only difference between this entry and
  # one that arrived a microsecond later is timing, and timing must not change the answer").
  # That is the spec's upsert semantics, not a missing CONFLICT.
  # ADJACENT, NOT FIXED HERE: BR-UC-003 @T-UC-003-v31-error-conflict-version is the same
  # generated template on update_media_buy, where a revision DOES exist; it is parked behind
  # that file's xfail set and will meet the same wiring question when the park is lifted.

  @T-UC-011-v31-error-idempotency-conflict @v3-1 @idempotency @ext-h @post-f1 @post-f2 @post-f3
  Scenario: Re-sending one idempotency_key with a different accounts array is refused, and leaks nothing
    Given the Buyer is authenticated
    And idempotency_key "sync-acct-20260521-001" was already spent syncing brand domain "first-payload.example"
    When the Buyer Agent re-sends sync_accounts with idempotency_key "sync-acct-20260521-001" and brand domain "second-payload.example"
    Then the response is compliant with the sync_accounts error spec
    And the operation should fail
    And the error code should be "IDEMPOTENCY_CONFLICT"
    And the error recovery should be "correctable"
    And the error should include "suggestion" field with remediation guidance
    And the wire envelope should not carry the marker "first-payload.example"
    And the wire error object carries no "field" key
    And the wire error object carries no "details" key
    And the seller holds an account for brand domain "first-payload.example" and none for "second-payload.example"
    # @bva idempotency_key: same key reused with a different accounts payload
    #
    # CORRECTED @3.1: this scenario used to demand
    #   And the error "details" object should include "current_version" with value "W/\"etag-zzz\""
    # off a Given that recorded an ETag for the spent key. That is behavior the pinned spec
    # FORBIDS, not behavior it declares. L1/security.mdx § "IDEMPOTENCY_CONFLICT response
    # shape": the body "MUST include code ... and a human-readable message" and "MUST NOT
    # include the cached response, the original payload, a canonical-form diff, or any
    # fingerprint derived from them" -- it refuses even a `field` pointer, because "a field
    # json-pointer hint seems harmless but reveals schema shape", and closes with "The error
    # body exposes only the code." An ETag of the first payload's state is exactly such a
    # fingerprint: "Leaking cached state turns key-reuse into a read oracle." The graded
    # conformance artifact says the same thing as an invariant rather than prose --
    # dist/compliance/3.1.1/universal/idempotency.yaml declares
    # `idempotency.conflict_no_payload_leak`, which "catches the stolen-key read oracle the
    # key-reuse phase calls out". There is also no ETag anywhere on this surface to record:
    # core/account.json declares none. So the details assertion is replaced by the
    # obligations the pin does state, and the leak it invited is now GRADED as an absence.
    #
    # The refusal itself is security.mdx#idempotency rule 5: "Same key, different canonical
    # hash within the replay window MUST be rejected with IDEMPOTENCY_CONFLICT. Sellers MUST
    # NOT silently apply the second request." The last Then is that second sentence: the
    # first payload's account is on the seller and the second payload's is not. It names both
    # domains on purpose -- an assertion that only checked the absence would also pass if the
    # Given had never run.
    # POST-F3: recovery suggestion (use a fresh idempotency_key or re-read current state)
    # accompanies the conflict; recovery=correctable per enums/error-code.json enumMetadata.
    # @source repo=adcp ref=v3.1.1 path=docs/building/by-layer/L1/security.mdx pointer=L428-L441
    # @source repo=adcp ref=v3.1.1 path=dist/compliance/3.1.1/universal/idempotency.yaml pointer=/invariants
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/account/sync-accounts-request.json pointer=/properties/idempotency_key
    # @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/error-code.json pointer=/enumMetadata/IDEMPOTENCY_CONFLICT
