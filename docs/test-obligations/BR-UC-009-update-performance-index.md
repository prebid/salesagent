# UC-009: Update Performance Index -- Test Obligations

## Source
- Requirements: `/Users/konst/projects/adcp-req/docs/requirements/use-cases/BR-UC-009-update-performance-index/`
- Use Case ID: BR-UC-009
- Files: BR-UC-009.md, BR-UC-009-main-mcp.md, BR-UC-009-main-rest.md, BR-UC-009-ext-a.md, BR-UC-009-ext-b.md, BR-UC-009-ext-c.md, BR-UC-009-ext-d.md
- Referenced Rules: BR-RULE-018, BR-RULE-043, BR-RULE-051

## 3.6 Upgrade Impact
Medium impact. The `provide-performance-feedback-request.json` and `provide-performance-feedback-response.json` schemas are key. The salesagent implementation diverges from the protocol: it uses `UpdatePerformanceIndexRequest` (batch `performance_data` list) rather than the protocol's single-metric-per-invocation pattern. If adcp 3.6 changed the `metric-type.json` enum, `feedback-source.json` enum, or the `performance-feedback.json` core entity, the Pydantic models must be updated. The `measurement_period` with ISO 8601 timestamps and `performance_index` >= 0 normalization should be verified against 3.6 constraints.

## Test Scenarios

### Main Flow (MCP): update_performance_index via MCP tool

#### Scenario: Successful performance index update
**Obligation ID** UC-009-MAIN-MCP-01
**Layer** behavioral
**Given** an authenticated buyer who owns media buy "gam_123" with 2 products
**When** the buyer agent calls `update_performance_index` with `media_buy_id: "gam_123"` and `performance_data: [{product_id: "prod_1", performance_index: 1.35, confidence_score: 0.9}, {product_id: "prod_2", performance_index: 0.8, confidence_score: 0.7}]`
**Then** the adapter receives `PackagePerformance` objects with package_id mapped from product_id
**And** the response has `status: "success"` and a human-readable `detail` message
**And** the response is wrapped in a ToolResult with content and structured_content
**Business Rule:** BR-7 (context echo)
**Priority:** P0

#### Scenario: ProductPerformance to PackagePerformance conversion
**Obligation ID** UC-009-MAIN-MCP-02
**Layer** behavioral
**Given** performance_data with product_id fields
**When** the data is processed
**Then** each `product_id` is mapped to `package_id` in the adapter call
**Priority:** P1

#### Scenario: Audit log records operation details
**Obligation ID** UC-009-MAIN-MCP-03
**Layer** behavioral
**Given** a successful performance update for media_buy_id "gam_123" with 2 products averaging index 1.075
**When** the operation completes
**Then** the audit log contains media_buy_id, product count (2), and average performance index (1.075)
**Priority:** P2

#### Scenario: Context echo in success response
**Obligation ID** UC-009-MAIN-MCP-04
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer calls `update_performance_index` with `context: {"campaign_id": "c1"}`
**Then** the response echoes `context: {"campaign_id": "c1"}`
**Business Rule:** BR-7
**Priority:** P1

#### Scenario: Default metric_type is overall_performance
**Obligation ID** UC-009-MAIN-MCP-05
**Layer** schema
**Given** a request without explicit `metric_type`
**When** the system processes the request
**Then** the metric_type defaults to `overall_performance`
**Business Rule:** BR-8
**Priority:** P2

#### Scenario: Default feedback_source is buyer_attribution
**Obligation ID** UC-009-MAIN-MCP-06
**Layer** schema
**Given** a request without explicit `feedback_source`
**When** the system processes the request
**Then** the feedback_source defaults to `buyer_attribution`
**Business Rule:** BR-9
**Priority:** P2

### Main Flow (REST/A2A): update_performance_index via A2A

#### Scenario: Successful A2A performance update
**Obligation ID** UC-009-MAIN-REST-01
**Layer** behavioral
**Given** an authenticated buyer via A2A with a valid media buy
**When** the buyer sends `update_performance_index` A2A skill request
**Then** the handler validates against `UpdatePerformanceIndexRequest`
**And** creates `ToolContext` from A2A auth token
**And** delegates to the shared `_update_performance_index_impl`
**And** returns the response object directly (no ToolResult wrapper)
**Priority:** P1

#### Scenario: A2A validation failure returns structured error
**Obligation ID** UC-009-MAIN-REST-02
**Layer** behavioral
**Given** an A2A request with missing required parameters
**When** `UpdatePerformanceIndexRequest.model_validate` fails
**Then** the response contains `success: False`, `message`, `required_parameters`, and `received_parameters`
**Priority:** P1

### Extension A: Media Buy Not Found

#### Scenario: Non-existent media_buy_id
**Obligation ID** UC-009-EXT-A-01
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer calls `update_performance_index` with `media_buy_id: "nonexistent_999"`
**Then** the system raises a ToolError indicating the media buy cannot be found
**And** no performance data is written
**Business Rule:** POST-F1
**Priority:** P0

#### Scenario: Media buy not found preserves context echo
**Obligation ID** UC-009-EXT-A-02
**Layer** behavioral
**Given** an authenticated buyer with context
**When** the media buy is not found
**Then** the error response echoes the context if possible
**Business Rule:** POST-F3
**Priority:** P2

### Extension B: Validation Error

#### Scenario: Missing media_buy_id
**Obligation ID** UC-009-EXT-B-01
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer calls `update_performance_index` without `media_buy_id`
**Then** a validation error is raised with specific field failure details
**And** no performance data is written
**Priority:** P0

#### Scenario: Invalid performance_index (non-numeric)
**Obligation ID** UC-009-EXT-B-02
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer provides `performance_data: [{product_id: "p1", performance_index: "not_a_number"}]`
**Then** a ValidationError is raised when constructing ProductPerformance
**And** the formatted error includes specific field failures
**Priority:** P1

#### Scenario: Negative performance_index
**Obligation ID** UC-009-EXT-B-03
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer provides `performance_data: [{product_id: "p1", performance_index: -0.5}]`
**Then** the request is rejected (performance_index must be >= 0)
**Business Rule:** BR-3
**Priority:** P1

#### Scenario: Empty performance_data
**Obligation ID** UC-009-EXT-B-04
**Layer** behavioral
**Given** an authenticated buyer
**When** the buyer provides `performance_data: []`
**Then** the request is rejected
**Priority:** P1

#### Scenario: A2A-specific validation returns required_parameters hint
**Obligation ID** UC-009-EXT-B-05
**Layer** behavioral
**Given** an A2A request with invalid parameters
**When** model_validate fails
**Then** the error dict includes `required_parameters` and `received_parameters`
**Priority:** P2

### Extension C: Principal Ownership Failure

#### Scenario: No authentication context (ctx is None)
**Obligation ID** UC-009-EXT-C-01
**Layer** behavioral
**Given** no authentication context (ctx is None)
**When** the buyer calls `update_performance_index`
**Then** a ValueError is raised: "Context is required for update_performance_index"
**Business Rule:** BR-4
**Priority:** P0

#### Scenario: Principal does not own the media buy
**Obligation ID** UC-009-EXT-C-02
**Layer** behavioral
**Given** buyer "alice" is authenticated but media buy "gam_123" is owned by "bob"
**When** alice calls `update_performance_index` with `media_buy_id: "gam_123"`
**Then** a ToolError is raised (ownership verification fails)
**And** no performance data is written
**Business Rule:** BR-5
**Priority:** P0

#### Scenario: Principal object not found in database
**Obligation ID** UC-009-EXT-C-03
**Layer** behavioral
**Given** a valid authentication token with principal_id "p_999" but no corresponding Principal record in DB
**When** the buyer calls `update_performance_index`
**Then** a ToolError is raised: "Principal p_999 not found"
**And** no performance data is written
**Priority:** P1

### Extension D: Adapter Processing Failure

#### Scenario: Adapter returns False (failure)
**Obligation ID** UC-009-EXT-D-01
**Layer** behavioral
**Given** an authenticated buyer owning the media buy, but the adapter returns False
**When** the adapter processes the performance update
**Then** the response has `status: "failed"`
**And** the audit log records success=False
**And** no partial performance updates are applied
**Business Rule:** POST-F1
**Priority:** P1

#### Scenario: A2A adapter exception raises ServerError
**Obligation ID** UC-009-EXT-D-02
**Layer** behavioral
**Given** an A2A request where the adapter throws an exception
**When** the shared implementation propagates the exception
**Then** the A2A handler catches it and raises ServerError with "Unable to update performance index"
**Priority:** P1

#### Scenario: Context echoed on adapter failure
**Obligation ID** UC-009-EXT-D-03
**Layer** behavioral
**Given** a request with context that reaches the adapter but fails
**When** the adapter returns False
**Then** the response still includes the echoed context
**Business Rule:** BR-7, POST-F3
**Priority:** P2

### Schema Compliance

#### Scenario: Response is atomic -- success XOR errors
**Obligation ID** UC-009-SCHEMA-01
**Layer** behavioral
**Given** any performance update response
**When** the response is constructed
**Then** the response contains EITHER success fields OR error fields, never both
**Business Rule:** BR-6
**Priority:** P0

#### Scenario: performance_index normalization scale
**Obligation ID** UC-009-SCHEMA-02
**Layer** schema
**Given** various performance_index values
**When** validated
**Then** 0.0 = no value, 1.0 = expected, > 1.0 = above expected, and all values >= 0 are accepted
**Business Rule:** BR-3
**Priority:** P1

#### Scenario: measurement_period has valid start < end
**Obligation ID** UC-009-SCHEMA-03
**Layer** schema
**Given** a performance feedback request
**When** the measurement_period is validated
**Then** `start` and `end` are ISO 8601 timestamps and `start` < `end`
**Business Rule:** BR-2
**Priority:** P1
