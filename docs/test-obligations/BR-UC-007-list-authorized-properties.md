# UC-007: Discover Publisher Properties -- Test Obligations

## Source
- Requirements: `/Users/konst/projects/adcp-req/docs/requirements/use-cases/BR-UC-007-list-authorized-properties/`
- Use Case ID: BR-UC-007
- Files: BR-UC-007.md, BR-UC-007-main-mcp.md, BR-UC-007-main-rest.md, BR-UC-007-ext-a.md, BR-UC-007-ext-b.md, BR-UC-007-ext-c.md
- Referenced Rules: BR-RULE-041, BR-RULE-042, BR-RULE-043, BR-RULE-044, BR-RULE-045

## 3.6 Upgrade Impact
Low direct impact. This use case is a read-only discovery endpoint. The primary concern is ensuring the `ListAuthorizedPropertiesResponse` schema remains compatible with adcp 3.6 schema definitions. If the `list-authorized-properties-response.json` schema added or renamed fields in 3.6, the Pydantic model must be updated accordingly. The `media-channel` enum (18 types) should be verified against the 3.6 enum definition.

## Test Scenarios

### Main Flow (MCP): Buyer discovers properties via MCP tool call

#### Scenario: Successful discovery with publishers configured
**Obligation ID** UC-007-MAIN-MCP-01
**Layer** schema
**Given** a tenant with 3 PublisherPartner records (cnn.com, bbc.com, nytimes.com) and advertising_policy enabled
**When** the buyer agent invokes `list_authorized_properties` MCP tool without filters
**Then** the response contains `publisher_domains` array with all 3 domains sorted alphabetically: ["bbc.com", "cnn.com", "nytimes.com"]
**And** the response includes `advertising_policies` text assembled from tenant config
**And** the response conforms to `list-authorized-properties-response.json` schema
**Business Rule:** BR-2 (all registered partnerships returned regardless of verification status), BR-3 (sorted alphabetically)
**Priority:** P0

#### Scenario: Successful discovery with domain filter
**Obligation ID** UC-007-MAIN-MCP-02
**Layer** behavioral
**Given** a tenant with publishers cnn.com, bbc.com, nytimes.com
**When** the buyer agent invokes `list_authorized_properties` with `publisher_domains: ["cnn.com"]`
**Then** the response contains only `publisher_domains: ["cnn.com"]`
**Priority:** P1

#### Scenario: Domain filter with no matching publishers
**Obligation ID** UC-007-MAIN-MCP-03
**Layer** behavioral
**Given** a tenant with publishers cnn.com, bbc.com
**When** the buyer agent invokes `list_authorized_properties` with `publisher_domains: ["fox.com"]`
**Then** the response contains `publisher_domains: []` (empty array, not an error)
**Priority:** P1

#### Scenario: Empty portfolio returns descriptive message
**Obligation ID** UC-007-MAIN-MCP-04
**Layer** schema
**Given** a tenant with no PublisherPartner records configured
**When** the buyer agent invokes `list_authorized_properties`
**Then** the response contains `publisher_domains: []`
**And** the response includes a descriptive `portfolio_description` explaining the empty portfolio
**Business Rule:** BR-4 (empty portfolio returns empty publisher_domains with descriptive message)
**Priority:** P1

#### Scenario: Authentication is optional
**Obligation ID** UC-007-MAIN-MCP-05
**Layer** behavioral
**Given** a tenant with publishers configured
**When** the buyer agent invokes `list_authorized_properties` without any authentication token
**Then** the response still returns the publisher portfolio successfully (no auth error)
**Business Rule:** BR-1 (authentication is optional for discovery endpoints)
**Priority:** P0

#### Scenario: Advertising policies assembled from tenant config
**Obligation ID** UC-007-MAIN-MCP-06
**Layer** behavioral
**Given** a tenant with advertising_policy enabled, prohibited categories ["gambling"], tactics ["popup"], and blocked advertisers ["badco.com"]
**When** the buyer agent invokes `list_authorized_properties`
**Then** the response `advertising_policies` field contains human-readable text referencing the prohibited categories, tactics, and blocked advertisers
**Business Rule:** BR-6
**Priority:** P1

#### Scenario: Advertising policies omitted when disabled
**Obligation ID** UC-007-MAIN-MCP-07
**Layer** behavioral
**Given** a tenant with advertising_policy disabled (or not configured)
**When** the buyer agent invokes `list_authorized_properties`
**Then** the response does not include `advertising_policies` field (or it is null)
**Priority:** P2

#### Scenario: Context echo in response
**Obligation ID** UC-007-MAIN-MCP-08
**Layer** schema
**Given** a valid tenant
**When** the buyer agent invokes `list_authorized_properties` with `context: {"request_id": "abc-123", "campaign": "summer"}`
**Then** the response `context` field contains the exact same object: `{"request_id": "abc-123", "campaign": "summer"}`
**Business Rule:** BR-5 (request context echoed unchanged)
**Priority:** P0

#### Scenario: Context omitted when not provided
**Obligation ID** UC-007-MAIN-MCP-09
**Layer** schema
**Given** a valid tenant
**When** the buyer agent invokes `list_authorized_properties` without `context`
**Then** the response does not include a `context` field (or it is null)
**Priority:** P2

#### Scenario: Publishers returned regardless of verification status
**Obligation ID** UC-007-MAIN-MCP-10
**Layer** behavioral
**Given** a tenant with 2 verified and 1 unverified PublisherPartner records
**When** the buyer agent invokes `list_authorized_properties`
**Then** all 3 publisher domains are returned
**Business Rule:** BR-2
**Priority:** P1

#### Scenario: Audit event is logged
**Obligation ID** UC-007-MAIN-MCP-11
**Layer** behavioral
**Given** a valid tenant with publishers
**When** the buyer agent invokes `list_authorized_properties`
**Then** an audit event is logged with operation details including publisher_count and publisher_domains
**Priority:** P2

#### Scenario: Response wrapping for MCP path
**Obligation ID** UC-007-MAIN-MCP-12
**Layer** behavioral
**Given** a valid tenant with publishers
**When** the buyer agent invokes `list_authorized_properties` via MCP
**Then** the response is wrapped in a ToolResult with human-readable text and structured data
**Priority:** P1

### Main Flow (REST/A2A): Buyer discovers properties via A2A endpoint

#### Scenario: Successful A2A discovery
**Obligation ID** UC-007-MAIN-REST-01
**Layer** behavioral
**Given** a tenant with publishers configured
**When** the buyer agent sends `list_authorized_properties` via A2A
**Then** the response returns `ListAuthorizedPropertiesResponse` directly (no ToolResult wrapper)
**And** the response content is identical to the MCP path
**Priority:** P1

#### Scenario: A2A path shares implementation with MCP path
**Obligation ID** UC-007-MAIN-REST-02
**Layer** behavioral
**Given** the same tenant and publisher data
**When** the buyer calls via MCP and via A2A
**Then** both paths produce structurally identical responses (same publisher_domains, same policies, same context echo)
**Priority:** P1

### Extension A: TENANT_ERROR

#### Scenario: Tenant resolution fails -- no subdomain, no virtual host, no header
**Obligation ID** UC-007-EXT-A-01
**Layer** behavioral
**Given** a request with no subdomain, no virtual host, and no x-adcp-tenant header
**When** the buyer agent invokes `list_authorized_properties`
**Then** the response is a ToolError with code "TENANT_ERROR"
**And** the error message describes the resolution failure
**And** system state is unchanged
**Business Rule:** POST-F1, POST-F2
**Priority:** P0

#### Scenario: TENANT_ERROR preserves system state
**Obligation ID** UC-007-EXT-A-02
**Layer** behavioral
**Given** a request that will trigger TENANT_ERROR
**When** the request is processed
**Then** no database writes occur
**And** the read-only invariant holds
**Priority:** P2

### Extension B: PROPERTIES_ERROR

#### Scenario: Database query failure
**Obligation ID** UC-007-EXT-B-01
**Layer** behavioral
**Given** a valid tenant but the database is unreachable or the query fails
**When** the buyer agent invokes `list_authorized_properties`
**Then** the response is a ToolError with code "PROPERTIES_ERROR"
**And** the error message includes original error details
**And** the error is logged
**And** an audit event records the failure
**Business Rule:** POST-F1, POST-F2
**Priority:** P1

#### Scenario: PROPERTIES_ERROR on response construction failure
**Obligation ID** UC-007-EXT-B-02
**Layer** behavioral
**Given** a valid tenant and a successful DB query but an exception during response construction
**When** the response is being assembled
**Then** the error is caught and returned as PROPERTIES_ERROR
**Priority:** P2

### Extension C: DOMAIN_INVALID_FORMAT

#### Scenario: Invalid domain format in filter (uppercase)
**Obligation ID** UC-007-EXT-C-01
**Layer** schema
**Given** a valid tenant
**When** the buyer agent invokes `list_authorized_properties` with `publisher_domains: ["CNN.COM"]`
**Then** the system returns a validation error with code DOMAIN_INVALID_FORMAT (if schema validation is enforced)
**Or** the domain simply does not match any publishers and returns empty result (if schema validation is not enforced)
**Business Rule:** BR-7 (domain format validation)
**Priority:** P2

#### Scenario: Invalid domain format in filter (special characters)
**Obligation ID** UC-007-EXT-C-02
**Layer** behavioral
**Given** a valid tenant
**When** the buyer agent invokes `list_authorized_properties` with `publisher_domains: ["cnn com"]` (contains space)
**Then** the system rejects the domain format or returns empty results
**Business Rule:** BR-7
**Priority:** P2

#### Scenario: Valid domain format passes validation
**Obligation ID** UC-007-EXT-C-03
**Layer** schema
**Given** a valid tenant
**When** the buyer agent provides `publisher_domains: ["valid-domain.example.com"]`
**Then** the domain passes format validation (matches `^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$`)
**Priority:** P2

#### Scenario: Empty publisher_domains array is rejected
**Obligation ID** UC-007-EXT-C-04
**Layer** schema
**Given** a valid tenant
**When** the buyer agent provides `publisher_domains: []` (empty array)
**Then** the request is rejected (minItems: 1 constraint)
**Business Rule:** BR-8
**Priority:** P2

### Schema Compliance

#### Scenario: Response conforms to list-authorized-properties-response.json
**Obligation ID** UC-007-SCHEMA-01
**Layer** schema
**Given** any successful response
**When** the response is serialized to JSON
**Then** it validates against the `list-authorized-properties-response.json` schema
**And** `publisher_domains` is a required array of strings
**And** optional fields (primary_channels, primary_countries, portfolio_description, advertising_policies, last_updated) are either absent or correctly typed
**Priority:** P0

#### Scenario: MediaChannel enum values are valid
**Obligation ID** UC-007-SCHEMA-02
**Layer** schema
**Given** a response that includes `primary_channels`
**When** the channels are serialized
**Then** each value is one of the 18 standardized media-channel enum values
**Priority:** P1
