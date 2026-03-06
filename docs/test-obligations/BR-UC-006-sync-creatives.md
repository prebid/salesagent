# BR-UC-006: Sync Creative Assets -- Test Obligations

## Source

- **BR-UC-006.md** -- Use case overview: Buyer syncs creative assets to Seller's library
- **BR-UC-006-main-mcp.md** -- Main flow via MCP tool
- **BR-UC-006-main-rest.md** -- Main flow via A2A/REST endpoint
- **BR-UC-006-ext-a.md** -- Extension: Authentication required (missing principal_id)
- **BR-UC-006-ext-b.md** -- Extension: Tenant not found
- **BR-UC-006-ext-c.md** -- Extension: Creative validation failed (schema error)
- **BR-UC-006-ext-d.md** -- Extension: Creative name empty
- **BR-UC-006-ext-e.md** -- Extension: Creative format required (missing format_id)
- **BR-UC-006-ext-f.md** -- Extension: Creative format unknown (not in registry)
- **BR-UC-006-ext-g.md** -- Extension: Creative agent unreachable
- **BR-UC-006-ext-h.md** -- Extension: Creative preview failed (no previews, no media_url)
- **BR-UC-006-ext-i.md** -- Extension: Gemini key missing (generative creative)
- **BR-UC-006-ext-j.md** -- Extension: Package not found (assignment target missing)
- **BR-UC-006-ext-k.md** -- Extension: Format mismatch (format incompatible with product)

### Referenced Business Rules

- **BR-RULE-033** -- Validation Mode Semantics (strict vs lenient)
- **BR-RULE-034** -- Cross-Principal Creative Isolation
- **BR-RULE-035** -- Creative Format Validation
- **BR-RULE-036** -- Generative Creative Build
- **BR-RULE-037** -- Creative Approval Workflow
- **BR-RULE-038** -- Assignment Package Validation
- **BR-RULE-039** -- Assignment Format Compatibility
- **BR-RULE-040** -- Media Buy Status Transition on Assignment

## 3.6 Upgrade Impact

**CRITICAL.** This use case is directly affected by bug **salesagent-goy2**: Creative extends the WRONG adcp base class.

### Bug: salesagent-goy2 -- Creative Wrong Base Class

**Current state:** `salesagent.schemas.Creative` extends `adcp.types.Creative` which resolves to `adcp.types.generated_poc.creative.get_creative_delivery_response.Creative` (the **delivery** Creative).

**Correct base class:** The listing Creative at `adcp.types.generated_poc.media_buy.list_creatives_response.Creative` which has 13 fields:
- `account`, `assets`, `assignments`, `catalogs`, `created_date`, `creative_id`, `format_id`, `name`, `performance`, `status`, `sub_assets`, `tags`, `updated_date`

**Wrong base class (currently used):** The delivery Creative has only 6 fields:
- `creative_id`, `format_id`, `media_buy_id`, `totals`, `variant_count`, `variants`

**Consequences:**
1. **4 required fields stripped** from every `list_creatives` response: `name`, `status`, `created_date`, `updated_date` -- these are marked `exclude=True` in salesagent's Creative to work around the wrong base class, but they are REQUIRED fields in the listing Creative schema
2. **`variants=[]` hardcoded** to satisfy the wrong base class's required `variants` field -- the listing Creative has no `variants` field at all
3. **Every `list_creatives` response violates the AdCP schema** -- buyers expecting listing Creative fields get delivery Creative fields
4. **Fields that SHOULD be in the response** (from listing Creative): `assignments`, `performance`, `status_summary`, `account`, `catalogs`, `sub_assets`
5. **Fields that SHOULD NOT be in the response** (from delivery Creative): `variants`, `variant_count`, `totals`, `media_buy_id`

### Bug: salesagent-mq3n -- PricingOption delivery lookup

PricingOption uses string vs integer PK for delivery lookups. Not directly in UC-006 scope but affects downstream delivery after creative assignment.

### Bug: salesagent-7gnv -- MediaBuy boundary drops fields

MediaBuy boundary layer drops fields during serialization. Affects media buy status transitions triggered by creative assignments (BR-RULE-040).

### Upgrade test priorities for this UC:
- **P0**: Creative schema compliance -- verify Creative extends correct listing base class with all 13 fields
- **P0**: list_creatives response includes `name`, `status`, `created_date`, `updated_date` (currently stripped)
- **P0**: list_creatives response does NOT include `variants`, `variant_count` from delivery Creative
- **P1**: SyncCreativesRequest/Response schema compliance with 3.6.0
- **P1**: CreativeAsset schema validation against 3.6.0 core schema
- **P1**: CreativeAction enum values match 3.6.0 (created, updated, unchanged, failed, deleted)
- **P2**: Async response schemas (submitted/working/input-required) match 3.6.0

---

## Test Scenarios

### Main Flow (MCP): Sync Creatives via MCP Tool

Source: BR-UC-006-main-mcp.md

#### Scenario: Single static creative created successfully
**Obligation ID** UC-006-MAIN-MCP-01
**Layer** behavioral

**Given** the Buyer is authenticated with a valid principal_id
**And** the tenant exists and is resolvable from MCP session
**And** at least one creative agent is registered and reachable
**When** the Buyer invokes `sync_creatives` with one creative having a valid name, format_id, and assets
**Then** the response contains one creative result with `action=created`
**And** the creative is persisted in the database
**And** POST-S1 is satisfied: Buyer knows the creative was created
**And** POST-S2 is satisfied: action=created is reported
**Priority** P0 -- core happy path

#### Scenario: Multiple static creatives created in batch
**Obligation ID** UC-006-MAIN-MCP-02
**Layer** behavioral

**Given** the Buyer is authenticated
**And** the tenant exists
**When** the Buyer invokes `sync_creatives` with 5 valid creatives
**Then** the response contains 5 creative results, each with `action=created`
**And** all 5 creatives are persisted in the database
**Priority** P1 -- batch processing

#### Scenario: Existing creative updated (upsert by triple key)
**Obligation ID** UC-006-MAIN-MCP-03
**Layer** behavioral

**Given** the Buyer is authenticated with principal_id "P1"
**And** a creative with creative_id "C1" already exists for tenant "T1" and principal "P1"
**When** the Buyer invokes `sync_creatives` with creative_id "C1" and modified assets
**Then** the response contains one result with `action=updated`
**And** the existing creative's data is updated in the database
**Business Rule** BR-3: Existing creatives matched by tenant_id + principal_id + creative_id
**Priority** P1 -- upsert semantics

#### Scenario: Unchanged creative detection
**Obligation ID** UC-006-MAIN-MCP-04
**Layer** behavioral

**Given** a creative with creative_id "C1" exists with identical data
**When** the Buyer invokes `sync_creatives` with the same creative_id and identical content
**Then** the response contains one result with `action=unchanged`
**And** the database record is NOT modified
**Priority** P2 -- idempotency

#### Scenario: Per-creative savepoint isolation (lenient mode)
**Obligation ID** UC-006-MAIN-MCP-05
**Layer** behavioral

**Given** the Buyer sends 3 creatives: valid, invalid (bad format), valid
**And** validation_mode is lenient
**When** the system processes the batch
**Then** creative 1 gets `action=created`
**And** creative 2 gets `action=failed` with errors
**And** creative 3 gets `action=created` (NOT aborted by creative 2's failure)
**Business Rule** BR-RULE-033 INV-1: Per-creative failures are independent; other creatives continue
**Priority** P0 -- partial success

#### Scenario: Strict mode aborts on assignment error
**Obligation ID** UC-006-MAIN-MCP-06
**Layer** behavioral

**Given** the Buyer sends 2 creatives with assignments
**And** validation_mode is strict (default)
**And** the first assignment references a non-existent package
**When** the system processes assignments
**Then** a ToolError is raised
**And** all remaining assignments are aborted
**Business Rule** BR-RULE-033 INV-2: Strict mode aborts on assignment error
**Priority** P1 -- strict mode semantics

#### Scenario: Lenient mode continues on assignment error
**Obligation ID** UC-006-MAIN-MCP-07
**Layer** behavioral

**Given** the Buyer sends 2 creatives with assignments
**And** validation_mode is lenient
**And** the first assignment references a non-existent package
**When** the system processes assignments
**Then** the first assignment is recorded in `assignment_errors`
**And** the second assignment is processed normally
**Business Rule** BR-RULE-033 INV-3: Lenient mode logs warnings and continues
**Priority** P1 -- lenient mode semantics

#### Scenario: Default validation_mode is strict
**Obligation ID** UC-006-MAIN-MCP-08
**Layer** schema

**Given** the Buyer invokes `sync_creatives` without specifying validation_mode
**When** the system checks the validation_mode
**Then** it defaults to `strict`
**Business Rule** BR-RULE-033 INV-5: Default is strict
**Priority** P1 -- default contract

#### Scenario: Format registry pre-fetched once per sync operation
**Obligation ID** UC-006-MAIN-MCP-09
**Layer** behavioral

**Given** the Buyer sends multiple creatives referencing different formats
**When** the system processes the batch
**Then** the creative agent format registry is fetched once (step 4) and reused for all creatives
**And** individual creative validation does not trigger separate registry fetches
**Business Rule** Step 4: Format registry cached for duration of sync
**Priority** P2 -- performance optimization

#### Scenario: MCP response is valid SyncCreativesResponse
**Obligation ID** UC-006-MAIN-MCP-10
**Layer** behavioral

**Given** the Buyer invokes `sync_creatives` via MCP
**When** the system returns results
**Then** the MCP response contains a valid `SyncCreativesResponse` with per-creative results
**And** the response is parseable as JSON
**And** POST-S1, POST-S2 are satisfied
**Priority** P0 -- protocol correctness

---

### Main Flow (REST): Sync Creatives via A2A/REST

Source: BR-UC-006-main-rest.md

#### Scenario: Creatives synced via A2A endpoint
**Obligation ID** UC-006-MAIN-REST-01
**Layer** behavioral

**Given** the Buyer is authenticated via A2A context
**And** the tenant is resolvable
**When** the Buyer sends `sync_creatives` task via A2A protocol
**Then** the A2A task response contains a valid `SyncCreativesResponse` payload
**And** per-creative results are included
**Priority** P1 -- REST happy path

#### Scenario: Slack notification for require-human approval (REST)
**Obligation ID** UC-006-MAIN-REST-02
**Layer** behavioral

**Given** the tenant has `approval_mode=require-human` and a configured `slack_webhook_url`
**When** the Buyer syncs creatives via A2A
**Then** Slack notification is sent immediately for creatives needing approval
**Business Rule** BR-RULE-037 INV-3
**Priority** P2 -- notification integration

#### Scenario: AI review submission for ai-powered approval (REST)
**Obligation ID** UC-006-MAIN-REST-03
**Layer** behavioral

**Given** the tenant has `approval_mode=ai-powered`
**When** the Buyer syncs creatives via A2A
**Then** background AI review is submitted
**And** Slack notification is deferred until review completes
**Business Rule** BR-RULE-037 INV-4, INV-7
**Priority** P2 -- AI workflow

---

### Creative Schema Compliance (3.6 Upgrade -- CRITICAL)

These scenarios directly test the salesagent-goy2 fix.

#### Scenario: Creative extends correct listing base class (P0)
**Obligation ID** UC-006-CREATIVE-SCHEMA-COMPLIANCE-01
**Layer** schema

**Given** the salesagent `Creative` class in `src/core/schemas.py`
**When** inspected for its base class lineage
**Then** it extends the listing Creative (from `list_creatives_response`) NOT the delivery Creative (from `get_creative_delivery_response`)
**And** the listing Creative has fields: `account`, `assets`, `assignments`, `catalogs`, `created_date`, `creative_id`, `format_id`, `name`, `performance`, `status`, `sub_assets`, `tags`, `updated_date`
**Business Rule** AdCP schema compliance -- Critical Pattern #1
**Priority** P0 -- 3.6 upgrade blocker

#### Scenario: list_creatives response includes name field (P0)
**Obligation ID** UC-006-CREATIVE-SCHEMA-COMPLIANCE-02
**Layer** schema

**Given** a creative exists in the database with name "Test Banner Ad"
**When** `list_creatives` is called and the response is serialized
**Then** the `name` field is present in the serialized creative object
**And** the value is "Test Banner Ad"
**Business Rule** Listing Creative requires `name`; currently stripped via `exclude=True`
**Priority** P0 -- 3.6 regression fix

#### Scenario: list_creatives response includes status field (P0)
**Obligation ID** UC-006-CREATIVE-SCHEMA-COMPLIANCE-03
**Layer** schema

**Given** a creative exists with status "approved"
**When** `list_creatives` is called and the response is serialized
**Then** the `status` field is present in the serialized creative object
**And** the value is "approved"
**Business Rule** Listing Creative requires `status`; currently stripped via `exclude=True`
**Priority** P0 -- 3.6 regression fix

#### Scenario: list_creatives response includes created_date field (P0)
**Obligation ID** UC-006-CREATIVE-SCHEMA-COMPLIANCE-04
**Layer** schema

**Given** a creative exists with created_date "2026-01-15T10:00:00Z"
**When** `list_creatives` is called and the response is serialized
**Then** the `created_date` field is present in the serialized creative
**Business Rule** Listing Creative requires `created_date`; currently stripped via `exclude=True`
**Priority** P0 -- 3.6 regression fix

#### Scenario: list_creatives response includes updated_date field (P0)
**Obligation ID** UC-006-CREATIVE-SCHEMA-COMPLIANCE-05
**Layer** schema

**Given** a creative exists with updated_date "2026-02-20T14:30:00Z"
**When** `list_creatives` is called and the response is serialized
**Then** the `updated_date` field is present in the serialized creative
**Business Rule** Listing Creative requires `updated_date`; currently stripped via `exclude=True`
**Priority** P0 -- 3.6 regression fix

#### Scenario: list_creatives response does NOT include delivery-only fields (P0)
**Obligation ID** UC-006-CREATIVE-SCHEMA-COMPLIANCE-06
**Layer** schema

**Given** creatives exist in the database
**When** `list_creatives` is called and the response is serialized
**Then** the serialized creative does NOT contain `variants` field
**And** does NOT contain `variant_count` field
**And** does NOT contain `totals` field
**And** does NOT contain `media_buy_id` field (unless the listing schema includes it)
**Business Rule** Delivery Creative fields should not leak into listing response
**Priority** P0 -- 3.6 schema correctness

#### Scenario: Creative model_dump produces listing-schema-compliant JSON
**Obligation ID** UC-006-CREATIVE-SCHEMA-COMPLIANCE-07
**Layer** schema

**Given** a fully populated Creative instance
**When** `model_dump()` is called
**Then** the output validates against the adcp 3.6.0 `list-creatives-response` Creative sub-schema
**And** all required listing fields are present
**And** no delivery-only fields are included
**Priority** P0 -- schema contract (adcp compliance test)

#### Scenario: SyncCreativesResponse conforms to adcp 3.6.0 schema
**Obligation ID** UC-006-CREATIVE-SCHEMA-COMPLIANCE-08
**Layer** behavioral

**Given** a sync operation completes with mixed results (created, updated, failed)
**When** the response is serialized
**Then** the output validates against adcp 3.6.0 `sync-creatives-response.json` schema
**And** uses the discriminated union correctly (success variant vs error variant)
**Priority** P0 -- response schema compliance

#### Scenario: CreativeAction enum values match 3.6.0
**Obligation ID** UC-006-CREATIVE-SCHEMA-COMPLIANCE-09
**Layer** schema

**Given** the system reports creative actions
**When** any action is serialized
**Then** the action value is one of: `created`, `updated`, `unchanged`, `failed`, `deleted`
**And** these match the adcp 3.6.0 `creative-action.json` enum
**Priority** P1 -- enum compliance

#### Scenario: CreativeAsset schema accepts all 11 asset types
**Obligation ID** UC-006-CREATIVE-SCHEMA-COMPLIANCE-10
**Layer** behavioral

**Given** a creative submitted with assets of each type (image, video, audio, text, markdown, html, css, javascript, vast, daast, promoted_offerings, url, webhook)
**When** the system validates the creative
**Then** all asset types are accepted without validation errors
**Priority** P1 -- asset type coverage

---

### Cross-Principal Creative Isolation (BR-RULE-034)

#### Scenario: Creative lookup filters by triple key (INV-1)
**Obligation ID** UC-006-CROSS-PRINCIPAL-CREATIVE-01
**Layer** behavioral

**Given** principal "P1" has creative "C1" in tenant "T1"
**And** principal "P2" also has creative "C1" in tenant "T1"
**When** principal "P1" syncs creative "C1"
**Then** the system matches against P1's creative only
**And** P2's creative is not affected
**Business Rule** BR-RULE-034 INV-1: Always filters by tenant_id + principal_id + creative_id
**Priority** P0 -- security isolation

#### Scenario: Same creative_id under different principal creates new creative (INV-2)
**Obligation ID** UC-006-CROSS-PRINCIPAL-CREATIVE-02
**Layer** behavioral

**Given** principal "P1" has creative "C1" in tenant "T1"
**When** principal "P2" syncs creative "C1" in tenant "T1"
**Then** a NEW creative is created for principal "P2" (not P1's creative overwritten)
**And** the new creative is stamped with principal_id "P2"
**And** no error is raised about duplicate creative_id
**Business Rule** BR-RULE-034 INV-2: Silent creation, no cross-principal visibility
**Priority** P0 -- security: no information leakage

#### Scenario: New creative always stamped with authenticated principal_id (INV-3)
**Obligation ID** UC-006-CROSS-PRINCIPAL-CREATIVE-03
**Layer** behavioral

**Given** the Buyer is authenticated as principal "P1"
**When** the Buyer creates a new creative
**Then** the creative record in the database has principal_id = "P1"
**Business Rule** BR-RULE-034 INV-3
**Priority** P1 -- data integrity

---

### Creative Format Validation (BR-RULE-035)

#### Scenario: Missing format_id raises CREATIVE_FORMAT_REQUIRED (INV-1)
**Obligation ID** UC-006-CREATIVE-FORMAT-VALIDATION-01
**Layer** schema

**Given** a creative with format_id set to None
**When** the system validates the creative
**Then** a ValueError is raised: "Creative format is required"
**And** the per-creative result has action=failed
**Business Rule** BR-RULE-035 INV-1
**Priority** P1 -- validation

#### Scenario: Adapter format (non-HTTP agent_url) skips external validation (INV-2)
**Obligation ID** UC-006-CREATIVE-FORMAT-VALIDATION-02
**Layer** behavioral

**Given** a creative with format_id having agent_url "adapter://gam"
**When** the system validates the format
**Then** external creative agent validation is skipped entirely
**And** the creative is processed without contacting any external agent
**Business Rule** BR-RULE-035 INV-2: Non-HTTP agent_url = adapter format
**Priority** P2 -- adapter shortcut

#### Scenario: Unreachable creative agent returns retry suggestion (INV-3)
**Obligation ID** UC-006-CREATIVE-FORMAT-VALIDATION-03
**Layer** behavioral

**Given** a creative with format_id having agent_url "https://agent.example.com"
**And** the agent at that URL is unreachable (timeout, connection error)
**When** the system validates the format
**Then** a ValueError is raised with agent-unreachable message
**And** the suggestion includes "try again later"
**Business Rule** BR-RULE-035 INV-3
**Priority** P1 -- transient failure handling

#### Scenario: Reachable agent but unknown format returns discovery suggestion (INV-4)
**Obligation ID** UC-006-CREATIVE-FORMAT-VALIDATION-04
**Layer** behavioral

**Given** a creative with format_id having agent_url "https://agent.example.com" and id "nonexistent_format"
**And** the agent is reachable but does not have that format in its registry
**When** the system validates the format
**Then** a ValueError is raised with unknown-format message
**And** the suggestion includes "Use list_creative_formats to see available formats"
**Business Rule** BR-RULE-035 INV-4
**Priority** P1 -- discovery guidance

---

### Generative Creative Build (BR-RULE-036)

#### Scenario: Format with output_format_ids classified as generative (INV-1)
**Obligation ID** UC-006-GENERATIVE-CREATIVE-BUILD-01
**Layer** behavioral

**Given** a creative whose format has `output_format_ids` = ["banner_300x250"]
**When** the system processes the creative
**Then** it is classified as a generative creative
**And** Gemini API is used for building
**Business Rule** BR-RULE-036 INV-1
**Priority** P1 -- classification

#### Scenario: Prompt extracted from message asset role (INV-2)
**Obligation ID** UC-006-GENERATIVE-CREATIVE-BUILD-02
**Layer** behavioral

**Given** a generative creative with assets containing a `message` role text "Create a holiday banner"
**When** the system extracts the prompt
**Then** "Create a holiday banner" is used as the Gemini build prompt
**Business Rule** BR-RULE-036 INV-2: message > brief > prompt priority
**Priority** P2 -- prompt extraction

#### Scenario: Prompt extracted from brief asset role (INV-2)
**Obligation ID** UC-006-GENERATIVE-CREATIVE-BUILD-03
**Layer** behavioral

**Given** a generative creative with no `message` asset but a `brief` role text "Promote summer sale"
**When** the system extracts the prompt
**Then** "Promote summer sale" is used as the Gemini build prompt
**Business Rule** BR-RULE-036 INV-2: brief as second priority
**Priority** P2 -- prompt extraction fallback

#### Scenario: Prompt extracted from prompt asset role (INV-2)
**Obligation ID** UC-006-GENERATIVE-CREATIVE-BUILD-04
**Layer** behavioral

**Given** a generative creative with no `message` or `brief` assets but a `prompt` role text
**When** the system extracts the prompt
**Then** the prompt role text is used as the Gemini build prompt
**Business Rule** BR-RULE-036 INV-2: prompt as third priority
**Priority** P2 -- prompt extraction fallback

#### Scenario: Prompt from inputs[0].context_description (INV-3)
**Obligation ID** UC-006-GENERATIVE-CREATIVE-BUILD-05
**Layer** behavioral

**Given** a generative creative with no message/brief/prompt assets
**And** inputs[0].context_description = "Design for Q4 campaign"
**When** the system extracts the prompt
**Then** "Design for Q4 campaign" is used as the build prompt
**Business Rule** BR-RULE-036 INV-3
**Priority** P2 -- prompt extraction fallback

#### Scenario: Creative name as fallback prompt on create (INV-4)
**Obligation ID** UC-006-GENERATIVE-CREATIVE-BUILD-06
**Layer** behavioral

**Given** a NEW generative creative with no assets and no inputs
**And** the creative name is "Holiday Sale Banner"
**When** the system extracts the prompt
**Then** "Create a creative for: Holiday Sale Banner" is used as the build prompt
**Business Rule** BR-RULE-036 INV-4: Name fallback on create
**Priority** P2 -- prompt extraction last resort

#### Scenario: Update without prompt preserves existing data (INV-5)
**Obligation ID** UC-006-GENERATIVE-CREATIVE-BUILD-07
**Layer** behavioral

**Given** an EXISTING generative creative with previously generated content
**And** the update request has no prompt in assets or inputs
**When** the system processes the update
**Then** the generative build is SKIPPED
**And** existing creative data is preserved unchanged
**Business Rule** BR-RULE-036 INV-5: Updates without prompt preserve data
**Priority** P2 -- data preservation

#### Scenario: User assets take priority over generative output (INV-6)
**Obligation ID** UC-006-GENERATIVE-CREATIVE-BUILD-08
**Layer** behavioral

**Given** a generative creative with user-provided image assets AND a generative prompt
**When** the system processes the creative
**Then** user-provided assets are used (not overwritten by generative output)
**Business Rule** BR-RULE-036 INV-6
**Priority** P2 -- asset priority

---

### Creative Approval Workflow (BR-RULE-037)

#### Scenario: Auto-approve sets status=approved with no workflow steps (INV-2)
**Obligation ID** UC-006-CREATIVE-APPROVAL-WORKFLOW-01
**Layer** behavioral

**Given** the tenant has `approval_mode=auto-approve`
**When** a creative is synced
**Then** the creative status is set to `approved`
**And** no workflow steps are created
**And** no notifications are sent
**Business Rule** BR-RULE-037 INV-2
**Priority** P1 -- approval routing

#### Scenario: Require-human sets pending_review with immediate Slack (INV-3)
**Obligation ID** UC-006-CREATIVE-APPROVAL-WORKFLOW-02
**Layer** behavioral

**Given** the tenant has `approval_mode=require-human`
**And** the tenant has a configured `slack_webhook_url`
**When** a creative is synced
**Then** the creative status is set to `pending_review`
**And** workflow steps are created with step_type="creative_approval", owner="publisher", status="requires_approval"
**And** Slack notification is sent immediately
**Business Rule** BR-RULE-037 INV-3, INV-5, INV-6
**Priority** P1 -- human review workflow

#### Scenario: AI-powered sets pending_review with deferred Slack (INV-4)
**Obligation ID** UC-006-CREATIVE-APPROVAL-WORKFLOW-03
**Layer** behavioral

**Given** the tenant has `approval_mode=ai-powered`
**When** a creative is synced
**Then** the creative status is set to `pending_review`
**And** workflow steps are created
**And** background AI review is submitted
**And** Slack notification is NOT sent immediately (deferred until review completes)
**Business Rule** BR-RULE-037 INV-4, INV-7
**Priority** P1 -- AI review workflow

#### Scenario: Default approval_mode is require-human (INV-1)
**Obligation ID** UC-006-CREATIVE-APPROVAL-WORKFLOW-04
**Layer** behavioral

**Given** the tenant has no `approval_mode` setting
**When** a creative is synced
**Then** the system defaults to `require-human` behavior
**Business Rule** BR-RULE-037 INV-1
**Priority** P1 -- default contract

#### Scenario: Slack notification only sent when webhook configured (INV-6)
**Obligation ID** UC-006-CREATIVE-APPROVAL-WORKFLOW-05
**Layer** behavioral

**Given** the tenant has `approval_mode=require-human`
**And** the tenant has NO `slack_webhook_url` configured
**When** a creative is synced requiring approval
**Then** workflow steps are created
**But** no Slack notification is sent (no error raised)
**Business Rule** BR-RULE-037 INV-6: Only if slack_webhook_url is configured AND creatives need approval
**Priority** P2 -- graceful degradation

---

### Assignment Package Validation (BR-RULE-038)

#### Scenario: Package resolved by joining MediaPackage to MediaBuy with tenant filter (INV-1)
**Obligation ID** UC-006-ASSIGNMENT-PACKAGE-VALIDATION-01
**Layer** behavioral

**Given** a creative assignment references package_id "PKG-1"
**And** PKG-1 exists in a MediaBuy for the current tenant
**When** the system validates the assignment
**Then** the package is resolved successfully
**And** the assignment is created
**Business Rule** BR-RULE-038 INV-1
**Priority** P1 -- assignment happy path

#### Scenario: Package not found in strict mode raises ToolError (INV-2)
**Obligation ID** UC-006-ASSIGNMENT-PACKAGE-VALIDATION-02
**Layer** behavioral

**Given** a creative assignment references package_id "PKG-MISSING"
**And** validation_mode is strict
**When** the system validates the assignment
**Then** a ToolError is raised with code `PACKAGE_NOT_FOUND`
**And** all remaining assignments are aborted
**Business Rule** BR-RULE-038 INV-2 via BR-RULE-033 INV-2
**Priority** P1 -- strict assignment error

#### Scenario: Package not found in lenient mode logs warning (INV-2)
**Obligation ID** UC-006-ASSIGNMENT-PACKAGE-VALIDATION-03
**Layer** behavioral

**Given** a creative assignment references package_id "PKG-MISSING"
**And** validation_mode is lenient
**When** the system validates the assignment
**Then** the warning is logged
**And** the assignment is skipped
**And** assignment_errors includes the package_id with "Package not found"
**And** remaining assignments continue processing
**Business Rule** BR-RULE-038 INV-2 via BR-RULE-033 INV-3
**Priority** P1 -- lenient assignment error

#### Scenario: Idempotent upsert for duplicate assignment (INV-3)
**Obligation ID** UC-006-ASSIGNMENT-PACKAGE-VALIDATION-04
**Layer** behavioral

**Given** creative "C1" is already assigned to package "PKG-1"
**When** the Buyer syncs creative "C1" with assignment to package "PKG-1" again
**Then** the existing assignment is updated (weight reset to 100)
**And** no duplicate assignment record is created
**Business Rule** BR-RULE-038 INV-3
**Priority** P1 -- idempotent upsert

#### Scenario: Cross-tenant package isolation
**Obligation ID** UC-006-ASSIGNMENT-PACKAGE-VALIDATION-05
**Layer** behavioral

**Given** package "PKG-1" exists in tenant "T1" but not in tenant "T2"
**When** a Buyer in tenant "T2" tries to assign to "PKG-1"
**Then** the package is NOT found (tenant-scoped lookup)
**And** appropriate error is returned based on validation_mode
**Business Rule** BR-RULE-038 INV-1: filtered by tenant_id
**Priority** P1 -- security

---

### Assignment Format Compatibility (BR-RULE-039)

#### Scenario: Format compatible -- exact match after URL normalization (INV-1, INV-2)
**Obligation ID** UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-01
**Layer** behavioral

**Given** a creative has format_id with agent_url "https://agent.example.com/mcp/" and id "banner_300x250"
**And** the product has format_ids containing agent_url "https://agent.example.com" and id "banner_300x250"
**When** the system checks format compatibility
**Then** the formats match (after stripping trailing "/" and "/mcp")
**And** the assignment is created
**Business Rule** BR-RULE-039 INV-1, INV-2
**Priority** P1 -- URL normalization

#### Scenario: Format incompatible in strict mode raises ToolError
**Obligation ID** UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-02
**Layer** behavioral

**Given** a creative with format_id "video_preroll" assigned to a product that only accepts "banner_300x250"
**And** validation_mode is strict
**When** the system checks format compatibility
**Then** a ToolError is raised with code `FORMAT_MISMATCH`
**Business Rule** BR-RULE-039 INV-5 via BR-RULE-033 INV-2
**Priority** P1 -- strict format mismatch

#### Scenario: Format incompatible in lenient mode logs warning
**Obligation ID** UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-03
**Layer** behavioral

**Given** a creative with format_id "video_preroll" assigned to a product that only accepts "banner_300x250"
**And** validation_mode is lenient
**When** the system checks format compatibility
**Then** warning is logged
**And** assignment is skipped
**And** assignment_errors includes "Format mismatch"
**Business Rule** BR-RULE-039 INV-5 via BR-RULE-033 INV-3
**Priority** P1 -- lenient format mismatch

#### Scenario: Product with no format_ids allows all formats (INV-3)
**Obligation ID** UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-04
**Layer** behavioral

**Given** a creative with any format_id assigned to a product with empty format_ids
**When** the system checks format compatibility
**Then** the assignment is allowed (no format restriction)
**Business Rule** BR-RULE-039 INV-3: Empty format_ids = all formats allowed
**Priority** P1 -- open format policy

#### Scenario: Product format_ids accepts both "id" and "format_id" keys (INV-4)
**Obligation ID** UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-05
**Layer** behavioral

**Given** a product with format_ids entries using the key "id" (not "format_id")
**And** a creative with a matching format
**When** the system checks format compatibility
**Then** the match succeeds (system checks both key names)
**Business Rule** BR-RULE-039 INV-4
**Priority** P2 -- key flexibility

#### Scenario: Package without product_id skips format check (INV-6)
**Obligation ID** UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-06
**Layer** behavioral

**Given** a package that has no product_id associated
**When** a creative is assigned to that package
**Then** format compatibility check is skipped entirely
**And** the assignment proceeds
**Business Rule** BR-RULE-039 INV-6
**Priority** P2 -- null product handling

---

### Media Buy Status Transition on Assignment (BR-RULE-040)

#### Scenario: Draft media buy with approved_at transitions to pending_creatives (INV-1)
**Obligation ID** UC-006-MEDIA-BUY-STATUS-01
**Layer** behavioral

**Given** a media buy with status "draft" and approved_at = "2026-01-15T10:00:00Z"
**When** a creative is assigned to a package in that media buy
**Then** the media buy status transitions to "pending_creatives"
**Business Rule** BR-RULE-040 INV-1
**Priority** P1 -- lifecycle transition

#### Scenario: Draft media buy without approved_at does NOT transition (INV-2)
**Obligation ID** UC-006-MEDIA-BUY-STATUS-02
**Layer** behavioral

**Given** a media buy with status "draft" and approved_at = null
**When** a creative is assigned to a package in that media buy
**Then** the media buy remains in "draft" status
**Business Rule** BR-RULE-040 INV-2
**Priority** P1 -- guard condition

#### Scenario: Non-draft media buy does NOT transition (INV-3)
**Obligation ID** UC-006-MEDIA-BUY-STATUS-03
**Layer** behavioral

**Given** a media buy with status "active" (not "draft")
**When** a creative is assigned to a package in that media buy
**Then** the media buy status remains "active" (unchanged)
**Business Rule** BR-RULE-040 INV-3
**Priority** P1 -- guard condition

#### Scenario: Transition fires for both new and updated assignments (INV-4)
**Obligation ID** UC-006-MEDIA-BUY-STATUS-04
**Layer** behavioral

**Given** a media buy with status "draft" and approved_at set
**When** an existing creative assignment is updated (upsert)
**Then** the status transition check still fires
**And** the media buy transitions to "pending_creatives" if still in draft
**Business Rule** BR-RULE-040 INV-4
**Priority** P2 -- upsert trigger

---

### Extension A: Authentication Required

Source: BR-UC-006-ext-a.md

#### Scenario: Missing principal_id returns AUTH_REQUIRED error
**Obligation ID** UC-006-EXT-A-01
**Layer** behavioral

**Given** the Buyer sends `sync_creatives` without authentication context
**When** the system attempts to extract principal_id
**Then** the response is an error with code `AUTH_REQUIRED`
**And** the error message indicates authentication is required for creative sync
**And** the suggestion advises providing valid credentials
**And** POST-F1, POST-F2, POST-F3 are all satisfied
**Priority** P0 -- authentication gate

#### Scenario: AUTH_REQUIRED is an operation-level error (not per-creative)
**Obligation ID** UC-006-EXT-A-02
**Layer** behavioral

**Given** the Buyer sends `sync_creatives` without authentication
**When** the system responds
**Then** the error is operation-level (not per-creative results)
**And** no creatives are processed at all
**Priority** P1 -- error scope

---

### Extension B: Tenant Not Found

Source: BR-UC-006-ext-b.md

#### Scenario: Authentication present but tenant unresolvable
**Obligation ID** UC-006-EXT-B-01
**Layer** behavioral

**Given** the Buyer is authenticated (principal_id present)
**But** no tenant can be determined from the auth context
**When** the Buyer sends `sync_creatives`
**Then** the response is an error with code `TENANT_NOT_FOUND`
**And** the error message indicates no tenant context could be determined
**And** the suggestion advises verifying account configuration with the seller
**And** POST-F1, POST-F2, POST-F3 are all satisfied
**Priority** P1 -- tenant resolution failure

#### Scenario: TENANT_NOT_FOUND is an operation-level error
**Obligation ID** UC-006-EXT-B-02
**Layer** behavioral

**Given** tenant resolution fails
**When** the system responds
**Then** the error is operation-level
**And** no creatives are processed
**Priority** P1 -- error scope

---

### Extension C: Creative Validation Failed

Source: BR-UC-006-ext-c.md

#### Scenario: Invalid creative structure returns per-creative failure
**Obligation ID** UC-006-EXT-C-01
**Layer** schema

**Given** a creative with invalid structure (e.g., invalid field types, extra required fields missing)
**When** the system validates against CreativeAsset schema
**Then** the per-creative result has `action=failed`
**And** the errors array contains formatted validation error messages with field names
**And** POST-F1, POST-F2, POST-F3 are satisfied per-creative
**Business Rule** ext-c: Pydantic ValidationError
**Priority** P1 -- validation failure

#### Scenario: Validation failure in strict mode -- other creatives still processed
**Obligation ID** UC-006-EXT-C-02
**Layer** behavioral

**Given** creative 1 is invalid, creative 2 is valid
**And** validation_mode is strict
**When** the system processes the batch
**Then** creative 1 gets `action=failed`
**And** creative 2 is still processed (per-creative validation is always independent per BR-RULE-033 INV-1)
**Business Rule** BR-RULE-033 INV-1: Per-creative validation failures are always handled individually
**Priority** P1 -- strict mode does NOT abort on validation failure

#### Scenario: Validation failure in lenient mode -- other creatives still processed
**Obligation ID** UC-006-EXT-C-03
**Layer** behavioral

**Given** creative 1 is invalid, creative 2 is valid
**And** validation_mode is lenient
**When** the system processes the batch
**Then** creative 1 gets `action=failed`
**And** creative 2 is still processed
**Business Rule** BR-RULE-033 INV-1
**Priority** P1 -- lenient mode validation

---

### Extension D: Creative Name Empty

Source: BR-UC-006-ext-d.md

#### Scenario: Empty string name returns per-creative failure
**Obligation ID** UC-006-EXT-D-01
**Layer** behavioral

**Given** a creative with name = "" (empty string)
**When** the system validates the creative
**Then** the per-creative result has `action=failed`
**And** errors include "Creative name cannot be empty"
**Business Rule** ext-d
**Priority** P1 -- field validation

#### Scenario: Missing name field returns per-creative failure
**Obligation ID** UC-006-EXT-D-02
**Layer** behavioral

**Given** a creative with no name field at all
**When** the system validates the creative
**Then** the per-creative result has `action=failed`
**And** errors indicate name is missing/empty
**Priority** P1 -- field validation

---

### Extension E: Creative Format Required

Source: BR-UC-006-ext-e.md

#### Scenario: Missing format_id returns per-creative failure
**Obligation ID** UC-006-EXT-E-01
**Layer** schema

**Given** a creative with no format_id field
**When** the system validates the creative
**Then** the per-creative result has `action=failed`
**And** errors include "Creative format is required"
**And** suggestion includes "use list_creative_formats to discover available formats"
**Business Rule** ext-e, BR-RULE-035 INV-1
**Priority** P1 -- field validation with discovery guidance

---

### Extension F: Creative Format Unknown

Source: BR-UC-006-ext-f.md

#### Scenario: Unknown format_id returns per-creative failure with agent info
**Obligation ID** UC-006-EXT-F-01
**Layer** behavioral

**Given** a creative with format_id having agent_url "https://agent.example.com" and id "nonexistent_format"
**And** the agent is reachable but does not list that format
**When** the system validates the creative
**Then** the per-creative result has `action=failed`
**And** errors include "Unknown format 'nonexistent_format' from agent https://agent.example.com"
**And** suggestion includes "Use list_creative_formats to see available formats"
**Business Rule** ext-f, BR-RULE-035 INV-4
**Priority** P1 -- discovery guidance

---

### Extension G: Creative Agent Unreachable

Source: BR-UC-006-ext-g.md

#### Scenario: Unreachable agent returns per-creative failure with retry guidance
**Obligation ID** UC-006-EXT-G-01
**Layer** behavioral

**Given** a creative with format_id having agent_url "https://unreachable-agent.example.com"
**And** the agent is down or timing out
**When** the system validates the creative
**Then** the per-creative result has `action=failed`
**And** errors include "Cannot validate format ... Creative agent at ... is unreachable"
**And** suggestion includes "Please try again later"
**Business Rule** ext-g, BR-RULE-035 INV-3
**Priority** P1 -- transient failure with retry

---

### Extension H: Creative Preview Failed

Source: BR-UC-006-ext-h.md

#### Scenario: No previews and no media_url returns per-creative failure
**Obligation ID** UC-006-EXT-H-01
**Layer** behavioral

**Given** a static creative submitted to the creative agent for preview
**And** the agent returns no previews
**And** the creative has no user-provided media_url
**When** the system processes the creative
**Then** the per-creative result has `action=failed`
**And** errors include "No previews returned and no media_url provided"
**And** suggestion advises providing a media_url or verifying asset content
**Business Rule** ext-h
**Priority** P2 -- preview failure

#### Scenario: media_url fallback when no previews returned
**Obligation ID** UC-006-EXT-H-02
**Layer** behavioral

**Given** a static creative that the agent returns no previews for
**But** the creative has a user-provided media_url
**When** the system processes the creative
**Then** the creative is NOT failed
**And** the media_url is used as the preview fallback
**Priority** P2 -- fallback path

---

### Extension I: Gemini Key Missing

Source: BR-UC-006-ext-i.md

#### Scenario: Generative creative without GEMINI_API_KEY returns failure
**Obligation ID** UC-006-EXT-I-01
**Layer** behavioral

**Given** a creative with a generative format (output_format_ids present)
**And** the Seller Agent does NOT have GEMINI_API_KEY configured
**When** the system attempts to build the generative creative
**Then** the per-creative result has `action=failed`
**And** errors include "Cannot build generative creative: GEMINI_API_KEY not configured"
**And** suggestion advises contacting the seller or using static formats
**Business Rule** ext-i
**Priority** P2 -- infrastructure prerequisite

---

### Extension J: Package Not Found

Source: BR-UC-006-ext-j.md

#### Scenario: Non-existent package in strict mode -- operation-level error
**Obligation ID** UC-006-EXT-J-01
**Layer** behavioral

**Given** an assignment references package_id "PKG-GONE"
**And** "PKG-GONE" does not exist in any media buy
**And** validation_mode is strict
**When** the system processes assignments
**Then** an operation-level error with code `PACKAGE_NOT_FOUND` is returned
**And** the error message identifies the non-existent package_id
**And** the suggestion advises verifying package_id by checking existing media buys
**And** POST-F1, POST-F2, POST-F3 are satisfied
**Business Rule** ext-j, BR-RULE-038 INV-2
**Priority** P1 -- strict assignment error

#### Scenario: Non-existent package in lenient mode -- assignment_errors
**Obligation ID** UC-006-EXT-J-02
**Layer** behavioral

**Given** an assignment references package_id "PKG-GONE"
**And** validation_mode is lenient
**When** the system processes assignments
**Then** the per-creative result includes `assignment_errors: { "PKG-GONE": "Package not found" }`
**And** other valid assignments are processed
**Business Rule** ext-j, BR-RULE-038 INV-2
**Priority** P1 -- lenient assignment error

---

### Extension K: Format Mismatch

Source: BR-UC-006-ext-k.md

#### Scenario: Incompatible format in strict mode -- operation-level error
**Obligation ID** UC-006-EXT-K-01
**Layer** behavioral

**Given** an assignment links a creative with format "video_preroll" to a package whose product only accepts "banner_300x250"
**And** validation_mode is strict
**When** the system processes assignments
**Then** an operation-level error with code `FORMAT_MISMATCH` is returned
**And** the error message identifies the format incompatibility
**And** the suggestion advises using list_creative_formats or assigning to a different package
**And** POST-F1, POST-F2, POST-F3 are satisfied
**Business Rule** ext-k, BR-RULE-039 INV-5
**Priority** P1 -- strict format mismatch

#### Scenario: Incompatible format in lenient mode -- assignment_errors
**Obligation ID** UC-006-EXT-K-02
**Layer** behavioral

**Given** an assignment links a creative with incompatible format
**And** validation_mode is lenient
**When** the system processes assignments
**Then** the per-creative result includes `assignment_errors: { "PKG-ID": "Format mismatch" }`
**And** other valid assignments continue processing
**Business Rule** ext-k, BR-RULE-039 INV-5
**Priority** P1 -- lenient format mismatch

---

### Assignments Response Completeness (POST-S3, POST-S4)

#### Scenario: Successful assignment shows assigned_to array
**Obligation ID** UC-006-ASSIGNMENTS-RESPONSE-COMPLETENESS-01
**Layer** schema

**Given** a creative is synced with assignment to package "PKG-1"
**And** the package exists and format is compatible
**When** the system returns per-creative results
**Then** the creative result includes `assigned_to` array containing "PKG-1"
**Business Rule** POST-S3
**Priority** P1 -- assignment visibility

#### Scenario: Warnings included in per-creative results
**Obligation ID** UC-006-ASSIGNMENTS-RESPONSE-COMPLETENESS-02
**Layer** behavioral

**Given** a creative sync encounters non-fatal issues (e.g., lenient mode assignment warning)
**When** the system returns per-creative results
**Then** the creative result includes `warnings` array with descriptive messages
**Business Rule** POST-S4
**Priority** P2 -- warning visibility

#### Scenario: assignment_errors included in per-creative results
**Obligation ID** UC-006-ASSIGNMENTS-RESPONSE-COMPLETENESS-03
**Layer** behavioral

**Given** an assignment fails in lenient mode
**When** the system returns per-creative results
**Then** the creative result includes `assignment_errors` dict mapping package_id to error message
**Business Rule** POST-S4, BR-RULE-033 INV-4
**Priority** P1 -- error traceability

---

### Delete Missing (delete_missing flag)

#### Scenario: delete_missing=true archives creatives not in batch
**Obligation ID** UC-006-DELETE-MISSING-01
**Layer** behavioral

**Given** principal "P1" has creatives C1, C2, C3 in the tenant
**When** the Buyer syncs only [C1, C2] with `delete_missing=true`
**Then** C3 gets action=deleted (archived)
**And** C1 and C2 are processed normally (created/updated/unchanged)
**Business Rule** "Replace all creatives -- archive anything not in this batch" (user intent from overview)
**Priority** P2 -- delete semantics

#### Scenario: delete_missing=false (default) preserves unlisted creatives
**Obligation ID** UC-006-DELETE-MISSING-02
**Layer** behavioral

**Given** principal "P1" has creatives C1, C2, C3
**When** the Buyer syncs only [C1, C2] without delete_missing flag
**Then** C3 is NOT affected (remains as-is)
**And** only C1 and C2 are in the response
**Priority** P2 -- default preservation

---

### Dry Run (dry_run flag)

#### Scenario: dry_run=true validates without persisting
**Obligation ID** UC-006-DRY-RUN-01
**Layer** behavioral

**Given** the Buyer sends `sync_creatives` with `dry_run=true`
**When** the system processes the request
**Then** all validation and processing runs normally
**And** per-creative results show what WOULD happen (action=created, etc.)
**But** nothing is persisted to the database
**Priority** P2 -- dry run semantics

---

### creative_ids Scope Filter

#### Scenario: creative_ids limits which creatives in request are processed
**Obligation ID** UC-006-CREATIVE-IDS-SCOPE-01
**Layer** behavioral

**Given** the Buyer sends creatives [C1, C2, C3] with `creative_ids=[C1, C3]`
**When** the system processes the request
**Then** only C1 and C3 are processed
**And** C2 is ignored
**Priority** P3 -- scope filter

---

### Async Lifecycle

#### Scenario: Async submitted acknowledgment
**Obligation ID** UC-006-ASYNC-LIFECYCLE-01
**Layer** behavioral

**Given** the system supports async creative sync
**When** a sync operation is queued
**Then** a `SyncCreativesAsyncResponseSubmitted` is returned
**And** it conforms to the adcp 3.6.0 async-response-submitted schema
**Priority** P3 -- async protocol

#### Scenario: Async working progress
**Obligation ID** UC-006-ASYNC-LIFECYCLE-02
**Layer** behavioral

**Given** an async sync operation is in progress
**When** the Buyer checks status
**Then** a `SyncCreativesAsyncResponseWorking` is returned with progress information
**And** includes percentage, steps, and creatives processed counts
**Priority** P3 -- async protocol

#### Scenario: Async input required
**Obligation ID** UC-006-ASYNC-LIFECYCLE-03
**Layer** behavioral

**Given** an async sync operation requires Buyer input (approval, asset confirmation)
**When** the system pauses
**Then** a `SyncCreativesAsyncResponseInputRequired` is returned
**And** indicates what input is needed
**Priority** P3 -- async protocol

---

### Request Constraint Validation

#### Scenario: Request with zero creatives is rejected
**Obligation ID** UC-006-REQUEST-CONSTRAINT-VALIDATION-01
**Layer** schema

**Given** the Buyer sends `sync_creatives` with an empty creatives array
**When** the system validates the request
**Then** the request is rejected (minItems: 1 per schema)
**Business Rule** PRE-B2: At least one creative required
**Priority** P1 -- input constraint

#### Scenario: Request with more than 100 creatives is rejected
**Obligation ID** UC-006-REQUEST-CONSTRAINT-VALIDATION-02
**Layer** schema

**Given** the Buyer sends `sync_creatives` with 101 creatives
**When** the system validates the request
**Then** the request is rejected (creatives array 1-100 per schema)
**Priority** P2 -- input constraint
