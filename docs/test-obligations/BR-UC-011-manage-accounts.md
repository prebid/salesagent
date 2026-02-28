# UC-011: Manage Accounts -- Test Obligations

## Source
- Requirements: `/Users/konst/projects/adcp-req/docs/requirements/use-cases/BR-UC-011-manage-accounts/`
- Use Case ID: BR-UC-011
- Files: BR-UC-011.md, BR-UC-011-main-list-mcp.md, BR-UC-011-main-list-a2a.md, BR-UC-011-main-sync.md, BR-UC-011-ext-a.md, BR-UC-011-ext-b.md, BR-UC-011-ext-c.md, BR-UC-011-ext-d.md, BR-UC-011-ext-e.md, BR-UC-011-ext-f.md, BR-UC-011-ext-g.md
- Referenced Rules: BR-RULE-054, BR-RULE-055, BR-RULE-056, BR-RULE-057, BR-RULE-058, BR-RULE-059, BR-RULE-060, BR-RULE-061, BR-RULE-062, BR-RULE-043

## 3.6 Upgrade Impact
High impact. Account management is a new protocol domain in adcp 3.x. The schemas (`account.json`, `list-accounts-request.json`, `list-accounts-response.json`, `sync-accounts-request.json`, `sync-accounts-response.json`, `pagination-request.json`, `pagination-response.json`, `push-notification-config.json`) were likely added or significantly evolved between 3.2 and 3.6. The `ADCPHandler` base class in adcp server (`adcp/server/base.py`) provides `list_accounts` and MCP tool registration (`adcp/server/mcp_tools.py`) which may have changed. The account status enum (active, pending_approval, payment_required, suspended, closed) and billing model enum (brand, operator, agent) should be verified. The sync_accounts operation is NOT registered as an MCP tool -- only as A2A -- so the integration point with the adcp library must be checked.

## Test Scenarios

### Main Flow: list_accounts via MCP

#### Scenario: List all accounts (no filter)
**Obligation ID** UC-011-MAIN-01
**Layer** behavioral
**Given** an authenticated buyer agent with 3 accounts (1 active, 1 pending_approval, 1 suspended)
**When** the buyer invokes `list_accounts` MCP tool without status filter
**Then** the response contains all 3 accounts
**And** each account includes account_id, name, status, advertiser, billing
**And** pagination metadata is included (has_more, cursor, total_count)
**Business Rule:** BR-1 (returns only accounts accessible to authenticated agent), BR-2 (no filter = all statuses)
**Priority:** P0

#### Scenario: List accounts with status filter
**Obligation ID** UC-011-MAIN-02
**Layer** behavioral
**Given** an authenticated buyer with accounts in multiple statuses
**When** the buyer invokes `list_accounts` with `status: "active"`
**Then** only active accounts are returned
**Business Rule:** BR-2
**Priority:** P1

#### Scenario: Status filter validates enum values
**Obligation ID** UC-011-MAIN-03
**Layer** schema
**Given** a buyer requesting accounts
**When** the buyer provides `status: "invalid_status"`
**Then** the request is rejected (status must be one of: active, pending_approval, payment_required, suspended, closed)
**Business Rule:** PRE-B1
**Priority:** P1

#### Scenario: Pagination with max_results
**Obligation ID** UC-011-MAIN-04
**Layer** behavioral
**Given** an authenticated buyer with 20 accounts
**When** the buyer invokes `list_accounts` with `pagination: {max_results: 5}`
**Then** only 5 accounts are returned
**And** `has_more: true` and a cursor for the next page
**Business Rule:** PRE-B2
**Priority:** P1

#### Scenario: Pagination max_results bounds (1-100)
**Obligation ID** UC-011-MAIN-05
**Layer** schema
**Given** a buyer requesting accounts
**When** the buyer provides `pagination: {max_results: 0}`
**Then** the request is rejected (must be 1-100)
**Business Rule:** PRE-B2
**Priority:** P2

#### Scenario: Default page size is 50
**Obligation ID** UC-011-MAIN-06
**Layer** behavioral
**Given** 60 accounts
**When** the buyer invokes `list_accounts` without pagination params
**Then** 50 accounts are returned with `has_more: true`
**Priority:** P2

#### Scenario: Cursor pagination -- second page
**Obligation ID** UC-011-MAIN-07
**Layer** behavioral
**Given** 10 accounts, first page requested with max_results=5
**When** the buyer invokes `list_accounts` with the cursor from the first page
**Then** the remaining 5 accounts are returned
**And** `has_more: false`
**Priority:** P1

#### Scenario: Account fields include required data
**Obligation ID** UC-011-MAIN-08
**Layer** schema
**Given** a returned account
**When** the response is inspected
**Then** each account includes at minimum: account_id, status
**And** optional fields (advertiser, billing_proxy, rate_card, payment_terms, credit_limit) are correctly typed when present
**Priority:** P1

### Main Flow: list_accounts via A2A

#### Scenario: A2A list_accounts returns same structure
**Obligation ID** UC-011-MAIN-09
**Layer** behavioral
**Given** the same data as MCP test
**When** the buyer sends `list_accounts` via A2A
**Then** the response structure is identical to MCP (same accounts, same pagination)
**Priority:** P1

### Main Flow: sync_accounts via A2A

#### Scenario: Successful sync -- create new accounts
**Obligation ID** UC-011-MAIN-10
**Layer** behavioral
**Given** an authenticated buyer with no existing accounts
**When** the buyer sends `sync_accounts` with `accounts: [{house: "brandco.com"}, {house: "adexample.com"}]`
**Then** each account receives a seller-assigned `account_id`
**And** each account result has `action: "created"` and a status
**And** billing models are assigned
**Business Rule:** BR-3 (upsert semantics), BR-4 (brand identity via house domain)
**Priority:** P0

#### Scenario: Sync requires authentication
**Obligation ID** UC-011-MAIN-11
**Layer** behavioral
**Given** no authentication
**When** the buyer sends `sync_accounts`
**Then** the request is rejected with AUTH_TOKEN_INVALID
**Business Rule:** BR-12
**Priority:** P0

#### Scenario: Sync requires at least one account
**Obligation ID** UC-011-MAIN-12
**Layer** schema
**Given** an authenticated buyer
**When** the buyer sends `sync_accounts` with `accounts: []`
**Then** the request is rejected (minItems: 1)
**Business Rule:** PRE-B3
**Priority:** P1

#### Scenario: House domain is required per account
**Obligation ID** UC-011-MAIN-13
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer sends `sync_accounts` with an account missing `house`
**Then** the request is rejected
**Business Rule:** PRE-B4
**Priority:** P1

#### Scenario: House domain format validation
**Obligation ID** UC-011-MAIN-14
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer provides `house: "INVALID DOMAIN!"`
**Then** the request is rejected (must match lowercase alphanumeric with hyphens and dots)
**Business Rule:** PRE-B5
**Priority:** P2

#### Scenario: brand_id format validation
**Obligation ID** UC-011-MAIN-15
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer provides `brand_id: "INVALID"`
**Then** the request is rejected (must match lowercase alphanumeric with underscores)
**Business Rule:** PRE-B6
**Priority:** P2

#### Scenario: operator format validation
**Obligation ID** UC-011-MAIN-16
**Layer** schema
**Given** an authenticated buyer
**When** the buyer provides `operator: "not a domain"`
**Then** the request is rejected (must match domain pattern)
**Business Rule:** PRE-B7
**Priority:** P2

#### Scenario: Billing model validation
**Obligation ID** UC-011-MAIN-17
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer provides `billing: "invalid_model"`
**Then** the request is rejected (must be brand, operator, or agent)
**Business Rule:** PRE-B8
**Priority:** P2

#### Scenario: Accounts array max 1000 items
**Obligation ID** UC-011-MAIN-18
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer sends `sync_accounts` with 1001 accounts
**Then** the request is rejected
**Business Rule:** PRE-B9
**Priority:** P2

#### Scenario: Upsert -- update existing account
**Obligation ID** UC-011-MAIN-19
**Layer** behavioral
**Given** an authenticated buyer with an existing account for house "brandco.com"
**When** the buyer sends `sync_accounts` with `accounts: [{house: "brandco.com", billing: "agent"}]`
**Then** the existing account is updated (not duplicated)
**And** the result has `action: "updated"`
**Business Rule:** BR-3
**Priority:** P1

#### Scenario: Upsert -- unchanged account
**Obligation ID** UC-011-MAIN-20
**Layer** behavioral
**Given** an existing account matching the sync request exactly
**When** the buyer syncs with identical data
**Then** the result has `action: "unchanged"`
**Priority:** P2

#### Scenario: Response is atomic -- accounts[] XOR errors[]
**Obligation ID** UC-011-MAIN-21
**Layer** behavioral
**Given** any sync_accounts response
**When** the response is inspected
**Then** it contains EITHER `accounts` array (success variant) OR `errors` array (error variant), never both
**Business Rule:** BR-10
**Priority:** P0

### Extension A: AUTH_TOKEN_INVALID

#### Scenario: Missing auth token on sync_accounts
**Obligation ID** UC-011-EXT-A-01
**Layer** behavioral
**Given** no Bearer token in request
**When** the buyer sends `sync_accounts`
**Then** the response is error variant with `AUTH_TOKEN_INVALID`
**And** no accounts are modified
**And** context is echoed
**Business Rule:** BR-12, POST-F1
**Priority:** P0

#### Scenario: Expired auth token on sync_accounts
**Obligation ID** UC-011-EXT-A-02
**Layer** behavioral
**Given** an expired Bearer token
**When** the buyer sends `sync_accounts`
**Then** the response is error variant with `AUTH_TOKEN_INVALID`
**Priority:** P1

#### Scenario: Malformed auth token on sync_accounts
**Obligation ID** UC-011-EXT-A-03
**Layer** behavioral
**Given** a malformed Bearer token
**When** the buyer sends `sync_accounts`
**Then** the response is error variant with `AUTH_TOKEN_INVALID`
**Priority:** P1

### Extension B: SYNC_PARTIAL_FAILURE

#### Scenario: Some accounts fail, others succeed
**Obligation ID** UC-011-EXT-B-01
**Layer** behavioral
**Given** an authenticated buyer sending 3 accounts: 2 valid, 1 with unresolvable brand
**When** the buyer sends `sync_accounts`
**Then** 2 accounts have `action: "created"` or "updated"
**And** 1 account has `action: "failed"` with per-account `errors` array
**And** the operation-level response is the success variant (accounts array present)
**Business Rule:** BR-11
**Priority:** P1

#### Scenario: Partial failure still returns accounts array
**Obligation ID** UC-011-EXT-B-02
**Layer** behavioral
**Given** a mix of successful and failed accounts
**When** the response is constructed
**Then** the response uses the success variant (has `accounts` array)
**And** failed accounts are included in the array with `action: "failed"`
**Priority:** P1

### Extension C: BILLING_MODEL_OVERRIDE

#### Scenario: Unsupported billing model is overridden
**Obligation ID** UC-011-EXT-C-01
**Layer** behavioral
**Given** an authenticated buyer requesting `billing: "brand"` but the seller only supports "agent"
**When** the buyer sends `sync_accounts`
**Then** the account is provisioned successfully with `billing: "agent"`
**And** the per-account `warnings` array explains the override
**Business Rule:** BR-5
**Priority:** P1

#### Scenario: Override is not an error
**Obligation ID** UC-011-EXT-C-02
**Layer** behavioral
**Given** a billing model override
**When** the account result is inspected
**Then** the action is "created" or "updated" (not "failed")
**And** the `billing` field reflects the actual model applied
**Priority:** P2

### Extension D: ACCOUNT_PENDING_APPROVAL

#### Scenario: New account requires seller approval
**Obligation ID** UC-011-EXT-D-01
**Layer** behavioral
**Given** an authenticated buyer syncing a new brand that requires credit review
**When** the buyer sends `sync_accounts`
**Then** the account has `status: "pending_approval"` and `action: "created"`
**And** the account result includes `setup` object with `url`, `message`, and `expiry`
**Business Rule:** BR-6
**Priority:** P1

#### Scenario: Setup URL is actionable
**Obligation ID** UC-011-EXT-D-02
**Layer** schema
**Given** a pending_approval account
**When** the setup object is inspected
**Then** `setup.url` is a valid URL for the human to visit
**And** `setup.message` describes the required action
**And** `setup.expiry` is a future ISO 8601 timestamp
**Priority:** P2

#### Scenario: Push notification on status change
**Obligation ID** UC-011-EXT-D-03
**Layer** behavioral
**Given** a pending_approval account and a configured push_notification_config webhook
**When** the seller approves the account (status transitions to "active")
**Then** a push notification is sent to the agent's webhook URL with the updated status
**Priority:** P2

### Extension E: DRY_RUN

#### Scenario: Dry run previews changes without applying
**Obligation ID** UC-011-EXT-E-01
**Layer** schema
**Given** an authenticated buyer
**When** the buyer sends `sync_accounts` with `dry_run: true` and 2 new accounts
**Then** the response includes `dry_run: true`
**And** per-account results show what WOULD happen (action: "created") without actually creating
**And** no accounts are created or modified in the database
**Business Rule:** BR-9
**Priority:** P1

#### Scenario: Dry run still validates authentication
**Obligation ID** UC-011-EXT-E-02
**Layer** behavioral
**Given** no authentication
**When** the buyer sends `sync_accounts` with `dry_run: true`
**Then** the request still requires authentication (dry_run does not bypass auth)
**Priority:** P2

#### Scenario: Dry run still resolves brand identities
**Obligation ID** UC-011-EXT-E-03
**Layer** behavioral
**Given** an authenticated buyer with dry_run: true
**When** brand resolution fails for an account
**Then** the dry run preview shows which accounts would fail
**Priority:** P2

### Extension F: DELETE_MISSING

#### Scenario: Delete missing accounts absent from sync
**Obligation ID** UC-011-EXT-F-01
**Layer** behavioral
**Given** an authenticated buyer who previously synced accounts A, B, C; now syncs only A, B
**When** the buyer sends `sync_accounts` with `delete_missing: true`
**Then** accounts A and B are processed normally
**And** account C is deactivated (status transitions to closed or suspended)
**And** the deactivation appears in the results
**Business Rule:** BR-7
**Priority:** P1

#### Scenario: Delete missing is scoped to authenticated agent
**Obligation ID** UC-011-EXT-F-02
**Layer** behavioral
**Given** agent1 synced accounts A, B; agent2 synced accounts C, D
**When** agent1 sends `sync_accounts` with `delete_missing: true` and only account A
**Then** account B is deactivated (agent1's missing account)
**And** accounts C and D are NOT affected (belong to agent2)
**Business Rule:** BR-8
**Priority:** P1

#### Scenario: Delete missing without flag does not deactivate
**Obligation ID** UC-011-EXT-F-03
**Layer** behavioral
**Given** a buyer who previously synced accounts A, B, C; now syncs only A
**When** the buyer sends `sync_accounts` WITHOUT `delete_missing` (default false)
**Then** accounts B and C remain unchanged
**Priority:** P1

### Extension G: CONTEXT_ECHO

#### Scenario: Context echoed in list_accounts response
**Obligation ID** UC-011-EXT-G-01
**Layer** schema
**Given** a buyer requesting `list_accounts` with `context: {"req": "r1"}`
**When** the response is returned
**Then** the response includes `context: {"req": "r1"}`
**Business Rule:** BR-13
**Priority:** P1

#### Scenario: Context echoed in sync_accounts success response
**Obligation ID** UC-011-EXT-G-02
**Layer** schema
**Given** an authenticated buyer syncing with `context: {"batch": "b1"}`
**When** the sync succeeds
**Then** the success response includes `context: {"batch": "b1"}`
**Priority:** P1

#### Scenario: Context echoed in sync_accounts error response
**Obligation ID** UC-011-EXT-G-03
**Layer** schema
**Given** an unauthenticated buyer sending sync_accounts with `context: {"trace": "t1"}`
**When** the AUTH_TOKEN_INVALID error is returned
**Then** the error response includes `context: {"trace": "t1"}`
**Business Rule:** POST-F3
**Priority:** P1

#### Scenario: Context is opaque and unmodified
**Obligation ID** UC-011-EXT-G-04
**Layer** schema
**Given** a deeply nested context object
**When** echoed in the response
**Then** the structure is preserved exactly
**Priority:** P2

### Schema Compliance

#### Scenario: list-accounts-response conforms to schema
**Obligation ID** UC-011-SCHEMA-01
**Layer** behavioral
**Given** any list_accounts response
**When** serialized to JSON
**Then** it validates against `list-accounts-response.json`
**And** includes accounts array and pagination metadata
**Priority:** P0

#### Scenario: sync-accounts-response success variant
**Obligation ID** UC-011-SCHEMA-02
**Layer** schema
**Given** a successful sync response
**When** serialized
**Then** it validates against `sync-accounts-response.json` (success oneOf variant)
**And** has accounts array with per-account results
**Priority:** P0

#### Scenario: sync-accounts-response error variant
**Obligation ID** UC-011-SCHEMA-03
**Layer** schema
**Given** an operation-level error response (e.g., AUTH_TOKEN_INVALID)
**When** serialized
**Then** it validates against `sync-accounts-response.json` (error oneOf variant)
**And** has errors array
**Priority:** P0

#### Scenario: Account status enum values are valid
**Obligation ID** UC-011-SCHEMA-04
**Layer** schema
**Given** any account in a response
**When** the status is inspected
**Then** it is one of: active, pending_approval, payment_required, suspended, closed
**Priority:** P1

#### Scenario: Billing model enum values are valid
**Obligation ID** UC-011-SCHEMA-05
**Layer** schema
**Given** any account with billing set
**When** the billing is inspected
**Then** it is one of: brand, operator, agent
**Priority:** P1
