# UC-012: Manage Content Standards -- Test Obligations

## Source
- Requirements: `/Users/konst/projects/adcp-req/docs/requirements/use-cases/BR-UC-012-manage-content-standards/`
- Use Case ID: BR-UC-012
- Files: BR-UC-012.md, BR-UC-012-main-mcp.md, BR-UC-012-main-rest.md, BR-UC-012-ext-a.md, BR-UC-012-ext-b.md, BR-UC-012-ext-c.md, BR-UC-012-ext-d.md, BR-UC-012-ext-e.md, BR-UC-012-ext-f.md, BR-UC-012-ext-g.md
- Referenced Rules: BR-RULE-043, BR-RULE-063, BR-RULE-064, BR-RULE-065, BR-RULE-066, BR-RULE-067, BR-RULE-068, BR-RULE-069

## 3.6 Upgrade Impact
High impact. Content standards is a governance protocol domain that was introduced or significantly expanded in the adcp 3.x series. The schemas (`content-standards.json`, create/get/list/update CRUD request/response schemas) may have evolved between 3.2 and 3.6. The `calibration_exemplars` model referencing both URL references and inline `artifact.json` objects is a complex structure that should be checked. The `channels.json` enum (18 types) and country/language code formats (ISO 3166-1 alpha-2 / BCP 47) may have been refined. The salesagent currently reports `content_standards=false` in capabilities, meaning this is likely a new implementation area. Schema changes in 3.6 affect the implementation target.

## Test Scenarios

### Main Flow (MCP): list_content_standards

#### Scenario: List all content standards for a tenant
**Obligation ID** UC-012-MAIN-MCP-01
**Layer** behavioral
**Given** an authenticated buyer with 3 content standards defined for the tenant
**When** the buyer invokes `list_content_standards` MCP tool without filters
**Then** the response contains `standards` array with all 3 content standard objects
**And** each standard includes `standards_id`, scope fields, `policy` text
**Business Rule:** BR-1 (auth required), BR-2 (tenant scoped)
**Priority:** P0

#### Scenario: List with channel filter (OR matching)
**Obligation ID** UC-012-MAIN-MCP-02
**Layer** behavioral
**Given** content standards: A (channels: [display]), B (channels: [video]), C (channels: [display, video])
**When** the buyer invokes `list_content_standards` with `channels: ["display"]`
**Then** standards A and C are returned (OR matching -- any overlap)
**Business Rule:** BR-7
**Priority:** P1

#### Scenario: List with language filter (OR matching)
**Obligation ID** UC-012-MAIN-MCP-03
**Layer** behavioral
**Given** content standards: A (languages: [en]), B (languages: [fr]), C (languages: [en, fr])
**When** the buyer invokes `list_content_standards` with `languages: ["en"]`
**Then** standards A and C are returned (OR matching)
**Business Rule:** BR-7
**Priority:** P1

#### Scenario: List with country filter (AND matching)
**Obligation ID** UC-012-MAIN-MCP-04
**Layer** behavioral
**Given** content standards: A (countries: [US, UK]), B (countries: [US]), C (countries: [UK, FR])
**When** the buyer invokes `list_content_standards` with `countries: ["US", "UK"]`
**Then** only standard A is returned (AND matching -- must cover all requested countries)
**Business Rule:** BR-7
**Priority:** P1

#### Scenario: List with no matching standards returns empty array
**Obligation ID** UC-012-MAIN-MCP-05
**Layer** behavioral
**Given** content standards that don't match the filters
**When** the buyer invokes `list_content_standards` with restrictive filters
**Then** the response contains `standards: []` (empty array, not an error)
**Priority:** P1

#### Scenario: Authentication is required
**Obligation ID** UC-012-MAIN-MCP-06
**Layer** behavioral
**Given** no authentication token
**When** the buyer invokes `list_content_standards`
**Then** the request is rejected with AUTH error
**Business Rule:** BR-1
**Priority:** P0

#### Scenario: Context echo in list response
**Obligation ID** UC-012-MAIN-MCP-07
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer invokes `list_content_standards` with `context: {"session": "s1"}`
**Then** the response echoes `context: {"session": "s1"}`
**Business Rule:** BR-8
**Priority:** P1

### Main Flow (REST/A2A): list_content_standards via A2A

#### Scenario: A2A list returns same structure
**Obligation ID** UC-012-MAIN-REST-01
**Layer** behavioral
**Given** the same tenant data
**When** the buyer sends `list_content_standards` via A2A
**Then** the response is identical to the MCP path (no ToolResult wrapper)
**Priority:** P1

### Extension A: Create Content Standard

#### Scenario: Successful creation with minimal required fields
**Obligation ID** UC-012-EXT-A-01
**Layer** schema
**Given** an authenticated buyer
**When** the buyer invokes `create_content_standards` with `scope: {languages_any: ["en"]}` and `policy: "No gambling content"`
**Then** the response contains a new `standards_id`
**And** the standard is stored in the tenant's content standards store
**Business Rule:** BR-3 (languages_any minItems: 1 + policy required)
**Priority:** P0

#### Scenario: Successful creation with full scope
**Obligation ID** UC-012-EXT-A-02
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer invokes `create_content_standards` with `scope: {languages_any: ["en", "es"], countries_all: ["US", "MX"], channels_any: ["display", "video"], description: "Brand safety for LATAM"}, policy: "No violence or adult content", calibration_exemplars: [{pass: {url: "https://example.com/good"}, fail: {url: "https://example.com/bad"}}]`
**Then** the response contains a new `standards_id`
**And** all scope fields are stored correctly
**And** calibration exemplars are stored
**Priority:** P1

#### Scenario: Create requires scope.languages_any
**Obligation ID** UC-012-EXT-A-03
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer invokes `create_content_standards` with `scope: {}` and `policy: "text"`
**Then** the request is rejected (languages_any minItems: 1)
**Business Rule:** BR-3
**Priority:** P0

#### Scenario: Create requires policy
**Obligation ID** UC-012-EXT-A-04
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer invokes `create_content_standards` with `scope: {languages_any: ["en"]}` but no `policy`
**Then** the request is rejected
**Business Rule:** BR-3
**Priority:** P0

#### Scenario: Calibration exemplars with URL references
**Obligation ID** UC-012-EXT-A-05
**Layer** behavioral
**Given** an authenticated buyer
**When** calibration_exemplars include `{pass: {url: "https://good.com"}, fail: {url: "https://bad.com"}}`
**Then** the exemplars are stored as URL references
**Business Rule:** BR-9
**Priority:** P2

#### Scenario: Calibration exemplars with inline artifacts
**Obligation ID** UC-012-EXT-A-06
**Layer** behavioral
**Given** an authenticated buyer
**When** calibration_exemplars include inline artifact objects
**Then** the exemplars are stored as inline artifacts
**Business Rule:** BR-9
**Priority:** P2

#### Scenario: Country codes follow ISO 3166-1 alpha-2
**Obligation ID** UC-012-EXT-A-07
**Layer** schema
**Given** an authenticated buyer
**When** providing `countries_all: ["US", "GB"]`
**Then** the values are accepted
**When** providing `countries_all: ["USA"]`
**Then** the request is rejected (must be 2-letter format)
**Business Rule:** BR-10
**Priority:** P2

#### Scenario: Language tags follow BCP 47
**Obligation ID** UC-012-EXT-A-08
**Layer** behavioral
**Given** an authenticated buyer
**When** providing `languages_any: ["en", "fr-CA"]`
**Then** the values are accepted
**Business Rule:** BR-10
**Priority:** P2

#### Scenario: Context echo on create
**Obligation ID** UC-012-EXT-A-09
**Layer** behavioral
**Given** an authenticated buyer with context
**When** create succeeds
**Then** context is echoed in the response
**Business Rule:** BR-8
**Priority:** P1

### Extension B: Get Content Standard

#### Scenario: Successful retrieval by standards_id
**Obligation ID** UC-012-EXT-B-01
**Layer** behavioral
**Given** an existing content standard with standards_id "cs_123"
**When** the buyer invokes `get_content_standards` with `standards_id: "cs_123"`
**Then** the response contains the full content standard: standards_id, name, countries_all, channels_any, languages_any, policy, calibration_exemplars
**Priority:** P0

#### Scenario: Get non-existent standard triggers STANDARDS_NOT_FOUND
**Obligation ID** UC-012-EXT-B-02
**Layer** behavioral
**Given** no standard with id "cs_nonexistent"
**When** the buyer invokes `get_content_standards` with `standards_id: "cs_nonexistent"`
**Then** Extension E (STANDARDS_NOT_FOUND) is triggered
**Priority:** P1

#### Scenario: Get standard from different tenant fails
**Obligation ID** UC-012-EXT-B-03
**Layer** behavioral
**Given** a content standard belonging to tenant A
**When** a buyer authenticated against tenant B requests the same standards_id
**Then** STANDARDS_NOT_FOUND is returned (tenant isolation)
**Business Rule:** BR-2
**Priority:** P1

#### Scenario: Context echo on get
**Obligation ID** UC-012-EXT-B-04
**Layer** behavioral
**Given** an authenticated buyer with context
**When** get succeeds
**Then** context is echoed
**Business Rule:** BR-8
**Priority:** P2

### Extension C: Update Content Standard

#### Scenario: Successful policy update
**Obligation ID** UC-012-EXT-C-01
**Layer** behavioral
**Given** an existing content standard "cs_123" with policy "No gambling"
**When** the buyer invokes `update_content_standards` with `standards_id: "cs_123"` and `policy: "No gambling or alcohol"`
**Then** a new version is created with the updated policy
**And** the response contains `standards_id: "cs_123"`
**Business Rule:** BR-5 (immutable versioning)
**Priority:** P0

#### Scenario: Partial update -- only provided fields change
**Obligation ID** UC-012-EXT-C-02
**Layer** behavioral
**Given** an existing standard with scope {countries_all: ["US"], languages_any: ["en"]} and policy "text"
**When** the buyer updates only `policy: "new text"`
**Then** the scope fields remain unchanged (countries_all: ["US"], languages_any: ["en"])
**And** only the policy is updated
**Priority:** P1

#### Scenario: Update scope triggers conflict check
**Obligation ID** UC-012-EXT-C-03
**Layer** behavioral
**Given** standard A with scope {languages_any: ["en"], channels_any: ["display"]} and standard B with scope {languages_any: ["en"], channels_any: ["video"]}
**When** the buyer updates standard B's scope to {channels_any: ["display"]}
**Then** a scope conflict is detected with standard A -> Extension F
**Business Rule:** BR-4
**Priority:** P1

#### Scenario: Update non-existent standard triggers STANDARDS_NOT_FOUND
**Obligation ID** UC-012-EXT-C-04
**Layer** behavioral
**Given** no standard with id "cs_nonexistent"
**When** the buyer invokes `update_content_standards` with `standards_id: "cs_nonexistent"`
**Then** Extension E (STANDARDS_NOT_FOUND) is triggered
**Priority:** P1

#### Scenario: Update with languages_any validates minItems
**Obligation ID** UC-012-EXT-C-05
**Layer** schema
**Given** an existing standard
**When** the buyer updates with `scope: {languages_any: []}`
**Then** the request is rejected (minItems: 1)
**Priority:** P2

#### Scenario: Context echo on update
**Obligation ID** UC-012-EXT-C-06
**Layer** behavioral
**Given** an authenticated buyer with context
**When** update succeeds
**Then** context is echoed
**Business Rule:** BR-8
**Priority:** P2

### Extension D: Delete Content Standard

#### Scenario: Successful deletion of unreferenced standard
**Obligation ID** UC-012-EXT-D-01
**Layer** behavioral
**Given** content standard "cs_123" not referenced by any active media buy
**When** the buyer invokes `delete_content_standards` with `standards_id: "cs_123"`
**Then** the standard and all associated versions and calibration exemplars are deleted
**And** the response confirms deletion
**Priority:** P0

#### Scenario: Delete non-existent standard triggers STANDARDS_NOT_FOUND
**Obligation ID** UC-012-EXT-D-02
**Layer** behavioral
**Given** no standard with id "cs_nonexistent"
**When** the buyer invokes `delete_content_standards`
**Then** Extension E (STANDARDS_NOT_FOUND) is triggered
**Priority:** P1

#### Scenario: Delete referenced standard triggers STANDARDS_IN_USE
**Obligation ID** UC-012-EXT-D-03
**Layer** behavioral
**Given** content standard "cs_123" referenced by active media buy "mb_456"
**When** the buyer invokes `delete_content_standards` with `standards_id: "cs_123"`
**Then** Extension G (STANDARDS_IN_USE) is triggered
**And** the standard is NOT deleted
**Priority:** P0

#### Scenario: Context echo on delete
**Obligation ID** UC-012-EXT-D-04
**Layer** behavioral
**Given** an authenticated buyer with context
**When** delete succeeds
**Then** context is echoed
**Business Rule:** BR-8
**Priority:** P2

### Extension E: STANDARDS_NOT_FOUND

#### Scenario: Error response for missing standard
**Obligation ID** UC-012-EXT-E-01
**Layer** behavioral
**Given** a get/update/delete request with non-existent standards_id "cs_999"
**When** the lookup fails
**Then** the error response has code `STANDARDS_NOT_FOUND`
**And** the error message includes the standards_id "cs_999"
**And** system state is unchanged
**And** context is echoed when possible
**Business Rule:** POST-F1, POST-F2, POST-F3
**Priority:** P1

### Extension F: SCOPE_CONFLICT

#### Scenario: Create blocked by scope overlap
**Obligation ID** UC-012-EXT-F-01
**Layer** behavioral
**Given** existing standard A with scope {countries_all: ["US"], channels_any: ["display"], languages_any: ["en"]}
**When** the buyer creates a new standard with overlapping scope {countries_all: ["US"], channels_any: ["display"], languages_any: ["en"]}
**Then** the error response has code `SCOPE_CONFLICT`
**And** includes `conflicting_standards_id` identifying standard A
**And** system state is unchanged
**Business Rule:** BR-4, POST-F4
**Priority:** P1

#### Scenario: Update blocked by scope overlap
**Obligation ID** UC-012-EXT-F-02
**Layer** behavioral
**Given** existing standards A and B with non-overlapping scopes
**When** the buyer updates B's scope to overlap with A
**Then** SCOPE_CONFLICT is returned with conflicting_standards_id = A
**Business Rule:** BR-4
**Priority:** P1

#### Scenario: No conflict when scopes are disjoint
**Obligation ID** UC-012-EXT-F-03
**Layer** behavioral
**Given** existing standard A with {channels_any: ["display"]}
**When** creating a new standard with {channels_any: ["video"]}
**Then** no conflict is detected and creation succeeds
**Priority:** P1

### Extension G: STANDARDS_IN_USE

#### Scenario: Delete blocked by active media buy reference
**Obligation ID** UC-012-EXT-G-01
**Layer** behavioral
**Given** content standard "cs_123" referenced by active media buy
**When** the buyer attempts to delete "cs_123"
**Then** the error response has code `STANDARDS_IN_USE`
**And** the message indicates the standard cannot be deleted while in use
**And** the standard is NOT deleted
**Business Rule:** BR-6, POST-F1
**Priority:** P0

#### Scenario: Delete succeeds after media buy completes
**Obligation ID** UC-012-EXT-G-02
**Layer** behavioral
**Given** content standard "cs_123" previously referenced, but the media buy is now completed
**When** the buyer attempts to delete "cs_123"
**Then** the deletion succeeds (no active references)
**Priority:** P2

### Schema Compliance

#### Scenario: list-content-standards-response conforms to schema
**Obligation ID** UC-012-SCHEMA-01
**Layer** behavioral
**Given** any list response
**When** serialized to JSON
**Then** it validates against `list-content-standards-response.json`
**And** `standards` is a required array
**Priority:** P0

#### Scenario: create-content-standards-response conforms to schema
**Obligation ID** UC-012-SCHEMA-02
**Layer** behavioral
**Given** a successful create response
**When** serialized
**Then** it validates against `create-content-standards-response.json`
**And** includes `standards_id`
**Priority:** P0

#### Scenario: Content standard object structure
**Obligation ID** UC-012-SCHEMA-03
**Layer** schema
**Given** any content standard in a response
**When** inspected
**Then** it includes `standards_id`, scope fields (`countries_all`, `channels_any`, `languages_any`), and `policy`
**And** optional fields (`calibration_exemplars`, `name`, `description`) are correctly typed when present
**Priority:** P1

#### Scenario: Channels enum values match adcp 3.6
**Obligation ID** UC-012-SCHEMA-04
**Layer** schema
**Given** a content standard with channels_any
**When** serialized
**Then** each channel value is one of the 18 standardized channel types
**Priority:** P1
