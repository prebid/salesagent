# UC-001: Discover Available Inventory -- Test Obligations

## Source
- Requirements: `/Users/konst/projects/adcp-req/docs/requirements/use-cases/UC-001-discover-available-inventory/`
- Use Case ID: BR-UC-001
- Business Rules: BR-RULE-001 through BR-RULE-007

## 3.6 Upgrade Impact

### salesagent-qo8a (FIXED): 6 Product fields missing from DB
The Product schema in adcp 3.6.0 added 6 fields that were not persisted in the salesagent database:
- `catalog_match` -- catalog matching configuration
- `catalog_types` -- catalog type classifications
- `conversion_tracking` -- conversion tracking configuration
- `data_provider_signals` -- data provider signal definitions
- `forecast` -- delivery forecast data
- `signal_targeting_allowed` -- whether signal targeting is allowed

These fields are now present in the DB model (`src/core/database/models.py`) and populated during product conversion (`src/core/product_conversion.py`). Every scenario that returns products must verify these fields are present in the response when set on the product.

### salesagent-goy2: Creative extends wrong adcp type
Not directly impacting UC-001 (discovery), but if proposal allocations reference creative formats, the wrong base type could strip fields. Indirect risk during proposal generation.

### salesagent-mq3n: PricingOption delivery lookup string vs integer PK
Affects UC-001 at step 17 (adapter support annotation) and any filter that relies on pricing option lookup. The comparison of string pricing model name against integer PK will cause lookup failures, potentially marking all pricing options as unsupported.

### salesagent-7gnv: MediaBuy boundary drops fields
Not directly impacting UC-001 (discovery is read-only), but proposals generated in UC-001 feed into UC-002 (create media buy). If buyer_campaign_ref, creative_deadline, or ext are lost at the boundary, proposals that reference these will silently fail downstream.

### Filter Fields Impact
The new `signal_targeting` filter requires `signal_targeting_allowed` and `data_provider_signals` to be present on products. The `forecast` field is needed for `min_exposures` filter evaluation on guaranteed products. Without the qo8a fix, these filters would silently return empty results.

---

## Test Scenarios

### Preconditions

#### Scenario: System operational check (PRE-C1)
**Obligation ID** UC-001-PRECOND-01
**Layer** behavioral
**Given** the Seller Agent is deployed and running
**When** a health check is performed
**Then** the system responds with a healthy status
**Priority:** P0

#### Scenario: Product catalog exists (PRE-C2)
**Obligation ID** UC-001-PRECOND-02
**Layer** behavioral
**Given** a tenant is configured in the system
**When** at least one product is defined for the tenant
**Then** the product catalog is available for discovery
**Priority:** P0

#### Scenario: MCP connection established (PRE-MCP1)
**Obligation ID** UC-001-PRECOND-03
**Layer** behavioral
**Given** a Buyer Agent has valid MCP connection parameters
**When** the Buyer Agent connects to the Seller Agent MCP endpoint
**Then** the MCP connection is established successfully
**Priority:** P0

#### Scenario: Product selectors require brand (PRE-BIZ4)
**Obligation ID** UC-001-PRECOND-04
**Layer** behavioral
**Given** a Buyer Agent sends a get_products request with `product_selectors` but no `brand`
**When** the system validates the request
**Then** the request is rejected with a validation error indicating brand is required when product_selectors is provided
**Priority:** P1

#### Scenario: Account ID required by seller capabilities (PRE-BIZ5)
**Obligation ID** UC-001-PRECOND-05
**Layer** behavioral
**Given** a tenant requires `account_id` in its seller capabilities
**When** a Buyer Agent sends a get_products request without `account_id`
**Then** the request is rejected with a validation error indicating account_id is required
**Priority:** P2

---

### Main Flow: Authenticated Discovery with Brief (UC-001-main-mcp)

#### Scenario: Full pipeline happy path -- authenticated buyer with brief returns ranked products
**Obligation ID** UC-001-MAIN-01
**Layer** behavioral
**Given** a Buyer Agent has valid authentication credentials
**And** the tenant has products defined in its catalog
**And** the tenant has a brand_manifest_policy of `require_auth`
**When** the Buyer Agent sends a `get_products` request with `brief`, `brand`, and authentication
**Then** the system returns a list of products ranked by brief relevance
**And** each product includes `pricing_options`, `format_ids`, `publisher_properties`, and `delivery_type`
**And** the response is wrapped in a protocol envelope with `status: completed`
**Business Rule:** BR-RULE-001 (INV-1 satisfied), BR-RULE-007 (INV-1)
**Priority:** P0

#### Scenario: Authentication extracts principal_id (Step 2)
**Obligation ID** UC-001-MAIN-02
**Layer** behavioral
**Given** a Buyer Agent sends a request with valid authentication credentials
**When** the system processes the request
**Then** the `principal_id` is extracted from the authentication context
**And** the principal_id is used for access control in subsequent steps
**Priority:** P0

#### Scenario: Brand manifest extraction -- name-based offering text (Step 3)
**Obligation ID** UC-001-MAIN-03
**Layer** schema
**Given** a request includes a `brand` reference with a `name` field
**When** the system extracts the brand manifest
**Then** the offering text is derived from the brand `name`
**Priority:** P1

#### Scenario: Brand manifest extraction -- URL-based offering text (Step 3)
**Obligation ID** UC-001-MAIN-04
**Layer** schema
**Given** a request includes a `brand` reference with a `url` field but no `name`
**When** the system extracts the brand manifest
**Then** the offering text is derived from the brand `url`
**Priority:** P1

#### Scenario: Brand manifest policy satisfied -- require_auth with authenticated buyer (Step 4)
**Obligation ID** UC-001-MAIN-05
**Layer** behavioral
**Given** the tenant's brand_manifest_policy is `require_auth`
**And** the request includes valid authentication
**When** the system evaluates brand manifest policy
**Then** the policy is satisfied and the pipeline continues
**Business Rule:** BR-RULE-001 (INV-1 not triggered)
**Priority:** P0

#### Scenario: Brief compliance check passes (Step 5)
**Obligation ID** UC-001-MAIN-06
**Layer** behavioral
**Given** the tenant has an `advertising_policy` configured and enabled
**And** the buyer's brief is compliant with the policy
**When** the system checks the brief against the policy via LLM
**Then** the LLM returns `ALLOWED`
**And** the pipeline continues with the full product catalog
**Business Rule:** BR-RULE-002 (INV-5)
**Priority:** P0

#### Scenario: Product selectors -- catalog matching with GTINs (Step 6)
**Obligation ID** UC-001-MAIN-07
**Layer** behavioral
**Given** a request includes `product_selectors` with GTIN identifiers
**And** a `brand` manifest is provided with a product catalog
**When** the system resolves catalog matching
**Then** only products matching the specified GTINs are eligible
**And** results are constrained by the catalog match (UNION logic across selector types)
**Priority:** P1

#### Scenario: Product selectors -- catalog matching with SKUs (Step 6)
**Obligation ID** UC-001-MAIN-08
**Layer** behavioral
**Given** a request includes `product_selectors` with SKU identifiers
**And** a `brand` manifest is provided
**When** the system resolves catalog matching
**Then** only products matching the specified SKUs are eligible
**Priority:** P1

#### Scenario: Product selectors -- catalog matching with tags (Step 6)
**Obligation ID** UC-001-MAIN-09
**Layer** behavioral
**Given** a request includes `product_selectors` with tag-based selectors
**And** a `brand` manifest is provided
**When** the system resolves catalog matching
**Then** only products matching the specified tags are eligible
**Priority:** P2

#### Scenario: Product selectors -- catalog matching with categories (Step 6)
**Obligation ID** UC-001-MAIN-10
**Layer** behavioral
**Given** a request includes `product_selectors` with category selectors
**And** a `brand` manifest is provided
**When** the system resolves catalog matching
**Then** only products matching the specified categories are eligible
**Priority:** P2

#### Scenario: Product selectors -- catalog matching with query (Step 6)
**Obligation ID** UC-001-MAIN-11
**Layer** behavioral
**Given** a request includes `product_selectors` with a free-text query
**And** a `brand` manifest is provided
**When** the system resolves catalog matching
**Then** only products matching the query are eligible
**Priority:** P2

#### Scenario: Product selectors -- UNION logic across selector types (Step 6)
**Obligation ID** UC-001-MAIN-12
**Layer** behavioral
**Given** a request includes `product_selectors` with both GTIN and tag selectors
**When** the system resolves catalog matching
**Then** products matching EITHER GTINs OR tags are eligible (UNION, not intersection)
**Priority:** P1

#### Scenario: Product catalog retrieval (Step 7)
**Obligation ID** UC-001-MAIN-13
**Layer** behavioral
**Given** a tenant has products in the database
**When** the system retrieves the product catalog
**Then** all products for the tenant are loaded from the database
**Priority:** P0

#### Scenario: Product conversion to AdCP schema -- valid product (Step 8)
**Obligation ID** UC-001-MAIN-14
**Layer** schema
**Given** a product in the database has >= 1 format_id, >= 1 publisher_property, >= 1 pricing_option
**When** the system converts the product to AdCP schema
**Then** the conversion succeeds
**And** the product includes all required AdCP fields
**Business Rule:** BR-RULE-007 (INV-1)
**Priority:** P0

#### Scenario: Product conversion to AdCP schema -- missing format_ids (Step 8)
**Obligation ID** UC-001-MAIN-15
**Layer** schema
**Given** a product in the database has 0 format_ids
**When** the system converts the product to AdCP schema
**Then** the conversion fails with a ValueError
**And** the entire request fails (data corruption)
**Business Rule:** BR-RULE-007 (INV-2)
**Priority:** P0

#### Scenario: Product conversion to AdCP schema -- missing publisher_properties (Step 8)
**Obligation ID** UC-001-MAIN-16
**Layer** schema
**Given** a product in the database has 0 publisher_properties
**When** the system converts the product to AdCP schema
**Then** the conversion fails with a ValueError
**Business Rule:** BR-RULE-007 (INV-3)
**Priority:** P0

#### Scenario: Product conversion to AdCP schema -- missing pricing_options (Step 8)
**Obligation ID** UC-001-MAIN-17
**Layer** schema
**Given** a product in the database has 0 pricing_options
**When** the system converts the product to AdCP schema
**Then** the conversion fails with a ValueError
**Business Rule:** BR-RULE-007 (INV-4)
**Priority:** P0

#### Scenario: Product conversion includes 3.6 fields (Step 8, qo8a fix)
**Obligation ID** UC-001-MAIN-18
**Layer** schema
**Given** a product in the database has `catalog_match`, `catalog_types`, `conversion_tracking`, `data_provider_signals`, `forecast`, and `signal_targeting_allowed` fields populated
**When** the system converts the product to AdCP schema
**Then** all 6 fields are present in the converted Product object
**And** the fields are included in the serialized response
**Priority:** P0

#### Scenario: Product conversion -- 3.6 fields are optional (Step 8, qo8a fix)
**Obligation ID** UC-001-MAIN-19
**Layer** schema
**Given** a product in the database has none of the 6 new fields populated (all null)
**When** the system converts the product to AdCP schema
**Then** the conversion succeeds
**And** the null fields are omitted from the serialized response (or included as null per schema)
**Priority:** P0

#### Scenario: Principal access control -- authorized principal (Step 9)
**Obligation ID** UC-001-MAIN-20
**Layer** behavioral
**Given** a product has `allowed_principal_ids` set to ["principal_A", "principal_B"]
**And** the request's principal_id is "principal_A"
**When** the system applies access control
**Then** the product is visible in the results
**Business Rule:** BR-RULE-003 (INV-1)
**Priority:** P0

#### Scenario: Principal access control -- unauthorized principal (Step 9)
**Obligation ID** UC-001-MAIN-21
**Layer** behavioral
**Given** a product has `allowed_principal_ids` set to ["principal_A"]
**And** the request's principal_id is "principal_C"
**When** the system applies access control
**Then** the product is hidden from the results
**Business Rule:** BR-RULE-003 (INV-2)
**Priority:** P0

#### Scenario: Principal access control -- unrestricted product (Step 9)
**Obligation ID** UC-001-MAIN-22
**Layer** behavioral
**Given** a product has no `allowed_principal_ids` (null)
**When** the system applies access control
**Then** the product is visible to all authenticated principals
**Business Rule:** BR-RULE-003 (INV-3)
**Priority:** P0

#### Scenario: Property list filtering -- external list applied (Step 10)
**Obligation ID** UC-001-MAIN-23
**Layer** schema
**Given** a request includes a `property_list` reference with `agent_url` and `list_id`
**And** the external property list agent is reachable
**When** the system resolves the property list
**Then** products are filtered to only those available on listed properties
**And** the response includes `property_list_applied: true`
**Priority:** P1

#### Scenario: Dynamic product variant generation (Step 11)
**Obligation ID** UC-001-MAIN-24
**Layer** behavioral
**Given** a brief is provided in the request
**And** signals agents are configured and reachable
**When** the system queries signals agents
**Then** dynamic product variants are generated with `is_custom: true` and `expires_at`
**Priority:** P2

#### Scenario: Pricing enrichment with price_guidance and forecast (Step 12)
**Obligation ID** UC-001-MAIN-25
**Layer** behavioral
**Given** products have dynamic pricing data available
**When** the system enriches products
**Then** each product includes `price_guidance` and/or `forecast` data
**Priority:** P1

#### Scenario: Pricing enrichment -- forecast field from DB (Step 12, qo8a fix)
**Obligation ID** UC-001-MAIN-26
**Layer** schema
**Given** a product has a `forecast` field populated in the database
**When** the system enriches and converts the product
**Then** the `forecast` field is included in the product response
**Priority:** P0

#### Scenario: AdCP filter application (Step 13)
**Obligation ID** UC-001-MAIN-27
**Layer** behavioral
**Given** a request includes AdCP `filters`
**When** the system applies filters
**Then** only products matching ALL filter dimensions are returned
**Priority:** P0

#### Scenario: Policy-based eligibility filtering (Step 14)
**Obligation ID** UC-001-MAIN-28
**Layer** behavioral
**Given** the policy compliance check returned restrictions
**When** the system filters by policy-based eligibility
**Then** products incompatible with the policy result are removed
**Priority:** P1

#### Scenario: Min exposures filter (Step 15)
**Obligation ID** UC-001-MAIN-29
**Layer** behavioral
**Given** a request includes `min_exposures` in filters
**When** the system evaluates the filter
**Then** guaranteed products with forecast below the threshold are excluded
**And** non-guaranteed products are included if they have `price_guidance`
**Priority:** P1

#### Scenario: AI ranking with brief -- products above threshold (Step 16)
**Obligation ID** UC-001-MAIN-30
**Layer** behavioral
**Given** a brief is provided
**And** the tenant has a `product_ranking_prompt` configured
**And** the AI ranking service is available
**When** the system ranks products by relevance
**Then** products with `relevance_score` >= 0.1 are included
**And** products are sorted by `relevance_score` descending
**And** each product includes a `brief_relevance` explanation
**Business Rule:** BR-RULE-005 (INV-2)
**Priority:** P0

#### Scenario: AI ranking with brief -- products below threshold filtered (Step 16)
**Obligation ID** UC-001-MAIN-31
**Layer** behavioral
**Given** a brief is provided and ranking is applied
**And** some products score below 0.1 relevance
**When** the system ranks products
**Then** products with `relevance_score` < 0.1 are excluded from results
**Business Rule:** BR-RULE-005 (INV-1)
**Priority:** P0

#### Scenario: AI ranking service failure -- fail open (Step 16)
**Obligation ID** UC-001-MAIN-32
**Layer** behavioral
**Given** the AI ranking service is unavailable
**When** the system attempts to rank products
**Then** products are returned unranked (no threshold applied)
**Business Rule:** BR-RULE-005 (INV-4)
**Priority:** P1

#### Scenario: Adapter support annotation (Step 17)
**Obligation ID** UC-001-MAIN-33
**Layer** behavioral
**Given** the request is authenticated (principal has an adapter)
**When** the system annotates pricing options
**Then** each pricing option includes a `supported` flag
**And** unsupported options include `unsupported_reason`
**Priority:** P1

#### Scenario: Adapter support annotation -- PricingOption lookup correctness (Step 17, mq3n impact)
**Obligation ID** UC-001-MAIN-34
**Layer** behavioral
**Given** the request is authenticated
**When** the system annotates adapter support for pricing options
**Then** the pricing option lookup correctly resolves by pricing option ID (not by string-to-integer comparison mismatch)
**Priority:** P0

#### Scenario: Proposal generation (Step 18)
**Obligation ID** UC-001-MAIN-35
**Layer** behavioral
**Given** a brief is provided
**And** proposal generation is appropriate for this tenant/request
**When** the system generates proposals
**Then** proposals include `proposal_id`, `name`, and `allocations[]`
**And** allocation percentages sum to 100
**Priority:** P1

#### Scenario: Response assembly with confirmation flags (Step 19)
**Obligation ID** UC-001-MAIN-36
**Layer** schema
**Given** the pipeline completes successfully
**When** the system assembles the response
**Then** the response includes `products[]` (required)
**And** `property_list_applied` is set if property list was used
**And** `product_selectors_applied` is set if catalog selectors were used
**Priority:** P0

#### Scenario: Protocol envelope wrapping (Step 20)
**Obligation ID** UC-001-MAIN-37
**Layer** schema
**Given** the response is assembled
**When** the system wraps it in a protocol envelope
**Then** the envelope has `status: completed`
**And** the response conforms to `protocol-envelope.json` schema
**Priority:** P0

#### Scenario: MCP transport wrapping
**Obligation ID** UC-001-MAIN-38
**Layer** schema
**Given** the response is delivered via MCP
**When** the system wraps the protocol envelope
**Then** the MCP `ToolResult` includes both `content` (human-readable text) and `structured_content` (typed response)
**Priority:** P1

---

### Extension *a: Brief Fails Policy Compliance (UC-001-ext-a)

#### Scenario: Brief blocked by policy -- BLOCKED status
**Obligation ID** UC-001-EXT-A-01
**Layer** behavioral
**Given** the tenant has an `advertising_policy` configured and enabled
**And** the buyer's brief contains content that violates the policy (e.g., tobacco advertising)
**When** the system checks the brief against the policy via LLM
**Then** the LLM returns `BLOCKED` with a reason
**And** the system logs the policy violation to the audit trail (operation: `policy_check`, success: false)
**And** the system returns error code `POLICY_VIOLATION` with the LLM-provided reason
**And** the system state is unchanged (read-only operation)
**Business Rule:** BR-RULE-002 (INV-1)
**Priority:** P0

#### Scenario: Brief restricted with manual review required -- RESTRICTED status
**Obligation ID** UC-001-EXT-A-02
**Layer** behavioral
**Given** the tenant has an `advertising_policy` with `require_manual_review: true`
**And** the buyer's brief triggers a `RESTRICTED` result from the LLM
**When** the system processes the policy result
**Then** the system logs the violation to the audit trail (operation: `get_products_policy_violation`)
**And** the system returns error code `POLICY_VIOLATION` with reason and restrictions list
**Business Rule:** BR-RULE-002 (INV-2)
**Priority:** P1

#### Scenario: Policy service unavailable -- fail open
**Obligation ID** UC-001-EXT-A-03
**Layer** behavioral
**Given** the tenant has an `advertising_policy` configured
**And** the LLM policy service is unreachable (network error, API error, timeout)
**When** the system attempts the policy compliance check
**Then** the system logs the service failure to the audit trail (operation: `policy_check_failure`)
**And** the system sets `policy_result = None`
**And** the pipeline continues as if the policy check was disabled
**Business Rule:** BR-RULE-002 (INV-4)
**Priority:** P0

#### Scenario: Policy check BLOCKED -- error response contains reason
**Obligation ID** UC-001-EXT-A-04
**Layer** behavioral
**Given** the brief was blocked by policy
**When** the buyer receives the error response
**Then** the error message includes the LLM-provided reason explaining why the brief was blocked
**And** the buyer knows how to revise their brief to comply
**Priority:** P1

#### Scenario: Policy check BLOCKED -- response schema compliance
**Obligation ID** UC-001-EXT-A-05
**Layer** schema
**Given** the brief was blocked by policy
**When** the error response is returned
**Then** the response conforms to `get-products-response.json` error variant schema
**Priority:** P1

#### Scenario: Policy disabled or no API key -- check skipped
**Obligation ID** UC-001-EXT-A-06
**Layer** behavioral
**Given** the tenant has no `advertising_policy` configured (or no API key)
**When** the system processes the request
**Then** the policy compliance check is skipped entirely
**And** the pipeline continues with all products
**Business Rule:** BR-RULE-002 (INV-3)
**Priority:** P1

---

### Extension *b: Authentication Required by Policy (UC-001-ext-b)

#### Scenario: Require_auth policy with unauthenticated request
**Obligation ID** UC-001-EXT-B-01
**Layer** behavioral
**Given** the tenant's brand_manifest_policy is `require_auth`
**And** the Buyer Agent sends a request without valid authentication credentials
**When** the system evaluates the brand manifest policy
**Then** the system returns error code `authentication_error`
**And** the error message is "Authentication required by tenant policy"
**And** the system state is unchanged
**Business Rule:** BR-RULE-001 (INV-1)
**Priority:** P0

#### Scenario: Invalid token treated as unauthenticated
**Obligation ID** UC-001-EXT-B-02
**Layer** behavioral
**Given** the tenant's brand_manifest_policy is `require_auth`
**And** the Buyer Agent sends a request with an invalid/expired token
**When** the system attempts authentication
**Then** the principal_id is set to null (invalid tokens treated as missing)
**And** the request is rejected with `authentication_error`
**Business Rule:** BR-RULE-001 (INV-1)
**Priority:** P0

#### Scenario: Require_auth policy with authenticated request -- passes
**Obligation ID** UC-001-EXT-B-03
**Layer** behavioral
**Given** the tenant's brand_manifest_policy is `require_auth`
**And** the Buyer Agent sends a request with valid authentication
**When** the system evaluates the brand manifest policy
**Then** the policy check passes and the pipeline continues
**Priority:** P0

#### Scenario: Authentication error response schema compliance
**Obligation ID** UC-001-EXT-B-04
**Layer** behavioral
**Given** the request was rejected due to authentication
**When** the error response is returned
**Then** the response conforms to `get-products-response.json` error variant schema
**Priority:** P1

---

### Extension *c: Brand Manifest Required by Policy (UC-001-ext-c)

#### Scenario: Require_brand policy with no brand reference
**Obligation ID** UC-001-EXT-C-01
**Layer** behavioral
**Given** the tenant's brand_manifest_policy is `require_brand`
**And** the Buyer Agent sends a request without a `brand` reference
**When** the system evaluates the brand manifest policy
**Then** the system returns error code `validation_error`
**And** the error message is "Brand manifest required by tenant policy"
**And** the system state is unchanged
**Business Rule:** BR-RULE-001 (INV-2)
**Priority:** P0

#### Scenario: Require_brand policy with unresolvable brand reference
**Obligation ID** UC-001-EXT-C-02
**Layer** behavioral
**Given** the tenant's brand_manifest_policy is `require_brand`
**And** the Buyer Agent sends a request with a brand reference that has no `name`, no `url`, and no string manifest
**When** the system attempts brand manifest extraction
**Then** no offering text can be derived
**And** the system returns error code `validation_error` with "Brand manifest required by tenant policy"
**Business Rule:** BR-RULE-001 (INV-2)
**Priority:** P1

#### Scenario: Require_brand policy with valid brand reference -- passes
**Obligation ID** UC-001-EXT-C-03
**Layer** behavioral
**Given** the tenant's brand_manifest_policy is `require_brand`
**And** the Buyer Agent provides a `brand` reference with a resolvable `domain`
**When** the system evaluates the brand manifest policy
**Then** offering text is derived
**And** the policy check passes
**Priority:** P0

#### Scenario: Public policy -- no brand or auth required
**Obligation ID** UC-001-EXT-C-04
**Layer** behavioral
**Given** the tenant's brand_manifest_policy is `public`
**When** the system evaluates the brand manifest policy
**Then** the policy check passes regardless of authentication or brand presence
**Business Rule:** BR-RULE-001 (INV-3)
**Priority:** P0

---

### Alternative: No Brief (UC-001-alt-no-brief)

#### Scenario: Discovery without brief returns all eligible products unranked
**Obligation ID** UC-001-ALT-NO-BRIEF-01
**Layer** schema
**Given** a Buyer Agent is authenticated
**And** the tenant has products in its catalog
**When** the Buyer Agent sends a `get_products` request without a `brief` field
**Then** all eligible products are returned in catalog order (by product_id)
**And** no `brief_relevance` field is present on any product
**And** the response is wrapped in a protocol envelope with `status: completed`
**Priority:** P0

#### Scenario: No brief -- offering text defaults to "Generic product inquiry"
**Obligation ID** UC-001-ALT-NO-BRIEF-02
**Layer** schema
**Given** a request is sent without a `brief` and without a brand manifest `name` or `url`
**When** the system extracts offering text
**Then** the offering text defaults to "Generic product inquiry"
**Priority:** P2

#### Scenario: No brief -- dynamic variant generation skipped
**Obligation ID** UC-001-ALT-NO-BRIEF-03
**Layer** behavioral
**Given** a request is sent without a `brief`
**When** the system reaches the dynamic variant generation step
**Then** variant generation is skipped (no brief to drive signals agents)
**And** no dynamic variants appear in the response
**Priority:** P1

#### Scenario: No brief -- AI ranking skipped
**Obligation ID** UC-001-ALT-NO-BRIEF-04
**Layer** behavioral
**Given** a request is sent without a `brief`
**When** the system reaches the ranking step
**Then** AI ranking is skipped
**And** no relevance_score threshold is applied
**And** products are returned in catalog order
**Business Rule:** BR-RULE-005 (INV-3)
**Priority:** P0

#### Scenario: No brief -- proposal generation skipped
**Obligation ID** UC-001-ALT-NO-BRIEF-05
**Layer** behavioral
**Given** a request is sent without a `brief`
**When** the system reaches the proposal generation step
**Then** proposal generation is skipped
**And** no `proposals` array is included in the response
**Priority:** P1

#### Scenario: No brief with filters -- filters still applied
**Obligation ID** UC-001-ALT-NO-BRIEF-06
**Layer** behavioral
**Given** a request is sent without a `brief` but with `filters`
**When** the system processes the request
**Then** all specified filters are applied
**And** only matching products are returned (in catalog order)
**Priority:** P1

#### Scenario: No brief with product_selectors -- catalog matching still applied
**Obligation ID** UC-001-ALT-NO-BRIEF-07
**Layer** behavioral
**Given** a request is sent without a `brief` but with `product_selectors` and `brand`
**When** the system processes the request
**Then** catalog matching is applied to constrain eligible products
**Priority:** P2

#### Scenario: No brief with property_list -- property filtering still applied
**Obligation ID** UC-001-ALT-NO-BRIEF-08
**Layer** behavioral
**Given** a request is sent without a `brief` but with `property_list`
**When** the system processes the request
**Then** property list filtering is applied
**Priority:** P2

#### Scenario: No brief with pagination -- pagination still works
**Obligation ID** UC-001-ALT-NO-BRIEF-09
**Layer** behavioral
**Given** a request is sent without a `brief` but with `pagination`
**When** the system processes the request
**Then** pagination is applied to the (unranked) result set
**Priority:** P2

---

### Alternative: Anonymous Discovery (UC-001-alt-anonymous)

#### Scenario: Anonymous request with public policy returns products without pricing
**Obligation ID** UC-001-ALT-ANONYMOUS-DISCOVERY-01
**Layer** behavioral
**Given** the tenant's brand_manifest_policy is `public`
**And** the Buyer Agent sends a request without authentication
**When** the system processes the request
**Then** the system returns products that have no `allowed_principal_ids` restrictions
**And** every product in the response has `pricing_options` set to an empty array
**And** the response is wrapped in a protocol envelope with `status: completed`
**Business Rule:** BR-RULE-003 (INV-4), BR-RULE-004 (INV-1)
**Priority:** P0

#### Scenario: Anonymous -- principal_id is null
**Obligation ID** UC-001-ALT-ANONYMOUS-DISCOVERY-02
**Layer** behavioral
**Given** the request has no authentication credentials
**When** the system attempts authentication
**Then** `principal_id` is set to null
**Priority:** P0

#### Scenario: Anonymous -- access control hides restricted products
**Obligation ID** UC-001-ALT-ANONYMOUS-DISCOVERY-03
**Layer** behavioral
**Given** a product has `allowed_principal_ids` set to ["principal_A"]
**And** the request is anonymous (no principal)
**When** the system applies access control
**Then** the product is hidden from the results
**Business Rule:** BR-RULE-003 (INV-4)
**Priority:** P0

#### Scenario: Anonymous -- unrestricted products are visible
**Obligation ID** UC-001-ALT-ANONYMOUS-DISCOVERY-04
**Layer** behavioral
**Given** a product has no `allowed_principal_ids` (null)
**And** the request is anonymous
**When** the system applies access control
**Then** the product is visible in the results
**Business Rule:** BR-RULE-003 (INV-3)
**Priority:** P0

#### Scenario: Anonymous -- pricing suppression on all products
**Obligation ID** UC-001-ALT-ANONYMOUS-DISCOVERY-05
**Layer** behavioral
**Given** the request is anonymous
**When** the system processes the response
**Then** every product has `pricing_options` set to an empty array `[]`
**And** no pricing information is exposed
**Business Rule:** BR-RULE-004 (INV-1)
**Priority:** P0

#### Scenario: Authenticated -- pricing retained
**Obligation ID** UC-001-ALT-ANONYMOUS-DISCOVERY-06
**Layer** behavioral
**Given** the request is authenticated
**When** the system processes the response
**Then** products retain their full `pricing_options` arrays
**Business Rule:** BR-RULE-004 (INV-2)
**Priority:** P0

#### Scenario: Anonymous -- adapter support annotation skipped
**Obligation ID** UC-001-ALT-ANONYMOUS-DISCOVERY-07
**Layer** behavioral
**Given** the request is anonymous (no principal)
**When** the system reaches the adapter annotation step
**Then** adapter support annotation is skipped
**And** pricing options have no `supported` flag
**Priority:** P2

#### Scenario: Anonymous with brief -- ranking still applied
**Obligation ID** UC-001-ALT-ANONYMOUS-DISCOVERY-08
**Layer** behavioral
**Given** the request is anonymous but includes a `brief`
**When** the system processes the request
**Then** products are ranked by brief relevance
**And** products below 0.1 threshold are excluded
**But** pricing is still suppressed on surviving products
**Priority:** P1

#### Scenario: Anonymous -- proposals with pricing suppressed
**Obligation ID** UC-001-ALT-ANONYMOUS-DISCOVERY-09
**Layer** behavioral
**Given** the request is anonymous and includes a `brief`
**And** proposal generation is triggered
**When** the system generates proposals
**Then** proposals are included in the response
**But** budget guidance and pricing data within proposals are suppressed
**Priority:** P2

#### Scenario: Anonymous with require_auth policy -- rejected
**Obligation ID** UC-001-ALT-ANONYMOUS-DISCOVERY-10
**Layer** behavioral
**Given** the tenant's brand_manifest_policy is `require_auth`
**And** the request is anonymous
**When** the system evaluates the brand manifest policy
**Then** the request is rejected (delegates to extension *b)
**Business Rule:** BR-RULE-001 (INV-1)
**Priority:** P0

---

### Alternative: Empty Results (UC-001-alt-empty)

#### Scenario: Valid request with no matching products returns empty list
**Obligation ID** UC-001-ALT-EMPTY-RESULTS-01
**Layer** schema
**Given** a Buyer Agent sends a valid authenticated request
**And** no products survive the filtering and ranking pipeline
**When** the system processes the request
**Then** the response includes an empty `products[]` array
**And** the response is wrapped in a protocol envelope with `status: completed`
**And** this is a successful response (not an error)
**Priority:** P0

#### Scenario: Empty results -- tenant has no products defined
**Obligation ID** UC-001-ALT-EMPTY-RESULTS-02
**Layer** schema
**Given** a tenant has zero products in its catalog
**When** the Buyer Agent sends a `get_products` request
**Then** the response includes an empty `products[]` array
**Priority:** P0

#### Scenario: Empty results -- all products excluded by access control
**Obligation ID** UC-001-ALT-EMPTY-RESULTS-03
**Layer** behavioral
**Given** all products have `allowed_principal_ids` restrictions
**And** the authenticated principal is not in any product's allowed list
**When** the system applies access control
**Then** zero products remain and an empty list is returned
**Priority:** P1

#### Scenario: Empty results -- all products excluded by property list filter
**Obligation ID** UC-001-ALT-EMPTY-RESULTS-04
**Layer** behavioral
**Given** a `property_list` is provided
**And** no products are available on the referenced properties
**When** the system applies property list filtering
**Then** zero products remain
**Priority:** P2

#### Scenario: Empty results -- all products excluded by product selectors
**Obligation ID** UC-001-ALT-EMPTY-RESULTS-05
**Layer** behavioral
**Given** `product_selectors` are provided
**And** no products match the catalog selectors
**When** the system resolves catalog matching
**Then** zero products are eligible
**Priority:** P2

#### Scenario: Empty results -- all products excluded by AdCP filters
**Obligation ID** UC-001-ALT-EMPTY-RESULTS-06
**Layer** behavioral
**Given** filters are provided (e.g., `delivery_type: "guaranteed"`)
**And** no products match the filter criteria
**When** the system applies filters
**Then** zero products remain
**Priority:** P1

#### Scenario: Empty results -- all products excluded by policy eligibility
**Obligation ID** UC-001-ALT-EMPTY-RESULTS-07
**Layer** behavioral
**Given** the policy compliance check returned restrictions
**And** all products are incompatible with the policy result
**When** the system filters by policy eligibility
**Then** zero products remain
**Priority:** P2

#### Scenario: Empty results -- all products below AI ranking threshold
**Obligation ID** UC-001-ALT-EMPTY-RESULTS-08
**Layer** behavioral
**Given** a brief is provided and ranking is applied
**And** all products score below 0.1 relevance
**When** the system ranks products
**Then** all products are excluded
**And** an empty list is returned
**Business Rule:** BR-RULE-005 (INV-1)
**Priority:** P1

#### Scenario: Empty results -- all products excluded by min_exposures filter
**Obligation ID** UC-001-ALT-EMPTY-RESULTS-09
**Layer** behavioral
**Given** a `min_exposures` filter is specified
**And** no products meet the exposure threshold
**When** the system applies the filter
**Then** zero products remain
**Priority:** P2

---

### Alternative: Filtered Discovery (UC-001-alt-filtered)

#### Scenario: Filter by delivery_type -- exact match
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-01
**Layer** behavioral
**Given** a request includes `filters: { delivery_type: "guaranteed" }`
**And** the catalog has both guaranteed and auction products
**When** the system applies the filter
**Then** only products with `delivery_type: "guaranteed"` are returned
**Priority:** P0

#### Scenario: Filter by is_fixed_price -- true
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-02
**Layer** behavioral
**Given** a request includes `filters: { is_fixed_price: true }`
**And** products have a mix of fixed and auction pricing options
**When** the system applies the filter
**Then** only products with at least one pricing option having `is_fixed: true` are returned
**Priority:** P1

#### Scenario: Filter by is_fixed_price -- false (auction)
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-03
**Layer** behavioral
**Given** a request includes `filters: { is_fixed_price: false }`
**When** the system applies the filter
**Then** only products with at least one pricing option having `is_fixed: false` are returned
**Priority:** P1

#### Scenario: Filter by is_fixed_price -- product with both fixed and auction options
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-04
**Layer** behavioral
**Given** a product has both a fixed-price and an auction pricing option
**When** filtered by `is_fixed_price: true`
**Then** the product is included
**When** filtered by `is_fixed_price: false`
**Then** the product is also included (matches both)
**Priority:** P1

#### Scenario: Filter by format_types -- OR matching
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-05
**Layer** behavioral
**Given** a request includes `filters: { format_types: ["video", "display"] }`
**And** a product supports "video" but not "display"
**When** the system applies the filter
**Then** the product is included (OR logic -- any single match satisfies)
**Priority:** P0

#### Scenario: Filter by format_ids -- OR matching
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-06
**Layer** behavioral
**Given** a request includes `filters: { format_ids: ["video_outstream", "display_300x250"] }`
**And** a product supports "display_300x250" only
**When** the system applies the filter
**Then** the product is included (OR logic)
**Priority:** P1

#### Scenario: Filter by standard_formats_only
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-07
**Layer** behavioral
**Given** a request includes `filters: { standard_formats_only: true }`
**And** a product has a mix of standard and custom format IDs
**When** the system applies the filter
**Then** only products whose ALL format IDs use standard prefixes (`display_`, `video_`, `audio_`, `native_`) are included
**Priority:** P2

#### Scenario: Filter by countries -- intersection matching
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-08
**Layer** behavioral
**Given** a request includes `filters: { countries: ["US", "CA"] }`
**And** a product is available in ["US", "MX"]
**When** the system applies the filter
**Then** the product is included (overlap: "US")
**Priority:** P0

#### Scenario: Filter by countries -- product with no country restriction
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-09
**Layer** behavioral
**Given** a request includes `filters: { countries: ["US"] }`
**And** a product has no country restriction (null or empty)
**When** the system applies the filter
**Then** the product is included (no restriction means matches any filter)
**Priority:** P1

#### Scenario: Filter by regions -- ISO 3166-2 intersection
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-10
**Layer** schema
**Given** a request includes `filters: { regions: ["US-NY", "US-CA"] }`
**And** a product covers region "US-NY"
**When** the system applies the filter
**Then** the product is included (overlap: "US-NY")
**Priority:** P1

#### Scenario: Filter by metros -- system + code intersection
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-11
**Layer** behavioral
**Given** a request includes `filters: { metros: [{ system: "nielsen_dma", code: "501" }] }`
**And** a product covers metro "501" in the "nielsen_dma" system
**When** the system applies the filter
**Then** the product is included
**Priority:** P2

#### Scenario: Filter by channels -- intersection matching
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-12
**Layer** behavioral
**Given** a request includes `filters: { channels: ["display", "video"] }`
**And** a product has channels ["display"]
**When** the system applies the filter
**Then** the product is included (overlap: "display")
**Priority:** P1

#### Scenario: Filter by channels -- product with no channels uses adapter defaults
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-13
**Layer** behavioral
**Given** a request includes `filters: { channels: ["display"] }`
**And** a product has no channels defined
**When** the system applies the filter
**Then** the product matches via adapter defaults (if adapter supports "display")
**Priority:** P2

#### Scenario: Filter by budget_range
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-14
**Layer** behavioral
**Given** a request includes `filters: { budget_range: { min: 1000, max: 5000, currency: "USD" } }`
**When** the system applies the filter
**Then** only products with pricing compatible with the budget range are returned
**Priority:** P2

#### Scenario: Filter by start_date and end_date
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-15
**Layer** behavioral
**Given** a request includes `filters: { start_date: "2026-03-01", end_date: "2026-03-31" }`
**When** the system applies the filter
**Then** only products available within the specified date range are returned
**Priority:** P2

#### Scenario: Filter by required_axe_integrations
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-16
**Layer** behavioral
**Given** a request includes `filters: { required_axe_integrations: ["https://axe.example.com"] }`
**When** the system applies the filter
**Then** only products executable through the specified agentic ad exchange URIs are returned
**Priority:** P2

#### Scenario: Filter by required_features -- only true values filter
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-17
**Layer** behavioral
**Given** a request includes `filters: { required_features: { guaranteed_delivery: true, real_time_bidding: false } }`
**When** the system applies the filter
**Then** only `guaranteed_delivery: true` is used as a filter (false values are ignored)
**Priority:** P2

#### Scenario: Filter by required_geo_targeting
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-18
**Layer** behavioral
**Given** a request includes `filters: { required_geo_targeting: { country: true, region: true } }`
**When** the system applies the filter
**Then** only products whose seller supports country and region geo targeting are returned
**Priority:** P2

#### Scenario: Filter by signal_targeting (qo8a impact)
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-19
**Layer** behavioral
**Given** a request includes `filters: { signal_targeting: { signals: ["purchase_intent"] } }`
**And** products have `signal_targeting_allowed` and `data_provider_signals` fields populated
**When** the system applies the filter
**Then** only products with `signal_targeting_allowed: true` AND `data_provider_signals` containing "purchase_intent" are returned
**Priority:** P1

#### Scenario: Filter by signal_targeting -- missing DB fields (pre-qo8a regression)
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-20
**Layer** behavioral
**Given** a request includes `filters: { signal_targeting: { signals: ["purchase_intent"] } }`
**And** products have `signal_targeting_allowed` as null (never populated)
**When** the system applies the filter
**Then** products are excluded (null is not true)
**And** the result may be empty if no products have signal targeting data
**Priority:** P1

#### Scenario: Multiple filters combined -- AND logic
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-21
**Layer** behavioral
**Given** a request includes `filters: { delivery_type: "guaranteed", countries: ["US"], format_types: ["video"] }`
**When** the system applies filters
**Then** ALL filter dimensions must be satisfied (AND across dimensions)
**And** only products matching guaranteed delivery AND available in US AND supporting video are returned
**Priority:** P0

#### Scenario: Filter by min_exposures -- guaranteed product with forecast
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-22
**Layer** behavioral
**Given** a request includes `filters: { min_exposures: 10000 }`
**And** a guaranteed product has `forecast.impressions: 50000`
**When** the system applies the filter
**Then** the product is included (forecast meets threshold)
**Priority:** P1

#### Scenario: Filter by min_exposures -- guaranteed product without sufficient forecast
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-23
**Layer** behavioral
**Given** a request includes `filters: { min_exposures: 100000 }`
**And** a guaranteed product has `forecast.impressions: 5000`
**When** the system applies the filter
**Then** the product is excluded
**Priority:** P1

#### Scenario: Filter by min_exposures -- non-guaranteed product with price_guidance
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-24
**Layer** behavioral
**Given** a request includes `filters: { min_exposures: 10000 }`
**And** a non-guaranteed product has `price_guidance` set
**When** the system applies the filter
**Then** the product is included (non-guaranteed with price_guidance always passes)
**Priority:** P2

#### Scenario: Filtered results with brief -- ranking applied after filtering
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-25
**Layer** behavioral
**Given** a request includes both `filters` and a `brief`
**When** the system processes the request
**Then** filters are applied first (step 13)
**And** AI ranking is applied to the filtered set (step 16)
**And** products are returned sorted by relevance_score
**Priority:** P1

#### Scenario: Filtered results without brief -- catalog order
**Obligation ID** UC-001-ALT-FILTERED-DISCOVERY-26
**Layer** behavioral
**Given** a request includes `filters` but no `brief`
**When** the system processes the request
**Then** filters narrow the product set
**And** products are returned in catalog order (no ranking)
**Priority:** P1

---

### Alternative: Paginated Discovery (UC-001-alt-paginated)

#### Scenario: First page -- request with max_results
**Obligation ID** UC-001-ALT-PAGINATED-DISCOVERY-01
**Layer** behavioral
**Given** a Buyer Agent sends a `get_products` request with `pagination: { max_results: 5 }`
**And** 12 products match the query
**When** the system processes the request
**Then** the response contains the first 5 products (ranked/filtered)
**And** `pagination.has_more` is `true`
**And** `pagination.cursor` is present (opaque string)
**And** `pagination.total_count` may be present (optional)
**Priority:** P1

#### Scenario: Subsequent page -- request with cursor
**Obligation ID** UC-001-ALT-PAGINATED-DISCOVERY-02
**Layer** behavioral
**Given** a Buyer Agent sends a follow-up `get_products` request with `pagination: { cursor: "<opaque_cursor>", max_results: 5 }`
**When** the system processes the request
**Then** the cursor is validated
**And** the next 5 products are returned
**And** `pagination.has_more` reflects whether more pages remain
**And** a new cursor is provided if `has_more` is true
**Priority:** P1

#### Scenario: Last page -- has_more is false
**Obligation ID** UC-001-ALT-PAGINATED-DISCOVERY-03
**Layer** behavioral
**Given** a Buyer Agent requests the final page with a cursor
**When** the system processes the request
**Then** the remaining products are returned
**And** `pagination.has_more` is `false`
**And** no `cursor` is included in the response
**Priority:** P1

#### Scenario: Paginated results maintain stable ordering
**Obligation ID** UC-001-ALT-PAGINATED-DISCOVERY-04
**Layer** behavioral
**Given** a Buyer Agent paginates through multiple pages
**When** comparing products across pages
**Then** products appear in the same order as they would without pagination
**And** no products are duplicated across pages
**And** no products are skipped between pages
**Priority:** P1

#### Scenario: Proposals only on first page
**Obligation ID** UC-001-ALT-PAGINATED-DISCOVERY-05
**Layer** behavioral
**Given** a paginated request generates proposals
**When** the first page is returned
**Then** proposals are included in the response
**When** subsequent pages are returned
**Then** proposals are NOT included (they apply to the full result set)
**Priority:** P2

#### Scenario: Default max_results when not specified
**Obligation ID** UC-001-ALT-PAGINATED-DISCOVERY-06
**Layer** schema
**Given** a request includes `pagination` but no `max_results`
**When** the system processes the request
**Then** the default page size of 50 is used
**Priority:** P2

#### Scenario: max_results bounds -- minimum 1
**Obligation ID** UC-001-ALT-PAGINATED-DISCOVERY-07
**Layer** schema
**Given** a request includes `pagination: { max_results: 0 }`
**When** the system validates the request
**Then** the request is rejected (max_results must be >= 1)
**Priority:** P2

#### Scenario: max_results bounds -- maximum 100
**Obligation ID** UC-001-ALT-PAGINATED-DISCOVERY-08
**Layer** schema
**Given** a request includes `pagination: { max_results: 200 }`
**When** the system validates the request
**Then** the request is rejected or max_results is clamped to 100
**Priority:** P2

#### Scenario: Invalid cursor handling
**Obligation ID** UC-001-ALT-PAGINATED-DISCOVERY-09
**Layer** behavioral
**Given** a request includes `pagination: { cursor: "invalid_cursor" }`
**When** the system validates the cursor
**Then** the system returns an error (behavior TBD per open question G27)
**Priority:** P2

#### Scenario: Expired cursor handling
**Obligation ID** UC-001-ALT-PAGINATED-DISCOVERY-10
**Layer** behavioral
**Given** a request includes a cursor that was previously valid but has expired
**When** the system validates the cursor
**Then** the system returns an error indicating the cursor is expired
**Priority:** P2

---

### Alternative: Discovery with Proposals (UC-001-alt-proposal)

#### Scenario: Response includes proposals with allocations
**Obligation ID** UC-001-ALT-DISCOVERY-WITH-PROPOSALS-01
**Layer** schema
**Given** a Buyer Agent sends a `get_products` request with `brief` and `brand`
**And** proposal generation is triggered
**When** the system processes the request
**Then** the response includes a `proposals[]` array
**And** each proposal has a unique `proposal_id`
**And** each proposal has a `name`
**And** each proposal has at least 1 allocation
**Priority:** P1

#### Scenario: Proposal allocation percentages sum to 100
**Obligation ID** UC-001-ALT-DISCOVERY-WITH-PROPOSALS-02
**Layer** schema
**Given** a proposal is generated with multiple allocations
**When** the proposal is assembled
**Then** the sum of `allocation_percentage` values across all allocations equals 100
**Priority:** P0

#### Scenario: Proposal allocation product_id references valid product
**Obligation ID** UC-001-ALT-DISCOVERY-WITH-PROPOSALS-03
**Layer** schema
**Given** a proposal allocation has a `product_id`
**When** the allocation is validated
**Then** the `product_id` must reference a product in the response's `products[]` array
**Priority:** P0

#### Scenario: Proposal includes optional budget guidance
**Obligation ID** UC-001-ALT-DISCOVERY-WITH-PROPOSALS-04
**Layer** schema
**Given** a proposal is generated
**When** budget guidance is available
**Then** `total_budget_guidance` includes `min`, `recommended`, `max`, and `currency`
**Priority:** P2

#### Scenario: Proposal includes expires_at
**Obligation ID** UC-001-ALT-DISCOVERY-WITH-PROPOSALS-05
**Layer** schema
**Given** a proposal is generated with an expiration
**When** the response is returned
**Then** `expires_at` is a valid ISO 8601 datetime
**Priority:** P2

#### Scenario: Proposal includes brief_alignment
**Obligation ID** UC-001-ALT-DISCOVERY-WITH-PROPOSALS-06
**Layer** behavioral
**Given** a proposal is generated from a brief
**When** the response is returned
**Then** `brief_alignment` explains how the proposal aligns with the campaign brief
**Priority:** P2

#### Scenario: Proposal includes aggregate forecast
**Obligation ID** UC-001-ALT-DISCOVERY-WITH-PROPOSALS-07
**Layer** behavioral
**Given** a proposal is generated with forecast data available
**When** the response is returned
**Then** the proposal includes an aggregate `forecast` for the entire plan
**Priority:** P2

#### Scenario: Allocation includes pricing_option_id recommendation
**Obligation ID** UC-001-ALT-DISCOVERY-WITH-PROPOSALS-08
**Layer** schema
**Given** a proposal allocation references a product with multiple pricing options
**When** the allocation is generated
**Then** `pricing_option_id` recommends a specific pricing option from the product's available options
**Priority:** P2

#### Scenario: Allocation includes rationale
**Obligation ID** UC-001-ALT-DISCOVERY-WITH-PROPOSALS-09
**Layer** behavioral
**Given** a proposal allocation is generated
**When** the allocation includes a `rationale`
**Then** the rationale explains why this product/allocation was recommended
**Priority:** P3

#### Scenario: Allocation includes sequence
**Obligation ID** UC-001-ALT-DISCOVERY-WITH-PROPOSALS-10
**Layer** schema
**Given** a proposal has multiple allocations
**When** allocations include `sequence` values
**Then** sequence represents recommended execution order (integer >= 1)
**Priority:** P3

#### Scenario: Allocation includes daypart_targets
**Obligation ID** UC-001-ALT-DISCOVERY-WITH-PROPOSALS-11
**Layer** behavioral
**Given** a proposal allocation is generated with time targeting
**When** the allocation includes `daypart_targets`
**Then** the daypart targets represent time-of-day targeting recommendations
**Priority:** P3

#### Scenario: Allocation-level forecast
**Obligation ID** UC-001-ALT-DISCOVERY-WITH-PROPOSALS-12
**Layer** behavioral
**Given** a proposal allocation is generated with forecast data
**When** the allocation includes a `forecast`
**Then** the forecast provides allocation-specific delivery predictions
**Priority:** P3

#### Scenario: Proposal actionability -- proposal_id links to create_media_buy
**Obligation ID** UC-001-ALT-DISCOVERY-WITH-PROPOSALS-13
**Layer** behavioral
**Given** a proposal is returned in a get_products response
**When** the buyer wants to execute the proposal
**Then** the buyer can send `create_media_buy` with the `proposal_id` (UC-002)
**Priority:** P1

---

### Business Rule: PricingOption XOR Constraint (BR-RULE-006)

#### Scenario: Valid pricing option -- fixed_price only
**Obligation ID** UC-001-BR-PRICINGOPTION-XOR-CONSTRAINT-01
**Layer** schema
**Given** a pricing option has `fixed_price` set and `floor_price` is null
**When** the system validates the pricing option
**Then** the pricing option is valid
**Business Rule:** BR-RULE-006 (INV-1)
**Priority:** P0

#### Scenario: Valid pricing option -- floor_price only
**Obligation ID** UC-001-BR-PRICINGOPTION-XOR-CONSTRAINT-02
**Layer** schema
**Given** a pricing option has `floor_price` set and `fixed_price` is null
**When** the system validates the pricing option
**Then** the pricing option is valid
**Business Rule:** BR-RULE-006 (INV-2)
**Priority:** P0

#### Scenario: Invalid pricing option -- both fixed_price and floor_price set
**Obligation ID** UC-001-BR-PRICINGOPTION-XOR-CONSTRAINT-03
**Layer** schema
**Given** a pricing option has both `fixed_price` and `floor_price` set
**When** the system validates the pricing option
**Then** the pricing option is invalid
**Business Rule:** BR-RULE-006 (INV-3)
**Priority:** P0

#### Scenario: Invalid pricing option -- neither fixed_price nor floor_price set
**Obligation ID** UC-001-BR-PRICINGOPTION-XOR-CONSTRAINT-04
**Layer** schema
**Given** a pricing option has neither `fixed_price` nor `floor_price` set
**When** the system validates the pricing option
**Then** the pricing option is invalid
**Business Rule:** BR-RULE-006 (INV-4)
**Priority:** P0

#### Scenario: CPA pricing model -- always fixed_price (model-specific)
**Obligation ID** UC-001-BR-PRICINGOPTION-XOR-CONSTRAINT-05
**Layer** schema
**Given** a pricing option uses the `cpa` model
**When** the system validates the pricing option
**Then** the pricing option is valid because CPA always has `fixed_price` (schema-enforced)
**And** the XOR check does not reject it
**Business Rule:** BR-RULE-006 (CPA note)
**Priority:** P1

---

### Business Rule: Product Schema Validity (BR-RULE-007)

#### Scenario: Product with all required arrays populated
**Obligation ID** UC-001-BR-PRODUCT-SCHEMA-VALIDITY-01
**Layer** schema
**Given** a product has format_ids: ["display_300x250"], publisher_properties: [{...}], pricing_options: [{...}]
**When** the system converts it to AdCP schema
**Then** conversion succeeds
**Business Rule:** BR-RULE-007 (INV-1)
**Priority:** P0

#### Scenario: Product conversion failure is fatal
**Obligation ID** UC-001-BR-PRODUCT-SCHEMA-VALIDITY-02
**Layer** schema
**Given** a product fails AdCP schema conversion (e.g., 0 format_ids)
**When** the system processes the get_products request
**Then** the entire request fails (not just that product)
**And** the error indicates data corruption
**Business Rule:** BR-RULE-007
**Priority:** P0

---

### Product Response Schema Completeness (3.6 Upgrade)

#### Scenario: Product response includes all mandatory AdCP fields
**Obligation ID** UC-001-PRODUCT-RESPONSE-SCHEMA-01
**Layer** schema
**Given** a product is returned in the response
**When** the product is serialized
**Then** it includes all required AdCP fields: `product_id`, `name`, `description`, `delivery_type`, `format_ids`, `publisher_properties`, `pricing_options`
**Priority:** P0

#### Scenario: Product response includes new 3.6 optional fields when populated
**Obligation ID** UC-001-PRODUCT-RESPONSE-SCHEMA-02
**Layer** schema
**Given** a product has the following fields populated in the database:
  - `catalog_match`
  - `catalog_types`
  - `conversion_tracking`
  - `data_provider_signals`
  - `forecast`
  - `signal_targeting_allowed`
**When** the product is serialized in the response
**Then** all 6 fields are present in the serialized product
**Priority:** P0

#### Scenario: Product response omits 3.6 optional fields when not populated
**Obligation ID** UC-001-PRODUCT-RESPONSE-SCHEMA-03
**Layer** schema
**Given** a product has none of the 6 new fields populated
**When** the product is serialized in the response
**Then** the serialized product either omits the fields or includes them as null
**And** the serialization is valid per the AdCP product schema
**Priority:** P1

#### Scenario: Roundtrip -- DB model to AdCP schema to response preserves all fields
**Obligation ID** UC-001-PRODUCT-RESPONSE-SCHEMA-04
**Layer** behavioral
**Given** a product is created in the database with all 6 new fields populated
**When** the product goes through: DB model -> product_conversion -> Product schema -> model_dump -> JSON response
**Then** all field values are preserved exactly through the entire roundtrip
**Priority:** P0

---

### Postcondition Verification

#### Scenario: POST-S1 -- Buyer knows what matches
**Obligation ID** UC-001-POST-01
**Layer** behavioral
**Given** a successful get_products response
**When** the buyer examines the response
**Then** the `products[]` array contains all matching products (may be empty)
**Priority:** P0

#### Scenario: POST-S2 -- Buyer can evaluate pricing, formats, and delivery
**Obligation ID** UC-001-POST-02
**Layer** behavioral
**Given** an authenticated successful get_products response with products
**When** the buyer examines each product
**Then** each product has `pricing_options` (non-empty for authenticated), `format_ids`, `publisher_properties`, and `delivery_type`
**Priority:** P0

#### Scenario: POST-S3 -- Products ordered by relevance when brief provided
**Obligation ID** UC-001-POST-03
**Layer** behavioral
**Given** a successful get_products response with brief provided
**When** the buyer examines the product order
**Then** products are sorted by `relevance_score` descending
**Priority:** P0

#### Scenario: POST-S4 -- Buyer only sees authorized products
**Obligation ID** UC-001-POST-04
**Layer** behavioral
**Given** a successful get_products response
**When** the buyer examines the products
**Then** no product with `allowed_principal_ids` excluding the buyer's principal is visible
**Priority:** P0

#### Scenario: POST-S5 -- Buyer knows request completed
**Obligation ID** UC-001-POST-05
**Layer** schema
**Given** a successful response
**When** the buyer examines the protocol envelope
**Then** `status` is `completed`
**Priority:** P0

#### Scenario: POST-S6 -- Buyer can evaluate proposals
**Obligation ID** UC-001-POST-06
**Layer** schema
**Given** a successful response with proposals
**When** the buyer examines the proposals
**Then** each proposal has actionable information (proposal_id, name, allocations)
**Priority:** P1

#### Scenario: POST-S7 -- Buyer knows pagination state
**Obligation ID** UC-001-POST-07
**Layer** schema
**Given** a paginated response
**When** the buyer examines the pagination metadata
**Then** `has_more` indicates if more pages exist
**And** `cursor` is provided when `has_more` is true
**Priority:** P1

#### Scenario: POST-F1 -- System state unchanged on failure
**Obligation ID** UC-001-POST-08
**Layer** behavioral
**Given** a failed get_products request (policy violation, auth error, etc.)
**When** the request completes with an error
**Then** no system state has been modified (read-only operation)
**Priority:** P0

#### Scenario: POST-F2 -- Buyer knows failure reason
**Obligation ID** UC-001-POST-09
**Layer** schema
**Given** a failed get_products request
**When** the buyer examines the error response
**Then** the error code and message explain why the request failed
**Priority:** P0

#### Scenario: POST-F3 -- Buyer knows how to fix
**Obligation ID** UC-001-POST-10
**Layer** behavioral
**Given** a failed get_products request
**When** the buyer examines the error response
**Then** the error provides enough information for the buyer to correct and retry
**Priority:** P1

---

## Cross-Cutting Concerns

### NFR-001: Security Hardening
- Authentication credentials are validated at request entry
- Brand manifest policy is enforced before catalog access
- Access control filters restrict product visibility per principal
- Property list agent URLs are validated before external fetch

### NFR-002: Prompt Injection Defense
- Policy LLM check (step 5) must be resistant to prompt injection in the brief
- Signals agent queries (step 11) must sanitize brief text
- Ranking LLM (step 16) must be resistant to injection in brief or product descriptions

### NFR-003: Audit and Logging
- All policy check decisions are logged (allowed, blocked, restricted, failed)
- Authentication decisions are logged
- The full pipeline produces an audit trail

### NFR-004: Response Latency SLAs
- LLM calls (policy check, ranking) have latency budgets
- Property list agent calls have timeout limits
- Signals agent queries have timeout limits

---

## Open Questions (from Gap Register)

These may generate additional test scenarios once resolved:

| ID | Question | Impact on Tests |
|----|----------|-----------------|
| G5 | AI ranking threshold 0.1 -- configurable per tenant? | If yes, need parameterized threshold tests |
| G7 | Dynamic product variant TTL semantics? | Need tests for expired variant references |
| G8 | Empty request returns all products -- intentional? | Need explicit test confirming this behavior |
| G25 | When does seller include proposals? | Need tests for proposal generation trigger conditions |
| G26 | Proposal expires between get_products and create_media_buy? | Cross-use-case test (UC-001 -> UC-002) |
| G27 | Invalid/expired pagination cursor behavior? | Need error handling tests for cursors |
| G28 | External property list fetch -- caching, error handling? | Need tests for unreachable agent_url |
| G29 | product_selectors reference items not in brand manifest? | Need tests for catalog miss scenarios |
