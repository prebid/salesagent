# UC-008: Manage Audience Signals -- Test Obligations

## Source
- Requirements: `/Users/konst/projects/adcp-req/docs/requirements/use-cases/BR-UC-008-manage-audience-signals/`
- Use Case ID: BR-UC-008
- Files: BR-UC-008.md, BR-UC-008-main-mcp.md, BR-UC-008-main-rest.md, BR-UC-008-ext-a.md, BR-UC-008-ext-b.md, BR-UC-008-ext-c.md, BR-UC-008-ext-d.md

## 3.6 Upgrade Impact
Medium impact. The signals subsystem depends on adcp signal schemas (`get-signals-request.json`, `get-signals-response.json`, `activate-signal-request.json`, `activate-signal-response.json`, `signal-filters.json`). If adcp 3.6 modified signal schema fields, enum values (`signal-catalog-type`, `signal-value-type`, `signal-source`), or the `signal-id.json` discriminated union structure, the Pydantic models in `src/core/schemas.py` (lines 3192-3395) must be updated. The `deliver_to` structure with deployments and countries should be verified.

## Test Scenarios

### Main Flow (MCP): get_signals -- Buyer discovers signals via MCP

#### Scenario: Successful signal discovery with signal_spec
**Obligation ID** UC-008-MAIN-MCP-01
**Layer** behavioral
**Given** a tenant with 2 enabled signals agents, each returning 3 signals matching "auto intenders"
**When** the buyer agent calls `get_signals` with `signal_spec: "auto intenders"` and `deliver_to: {deployments: ["dv360"], countries: ["US"]}`
**Then** the response contains up to 6 signals aggregated from both agents
**And** each signal includes `signal_agent_segment_id`, `name`, `description`, `signal_type`, `data_provider`, `coverage_percentage`, `deployments`, and `pricing` (cpm + currency)
**Business Rule:** BR-5 (aggregation from multiple agents)
**Priority:** P0

#### Scenario: Successful signal discovery with signal_ids
**Obligation ID** UC-008-MAIN-MCP-02
**Layer** behavioral
**Given** a tenant with enabled signals agents
**When** the buyer agent calls `get_signals` with `signal_ids: [{"domain": "example.com", "id": "seg_123"}]` and `deliver_to`
**Then** the response contains only signals matching the specified IDs
**Business Rule:** BR-1 (signal_spec OR signal_ids required)
**Priority:** P1

#### Scenario: Request must provide signal_spec or signal_ids
**Obligation ID** UC-008-MAIN-MCP-03
**Layer** schema
**Given** a valid tenant
**When** the buyer agent calls `get_signals` without either `signal_spec` or `signal_ids`
**Then** the request is rejected (anyOf constraint violation)
**Business Rule:** BR-1
**Priority:** P0

#### Scenario: deliver_to is required with deployments and countries
**Obligation ID** UC-008-MAIN-MCP-04
**Layer** schema
**Given** a valid tenant
**When** the buyer agent calls `get_signals` with `signal_spec` but without `deliver_to`
**Then** the request is rejected (required field missing)
**Business Rule:** BR-2
**Priority:** P0

#### Scenario: deliver_to requires at least one deployment and one country
**Obligation ID** UC-008-MAIN-MCP-05
**Layer** schema
**Given** a valid tenant
**When** the buyer agent calls `get_signals` with `deliver_to: {deployments: [], countries: ["US"]}`
**Then** the request is rejected (minItems: 1 on deployments)
**Business Rule:** BR-2
**Priority:** P1

#### Scenario: Country code format validation
**Obligation ID** UC-008-MAIN-MCP-06
**Layer** schema
**Given** a valid tenant
**When** the buyer agent calls `get_signals` with `deliver_to: {deployments: ["dv360"], countries: ["usa"]}`
**Then** the request is rejected (country codes must match `^[A-Z]{2}$`)
**Business Rule:** PRE-B6
**Priority:** P1

#### Scenario: Filter by catalog_types
**Obligation ID** UC-008-MAIN-MCP-07
**Layer** behavioral
**Given** signals agents returning marketplace, custom, and owned signals
**When** the buyer agent calls `get_signals` with `filters: {catalog_types: ["marketplace"]}`
**Then** only marketplace signals are returned
**Business Rule:** BR-3
**Priority:** P1

#### Scenario: Filter by data_providers
**Obligation ID** UC-008-MAIN-MCP-08
**Layer** behavioral
**Given** signals agents returning signals from providers "DataCo" and "AudiencePro"
**When** the buyer agent calls `get_signals` with `filters: {data_providers: ["DataCo"]}`
**Then** only signals from "DataCo" are returned
**Business Rule:** BR-3
**Priority:** P1

#### Scenario: Filter by max_cpm
**Obligation ID** UC-008-MAIN-MCP-09
**Layer** behavioral
**Given** signals with CPMs of $1.50, $3.00, and $5.00
**When** the buyer agent calls `get_signals` with `filters: {max_cpm: 3.0}`
**Then** only signals with CPM <= $3.00 are returned
**Business Rule:** BR-3
**Priority:** P1

#### Scenario: max_cpm must be >= 0
**Obligation ID** UC-008-MAIN-MCP-10
**Layer** behavioral
**Given** a valid tenant
**When** the buyer agent provides `filters: {max_cpm: -1}`
**Then** the request is rejected
**Business Rule:** PRE-B3
**Priority:** P2

#### Scenario: Filter by min_coverage_percentage
**Obligation ID** UC-008-MAIN-MCP-11
**Layer** behavioral
**Given** signals with coverage 50%, 75%, and 90%
**When** the buyer agent calls `get_signals` with `filters: {min_coverage_percentage: 80}`
**Then** only signals with coverage >= 80% are returned
**Business Rule:** BR-3
**Priority:** P1

#### Scenario: min_coverage_percentage must be 0-100
**Obligation ID** UC-008-MAIN-MCP-12
**Layer** behavioral
**Given** a valid tenant
**When** the buyer agent provides `filters: {min_coverage_percentage: 150}`
**Then** the request is rejected
**Business Rule:** PRE-B4
**Priority:** P2

#### Scenario: max_results limits output
**Obligation ID** UC-008-MAIN-MCP-13
**Layer** behavioral
**Given** 10 signals matching the query
**When** the buyer agent calls `get_signals` with `max_results: 3`
**Then** only the first 3 signals are returned (array slice after filtering)
**Business Rule:** BR-4
**Priority:** P1

#### Scenario: max_results must be >= 1
**Obligation ID** UC-008-MAIN-MCP-14
**Layer** behavioral
**Given** a valid tenant
**When** the buyer agent provides `max_results: 0`
**Then** the request is rejected
**Business Rule:** PRE-B5
**Priority:** P2

#### Scenario: Filter application order
**Obligation ID** UC-008-MAIN-MCP-15
**Layer** behavioral
**Given** signals agents with diverse signals
**When** all filters are applied (signal_spec, catalog_types, data_providers, max_cpm, min_coverage_percentage)
**Then** filters are applied in order: signal_spec match, then catalog_types, then data_providers, then max_cpm, then min_coverage_percentage
**And** max_results is enforced after all filtering
**Business Rule:** BR-3, BR-4
**Priority:** P2

#### Scenario: Graceful degradation when one signals agent fails
**Obligation ID** UC-008-MAIN-MCP-16
**Layer** behavioral
**Given** a tenant with 2 signals agents, one operational and one down
**When** the buyer agent calls `get_signals`
**Then** signals from the operational agent are returned
**And** a warning is logged for the failed agent
**And** no error is returned to the buyer
**Business Rule:** BR-6
**Priority:** P0

#### Scenario: Context echo in get_signals response
**Obligation ID** UC-008-MAIN-MCP-17
**Layer** behavioral
**Given** a valid tenant
**When** the buyer agent calls `get_signals` with `context: {"trace_id": "xyz"}`
**Then** the response echoes `context: {"trace_id": "xyz"}`
**Business Rule:** BR-10
**Priority:** P1

### Main Flow (REST/A2A): get_signals via A2A

#### Scenario: A2A get_signals returns identical structure to MCP
**Obligation ID** UC-008-MAIN-REST-01
**Layer** behavioral
**Given** the same tenant and signals data
**When** the buyer calls `get_signals` via A2A
**Then** the response structure is identical to the MCP path (no ToolResult wrapper)
**Priority:** P1

### Extension A: No Matching Signals (Empty Result)

#### Scenario: No signals match filters
**Obligation ID** UC-008-EXT-A-01
**Layer** behavioral
**Given** signals agents with signals that don't match the query
**When** the buyer agent calls `get_signals` with restrictive filters
**Then** the response contains `signals: []` (empty array)
**And** the response is valid (HTTP 200, no errors)
**And** context is echoed
**Priority:** P1

#### Scenario: Empty result is not an error
**Obligation ID** UC-008-EXT-A-02
**Layer** behavioral
**Given** no signals matching query
**When** the response is constructed
**Then** the response does NOT contain an `errors` field
**And** the HTTP status is 200
**Priority:** P1

### Extension B: Signal Activation (activate_signal)

#### Scenario: Successful signal activation
**Obligation ID** UC-008-EXT-B-01
**Layer** behavioral
**Given** an authenticated buyer with a valid `signal_agent_segment_id` from a prior get_signals response
**When** the buyer agent calls `activate_signal` with the segment ID and `deployments: ["dv360"]`
**Then** the response contains `deployments` array with activation details
**And** each deployment includes `decisioning_platform_segment_id` (format: `seg_{signal_id}_{uuid}`)
**And** each deployment includes `estimated_duration` (15 min) and `status: "processing"`
**And** the response does NOT contain `errors` (success variant)
**Business Rule:** BR-9 (atomic: success XOR error)
**Priority:** P0

#### Scenario: Authentication required for activate_signal
**Obligation ID** UC-008-EXT-B-02
**Layer** behavioral
**Given** no authentication context
**When** the buyer agent calls `activate_signal`
**Then** the response returns an error "Authentication required for signal activation"
**Business Rule:** BR-8
**Priority:** P0

#### Scenario: activate_signal requires at least one deployment
**Obligation ID** UC-008-EXT-B-03
**Layer** schema
**Given** an authenticated buyer
**When** the buyer agent calls `activate_signal` with `deployments: []`
**Then** the request is rejected (minItems: 1)
**Business Rule:** PRE-B8
**Priority:** P1

#### Scenario: signal_agent_segment_id is required
**Obligation ID** UC-008-EXT-B-04
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer agent calls `activate_signal` without `signal_agent_segment_id`
**Then** the request is rejected
**Business Rule:** PRE-B7
**Priority:** P1

#### Scenario: Context echo in activate_signal
**Obligation ID** UC-008-EXT-B-05
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer agent calls `activate_signal` with `context: {"ref": "123"}`
**Then** the response echoes the context regardless of success or failure
**Business Rule:** BR-10
**Priority:** P1

### Extension C: APPROVAL_REQUIRED (Premium Signal)

#### Scenario: Premium signal requires approval
**Obligation ID** UC-008-EXT-C-01
**Layer** behavioral
**Given** an authenticated buyer and a signal with `signal_agent_segment_id` starting with "premium_"
**When** the buyer agent calls `activate_signal` with this premium signal ID
**Then** the response contains `errors` array with code `APPROVAL_REQUIRED`
**And** the error message includes the signal ID and indicates manual approval is needed
**And** the response does NOT contain `deployments` (error variant)
**Business Rule:** BR-7, BR-9
**Priority:** P1

#### Scenario: Non-premium signal does not trigger approval
**Obligation ID** UC-008-EXT-C-02
**Layer** behavioral
**Given** an authenticated buyer and a signal with `signal_agent_segment_id: "standard_abc"`
**When** the buyer agent calls `activate_signal`
**Then** the signal is activated normally (no APPROVAL_REQUIRED error)
**Priority:** P1

#### Scenario: Premium detection is prefix-based
**Obligation ID** UC-008-EXT-C-03
**Layer** behavioral
**Given** signal IDs "premium_abc" (premium), "mypremium_abc" (not premium), "premium" (not premium -- no underscore suffix)
**When** each is checked for premium status
**Then** only "premium_abc" triggers the APPROVAL_REQUIRED flow
**Priority:** P2

### Extension D: Activation Failure

#### Scenario: ACTIVATION_FAILED -- provider unavailable
**Obligation ID** UC-008-EXT-D-01
**Layer** behavioral
**Given** an authenticated buyer and a signal whose provider is unreachable
**When** the buyer agent calls `activate_signal`
**Then** the response contains `errors` array with code `ACTIVATION_FAILED` and message "Signal provider unavailable"
**And** the response does NOT contain `deployments`
**And** context is echoed
**Business Rule:** BR-9
**Priority:** P1

#### Scenario: ACTIVATION_ERROR -- unexpected exception
**Obligation ID** UC-008-EXT-D-02
**Layer** behavioral
**Given** an authenticated buyer and an unexpected exception during activation
**When** the activation processing throws
**Then** the error is logged
**And** the response contains `errors` array with code `ACTIVATION_ERROR`
**And** the response does NOT contain `deployments`
**And** context is echoed
**Business Rule:** BR-9
**Priority:** P1

#### Scenario: Activation failure does not change system state
**Obligation ID** UC-008-EXT-D-03
**Layer** behavioral
**Given** a failed activation attempt
**When** the error is returned
**Then** no activation record is persisted
**And** no deployment is created
**Priority:** P2

### Schema Compliance

#### Scenario: get_signals response conforms to schema
**Obligation ID** UC-008-SCHEMA-01
**Layer** behavioral
**Given** any successful get_signals response
**When** serialized to JSON
**Then** it validates against `get-signals-response.json`
**Priority:** P0

#### Scenario: activate_signal success response conforms to schema
**Obligation ID** UC-008-SCHEMA-02
**Layer** behavioral
**Given** a successful activation
**When** the response is serialized
**Then** it validates against `activate-signal-response.json` (success variant with `deployments`)
**And** the response does NOT have both `deployments` and `errors`
**Priority:** P0

#### Scenario: activate_signal error response conforms to schema
**Obligation ID** UC-008-SCHEMA-03
**Layer** behavioral
**Given** a failed activation
**When** the response is serialized
**Then** it validates against `activate-signal-response.json` (error variant with `errors`)
**And** the response does NOT have both `deployments` and `errors`
**Priority:** P0
