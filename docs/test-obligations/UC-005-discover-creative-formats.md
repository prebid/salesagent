# UC-005: Discover Creative Formats -- Test Obligations

## Source

- **BR-UC-005.md** -- Use case overview: Buyer discovers creative formats the Seller accepts
- **BR-UC-005-main-mcp.md** -- Main flow via MCP tool
- **BR-UC-005-main-rest.md** -- Main flow via A2A/REST endpoint
- **BR-UC-005-ext-a.md** -- Extension: No tenant context available
- **BR-UC-005-ext-b.md** -- Extension: Invalid request parameters

## 3.6 Upgrade Impact

**Low direct impact.** UC-005 is a read-only discovery operation returning Format objects (not Creative objects). The Creative base-class bug (salesagent-goy2) does not directly affect this use case.

However, UC-005 is a precondition for UC-006 (PRE-B3: "Each creative has a valid format_id referencing a known format -- see UC-005"). Broken format discovery cascades into creative sync failures.

Key upgrade concerns:
- Format schema stability: Verify `Format`, `FormatId`, `FormatCategory`, `AssetContentType`, `WcagLevel` types still match adcp 3.6.0
- ListCreativeFormatsRequest/Response schema: Confirm field compatibility with 3.6.0 library types
- CreativeAgentCapability enum: Confirm values unchanged in 3.6.0

---

## Test Scenarios

### Main Flow (MCP): Discover Creative Formats via MCP Tool

Source: BR-UC-005-main-mcp.md

#### Scenario: Full catalog returned with no filters (BR-1)
**Obligation ID** UC-005-MAIN-MCP-01
**Layer** behavioral

**Given** the Seller Agent is operational and has at least one registered creative agent
**And** the MCP connection is established
**When** the Buyer calls `list_creative_formats` with no filter parameters
**Then** the response is a valid `ListCreativeFormatsResponse`
**And** the response contains the complete catalog of formats from all registered agents (POST-S1)
**And** each format includes asset requirements with type, dimensions, and required/optional flags (POST-S2)
**Business Rule** BR-1: All request filters are optional; empty request returns full catalog
**Priority** P1 -- core happy path

#### Scenario: Authentication is optional for discovery (BR-2)
**Obligation ID** UC-005-MAIN-MCP-02
**Layer** behavioral

**Given** the Seller Agent is operational
**When** the Buyer calls `list_creative_formats` without authentication credentials
**Then** the response is a valid `ListCreativeFormatsResponse` (not an auth error)
**And** the format catalog is returned normally
**Business Rule** BR-2: Authentication is optional for this discovery endpoint
**Priority** P1 -- security contract

#### Scenario: Formats aggregated from all registered agents (BR-3)
**Obligation ID** UC-005-MAIN-MCP-03
**Layer** behavioral

**Given** the tenant has a default creative agent AND at least one tenant-specific creative agent registered
**When** the Buyer calls `list_creative_formats`
**Then** the response contains formats from BOTH the default agent and tenant-specific agents
**And** adapter-specific formats (if any) are merged into the list
**Business Rule** BR-3: Formats aggregated from all registered creative agents plus adapter-specific formats
**Priority** P1 -- aggregation correctness

#### Scenario: Results sorted by format type then name (BR-4)
**Obligation ID** UC-005-MAIN-MCP-04
**Layer** behavioral

**Given** the format catalog contains multiple formats of different types
**When** the Buyer calls `list_creative_formats`
**Then** results are sorted first by format type, then by name within each type
**And** the ordering is deterministic across repeated calls
**Business Rule** BR-4: Results sorted by format type then name
**Priority** P2 -- ordering contract

#### Scenario: Filter by format category (type)
**Obligation ID** UC-005-MAIN-MCP-05
**Layer** behavioral

**Given** the format catalog contains formats of type `display`, `video`, and `audio`
**When** the Buyer calls `list_creative_formats` with `type=video`
**Then** only formats with type `video` are returned
**And** `display` and `audio` formats are excluded (POST-S3)
**Business Rule** BR-1 (filter semantics)
**Priority** P1 -- filter correctness

#### Scenario: Filter by format_ids
**Obligation ID** UC-005-MAIN-MCP-06
**Layer** behavioral

**Given** the format catalog contains formats with known FormatId values
**When** the Buyer calls `list_creative_formats` with specific `format_ids` list
**Then** only the formats matching those FormatId values are returned
**Business Rule** BR-1 (filter semantics)
**Priority** P1 -- filter correctness

#### Scenario: Filter by asset_types (BR-6)
**Obligation ID** UC-005-MAIN-MCP-07
**Layer** behavioral

**Given** the format catalog contains formats with `image` assets and formats with `video` assets
**When** the Buyer calls `list_creative_formats` with `asset_types=[video]`
**Then** only formats containing at least one `video` asset type are returned
**Business Rule** BR-6: Asset type filters match formats containing at least one of the requested types
**Priority** P1 -- filter correctness

#### Scenario: Dimension range filter matches ANY render (BR-5)
**Obligation ID** UC-005-MAIN-MCP-08
**Layer** schema

**Given** a format has renders with widths [320, 728, 970]
**When** the Buyer calls `list_creative_formats` with `max_width=400`
**Then** the format IS included (because the 320-width render satisfies the constraint)
**Business Rule** BR-5: Dimension filters match if ANY render in a format satisfies the constraint
**Priority** P2 -- filter nuance

#### Scenario: Dimension filter excludes format when NO render matches
**Obligation ID** UC-005-MAIN-MCP-09
**Layer** schema

**Given** a format has renders with minimum width 728
**When** the Buyer calls `list_creative_formats` with `max_width=400`
**Then** the format is NOT included (no render satisfies the width constraint)
**Business Rule** BR-5
**Priority** P2 -- negative filter case

#### Scenario: Filter by is_responsive
**Obligation ID** UC-005-MAIN-MCP-10
**Layer** behavioral

**Given** the format catalog contains responsive and non-responsive formats
**When** the Buyer calls `list_creative_formats` with `is_responsive=true`
**Then** only responsive formats are returned
**Business Rule** BR-1 (filter semantics)
**Priority** P2 -- filter correctness

#### Scenario: Name search is case-insensitive partial match (BR-7)
**Obligation ID** UC-005-MAIN-MCP-11
**Layer** schema

**Given** the format catalog contains a format named "Standard Banner 728x90"
**When** the Buyer calls `list_creative_formats` with `name_search="banner"`
**Then** the "Standard Banner 728x90" format is included in results
**Business Rule** BR-7: Name search is case-insensitive partial match
**Priority** P2 -- search semantics

#### Scenario: Filter by WCAG accessibility level
**Obligation ID** UC-005-MAIN-MCP-12
**Layer** behavioral

**Given** the format catalog contains formats with WCAG levels A, AA, and AAA
**When** the Buyer calls `list_creative_formats` with `wcag_level=AA`
**Then** only formats meeting WCAG AA level are returned
**Business Rule** BR-1 (filter semantics)
**Priority** P3 -- accessibility filter

#### Scenario: Creative agent referrals included in response (POST-S4)
**Obligation ID** UC-005-MAIN-MCP-13
**Layer** schema

**Given** the creative agent registry includes referral information for additional agents
**When** the Buyer calls `list_creative_formats`
**Then** the response includes `creative_agents` referrals with capability information
**And** each referral includes agent URL and capabilities (validation, assembly, generation, preview, delivery)
**Business Rule** POST-S4
**Priority** P2 -- referral completeness

#### Scenario: Pagination with cursor-based navigation
**Obligation ID** UC-005-MAIN-MCP-14
**Layer** schema

**Given** the format catalog has more formats than the requested `max_results`
**When** the Buyer calls `list_creative_formats` with `max_results=10`
**Then** the response contains at most 10 formats
**And** the pagination response includes a cursor for the next page
**Business Rule** Pagination (max_results 1-100, default 50)
**Priority** P2 -- pagination

#### Scenario: Pagination default (max_results=50)
**Obligation ID** UC-005-MAIN-MCP-15
**Layer** behavioral

**Given** the format catalog has 75 formats
**When** the Buyer calls `list_creative_formats` with no pagination parameters
**Then** at most 50 formats are returned (the default)
**And** pagination cursor indicates more results available
**Business Rule** PaginationRequest default
**Priority** P2 -- pagination defaults

#### Scenario: Combined filters narrow results
**Obligation ID** UC-005-MAIN-MCP-16
**Layer** behavioral

**Given** the format catalog contains diverse formats
**When** the Buyer calls `list_creative_formats` with `type=display`, `asset_types=[image]`, `max_width=728`
**Then** only display formats with image assets and at least one render under 728px are returned
**Business Rule** Multiple filters applied conjunctively
**Priority** P2 -- combined filters

#### Scenario: MCP response is valid ToolResult with structured content
**Obligation ID** UC-005-MAIN-MCP-17
**Layer** behavioral

**Given** the Seller Agent is operational
**When** the Buyer calls `list_creative_formats` via MCP
**Then** the MCP response wraps the `ListCreativeFormatsResponse` as structured content in a ToolResult
**And** the response is parseable as JSON
**Business Rule** Step 7 of main MCP flow
**Priority** P1 -- protocol correctness

---

### Main Flow (REST): Discover Creative Formats via A2A/REST

Source: BR-UC-005-main-rest.md

#### Scenario: Full catalog returned via A2A endpoint
**Obligation ID** UC-005-MAIN-REST-01
**Layer** behavioral

**Given** the Seller Agent is operational
**When** the Buyer sends a `list_creative_formats` task via A2A protocol
**Then** the A2A task response contains a valid `ListCreativeFormatsResponse` payload
**And** the format catalog is complete (POST-S1)
**Priority** P1 -- REST happy path

#### Scenario: Adapter-specific formats included via REST
**Obligation ID** UC-005-MAIN-REST-02
**Layer** behavioral

**Given** the tenant uses an adapter (e.g., Broadstreet) that provides additional format templates
**When** the Buyer sends `list_creative_formats` via A2A
**Then** adapter-specific formats are merged into the response alongside creative agent formats
**Business Rule** BR-3 (adapter format merging)
**Priority** P2 -- adapter integration

#### Scenario: Tenant context resolved from request headers (REST)
**Obligation ID** UC-005-MAIN-REST-03
**Layer** behavioral

**Given** the Buyer sends `list_creative_formats` via A2A
**When** the request includes tenant identification in headers
**Then** the system resolves tenant context from request headers
**And** the correct tenant's format catalog is returned
**Business Rule** PRE-C2 (tenant context determinable)
**Priority** P1 -- REST tenant resolution

---

### Extension A: No Tenant Context

Source: BR-UC-005-ext-a.md

#### Scenario: No authentication and no hostname mapping
**Obligation ID** UC-005-EXT-A-01
**Layer** behavioral

**Given** the Seller Agent is operational
**And** the Buyer provides no authentication token
**And** no hostname mapping resolves to a tenant
**When** the Buyer calls `list_creative_formats`
**Then** the response is an error with code `TENANT_REQUIRED`
**And** the error message indicates tenant context could not be determined (POST-F2)
**And** the suggestion advises providing authentication credentials or tenant identification (POST-F3)
**Business Rule** ext-a: tenant resolution failure
**Priority** P1 -- error handling contract

#### Scenario: Error response includes all three failure postconditions
**Obligation ID** UC-005-EXT-A-02
**Layer** schema

**Given** tenant resolution will fail
**When** the Buyer calls `list_creative_formats`
**Then** POST-F1 is satisfied: Buyer knows the operation failed
**And** POST-F2 is satisfied: Error explains why (tenant context missing)
**And** POST-F3 is satisfied: Suggestion provides recovery guidance
**Priority** P1 -- error completeness

---

### Extension B: Invalid Request Parameters

Source: BR-UC-005-ext-b.md

#### Scenario: Invalid format category enum value
**Obligation ID** UC-005-EXT-B-01
**Layer** schema

**Given** the Seller Agent is operational
**When** the Buyer calls `list_creative_formats` with `type="invalid_category"`
**Then** the response is an error with code `VALIDATION_ERROR`
**And** the error message identifies the invalid `type` field and why it failed
**And** the suggestion provides valid FormatCategory enum values
**Priority** P1 -- validation error contract

#### Scenario: Malformed FormatId objects
**Obligation ID** UC-005-EXT-B-02
**Layer** schema

**Given** the Seller Agent is operational
**When** the Buyer calls `list_creative_formats` with malformed `format_ids` (e.g., missing `agent_url`)
**Then** the response is a `VALIDATION_ERROR`
**And** the error identifies the malformed FormatId field
**Priority** P2 -- structural validation

#### Scenario: Non-integer dimension values
**Obligation ID** UC-005-EXT-B-03
**Layer** schema

**Given** the Seller Agent is operational
**When** the Buyer calls `list_creative_formats` with `max_width="not_a_number"`
**Then** the response is a `VALIDATION_ERROR`
**And** the error identifies the dimension field type mismatch
**Priority** P2 -- type coercion

#### Scenario: Invalid WCAG level
**Obligation ID** UC-005-EXT-B-04
**Layer** schema

**Given** the Seller Agent is operational
**When** the Buyer calls `list_creative_formats` with `wcag_level="INVALID"`
**Then** the response is a `VALIDATION_ERROR`
**And** the error identifies the invalid WcagLevel value
**And** the suggestion lists valid WCAG levels (A, AA, AAA)
**Priority** P2 -- enum validation

#### Scenario: Validation errors are detailed per field
**Obligation ID** UC-005-EXT-B-05
**Layer** schema

**Given** the Buyer sends a request with multiple invalid parameters
**When** the system validates the request
**Then** the error response includes per-field validation messages
**And** POST-F2 is satisfied (which parameters are invalid and why)
**And** POST-F3 is satisfied (valid values or format guidance)
**Priority** P1 -- multi-field validation

---

## Schema Compliance Scenarios

These verify ListCreativeFormatsRequest/Response roundtrip against adcp 3.6.0 schemas.

#### Scenario: ListCreativeFormatsResponse conforms to adcp 3.6.0 schema
**Obligation ID** UC-005-EXT-B-06
**Layer** schema

**Given** a valid `ListCreativeFormatsResponse` constructed by the system
**When** serialized via `model_dump()`
**Then** the output validates against adcp 3.6.0 `list-creative-formats-response.json` schema
**And** no extra fields are present (in strict/development mode)
**Priority** P0 -- schema contract (adcp compliance test)

#### Scenario: ListCreativeFormatsRequest accepts all valid filter combinations
**Obligation ID** UC-005-EXT-B-07
**Layer** schema

**Given** a request with every optional filter provided and valid
**When** parsed by Pydantic model
**Then** the model is successfully constructed with all fields populated
**Priority** P1 -- request schema coverage

#### Scenario: Format objects include all required fields
**Obligation ID** UC-005-EXT-B-08
**Layer** schema

**Given** a `ListCreativeFormatsResponse` with formats
**When** the Buyer inspects any format in the response
**Then** each format has: format_id (FormatId), name, type (FormatCategory)
**And** each format has: renders (asset requirements with dimensions), assets (type requirements)
**Priority** P1 -- response completeness (POST-S2)

#### Scenario: FormatId is a structured object with agent_url and id
**Obligation ID** UC-005-EXT-B-09
**Layer** schema

**Given** any format in the response
**When** the Buyer inspects its format_id
**Then** format_id is an object with `agent_url` (URL) and `id` (string)
**And** not a bare string identifier
**Priority** P1 -- schema structure
