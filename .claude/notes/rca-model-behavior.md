# RCA: Decisions about an object made outside the object

Repo: `.`, branch `feature/spec-gaps-1210`.
Pin: `adcp==6.6.0`, AdCP spec 3.1.1.
All line numbers verified at working-tree HEAD (`9585ace68` + working-tree edits) on 2026-08-19.

Method: AST scans (`ast.walk` over `src/`), not grep, for (1) class/method inventory,
(2) `Compare` nodes whose left operand is a model attribute and whose comparators are
literals, (3) functions annotated `-> bool` / `-> tuple[bool, ...]` taking a model
parameter. Scan scripts are in the session scratchpad.

---

## Section 1 — Model behavior inventory

### 1.1 Counts (AST, exact)

| Population | Classes | Zero-method classes |
|---|---|---|
| `src/core/schemas/**` | 154 | **100** |
| — of those, extending an SDK type (`Library*` / `AdCP*` alias) | 56 | 25 |
| — of those, local-only (`SalesAgentBaseModel` / `StrEnum` / `BaseModel`) | 98 | 75 |
| `src/core/database/models.py` (ORM) | 40 | **32** |

### 1.2 What the 104 schema methods actually are

| Category | Count |
|---|---|
| Serialization / repr (`__str__` 24, `model_dump` 11, `model_dump_internal` 10, `dict`, `_serialize*`, `__iter__`, `__repr__`) | **55** |
| Pydantic validators (`@field_validator` / `@model_validator`) | 21 |
| Everything else | 28 |

The 28 "everything else" methods, enumerated in full:

```
_base.py:373   CreateMediaBuySuccess.sync_success        @classmethod  (constructor)
_base.py:763   TaskStatus.from_operation_state           @classmethod  (constructor)
_base.py:999   Format.agent_url                          @property     (field accessor)
_base.py:1010  Format.get_primary_dimensions                           (field accessor)
_base.py:1044  Format.get_form_value                                   (field accessor)
_base.py:1478  Principal.get_adapter_id                                (dict lookup)
_base.py:1531  FormatId.get_dimensions                                 (field accessor)
_base.py:1541  FormatId.get_duration_ms                                (field accessor)
_base.py:1798  CreateMediaBuyRequest.flight_start_date    @property    (field alias)
_base.py:1807  CreateMediaBuyRequest.flight_end_date      @property    (field alias)
_base.py:1811  CreateMediaBuyRequest.get_total_budget                  (sum over packages)
_base.py:1824  CreateMediaBuyRequest.get_product_ids                   (projection)
_base.py:2137  UpdateMediaBuyRequest.has_updatable_fields              (PREDICATE)
_base.py:2160  UpdateMediaBuyRequest.flight_start_date    @property    (field alias)
_base.py:2168  UpdateMediaBuyRequest.flight_end_date      @property    (field alias)
_base.py:2407  Signal.pricing                             @property    (field accessor)
_base.py:2419  Signal.type                                @property    (field accessor)
_base.py:2502  ActivateSignalRequest.signal_id            @property    (field accessor)
capability_declarations.py:226 CapabilityDeclarations.from_tenant      @classmethod
capability_declarations.py:286 CapabilityDeclarations.validate_backing
capability_declarations.py:337 CapabilityDeclarations.emitted_specialisms
capability_declarations.py:341 CapabilityDeclarations.emitted_supported_protocols
capability_declarations.py:353 CapabilityDeclarations.emitted_experimental_features
creative.py:198  Creative.format                          @property    (field accessor)
creative.py:203  Creative.format_id_str                   @property    (field accessor)
creative.py:208  Creative.format_agent_url                @property    (field accessor)
creative.py:310  AddCreativeAssetsRequest.creatives       @property    (field alias)
delivery.py:350  GetMediaBuyDeliveryResponse.webhook_payload           (serialization)
```

### 1.3 The predicate count

Grep over `src/core/schemas/*.py` + `src/core/database/models.py` for
`def is_ / can_ / needs_ / has_ / matches`:

```
src/core/schemas/_base.py:2137     def has_updatable_fields(self) -> bool:
src/core/database/models.py:228    def is_gam_tenant(self) -> bool:          @property
src/core/database/models.py:2006   def is_production_strategy(self) -> bool: @property
```

**Three predicates across 194 model classes.** None of them are on `Creative`,
`MediaBuy`, `MediaPackage`, `Package`, `Product`, `Format`, or `FormatId`.

### 1.4 Zero-method models that matter most

ORM (`src/core/database/models.py`) — no methods at all:

- `Creative` (`:687`) — carries the `status` column at `:703` that four call sites write and eleven read
- `MediaBuy` (`:981`)
- `MediaPackage` (`:1174`)
- `WorkflowStep` (`:1887`)
- `CreativeAssignment` (`:791`)
- `CreativeReview` (`:736`)
- `PricingOption` (`:489`), `CurrencyLimit` (`:522`), `Account` (`:830`)

Schema (`src/core/schemas/`) — no methods at all, and directly implicated below:

- `MediaPackage` (`_base.py:1858`)
- `CreativePolicy` (`_base.py:1455`)
- `PricingParameters` (`_base.py:800`)
- `AffectedPackage` (`_base.py:565`)
- `CreativeApprovalStatus` (`creative.py:250`), `CreativeApproval` (`creative.py:761`)
- `SyncCreativesRequest` (`creative.py:326`)

### 1.5 Counter-examples — models that DO own behavior

Not everything is hollow. Three models carry real domain behavior:

- `Tenant.is_gam_tenant` (`models.py:227-232`) — docstring literally says
  *"This is the single source of truth for GAM tenant detection."* This is the
  shape the rest of the codebase does not have.
- `Product.effective_format_ids` / `effective_properties` / `effective_property_tags` /
  `effective_implementation_config` (`models.py:253` class; `effective_properties`
  body at `:400-430`) — resolves inventory-profile inheritance on the model.
- `CapabilityDeclarations` (`capability_declarations.py:207`) — `from_tenant`,
  `validate_backing`, `emitted_*`. The only schema class with a genuine behavioral surface.

---

## Section 2 — Externalized-predicate census

AST scan found **127** comparisons of a model attribute against a literal across `src/`.
Duplicate groups (same normalized predicate at 2+ sites) below. Groups where the left
operand is a *class* attribute (`GAMInventory.status`, `SyncJob.status`) are SQLAlchemy
query expressions, not instance predicates — listed separately at the end.

### 2.1 Instance-predicate duplicate groups

**DG-1 — "did this creative sync fail?" — 5 sites, expressed 2 ways (8 sites total)**

```
src/core/tools/media_buy_update.py:991           r.action == 'failed'
src/core/tools/creatives/_sync.py:234            update_result.action == 'failed'
src/core/tools/creatives/_sync.py:294            create_result.action == 'failed'
src/core/schemas/creative.py:524                 c.action == 'failed'
src/core/helpers/creative_helpers.py:581         r.action == 'failed'
src/core/tools/creatives/_sync.py:270            update_result.action != 'failed'
src/core/tools/creatives/_sync.py:323            create_result.action != 'failed'
src/core/helpers/creative_helpers.py:599         result.action != 'failed'
```
Model interrogated: `SyncCreativeResult` (`src/core/schemas/creative.py:369`).
Note `creative.py:524` is *inside the schemas module* — the response object counting
its own children by string-comparing their `action`, on a model whose own
`action` field is a `CreativeAction` StrEnum (comment at `creative.py:383-385`).
Equivalent method: `SyncCreativeResult.failed` / `.succeeded`.

**DG-2 — "is this creative unusable in a media buy?" — 2 sites**

```
src/core/tools/media_buy_create.py:396    hasattr(creative, "status") and creative.status in ("error", "rejected")
src/core/tools/media_buy_update.py:244    c.status in ('error', 'rejected')
```
Model: ORM `Creative` (`models.py:687`). `media_buy_create.py:396` guards with
`hasattr` on a column that is `nullable=False` — the same defend-where-it-exists
pattern the format ticket calls out. Both sites carry the tag `BR-RULE-026`.
Equivalent method: `Creative.is_terminally_rejected()`.

**DG-3 — "is this creative awaiting review?" — 4 sites**

```
src/core/database/queries.py:208                          Creative.status == 'pending_review'   (query)
src/core/tools/media_buy_create.py:1135                   creative.status == 'pending_review'
src/core/tools/media_buy_create.py:3932                   creative.status == 'pending_review'
src/admin/services/media_buy_readiness_service.py:132     c.status == 'pending_review'
```
Plus the writers: `_processing.py:290`, `:301`, `:928`, `:939`, `_sync.py:273`.
Equivalent method: `Creative.needs_approval()` — see Trace A.

**DG-4 — "is this creative approved?" — 3 sites, 2 spellings, plus a 2-site variant**

```
src/admin/blueprints/operations.py:409                creative.status != 'approved'
src/admin/blueprints/creatives.py:173                 c.status != 'approved'
src/services/media_buy_status_scheduler.py:190        creative.status != 'approved'
src/admin/blueprints/workflows.py:213                 c.status not in ['approved', 'active']
src/admin/blueprints/creatives.py:614                 c.status not in ['approved', 'active']
```
Two of these say a creative is unapproved unless `status == "approved"`; two say
unless `status in {"approved", "active"}`. They disagree about whether `"active"`
counts. Equivalent method: `Creative.is_approved()`.

**DG-5 — "is this media buy still a draft?" — 3 sites**

```
src/core/tools/media_buy_update.py:943         media_buy_obj.status == 'draft'
src/core/tools/media_buy_update.py:1179        media_buy_obj.status == 'draft'
src/core/tools/creatives/_assignments.py:316   mb_obj.status == 'draft'
```
Model: ORM `MediaBuy` (`models.py:981`, zero methods).
Equivalent method: `MediaBuy.is_draft()`.

**DG-6 — "is this media buy waiting on a human?" — 5 sites**

```
src/admin/blueprints/operations.py:390                media_buy.status == 'pending_approval'
src/admin/blueprints/operations.py:562                media_buy.status == 'pending_approval'
src/admin/blueprints/workflows.py:199                 media_buy.status == 'pending_approval'
src/admin/blueprints/workflows.py:40                  s.status == 'pending_approval'   (WorkflowStep)
src/admin/services/media_buy_readiness_service.py:265 media_buy.status == 'pending_approval'
```
Equivalent method: `MediaBuy.is_pending_approval()`.

**DG-7 — "did this media buy fail / is it live?" — 5 sites**

```
src/admin/services/media_buy_readiness_service.py:87   media_buy.status == 'failed'
src/admin/services/media_buy_readiness_service.py:258  media_buy.status == 'failed'
src/admin/services/dashboard_service.py:235            media_buy.status == 'completed'
src/admin/services/dashboard_service.py:239            media_buy.status == 'active'
src/admin/blueprints/workflows.py:48, :51              mb.status == 'active'
```

**DG-8 — "what kind of tenant route is this?" — 2 sites, verbatim pair**

```
src/app.py:598                          result.type == 'admin'
src/admin/blueprints/core.py:250        result.type == 'admin'
src/app.py:601                          result.type in ('custom_domain', 'subdomain')
src/admin/blueprints/core.py:255        result.type in ('custom_domain', 'subdomain')
```

**DG-9 — agent-task terminal states — 4 sites across two registries**

```
src/core/creative_agent_registry.py:430    result.status == 'completed'
src/core/creative_agent_registry.py:454    result.status == 'submitted'
src/core/creative_agent_registry.py:460    result.status == 'failed'
src/core/signals_agent_registry.py:175     result.status == 'completed'
src/core/signals_agent_registry.py:198     result.status == 'submitted'
```
Both registries hand-decode the same A2A task-state vocabulary independently.

**DG-10 — GAM inventory kind probes — 4 verbatim pairs in one file**

```
src/adapters/gam_inventory_discovery.py:829 / :908    k.type == 'PREDEFINED'
src/adapters/gam_inventory_discovery.py:830 / :909    k.type == 'FREEFORM'
src/adapters/gam_inventory_discovery.py:840 / :920    s.type == 'FIRST_PARTY'
src/adapters/gam_inventory_discovery.py:841 / :921    s.type == 'THIRD_PARTY'
src/adapters/gam_inventory_discovery.py:824 / :891    p.status == 'ACTIVE'
```

### 2.2 Functions taking a model and returning a bool (the "should have been a method" shape)

```
src/core/helpers/creative_helpers.py:411
    def validate_creative_format_against_product(
        creative_format_id: "FormatId", product: "Product | DBProduct"
    ) -> tuple[bool, str | None]
src/services/media_buy_status_scheduler.py:164
    def _are_creatives_approved(self, media_buy: MediaBuy, session) -> bool
src/core/tools/media_buy_delivery.py:932
    def _matches(buy: MediaBuy) -> bool
src/services/policy_check_service.py:166
    def check_product_eligibility(self, policy_result: PolicyCheckResult, product: Product) -> tuple[bool, str | None]
src/core/tools/creatives/_validation.py:144
    def check_provenance_required(creative: Creative, creative_policy: CreativePolicy | dict | None) -> str | None
src/core/helpers/adapter_helpers.py:334
    def resolve_manual_approval_signal(tenant: IdentityTenant | None) -> bool
src/core/billing_policy.py:41
    def resolve_account_sandbox(tenant: "TenantContext | Mapping[str, object] | None") -> bool
src/services/ai/factory.py:209
    def is_ai_enabled(self, tenant_ai_config: dict | TenantAIConfig | None) -> bool
```

Also the two that return `(result, bool)` where the bool is a decision about the
object just written — the pattern at the heart of Trace A:

```
src/core/tools/creatives/_processing.py:207  _update_existing_creative(...) -> tuple[SyncCreativeResult, bool]
src/core/tools/creatives/_processing.py:602  _create_new_creative(...)      -> tuple[SyncCreativeResult, bool]
```

### 2.3 Class-level (SQLAlchemy query) literal comparisons — NOT instance predicates

Recorded for completeness; these are `WHERE` clauses, correctly expressed:
`GAMInventory.status != 'STALE'` ×10 in `src/services/gam_inventory_service.py`
(`:881, :935, :948, :961, :974, :1029, :1340, :1351, :1378, :1388`);
`SyncJob.status == ...` ×8 across `src/admin/sync_api.py`, `src/adapters/gam/managers/sync.py`,
`src/services/background_sync_service.py`, `src/admin/blueprints/gam.py`;
`Creative.status == 'pending_review'` at `src/core/database/queries.py:208`;
`WebhookDeliveryLog.status == 'success'` at
`src/core/database/repositories/delivery.py:202` and
`src/services/delivery_webhook_scheduler.py:195` (a real duplicate — the repository
owns one copy, the scheduler bypasses it).

---

## Section 3 — The three deep traces

### Trace A — Creative approval: how many pieces of state say "needs approval"

**Answer: six, written by four writers, in an order that lets two of them disagree.**

| # | State | Declared at | Written by | Read by |
|---|---|---|---|---|
| 1 | `Creative.status` (DB column, `String(50)`, default `"pending_review"`) | `src/core/database/models.py:703` | `_processing.py:284, :290, :301` (update); `:911, :922, :928, :939` (create); `_sync.py:273` (provenance, update path only) | DG-3/DG-4 sites above; `media_buy_readiness_service.py:132`; `media_buy_create.py:1135, :3932` |
| 2 | `needs_approval` local `bool` | `_processing.py:281` (update), `:898` (create) — returned as tuple element 2 (`:207`, `:602` signatures) | `_processing.py:285, :291, :302, :923, :929, :940`; then re-assigned at `_sync.py:274` and `_sync.py:325` | `_sync.py:253`, `_sync.py:311` — **only once each, before the re-assignments** |
| 3 | `SyncCreativeResult.internal_status` (`str \| None`, `exclude=True`) | `src/core/schemas/creative.py:412` | `_processing.py:596` (update path, from `existing_creative.status`); `_processing.py:946` (create path, from `db_creative.status`) | `_sync.py:316` (create branch only) |
| 4 | membership in `creatives_needing_approval: list[dict]` | `_sync.py:130` | `_sync.py:267` (update), `_sync.py:320` (create) | `_sync.py:389` (workflow steps), `_sync.py:403` (Slack), `_sync.py:450` (response), `_sync.py:474-475` (message text) |
| 5 | `creative_info["status"]` — a *third* copy of #1, snapshotted into the dict | `_sync.py:258` (update: `existing_creative.status`), `_sync.py:316` (create: `create_result.internal_status`) | `_workflow.py:58-63` — re-derives the comment text by string-comparing this snapshot |
| 6 | `WorkflowStep` rows + `CreativeReview` rows | `models.py:1887`, `models.py:736` | `_create_sync_workflow_steps` (`_workflow.py`), async AI review | admin UI, `media_buy_readiness_service` |

**The exact interleaving that lets them disagree — update path, `_sync.py:219-276`:**

```
219   update_result, needs_approval = _update_existing_creative(...)
        └─ _processing.py:283-285  approval_mode=="auto-approve"
                                     existing_creative.status = "approved";  needs_approval = False
        └─ _processing.py:596      internal_status = existing_creative.status   # "approved"
        └─ _processing.py:598      returns (result, False)

253   if needs_approval:                       # False  -> block SKIPPED
254-267    creative_info = {... "status": existing_creative.status ...}
267        creatives_needing_approval.append(creative_info)      # NOT REACHED

269   if provenance_warning and update_result.action != "failed":
271       _append_warning(update_result, provenance_warning)
273       existing_creative.status = "pending_review"     # <-- state #1 flipped
274       needs_approval = True                            # <-- state #2 flipped

276   results.append(update_result)             # result.internal_status is still "approved"
                                                # loop iterates; needs_approval is overwritten at :219
```

Post-condition for an auto-approve tenant whose product sets
`creative_policy.provenance_required` (`_validation.py:144-173`) on a creative with
no provenance:

- DB `Creative.status` = `"pending_review"` (state #1)
- `SyncCreativeResult.internal_status` = `"approved"` (state #3) — snapshotted at
  `_processing.py:596`, 100+ lines and one function boundary before the flip at `_sync.py:273`
- `creatives_needing_approval` = does **not** contain the creative (state #4)
- therefore no `WorkflowStep`, no Slack notification, and the response message at
  `_sync.py:474-475` does not count it as requiring approval (states #5, #6)

The creative is parked in `pending_review` with nothing scheduled to review it.
`needs_approval = True` at `_sync.py:274` is a **dead write** — the variable is
re-bound at `_sync.py:219` on the next iteration and never read in between.

**The create path has the same shape with an additional asymmetry** (`_sync.py:311-325`):

```
311   if needs_approval:
316       "status": create_result.internal_status       # note: NOT db_creative.status
320       creatives_needing_approval.append(creative_info)

322   if provenance_warning and create_result.action != "failed":
324       _append_warning(create_result, provenance_warning)
325       needs_approval = True          # <-- dead write, AND no status write
```

The update branch writes `existing_creative.status = "pending_review"` at `:273`;
the create branch at `:322-325` does **not** write any status. So the same
provenance policy, applied to a new creative rather than an existing one, leaves
the DB row at whatever `approval_mode` chose (`"approved"` under auto-approve) —
a different outcome from the update path, from the same policy. Verified by
reading both branches; the create branch has no `db_creative.status` assignment
after `_processing.py:939`.

**Single source of truth, and where it lives:** the DB row. `Creative` is the ORM
model at `src/core/database/models.py:687` and has zero methods. The decision
"does this creative need approval" has three inputs — the tenant's `approval_mode`
(read at `_sync.py:136` from a `dict`), the product's `creative_policy.provenance_required`
(`_validation.py:157-167`), and the creative's own `provenance` field
(`_validation.py:169`) — and one output that must be written exactly once:
`Creative.status`. A `Creative.decide_review_status(approval_mode, creative_policy)`
that returns the status and is the only writer of the column removes states #2, #3, #5
entirely; #4 becomes `[c for c in creatives if c.needs_approval()]` computed after
all writes, not accumulated during them. See §5 for the layering caveat
(`approval_mode` and `creative_policy` are inputs, not model fields).

### Trace B — Format identity: the call-site guesses (ticket `salesagent-rtapr`)

The ticket's line numbers are from HEAD `2f989df54`; re-verified at working-tree HEAD:

**Guess 1 & 2 — verbatim duplicates, `_processing.py` (update path and create path):**

```python
# src/core/tools/creatives/_processing.py:315-324   (update)
            # Find matching format
            format_obj = None
            for fmt in all_formats:
                if fmt.format_id == creative_format:
                    format_obj = fmt
                    break

            if format_obj and format_obj.agent_url:
                # Check if format is generative (has output_format_ids)
                is_generative = bool(getattr(format_obj, "output_format_ids", None))
```
```python
# src/core/tools/creatives/_processing.py:643-652   (create)
            # Find matching format
            format_obj = None
            for fmt in all_formats:
                if fmt.format_id == creative_format:
                    format_obj = fmt
                    break

            if format_obj and format_obj.agent_url:
                # Check if format is generative (has output_format_ids)
                is_generative = bool(getattr(format_obj, "output_format_ids", None))
```
Byte-identical 10-line blocks. Both use a bare `==` on `FormatId` with **no URL
normalization at all**, and both probe declared fields defensively
(`getattr(..., "output_format_ids", None)`) while reading `format_obj.agent_url`
undefended — `agent_url` is declared on neither `adcp.types.Format` nor the local
subclass `src/core/schemas/_base.py:962`; the local `Format` exposes it only as a
`@property` at `_base.py:999`, which is what saves the read.

**Guess 3 — `_assignments.py:198-214`, the only site that normalizes:**

```python
                            # Allow /mcp URL variant (creative agent may return format with /mcp suffix)
                            def normalize_url(url: str | None) -> str | None:
                                if not url:
                                    return None
                                return url.rstrip("/").removesuffix("/mcp")

                            normalized_creative_url = normalize_url(creative_agent_url)
                            is_supported = False

                            for supported_url, supported_format_id in supported_formats:
                                normalized_supported_url = normalize_url(supported_url)
                                if (
                                    normalized_creative_url == normalized_supported_url
                                    and creative_format_id == supported_format_id
                                ):
                                    is_supported = True
                                    break
```
It also hand-builds the supported set at `_assignments.py:186-192`, reading
`fmt.get("id") or fmt.get("format_id")` from raw dicts.

**Guess 4 — `src/core/helpers/creative_helpers.py:453-470`, a SECOND normalization rule:**

```python
    # Helper to normalize URLs for comparison (strip trailing slashes)
    # Pydantic AnyUrl adds trailing slash when converting to string, causing mismatches
    def normalize_url(url_val: Any) -> str:
        if not url_val:
            return ""
        return str(url_val).rstrip("/")
```
Same question as guess 3, different rule: `rstrip("/")` only, no `/mcp` handling.
Called from `media_buy_create.py:3850` (`validate_creative_format_against_product`).
So guess 3 and guess 4 answer "is this creative's format accepted by this product"
with two rules that disagree on any `/mcp`-suffixed agent URL.

**Guess 5 (not in the ticket) — `src/core/format_resolver.py:88-90`, inside "the real resolver":**

```python
        all_formats = run_async_in_sync_context(registry.list_all_formats(tenant_id=tenant_id))
        for fmt in all_formats:
            if fmt.format_id == format_id:
                return fmt
```
Same bare `==`, comparing `Format.format_id` against a plain `str` parameter
(`get_format(format_id: str, ...)`, `:51-52`). Third normalization rule: none.

**What the SDK supplies and whether it is used — verified:**

`adcp/canonical_formats/identity.py:12`:
```python
def canonicalize_agent_url(raw: object) -> str:
    """Return ``raw`` with scheme + host lowercased and default port stripped.

    Per ``core/format-id.json`` (normative): callers MUST canonicalize
    ``agent_url`` before comparing two ``FormatId`` values for identity.
    Pydantic's ``AnyUrl`` does trailing-slash normalization but not
    RFC 3986 §6 host-casefolding or default-port stripping.
```

`adcp/canonical_formats/compat_helpers.py:83-109`:
```python
def formats_are_equivalent(
    a: str | FormatId | Mapping[str, Any],
    b: str | FormatId | Mapping[str, Any],
    *,
    default_agent_url: str = CANONICAL_CREATIVE_AGENT_URL,
) -> bool:
    ...
    left = upgrade_legacy_format_id(a, default_agent_url=default_agent_url)
    right = upgrade_legacy_format_id(b, default_agent_url=default_agent_url)
    if canonicalize_agent_url(left.agent_url) != canonicalize_agent_url(right.agent_url):
        return False
    if left.id != right.id:
        return False
    for field in ("width", "height", "duration_ms"):
        ...
```

`adcp/canonical_formats/compat_helpers.py:112-136`:
```python
def format_is_supported(
    requested: str | FormatId | Mapping[str, Any],
    supported: str | FormatId | Mapping[str, Any],
    *,
    default_agent_url: str = CANONICAL_CREATIVE_AGENT_URL,
) -> bool:
    """Return true when ``requested`` is acceptable for ``supported``.
    This is intentionally stricter than :func:`formats_are_equivalent`. ...
```

Usage in `src/`:

```
$ grep -rn "formats_are_equivalent\|format_is_supported\|canonicalize_agent_url" src/
(no matches)
```

**Zero.** The SDK ships the exact predicates — the SDK's own docstring says callers
*MUST* canonicalize — and all five call sites hand-roll it instead, three of them
with three different rules and two with no rule.

The SDK's `upgrade_legacy_format_id` is also re-implemented locally at
`src/core/format_cache.py:101-140` (converts `LibraryFormatId` → local `FormatId`
field by field, `:124-136`) rather than delegating to
`adcp.canonical_formats.compat_helpers.upgrade_legacy_format_id:48`. That local
re-implementation constructing the local subclass is the mechanism behind the
Pydantic `__eq__` type-mismatch the ticket documents.

### Trace C — Media buy / package validity: where the decision lives

**Answer: three different places, and it depends which question you ask.**

**C1 — request-shape validity: inline in `_impl`, ~110 lines.**
`_create_media_buy_impl` (`src/core/tools/media_buy_create.py:2008`) contains a
hand-numbered validation sequence at `:2192-2305`:

```
2192   try:
2193       # Validate input parameters
2194       # 1. Budget validation (shared validator)
2195       total_budget = req.get_total_budget()
2196       budget_err = validate_budget_positive(total_budget, field=package_field_path("budget"))
...
2205       # 2. DateTime validation
2206       now = datetime.now(UTC)
2209       if req.start_time is None:
2211           raise AdCPValidationError("start_time is required")
2215       raw_start_time = req.start_time.root
2216       if raw_start_time == "asap": ...
2234       if computed_start_time < now:
2238           raise AdCPInvalidRequestError(...)
2245       if req.end_time is None:
2247           raise AdCPValidationError("end_time is required")
2254       if computed_end_time <= computed_start_time:
2260           raise AdCPInvalidRequestError(...)
...
2275       # 3. Package/Product validation
2281       if not product_ids:
2283           raise AdCPValidationError("At least one product is required.")
2288       if not package.product_id:
2290           raise AdCPValidationError("Package must specify product_id.")
2292       # Check for duplicate product_ids across packages
2298       duplicate_products = [pid for pid, count in product_id_counts.items() if count > 1]
2299       if duplicate_products:
2301           raise AdCPValidationError(...)
...
2306       # 4. Currency-specific budget validation
```

Every one of steps 2 and 3 is decidable from `CreateMediaBuyRequest` alone —
no tenant, no adapter, no DB. The model already has `get_total_budget` (`:1811`)
and `get_product_ids` (`:1824`) but no validator for any of them.
`CreateMediaBuyRequest` (`src/core/schemas/_base.py:1740`) has validators
`_check_idempotency_key` and `validate_timezone_aware` only. The SDK parent
`adcp.types.generated_poc.media_buy.create_media_buy_request.CreateMediaBuyRequest`
is a generated DTO with `model_config = ConfigDict(extra='allow')` and no validators —
confirmed by reading the source; the SDK does not supply these either.

Equivalent: `@model_validator(mode="after")` on `CreateMediaBuyRequest` for
end>start, non-empty products, per-package `product_id`, and duplicate `product_id` —
turning four `AdCPValidationError` raises inside a 2600-line `_impl` into
model construction failures.

Step 1 is the counter-example: `validate_budget_positive` is a shared validator
called from a single site, correctly extracted.

**C2 — pricing-model compatibility: a helper function, `_impl`-side.**

`src/core/tools/media_buy_create.py:1374-1378`:
```python
def _validate_pricing_model_selection(
    package: Package | PackageRequest | AdcpPackageRequest,
    product: Any,  # ProductModel from database
    campaign_currency: str | None,
) -> dict[str, Any]:
```
Returns a `dict[str, Any]` documented at `:1387-1394` as
`{"pricing_model", "rate", "currency", "is_fixed", "bid_price"}` — an untyped
bag reconstructed from a `PricingOption` that already exists as both a schema
(`_base.py:840`) and an ORM model (`models.py:489`). It reaches into
`product.pricing_options`, unwraps RootModels with a local `unwrap_option`
closure (`:1422-1423`), and raises `AdCPValidationError` at `:1411, :1472, :1480,
:1494, :1501, :1514`. Its docstring at `:1397` still says `Raises: ToolError`.

**C3 — line-item-type selection: correctly encapsulated in the adapter.**

`src/adapters/gam/pricing_compatibility.py` is the one place in this whole survey
that gets it right:
```python
:52   def is_compatible(cls, line_item_type: LineItemType, pricing_model: PricingModel) -> bool:
:68   def get_compatible_line_item_types(cls, pricing_model: PricingModel) -> set[str]:
:88   def select_line_item_type(cls, pricing_model, is_guaranteed=False, override_type=None) -> LineItemType:
:134  def get_gam_cost_type(cls, pricing_model: PricingModel) -> str:
:155  def get_default_priority(cls, line_item_type: LineItemType) -> int:
```
backed by a declared `COMPATIBILITY_MATRIX` and `ADCP_TO_GAM_COST_TYPE` table
(`:~30-49`). One class, one owner, tested decision tree at `:113-130`.

**But it is bypassed.** `src/adapters/gam/managers/orders.py` re-decides the same
things inline:
```
orders.py:801    if pricing_model == "flat_rate":
orders.py:826    if line_item_type == "SPONSORSHIP":
orders.py:832        if pricing_model == "flat_rate":
orders.py:840    elif line_item_type == "STANDARD":
orders.py:849        if pricing_model == "cpc":
orders.py:853        elif pricing_model == "vcpm":
orders.py:1134   if pricing_model in ["cpm", "vcpm"]:
orders.py:1137   elif pricing_model == "cpc":
orders.py:1140   elif pricing_model == "flat_rate":
```
So the pattern here is not "no owner" — it is "an owner exists and the call site
does not ask it". Which is the same failure mode with a different cause.

**C4 — "is this media buy ready?": externalized into an admin service.**

`src/admin/services/media_buy_readiness_service.py` — `_compute_state` (`:234`)
decides a `MediaBuy`'s readiness by string-comparing its status (`:258, :261, :265`),
counting creative statuses (`:131-133`), and reading GAM order/line-item statuses
(`:172, :174, :182`). It reads a `MediaBuy`, five `Creative`s, a `GAMOrder` and
N `GAMLineItem`s. `MediaBuy` (`models.py:981`) has zero methods.

Independently, `src/services/media_buy_status_scheduler.py:164-193`
(`_are_creatives_approved(self, media_buy: MediaBuy, session)`) answers a subset of
the same question with its own query and its own `creative.status != "approved"`
loop at `:189-191`. Two owners, two answers.

---

## Section 4 — SDK behaviors we are not using

Verified by reading the installed `adcp==6.6.0` at
`.venv/lib/python3.12/site-packages/adcp/`.

| SDK behavior | Location | Used in `src/`? | What we do instead |
|---|---|---|---|
| `canonicalize_agent_url(raw)` — docstring: *"callers MUST canonicalize agent_url before comparing two FormatId values for identity"*, per normative `core/format-id.json` | `adcp/canonical_formats/identity.py:12` | **No — 0 hits** | 3 hand-rolled rules (`_assignments.py:199-202`, `creative_helpers.py:455-458`) and 2 sites with no rule (`_processing.py:318`, `:646`, `format_resolver.py:89`) |
| `formats_are_equivalent(a, b)` — upgrades both sides, canonicalizes URLs, treats omitted params as wildcards | `adcp/canonical_formats/compat_helpers.py:83` | **No — 0 hits** | bare `==` on Pydantic models whose `__eq__` compares `self_type == other_type` first |
| `format_is_supported(requested, supported)` — *"intentionally stricter... a fixed supported product format requires the request to provide and match every fixed parameter"* | `adcp/canonical_formats/compat_helpers.py:112` | **No — 0 hits** | `validate_creative_format_against_product` (`creative_helpers.py:411`) + the inline loop at `_assignments.py:207-214`, neither of which handles width/height/duration_ms |
| `upgrade_legacy_format_id(value, *, default_agent_url)` | `adcp/canonical_formats/compat_helpers.py:48` | **No** — the name is imported nowhere from the SDK | re-implemented at `src/core/format_cache.py:101-140`, constructing the local `FormatId` subclass field-by-field (`:124-136`) |
| `adcp.server.idempotency` (`canonical_json_sha256`, `strip_excluded_fields`, `EXCLUDED_FIELDS`) | `adcp/server/idempotency.py` | **Yes** — `src/core/idempotency_canonical.py:34-36` | correctly delegated; the module docstring at `:9` even explains the delegation. This is the shape the format code should have |
| `adcp.server.helpers` (`STANDARD_ERROR_CODES`, `adcp_error`) | — | **Yes** — `src/core/exceptions.py:16` | correctly delegated |
| `CreateMediaBuyRequest` field validators | `adcp/types/generated_poc/media_buy/create_media_buy_request.py` | n/a | **The SDK supplies none.** Generated DTO, `ConfigDict(extra='allow')`, zero validators. The end>start / non-empty-products / duplicate-product checks in Trace C1 are genuinely ours to write — and belong on our `CreateMediaBuyRequest` subclass, not in `_impl` |
| `adcp.validation.*` (`schema_validator`, `envelope.detect_wire_version`, `legacy.validate_product`) | `adcp/validation/` | partially — `src/core/validation_helpers.py:28` wraps Pydantic errors; `legacy.validate_product` unused | unverified whether `legacy.validate_product` is applicable to our Product shape |

---

## Section 5 — Layer assignment: what actually belongs on the model

I disagree with the framing in five places. Sorted:

### 5A — Belongs on the model (the model already holds every input)

| Finding | Method it should be | Why |
|---|---|---|
| DG-1 (`SyncCreativeResult.action == 'failed'` ×8) | `SyncCreativeResult.failed` property | `action` is a field on the model; one of the 8 sites is already inside `schemas/creative.py:524` |
| DG-2 (`creative.status in ('error','rejected')` ×2) | `Creative.is_terminally_rejected()` | `status` is the only input |
| DG-4 (`status != 'approved'` / `not in ['approved','active']` ×5) | `Creative.is_approved()` | `status` is the only input; the two spellings disagree today |
| DG-5 (`media_buy.status == 'draft'` ×3) | `MediaBuy.is_draft()` | `status` is the only input |
| DG-6 (`status == 'pending_approval'` ×5) | `MediaBuy.is_pending_approval()` | `status` is the only input |
| Trace C1 steps 2 & 3 (`media_buy_create.py:2205-2305`) | `@model_validator(mode="after")` on `CreateMediaBuyRequest` (`_base.py:1740`) | end>start, non-empty products, per-package `product_id`, duplicate `product_id` are all decidable from the request alone. `now` for the past-start check is the one impurity — see 5C |
| Trace B guesses 1–5 | delegate to `adcp.canonical_formats` | the SDK owns it and we ship it; see §4 |

### 5B — Belongs on a domain service / helper, NOT the model — but must have exactly one owner

| Finding | Where it belongs | Why not the model |
|---|---|---|
| Trace A: "does this creative need approval" | a function of `(creative, approval_mode, creative_policy)` whose *only* output is the value written to `Creative.status` | `approval_mode` is tenant config (`_sync.py:136`) and `creative_policy` is product config — neither is a `Creative` field. But there must be **one** writer, not the current five (`_processing.py:284, :290, :301, :922, :928, :939` + `_sync.py:273`), and **one** derived read, not the current four (`needs_approval`, `internal_status`, list membership, `creative_info["status"]`) |
| Trace C4: media-buy readiness | `MediaBuyReadinessService` — where it already is | it aggregates `Creative`, `GAMOrder`, `GAMLineItem` rows the `MediaBuy` does not hold. The defect is not its location, it is that `media_buy_status_scheduler.py:164` answers a subset independently |
| `validate_creative_format_against_product` (`creative_helpers.py:411`) | a helper is right; it should call `format_is_supported` | it spans two aggregates (creative + product) |
| `check_provenance_required` (`_validation.py:144`) | correct as a helper | takes `creative` **and** `creative_policy` |
| DG-9 (agent-task states, 2 registries) | one shared A2A task-state decoder | it decodes a remote protocol vocabulary, not our domain |

### 5C — Legitimately outside the model; the framing does not apply

| Finding | Reason |
|---|---|
| `computed_start_time < now` (`media_buy_create.py:2234`) | depends on wall-clock at validation time. A model validator would make request objects un-round-trippable in tests. Belongs where it is, or behind an injected clock |
| Trace C3 `GAMPricingCompatibility` (`src/adapters/gam/pricing_compatibility.py`) | GAM line-item types are an ad-server concept; putting `SPONSORSHIP`/`PRICE_PRIORITY` on an AdCP `Package` would leak adapter vocabulary into the protocol layer. **The class is correctly placed and correctly designed.** The defect is only that `orders.py:801-853, :1134-1140` re-decides instead of calling it |
| DG-10 (`gam_inventory_discovery.py` GAM `type`/`status` probes) | GAM API response shapes, not our models. The duplication (`:824/:891`, `:829/:908`, …) is a DRY defect within the adapter, not a layering one |
| `resolve_manual_approval_signal` (`adapter_helpers.py:334`), `resolve_account_sandbox` (`billing_policy.py:41`), `is_ai_enabled` (`services/ai/factory.py:209`) | all take tenant *config* (often a `Mapping`, not a model) and answer a deployment question. Application layer, correctly |
| DG-8 (`result.type == 'admin'`, `app.py:598` / `core.py:250`) | routing-resolution result, not a domain model. Duplicate pair worth extracting, but it is a transport concern |
| §2.3 class-level `Model.column == literal` | SQLAlchemy `WHERE` clauses. **Not** externalized predicates. Only exception: `WebhookDeliveryLog.status == 'success'` duplicated between `repositories/delivery.py:202` and `delivery_webhook_scheduler.py:195` — the scheduler bypasses the repository |
| `Creative.status` as a `String(50)` with no CHECK constraint | deliberate, and documented at `models.py:698-702`: the spec enum widens over time and DDL would make a spec bump a boot-blocking migration. Do not "fix" this into a PG enum |

### 5D — The one structural observation the numbers support

Of 104 methods across 154 schema classes, **55 are serialization** and 21 are
Pydantic validators. Three predicates exist across all 194 model classes
(§1.3), none on `Creative`, `MediaBuy`, `Package`, `Product`, `Format`, or `FormatId`.
The models in this codebase are serialization contracts, not domain objects —
so a call site that needs to know something about an object has no method to
call and writes a comparison instead. That is the mechanism; §5A/§5B/§5C are
which of those comparisons are worth moving.

---

## Unverified

- Whether `adcp.validation.legacy.validate_product` (`adcp/validation/legacy.py:212`)
  applies to our `Product` shape — not inspected.
- Whether the Trace-A disagreement is reachable in production: it requires a tenant
  with `approval_mode == "auto-approve"` AND a product with
  `creative_policy.provenance_required` AND a creative with no `provenance`. The code
  path is verified; the config combination's existence in any deployment is not.
- Whether any test currently grades the create-vs-update provenance asymmetry
  (`_sync.py:273` writes status, `_sync.py:325` does not) — not searched.
- `.beads` ticket `salesagent-rtapr` line references are from HEAD `2f989df54`;
  I re-verified the four call sites at working-tree HEAD and found a fifth
  (`format_resolver.py:89`) not listed in the ticket.
