# UC-010: Discover Seller Capabilities -- Test Obligations

## Source
- Requirements: `/Users/konst/projects/adcp-req/docs/requirements/use-cases/BR-UC-010-discover-seller-capabilities/`
- Use Case ID: BR-UC-010
- Files: BR-UC-010.md, BR-UC-010-main-mcp.md, BR-UC-010-main-rest.md, BR-UC-010-ext-a.md, BR-UC-010-ext-b.md, BR-UC-010-ext-c.md, BR-UC-010-ext-d.md, BR-UC-010-ext-e.md
- Referenced Rules: BR-RULE-041, BR-RULE-043, BR-RULE-052, BR-RULE-053

## 3.6 Upgrade Impact
High impact. The `get-adcp-capabilities-response.json` schema is central to the buyer-seller handshake and is likely to have changes in 3.6 (new protocol domains, new feature flags, new execution capabilities). The `media-buy-features.json` schema for feature flags (inline_creative_management, property_list_filtering, content_standards, conversion_tracking) may have new flags. The `channels` enum may have expanded. The `supported_protocols` array and any new protocol domain sections (signals, governance, sponsored_intelligence, creative) should be checked. The adcp version reporting (major_versions) must reflect 3.6 correctly.

## Test Scenarios

### Main Flow (MCP): get_adcp_capabilities via MCP

#### Scenario: Full capabilities with tenant and adapter
**Obligation ID** UC-010-MAIN-MCP-01
**Layer** behavioral
**Given** a tenant "default" with an operational adapter supporting channels ["display", "video"] and publisher partners ["pub1.com", "pub2.com"]
**When** the buyer agent invokes `get_adcp_capabilities` via MCP
**Then** the response contains `adcp: {major_versions: [3]}`
**And** `supported_protocols: ["media_buy"]`
**And** `media_buy.portfolio.primary_channels` includes MediaChannel values for display and video (mapped via aliases: video -> olv)
**And** `media_buy.portfolio.publisher_domains` includes ["pub1.com", "pub2.com"]
**And** `media_buy.features` reflects current implementation flags
**And** `media_buy.execution.targeting` includes dimensions from adapter.get_targeting_capabilities()
**And** `last_updated` timestamp is present
**Business Rule:** BR-3 (channel alias mapping), BR-6 (feature flags), BR-7 (targeting delegation)
**Priority:** P0

#### Scenario: Authentication is optional
**Obligation ID** UC-010-MAIN-MCP-02
**Layer** behavioral
**Given** a valid tenant
**When** the buyer agent invokes `get_adcp_capabilities` without authentication
**Then** the response returns capabilities successfully (no auth error)
**Business Rule:** BR-1
**Priority:** P0

#### Scenario: Channel alias mapping (video -> olv, audio -> streaming_audio)
**Obligation ID** UC-010-MAIN-MCP-03
**Layer** schema
**Given** an adapter reporting channels ["video", "audio", "display"]
**When** capabilities are assembled
**Then** channels are mapped to MediaChannel enum values: video -> olv, audio -> streaming_audio, display -> display
**Business Rule:** BR-3
**Priority:** P1

#### Scenario: Feature flags reflect implementation state
**Obligation ID** UC-010-MAIN-MCP-04
**Layer** schema
**Given** the current implementation
**When** capabilities are assembled
**Then** the features section contains at minimum:
- `inline_creative_management: true`
- `property_list_filtering: false`
- `content_standards: false` (or true if implemented)
- `conversion_tracking` status
**Business Rule:** BR-6
**Priority:** P1

#### Scenario: Targeting capabilities from adapter
**Obligation ID** UC-010-MAIN-MCP-05
**Layer** behavioral
**Given** an adapter with targeting capabilities including geo_countries, geo_regions, geo_metros, geo_postal_areas, age_restriction, device_platform, language
**When** capabilities are assembled
**Then** `media_buy.execution.targeting` includes all adapter-reported targeting dimensions
**Business Rule:** BR-7
**Priority:** P1

#### Scenario: Only media_buy protocol section is populated
**Obligation ID** UC-010-MAIN-MCP-06
**Layer** behavioral
**Given** the current implementation
**When** capabilities are assembled
**Then** `supported_protocols` includes "media_buy"
**And** signals, governance, sponsored_intelligence, and creative sections are not populated
**Business Rule:** BR-9
**Priority:** P2

#### Scenario: MCP response wrapping
**Obligation ID** UC-010-MAIN-MCP-07
**Layer** behavioral
**Given** a valid capabilities response
**When** returned via MCP
**Then** the response is wrapped in ToolResult with human-readable summary and structured data
**Priority:** P1

### Main Flow (REST/A2A): get_adcp_capabilities via A2A

#### Scenario: A2A capabilities response
**Obligation ID** UC-010-MAIN-REST-01
**Layer** behavioral
**Given** a valid tenant
**When** the buyer sends `get_adcp_capabilities` via A2A
**Then** the response is returned directly (no ToolResult wrapper)
**And** the response content is identical to the MCP path
**Priority:** P1

#### Scenario: A2A with Bearer token -- authentication succeeds
**Obligation ID** UC-010-MAIN-REST-02
**Layer** behavioral
**Given** a valid Bearer token in the A2A request
**When** the capabilities are requested
**Then** the system authenticates and may provide enriched response based on principal's adapter
**Priority:** P2

### Extension A: TENANT_UNAVAILABLE (Minimal Capabilities)

#### Scenario: No tenant context returns minimal capabilities
**Obligation ID** UC-010-EXT-A-01
**Layer** behavioral
**Given** a request with no subdomain, no virtual host, no x-adcp-tenant header, and no thread-local tenant context
**When** the buyer agent invokes `get_adcp_capabilities`
**Then** the response contains `adcp: {major_versions: [3]}` and `supported_protocols: ["media_buy"]`
**And** the response does NOT contain detailed media_buy section (no features, execution, or portfolio)
**Business Rule:** BR-2
**Priority:** P0

#### Scenario: TENANT_UNAVAILABLE is graceful degradation, not an error
**Obligation ID** UC-010-EXT-A-02
**Layer** behavioral
**Given** no tenant context
**When** the response is constructed
**Then** the response is a valid capabilities response (HTTP 200), not an error
**And** the buyer receives useful (minimal) information
**Priority:** P1

#### Scenario: Thread-local tenant context is checked as fallback
**Obligation ID** UC-010-EXT-A-03
**Layer** behavioral
**Given** a request with no explicit tenant identifiers but a valid thread-local tenant
**When** the capabilities are requested
**Then** the system uses the thread-local tenant and returns full capabilities
**Priority:** P2

### Extension B: ADAPTER_UNAVAILABLE (Degraded Capabilities)

#### Scenario: Adapter failure defaults channels to [display]
**Obligation ID** UC-010-EXT-B-01
**Layer** behavioral
**Given** a valid tenant but the adapter cannot be instantiated (no principal, adapter error)
**When** capabilities are assembled
**Then** `primary_channels` defaults to `["display"]`
**And** a warning is logged
**Business Rule:** BR-4
**Priority:** P1

#### Scenario: DB failure uses placeholder publisher domain
**Obligation ID** UC-010-EXT-B-02
**Layer** behavioral
**Given** a valid tenant "mystore" but the PublisherPartner DB query fails
**When** capabilities are assembled
**Then** `publisher_domains` contains `["mystore.example.com"]` (placeholder from subdomain)
**And** a warning is logged
**Business Rule:** BR-5
**Priority:** P1

#### Scenario: Both adapter and DB failures degrade independently
**Obligation ID** UC-010-EXT-B-03
**Layer** behavioral
**Given** a valid tenant where both adapter lookup and DB query fail simultaneously
**When** capabilities are assembled
**Then** channels default to ["display"] AND publisher_domains uses placeholder
**And** the response is still valid (not an error)
**Priority:** P1

#### Scenario: No targeting capabilities when adapter unavailable
**Obligation ID** UC-010-EXT-B-04
**Layer** behavioral
**Given** an adapter failure
**When** capabilities are assembled
**Then** targeting section has only minimal defaults (geo_countries=True, geo_regions=True)
**And** no adapter-specific targeting dimensions are included
**Priority:** P2

#### Scenario: Degraded capabilities are not propagated as errors
**Obligation ID** UC-010-EXT-B-05
**Layer** behavioral
**Given** adapter or DB failures
**When** the response is returned
**Then** the caller receives a valid response, not an error
**And** warnings are logged server-side only
**Priority:** P1

### Extension C: AUTH_TOKEN_INVALID

#### Scenario: A2A path -- invalid token provided causes error
**Obligation ID** UC-010-EXT-C-01
**Layer** behavioral
**Given** a Bearer token that is expired/malformed in an A2A request
**When** the buyer sends `get_adcp_capabilities` via A2A
**Then** a ServerError with AUTH_TOKEN_INVALID is raised
**And** no capabilities are returned
**Priority:** P1

#### Scenario: MCP path -- invalid token is silently ignored
**Obligation ID** UC-010-EXT-C-02
**Layer** behavioral
**Given** an invalid authentication token in an MCP request (require_valid_token=False)
**When** the buyer invokes `get_adcp_capabilities` via MCP
**Then** the system proceeds without principal context
**And** capabilities are returned (may be degraded due to no adapter)
**Priority:** P1

#### Scenario: A2A path -- no token at all succeeds (optional auth)
**Obligation ID** UC-010-EXT-C-03
**Layer** behavioral
**Given** an A2A request with no Bearer token at all
**When** the buyer sends `get_adcp_capabilities`
**Then** the system creates MinimalContext for tenant detection
**And** capabilities are returned normally
**Priority:** P1

### Extension D: PROTOCOL_FILTER_IGNORED

#### Scenario: Protocols filter is accepted but not honored
**Obligation ID** UC-010-EXT-D-01
**Layer** behavioral
**Given** a valid tenant
**When** the buyer invokes `get_adcp_capabilities` with `protocols: ["media_buy"]`
**Then** the system returns ALL capabilities sections, not just media_buy
**And** no error or warning is returned about the ignored filter
**Priority:** P2

#### Scenario: All valid protocol values accepted
**Obligation ID** UC-010-EXT-D-02
**Layer** behavioral
**Given** a valid tenant
**When** the buyer provides `protocols: ["media_buy", "signals", "governance", "sponsored_intelligence", "creative"]`
**Then** the request is accepted (no validation error)
**But** the filter is not applied to the response
**Priority:** P3

### Extension E: Context Echo

#### Scenario: Context echo in capabilities response
**Obligation ID** UC-010-EXT-E-01
**Layer** schema
**Given** a valid tenant
**When** the buyer invokes `get_adcp_capabilities` with `context: {"session_id": "s1"}`
**Then** the response includes `context: {"session_id": "s1"}`
**Business Rule:** BR-8
**Priority:** P1

#### Scenario: Context echo gap -- implementation may not echo context
**Obligation ID** UC-010-EXT-E-02
**Layer** behavioral
**Given** a request with context
**When** the capabilities response is constructed
**Then** verify whether the current implementation echoes context (noted as a potential gap in requirements)
**Priority:** P1

#### Scenario: Context is opaque -- not parsed or modified
**Obligation ID** UC-010-EXT-E-03
**Layer** behavioral
**Given** a context with arbitrary nested objects: `{"a": {"b": [1, 2, 3]}, "c": null}`
**When** the response is constructed
**Then** the exact same structure is echoed without modification
**Priority:** P2

### Schema Compliance

#### Scenario: Response conforms to get-adcp-capabilities-response.json
**Obligation ID** UC-010-SCHEMA-01
**Layer** schema
**Given** any capabilities response
**When** serialized to JSON
**Then** it validates against `get-adcp-capabilities-response.json` schema
**And** required fields `adcp` (major_versions) and `supported_protocols` are present
**Priority:** P0

#### Scenario: MediaChannel enum values are valid for 3.6
**Obligation ID** UC-010-SCHEMA-02
**Layer** schema
**Given** a capabilities response with primary_channels
**When** the channels are serialized
**Then** each value is a valid channels enum value as defined in adcp 3.6
**Priority:** P1

#### Scenario: media-buy-features schema compliance
**Obligation ID** UC-010-SCHEMA-03
**Layer** behavioral
**Given** a capabilities response with media_buy.features
**When** the features are serialized
**Then** they conform to `media-buy-features.json` schema
**Priority:** P1
