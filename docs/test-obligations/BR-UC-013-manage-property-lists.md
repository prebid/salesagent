# UC-013: Manage Property Lists -- Test Obligations

## Source
- Requirements: `/Users/konst/projects/adcp-req/docs/requirements/use-cases/BR-UC-013-manage-property-lists/`
- Use Case ID: BR-UC-013
- Files: BR-UC-013.md, BR-UC-013-main-mcp.md, BR-UC-013-main-rest.md, BR-UC-013-ext-a.md, BR-UC-013-ext-b.md, BR-UC-013-ext-c.md, BR-UC-013-ext-d.md, BR-UC-013-ext-e.md, BR-UC-013-ext-f.md, BR-UC-013-ext-g.md
- Referenced Rules: BR-RULE-043, BR-RULE-070, BR-RULE-071, BR-RULE-072, BR-RULE-073, BR-RULE-074, BR-RULE-075, BR-RULE-076, BR-RULE-077, BR-RULE-078

## 3.6 Upgrade Impact
High impact. Property lists is a targeting/governance feature introduced in adcp 3.x. The salesagent currently reports `property_list_filtering=false` in capabilities, meaning this is largely a new implementation area. The 3.6 schemas define a full 5-operation CRUD (`create_property_list`, `get_property_list`, `list_property_lists`, `update_property_list`, `delete_property_list`). Key schema concerns: the `base-property-source.json` discriminated union (publisher_tags, publisher_ids, identifiers), the `property-list-filters.json` (countries_all AND logic, channels_any OR logic, property_types, feature_requirements), the `feature-requirement.json` model (feature_id, min/max_value, allowed_values, if_not_covered), and the `property-error.json` error codes (LIST_NOT_FOUND, LIST_ACCESS_DENIED, PROPERTY_NOT_FOUND). The one-time `auth_token` pattern at creation and the 10,000-item pagination on resolution are critical behavioral requirements. The adcp-client has full client adapter support.

## Test Scenarios

### Main Flow (MCP): list_property_lists

#### Scenario: List all property lists for a tenant
**Obligation ID** UC-013-MAIN-MCP-01
**Layer** behavioral
**Given** an authenticated buyer with 3 property lists defined for the tenant
**When** the buyer invokes `list_property_lists` MCP tool without filters
**Then** the response contains `lists` array with all 3 property list metadata objects
**And** each list includes `list_id`, `name`, and optional metadata
**And** resolved identifiers are NOT included (metadata only)
**Business Rule:** BR-1 (auth required), BR-2 (tenant scoped)
**Priority:** P0

#### Scenario: List with principal filter
**Obligation ID** UC-013-MAIN-MCP-02
**Layer** behavioral
**Given** property lists owned by principal A (2 lists) and principal B (1 list)
**When** the buyer invokes `list_property_lists` with `principal: "A"`
**Then** only the 2 lists owned by principal A are returned
**Business Rule:** BR-10
**Priority:** P1

#### Scenario: List with name_contains filter
**Obligation ID** UC-013-MAIN-MCP-03
**Layer** behavioral
**Given** property lists named "TV Campaign Include", "Radio Campaign Include", "TV Exclusion"
**When** the buyer invokes `list_property_lists` with `name_contains: "TV"`
**Then** "TV Campaign Include" and "TV Exclusion" are returned (substring match)
**Business Rule:** BR-10
**Priority:** P1

#### Scenario: List with pagination
**Obligation ID** UC-013-MAIN-MCP-04
**Layer** behavioral
**Given** 20 property lists
**When** the buyer invokes `list_property_lists` with `pagination: {max_results: 5}`
**Then** 5 lists are returned with pagination metadata (has_more, cursor)
**Priority:** P1

#### Scenario: Default pagination is 50, max is 100
**Obligation ID** UC-013-MAIN-MCP-05
**Layer** behavioral
**Given** 60 property lists
**When** the buyer invokes without pagination params
**Then** 50 lists are returned with has_more: true
**Priority:** P2

#### Scenario: Empty list returns empty array
**Obligation ID** UC-013-MAIN-MCP-06
**Layer** behavioral
**Given** no property lists for the tenant
**When** the buyer invokes `list_property_lists`
**Then** the response contains `lists: []` (empty array, not an error)
**Priority:** P1

#### Scenario: Authentication is required
**Obligation ID** UC-013-MAIN-MCP-07
**Layer** behavioral
**Given** no authentication token
**When** the buyer invokes `list_property_lists`
**Then** the request is rejected with AUTH error
**Business Rule:** BR-1
**Priority:** P0

#### Scenario: Context echo in list response
**Obligation ID** UC-013-MAIN-MCP-08
**Layer** behavioral
**Given** an authenticated buyer with context
**When** the list response is returned
**Then** context is echoed unchanged
**Business Rule:** BR-11
**Priority:** P1

### Main Flow (REST/A2A): list_property_lists via A2A

#### Scenario: A2A list returns same structure
**Obligation ID** UC-013-MAIN-REST-01
**Layer** behavioral
**Given** the same tenant data
**When** the buyer sends `list_property_lists` via A2A
**Then** the response is identical to the MCP path
**Priority:** P1

### Extension A: Create Property List

#### Scenario: Successful creation with name only
**Obligation ID** UC-013-EXT-A-01
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer invokes `create_property_list` with `name: "My Include List"`
**Then** the response contains a `list` object with generated `list_id`
**And** the response contains a one-time `auth_token`
**And** `base_properties` and `filters` are null or absent
**Business Rule:** BR-3 (name required), BR-6 (auth_token one-shot)
**Priority:** P0

#### Scenario: auth_token is returned only at creation
**Obligation ID** UC-013-EXT-A-02
**Layer** behavioral
**Given** a newly created property list with list_id "pl_123"
**When** the buyer subsequently calls `get_property_list` with `list_id: "pl_123"`
**Then** the get response does NOT include the auth_token
**And** the auth_token is a one-time secret
**Business Rule:** BR-6, POST-S7
**Priority:** P0

#### Scenario: Creation with base_properties -- publisher_tags
**Obligation ID** UC-013-EXT-A-03
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer creates with `base_properties: {publisher_tags: ["premium", "news"]}`
**Then** the property list is created with publisher_tags source type
**Business Rule:** BR-4 (discriminated union)
**Priority:** P1

#### Scenario: Creation with base_properties -- publisher_ids
**Obligation ID** UC-013-EXT-A-04
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer creates with `base_properties: {publisher_ids: ["pub_001", "pub_002"]}`
**Then** the property list is created with publisher_ids source type
**Business Rule:** BR-4
**Priority:** P1

#### Scenario: Creation with base_properties -- identifiers
**Obligation ID** UC-013-EXT-A-05
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer creates with `base_properties: {identifiers: ["cnn.com", "bbc.com"]}`
**Then** the property list is created with direct identifiers source type
**Business Rule:** BR-4
**Priority:** P1

#### Scenario: Creation with filters
**Obligation ID** UC-013-EXT-A-06
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer creates with `filters: {countries_all: ["US", "UK"], channels_any: ["display", "video"]}`
**Then** the property list is created with dynamic filters
**Business Rule:** BR-5 (filters require both countries_all and channels_any)
**Priority:** P1

#### Scenario: Filters require both countries_all and channels_any
**Obligation ID** UC-013-EXT-A-07
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer creates with `filters: {countries_all: ["US"]}` (missing channels_any)
**Then** the request is rejected
**Business Rule:** BR-5
**Priority:** P1

#### Scenario: Filters countries_all and channels_any need minItems: 1
**Obligation ID** UC-013-EXT-A-08
**Layer** schema
**Given** an authenticated buyer
**When** the buyer provides `filters: {countries_all: [], channels_any: ["display"]}`
**Then** the request is rejected (countries_all minItems: 1)
**Priority:** P2

#### Scenario: Creation with brand reference
**Obligation ID** UC-013-EXT-A-09
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer creates with `brand: {house: "example.com", brand_id: "main"}`
**Then** the property list includes the brand reference
**Priority:** P2

#### Scenario: Name is required
**Obligation ID** UC-013-EXT-A-10
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer invokes `create_property_list` without `name`
**Then** the request is rejected
**Business Rule:** BR-3
**Priority:** P0

#### Scenario: Context echo on create
**Obligation ID** UC-013-EXT-A-11
**Layer** behavioral
**Given** an authenticated buyer with context
**When** create succeeds
**Then** context is echoed
**Business Rule:** BR-11
**Priority:** P1

### Extension B: Get Property List

#### Scenario: Get with resolution (default behavior)
**Obligation ID** UC-013-EXT-B-01
**Layer** schema
**Given** a property list "pl_123" with base_properties and filters
**When** the buyer invokes `get_property_list` with `list_id: "pl_123"` (resolve defaults to true)
**Then** the response contains `list` metadata
**And** `identifiers` array with resolved matching properties
**And** `resolved_at` timestamp and `cache_valid_until` timestamp
**And** pagination metadata if applicable
**Business Rule:** BR-9
**Priority:** P0

#### Scenario: Get without resolution
**Obligation ID** UC-013-EXT-B-02
**Layer** behavioral
**Given** a property list "pl_123"
**When** the buyer invokes `get_property_list` with `list_id: "pl_123"` and `resolve: false`
**Then** the response contains `list` metadata only
**And** `identifiers` is absent or null
**Business Rule:** BR-9
**Priority:** P1

#### Scenario: Resolution pagination -- default 1000, max 10000
**Obligation ID** UC-013-EXT-B-03
**Layer** behavioral
**Given** a property list resolving to 5000 identifiers
**When** the buyer invokes with `resolve: true` and default pagination
**Then** the first 1000 identifiers are returned with has_more: true and a cursor
**Business Rule:** BR-9
**Priority:** P1

#### Scenario: Resolution pagination -- cursor continuation
**Obligation ID** UC-013-EXT-B-04
**Layer** behavioral
**Given** a large resolved list and a cursor from the first page
**When** the buyer invokes with the cursor
**Then** the next page of identifiers is returned
**Priority:** P1

#### Scenario: Resolution max page size is 10,000
**Obligation ID** UC-013-EXT-B-05
**Layer** behavioral
**Given** a property list
**When** the buyer requests `pagination: {max_results: 10000}`
**Then** the request is accepted (10,000 is the max)
**When** the buyer requests `pagination: {max_results: 10001}`
**Then** the request is rejected
**Priority:** P2

#### Scenario: Get non-existent list triggers LIST_NOT_FOUND
**Obligation ID** UC-013-EXT-B-06
**Layer** behavioral
**Given** no list with id "pl_nonexistent"
**When** the buyer invokes `get_property_list`
**Then** Extension E (LIST_NOT_FOUND) is triggered
**Priority:** P1

#### Scenario: Get list from different tenant fails (tenant isolation)
**Obligation ID** UC-013-EXT-B-07
**Layer** behavioral
**Given** a property list belonging to tenant A
**When** a buyer authenticated against tenant B requests the same list_id
**Then** LIST_NOT_FOUND is returned
**Business Rule:** BR-2
**Priority:** P1

#### Scenario: Access control check
**Obligation ID** UC-013-EXT-B-08
**Layer** behavioral
**Given** a property list owned by principal A
**When** principal B attempts to get the list
**Then** Extension F (LIST_ACCESS_DENIED) is triggered
**Priority:** P1

#### Scenario: Coverage gaps reported when if_not_covered=include
**Obligation ID** UC-013-EXT-B-09
**Layer** schema
**Given** a property list with feature_requirements using if_not_covered: "include"
**When** resolution is performed and some properties lack the required feature
**Then** the response includes `coverage_gaps` indicating properties without full feature coverage
**Priority:** P2

#### Scenario: Context echo on get
**Obligation ID** UC-013-EXT-B-10
**Layer** behavioral
**Given** an authenticated buyer with context
**When** get succeeds
**Then** context is echoed
**Business Rule:** BR-11
**Priority:** P2

### Extension C: Update Property List

#### Scenario: Update name (full replacement)
**Obligation ID** UC-013-EXT-C-01
**Layer** behavioral
**Given** a property list "pl_123" with name "Old Name"
**When** the buyer invokes `update_property_list` with `list_id: "pl_123"` and `name: "New Name"`
**Then** the list name is replaced with "New Name"
**And** the response contains the updated list object
**Business Rule:** BR-7 (full replacement semantics)
**Priority:** P0

#### Scenario: Update base_properties replaces entirely
**Obligation ID** UC-013-EXT-C-02
**Layer** behavioral
**Given** a property list with `base_properties: {publisher_tags: ["premium"]}`
**When** the buyer updates with `base_properties: {publisher_ids: ["pub_001"]}`
**Then** the old publisher_tags source is replaced entirely with publisher_ids
**Business Rule:** BR-7
**Priority:** P1

#### Scenario: Fields not provided remain unchanged
**Obligation ID** UC-013-EXT-C-03
**Layer** behavioral
**Given** a property list with name "My List" and description "Description"
**When** the buyer updates only `name: "Updated List"`
**Then** description remains "Description"
**Priority:** P1

#### Scenario: webhook_url is settable via update (not create)
**Obligation ID** UC-013-EXT-C-04
**Layer** behavioral
**Given** a property list without webhook_url
**When** the buyer updates with `webhook_url: "https://example.com/hook"`
**Then** the webhook_url is set
**Business Rule:** BR-12
**Priority:** P2

#### Scenario: Empty string removes webhook_url
**Obligation ID** UC-013-EXT-C-05
**Layer** behavioral
**Given** a property list with webhook_url "https://example.com/hook"
**When** the buyer updates with `webhook_url: ""`
**Then** the webhook_url is removed
**Priority:** P2

#### Scenario: Update validates filters structure
**Obligation ID** UC-013-EXT-C-06
**Layer** behavioral
**Given** a property list
**When** the buyer updates with `filters: {countries_all: ["US"]}` (missing channels_any)
**Then** the request is rejected
**Business Rule:** BR-5
**Priority:** P1

#### Scenario: Update non-existent list triggers LIST_NOT_FOUND
**Obligation ID** UC-013-EXT-C-07
**Layer** behavioral
**Given** no list with id "pl_nonexistent"
**When** the buyer invokes `update_property_list`
**Then** Extension E (LIST_NOT_FOUND) is triggered
**Priority:** P1

#### Scenario: Update access control check
**Obligation ID** UC-013-EXT-C-08
**Layer** behavioral
**Given** a property list owned by principal A
**When** principal B attempts to update
**Then** Extension F (LIST_ACCESS_DENIED) is triggered
**Priority:** P1

#### Scenario: Update triggers webhook notification
**Obligation ID** UC-013-EXT-C-09
**Layer** behavioral
**Given** a property list with a configured webhook_url
**When** the buyer updates the list
**Then** a `property_list_changed` webhook notification is sent to the webhook URL
**Priority:** P2

#### Scenario: Context echo on update
**Obligation ID** UC-013-EXT-C-10
**Layer** behavioral
**Given** an authenticated buyer with context
**When** update succeeds
**Then** context is echoed
**Business Rule:** BR-11
**Priority:** P2

### Extension D: Delete Property List

#### Scenario: Successful deletion of unreferenced list
**Obligation ID** UC-013-EXT-D-01
**Layer** behavioral
**Given** property list "pl_123" not referenced by any active media buy
**When** the buyer invokes `delete_property_list` with `list_id: "pl_123"`
**Then** the response contains `deleted: true` and `list_id: "pl_123"`
**And** the list and all associated data are removed
**Priority:** P0

#### Scenario: Delete non-existent list triggers LIST_NOT_FOUND
**Obligation ID** UC-013-EXT-D-02
**Layer** behavioral
**Given** no list with id "pl_nonexistent"
**When** the buyer invokes `delete_property_list`
**Then** Extension E (LIST_NOT_FOUND) is triggered
**Priority:** P1

#### Scenario: Delete access control check
**Obligation ID** UC-013-EXT-D-03
**Layer** behavioral
**Given** a property list owned by principal A
**When** principal B attempts to delete
**Then** Extension F (LIST_ACCESS_DENIED) is triggered
**Priority:** P1

#### Scenario: Delete referenced list triggers LIST_IN_USE
**Obligation ID** UC-013-EXT-D-04
**Layer** behavioral
**Given** property list "pl_123" referenced by active media buy targeting
**When** the buyer invokes `delete_property_list` with `list_id: "pl_123"`
**Then** Extension G (LIST_IN_USE) is triggered
**And** the list is NOT deleted
**Business Rule:** BR-8
**Priority:** P0

#### Scenario: Context echo on delete
**Obligation ID** UC-013-EXT-D-05
**Layer** behavioral
**Given** an authenticated buyer with context
**When** delete succeeds
**Then** context is echoed
**Business Rule:** BR-11
**Priority:** P2

### Extension E: LIST_NOT_FOUND

#### Scenario: Error response includes list_id
**Obligation ID** UC-013-EXT-E-01
**Layer** behavioral
**Given** a get/update/delete request with non-existent list_id "pl_999"
**When** the lookup fails
**Then** the error response has code `LIST_NOT_FOUND`
**And** the error message includes "pl_999"
**And** the error includes a suggestion for recovery
**And** system state is unchanged
**And** context is echoed when possible
**Business Rule:** POST-F1, POST-F2, POST-F3, POST-F4
**Priority:** P1

### Extension F: LIST_ACCESS_DENIED

#### Scenario: Denied access returns error
**Obligation ID** UC-013-EXT-F-01
**Layer** behavioral
**Given** a property list existing but not accessible to the requesting principal
**When** the principal attempts get/update/delete
**Then** the error response has code `LIST_ACCESS_DENIED`
**And** includes a recovery suggestion
**And** system state is unchanged
**And** context is echoed when possible
**Business Rule:** POST-F1, POST-F2, POST-F3
**Priority:** P1

#### Scenario: LIST_ACCESS_DENIED vs LIST_NOT_FOUND
**Obligation ID** UC-013-EXT-F-02
**Layer** behavioral
**Given** a property list that exists but is not accessible
**When** the error is returned
**Then** the error is ACCESS_DENIED (not NOT_FOUND) -- the system distinguishes between missing and forbidden
**Priority:** P2

### Extension G: LIST_IN_USE

#### Scenario: Delete blocked by active media buy
**Obligation ID** UC-013-EXT-G-01
**Layer** behavioral
**Given** property list "pl_123" referenced in an active media buy's property_list targeting field
**When** the buyer attempts to delete "pl_123"
**Then** the error response has code `LIST_IN_USE`
**And** the message indicates active media buy references prevent deletion
**And** includes suggestion (update media buys first, or wait for campaigns to complete)
**And** the list is NOT deleted
**Business Rule:** BR-8, POST-F1
**Priority:** P0

#### Scenario: Delete succeeds after media buy targeting is removed
**Obligation ID** UC-013-EXT-G-02
**Layer** behavioral
**Given** property list "pl_123" previously referenced, but the media buy targeting has been updated to remove the reference
**When** the buyer attempts to delete "pl_123"
**Then** the deletion succeeds
**Priority:** P2

### Schema Compliance

#### Scenario: list-property-lists-response conforms to schema
**Obligation ID** UC-013-SCHEMA-01
**Layer** behavioral
**Given** any list response
**When** serialized to JSON
**Then** it validates against `list-property-lists-response.json`
**And** `lists` is a required array
**Priority:** P0

#### Scenario: create-property-list-response conforms to schema
**Obligation ID** UC-013-SCHEMA-02
**Layer** behavioral
**Given** a successful create response
**When** serialized
**Then** it validates against `create-property-list-response.json`
**And** includes `list` object and `auth_token`
**Priority:** P0

#### Scenario: get-property-list-response conforms to schema
**Obligation ID** UC-013-SCHEMA-03
**Layer** behavioral
**Given** a successful get response
**When** serialized
**Then** it validates against `get-property-list-response.json`
**And** includes `list` metadata and optional resolved fields
**Priority:** P0

#### Scenario: update-property-list-response conforms to schema
**Obligation ID** UC-013-SCHEMA-04
**Layer** behavioral
**Given** a successful update response
**When** serialized
**Then** it validates against `update-property-list-response.json`
**And** includes updated `list` object
**Priority:** P0

#### Scenario: delete-property-list-response conforms to schema
**Obligation ID** UC-013-SCHEMA-05
**Layer** behavioral
**Given** a successful delete response
**When** serialized
**Then** it validates against `delete-property-list-response.json`
**And** includes `deleted: true` and `list_id`
**Priority:** P0

#### Scenario: base-property-source discriminated union
**Obligation ID** UC-013-SCHEMA-06
**Layer** schema
**Given** a property list with base_properties
**When** serialized
**Then** the source matches exactly one of: publisher_tags, publisher_ids, or identifiers
**Priority:** P1

#### Scenario: property-list-filters schema compliance
**Obligation ID** UC-013-SCHEMA-07
**Layer** behavioral
**Given** a property list with filters
**When** serialized
**Then** the filters conform to `property-list-filters.json`
**And** `countries_all` uses AND logic and `channels_any` uses OR logic
**Priority:** P1

#### Scenario: Error codes from property-error.json
**Obligation ID** UC-013-SCHEMA-08
**Layer** behavioral
**Given** any error response from property list operations
**When** the error code is inspected
**Then** it is one of: LIST_NOT_FOUND, LIST_ACCESS_DENIED, PROPERTY_NOT_FOUND, LIST_IN_USE
**Priority:** P1
