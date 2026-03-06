# Code Review: Schema & Model Layer

## Summary

Reviewing the schema and model layer changes on branch `KonstantinMirin/adcp-v3-upgrade` for the adcp 3.2.0 to 3.6.0 migration. The diff spans `src/core/schemas.py`, `src/core/database/models.py`, `src/core/product_conversion.py`, `src/core/resolved_identity.py`, `src/core/version_compat.py`, `src/core/schema_helpers.py`, and `alembic/versions/add_adcp36_product_fields.py`.

The migration correctly adds 6 new product columns to the database (fixing `salesagent-qo8a`), introduces a clean `ResolvedIdentity` abstraction, and implements v2/v3 compat shims in `version_compat.py`. The `product_conversion.py` roundtrip for all 6 new adcp 3.6.0 fields is correct.

However, two critical pre-existing bugs are confirmed still present: the `Creative` base class mismatch (`salesagent-goy2`) and the `_get_pricing_options` int-vs-string PK lookup (`salesagent-mq3n`). Additionally, three new issues were found during review: the ORM `CheckConstraint` in `models.py` does not match the actual database constraint, `schema_helpers.create_get_products_request()` passes a `BrandReference` object to a `dict`-typed field, and `PricingOption.model_dump_internal()` silently discards any `exclude` set passed by the caller.

## Files Reviewed

| File | Change |
|---|---|
| `src/core/schemas.py` | 337 lines changed -- Pydantic models |
| `src/core/database/models.py` | 50 lines added -- SQLAlchemy models |
| `src/core/product_conversion.py` | 14 lines added -- DB-to-schema conversion |
| `src/core/resolved_identity.py` | 221 lines -- new file |
| `src/core/version_compat.py` | 51 lines -- new file |
| `src/core/schema_helpers.py` | 56 lines changed -- schema helpers |
| `alembic/versions/add_adcp36_product_fields.py` | Migration file |

## Critical Issues

### [CRITICAL-1]: `Creative` extends delivery `Creative`, not a library entity model (salesagent-goy2 -- confirmed)

**Confidence: 100**

**File:** `src/core/schemas.py:14,1655`

**Issue:** `LibraryCreative` is imported as:

```python
from adcp.types import Creative as LibraryCreative
```

In adcp 3.6.0, `adcp.types.Creative` resolves via `adcp/types/_generated.py:551` to:

```python
from adcp.types.generated_poc.creative.get_creative_delivery_response import (
    Creative,
    ...
)
```

This `Creative` is a delivery response model -- it represents a creative in a delivery metrics report, not a creative entity. Its schema is:

```python
class Creative(AdCPBaseModel):
    creative_id: str                        # required
    variants: list[CreativeVariant]         # required
    format_id: FormatId | None = None       # optional
    media_buy_id: str | None = None         # optional
    totals: DeliveryMetrics | None = None   # optional
    variant_count: int | None = None        # optional
```

This has no `name`, `status`, `created_date`, `updated_date` fields. The salesagent `Creative` class adds these as `exclude=True` internal fields (lines 1686-1695), which means they are stripped from all AdCP responses. Any buyer who calls `list_creatives` or `sync_creatives` gets back responses where the creative name, status, and timestamps are always absent.

**Impact:**
- `list_creatives` returns creatives with no `name`, `status`, `created_date`, `updated_date` in the payload -- these are standard fields buyers need to track creative approval state.
- Any buyer agent trying to check whether a creative was approved (`status`) by parsing the response will receive empty/missing data.
- `variants=[]` is hardcoded throughout listing code (`src/core/tools/creatives/listing.py:367`, `src/core/tools/creatives/_validation.py:60`) because no real variants exist in the DB -- but the field is required by the delivery `Creative` base class, so empty array is passed to satisfy validation.

**Fix:** The correct import for an entity-level Creative does not exist in adcp 3.6.0's stable types because the library redesigned the creative model. Two options:

Option A -- use the delivery model correctly and stop treating `Creative` as an entity (Wave 1 refactor per the comment in the code):
```python
# Remove entity-level Creative usage from list_creatives / sync_creatives responses
# Return CreativeAsset or a local entity type for internal workflow, convert at boundary
```

Option B -- define a local entity model and stop inheriting from the delivery model:
```python
class Creative(SalesAgentBaseModel):
    """Internal creative entity -- NOT extending adcp delivery Creative."""
    creative_id: str
    name: str
    status: CreativeStatus
    format_id: LibraryFormatId | None = None
    created_date: datetime | None = None
    updated_date: datetime | None = None
    principal_id: str | None = Field(default=None, exclude=True)
    assets: dict[str, Any] | None = Field(default=None, exclude=True)
```

---

### [CRITICAL-2]: `_get_pricing_options` queries integer PK with string IDs (salesagent-mq3n -- confirmed)

**Confidence: 100**

**File:** `src/core/tools/media_buy_delivery.py:645-649`

**Issue:** The function queries the database using `PricingOption.id` (an integer auto-increment primary key) with string values like `"cpm_usd_fixed"`:

```python
def _get_pricing_options(pricing_option_ids: list[Any | None]) -> dict[str, PricingOption]:
    with get_db_session() as session:
        statement = select(PricingOption).where(PricingOption.id.in_(pricing_option_ids))
        pricing_options = session.scalars(statement).all()
        return {str(pricing_option.id): pricing_option for pricing_option in pricing_options}
```

`pricing_option_ids` are populated from `buy.raw_request.get("pricing_option_id")` -- strings in the format `"cpm_usd_fixed"`. `PricingOption.id` is an `Integer` PK. The `IN` clause comparing an integer column to string values will always return zero rows. PostgreSQL silently returns nothing rather than erroring when the type cast fails in some contexts, or raises a `DataError` in others.

The return dict is then keyed on `str(pricing_option.id)` (the integer), but callers look up by string pricing_option_id (`"cpm_usd_fixed"`). Even if the query somehow returned rows, the key mismatch would prevent lookup.

**Impact:** Every call to `get_media_buy_delivery` that needs pricing option context returns `None` for all pricing options. Delivery metrics responses are returned without pricing context, silently degrading report accuracy.

**Fix:** Query by the string `pricing_option_id` field stored in the `raw_request` JSON. The `PricingOption` DB model does not have a `pricing_option_id` string column -- this needs to be either added as a column or looked up differently. The simplest fix using existing data:

```python
def _get_pricing_options(pricing_option_ids: list[str | None]) -> dict[str, Any]:
    """Look up pricing options from raw_request data, not by DB PK."""
    # pricing_option_id is a constructed string like "cpm_usd_fixed"
    # The PricingOption table stores pricing_model, currency, is_fixed
    # Reconstruct the lookup from those fields
    clean_ids = [p for p in pricing_option_ids if p is not None]
    if not clean_ids:
        return {}
    with get_db_session() as session:
        # pricing_option_id format: "{pricing_model}_{currency_lower}_{fixed|auction}"
        results = {}
        for po_id in clean_ids:
            parts = po_id.rsplit("_", 1)  # e.g. "cpm_usd_fixed" -> ["cpm_usd", "fixed"]
            if len(parts) != 2:
                continue
            is_fixed = parts[1] == "fixed"
            model_currency = parts[0].rsplit("_", 1)  # "cpm_usd" -> ["cpm", "usd"]
            if len(model_currency) != 2:
                continue
            pricing_model, currency = model_currency
            stmt = select(PricingOption).where(
                PricingOption.pricing_model == pricing_model,
                PricingOption.currency == currency.upper(),
                PricingOption.is_fixed == is_fixed,
            )
            po = session.scalars(stmt).first()
            if po:
                results[po_id] = po
        return results
```

## High Issues

### [HIGH-1]: ORM `CheckConstraint` does not match actual database constraint

**Confidence: 90**

**File:** `src/core/database/models.py:483-490`

**Issue:** The `Product` model defines:

```python
CheckConstraint(
    "(properties IS NOT NULL AND property_tags IS NULL) OR (properties IS NULL AND property_tags IS NOT NULL)",
    name="ck_product_properties_xor",
)
```

This is a two-field XOR covering only `properties` and `property_tags`. However, the actual database has a three-field XOR constraint named `ck_product_authorization` (added by migration `3d2f7ff99896`):

```sql
(properties IS NOT NULL AND property_ids IS NULL AND property_tags IS NULL) OR
(properties IS NULL AND property_ids IS NOT NULL AND property_tags IS NULL) OR
(properties IS NULL AND property_ids IS NULL AND property_tags IS NOT NULL)
```

The comment at line 250 correctly describes the three-way XOR intent: "XOR constraint: exactly one of (properties, property_ids, property_tags) must be set". The ORM model constraint contradicts this comment and would allow a product to have both `property_ids` and `property_tags` set simultaneously.

**Impact:** If `Base.metadata.create_all()` is ever used (e.g., in test setup), the wrong constraint is applied. In Alembic-managed environments, the correct constraint is in the DB but the ORM model is misleading. Newly written code that reads the model to understand the constraint will get incorrect information.

**Fix:**

```python
__table_args__ = (
    Index("idx_products_tenant", "tenant_id"),
    # Enforce AdCP spec: exactly one of (properties, property_ids, property_tags) must be set
    CheckConstraint(
        """
        (properties IS NOT NULL AND property_ids IS NULL AND property_tags IS NULL) OR
        (properties IS NULL AND property_ids IS NOT NULL AND property_tags IS NULL) OR
        (properties IS NULL AND property_ids IS NULL AND property_tags IS NOT NULL)
        """,
        name="ck_product_authorization",
    ),
)
```

---

### [HIGH-2]: `Creative.variants` hardcoded to `[]` makes the delivery Creative base class's required field semantically meaningless

**Confidence: 90**

**File:** `src/core/tools/creatives/listing.py:367`, `src/core/tools/creatives/_validation.py:60`

**Issue:** Every code path that constructs a `Creative` for the `list_creatives` response hardcodes `variants=[]`:

```python
# listing.py:367
creative = Creative(
    creative_id=db_creative.creative_id,
    ...
    variants=[],  # always empty
)
```

The delivery `Creative` base class requires `variants` as a non-optional `list[CreativeVariant]`. The empty list satisfies Pydantic's type check but violates the semantic contract: `CreativeVariant` objects carry the rendered manifest and delivery metrics. Buyers parsing the response get an empty variants list for every creative, making the response structurally correct but informationally empty.

**Impact:** Buyers calling `list_creatives` receive creatives where `variants: []` always -- they cannot retrieve creative manifests or delivery breakdowns from this endpoint. This is a direct consequence of the base class being a delivery model rather than an entity model (see CRITICAL-1).

**Fix:** Address via CRITICAL-1 fix. Once `Creative` no longer extends the delivery model, `variants` is no longer a required field on the entity schema.

## Medium Issues

### [MEDIUM-1]: `schema_helpers.create_get_products_request()` passes `BrandReference` to a `dict`-typed field

**Confidence: 85**

**File:** `src/core/schema_helpers.py:108-113` and `src/core/schemas.py:1484-1487`

**Issue:** `GetProductsRequest.brand` is overridden from the library's `BrandReference | None` to `dict[str, Any] | None`:

```python
# schemas.py:1484
brand: dict[str, Any] | None = Field(  # type: ignore[assignment]
    None,
    description="Brand reference for product discovery context (spec: brand-ref.json)",
)
```

`schema_helpers.create_get_products_request()` calls:

```python
return GetProductsRequest(
    brand=to_brand_reference(brand),  # Returns BrandReference | None
    ...
)
```

In Pydantic v2 with a `dict` target type, a `BrandReference` model is coerced via `.model_dump()`. This works at runtime (Pydantic v2 calls `.model_dump()` on nested models when the target is `dict`), but the caller at `src/core/tools/products.py:167` has a defensive fallback:

```python
brand_dict: dict[str, Any] = req.brand if isinstance(req.brand, dict) else {}
```

If Pydantic v2's coercion behavior ever changes, or if a `BrandReference` object is stored as-is (e.g., in test contexts where Pydantic validation is bypassed), the `isinstance(req.brand, dict)` check returns `False` and `brand_dict` silently becomes `{}`. In `require_brand` tenant policy this raises `AdCPAuthorizationError`; in `require_auth` policy it silently proceeds without brand context.

**Fix:** Remove the `brand` field override in `GetProductsRequest` and use the library's `BrandReference` type directly. Update `schema_helpers.py` to pass the `BrandReference` object, and update `products.py` to use `req.brand.domain` directly:

```python
# schemas.py -- remove the override, inherit from library
# GetProductsRequest already has brand: BrandReference | None from library

# products.py -- use typed access
if req.brand:
    domain = req.brand.domain  # BrandReference.domain is str (required)
    offering = f"Brand at {domain}"
```

---

### [MEDIUM-2]: `PricingOption.model_dump_internal()` silently discards any `exclude` kwarg passed by caller

**Confidence: 85**

**File:** `src/core/schemas.py:618-621`

**Issue:**

```python
def model_dump_internal(self, **kwargs):
    """Dump including all fields for database storage and internal processing."""
    kwargs.pop("exclude", None)  # Remove any exclude parameter
    return super().model_dump(**kwargs)
```

This pops any `exclude` set the caller passes, overriding their intent. If a caller does `po.model_dump_internal(exclude={"some_field"})` to exclude a specific field during internal processing, that exclusion is silently ignored and the field is included anyway.

**Impact:** Callers who expect to control which fields are included via `exclude` get unexpected results. The pattern conflicts with the project guideline "No Quiet Failures" -- silent behavior modification without warning.

**Fix:** Either document explicitly that `exclude` is not supported in `model_dump_internal`, or raise an error if `exclude` is passed:

```python
def model_dump_internal(self, **kwargs):
    """Dump including all fields for database storage and internal processing.

    Note: The `exclude` parameter is not supported -- internal dumps always include
    all fields. Use model_dump() for selective field exclusion.
    """
    if "exclude" in kwargs:
        raise ValueError("model_dump_internal() does not support 'exclude' -- use model_dump() instead")
    return super().model_dump(**kwargs)
```

## Low Issues

### [LOW-1]: `version_compat.py` module docstring inaccuracy

**Confidence: 80**

**File:** `src/core/version_compat.py:5`

**Issue:** The module docstring says "only applied when clients declare adcp_version < 3.0" but the actual comparison is `Version(adcp_version) < V3_VERSION` where `V3_VERSION = Version("3.0.0")`. This is correct behavior (`< 3.0.0` and `< 3.0` are equivalent for `packaging.version`), but the docstring omits the patch version and could confuse a reader checking whether 3.0.0 itself would trigger compat (it would not -- `3.0.0 < 3.0.0` is False).

**Fix:** Update the docstring:
```python
"""...only applied when clients declare adcp_version < 3.0.0."""
```

## Positive Observations

**Product conversion roundtrip is correct.** All 6 new adcp 3.6.0 fields (`signal_targeting_allowed`, `catalog_match`, `catalog_types`, `conversion_tracking`, `data_provider_signals`, `forecast`) are correctly mapped in `convert_product_model_to_schema()` at lines 333-344 using `getattr(..., None) is not None` guards. The `False` value for `signal_targeting_allowed` is correctly preserved (since `False is not None` evaluates to `True`).

**Migration file is correct.** `add_adcp36_product_fields.py` correctly adds all 6 columns, uses `JSONType` consistently with the rest of the codebase, includes a proper `server_default="false"` for the boolean column, and has a complete `downgrade()` path. Column types align exactly with the ORM model definitions.

**`ResolvedIdentity` design is sound.** The new `resolved_identity.py` correctly implements a transport-agnostic identity abstraction. The four-strategy tenant detection (host -> x-adcp-tenant -> Apx-Incoming-Host -> localhost fallback) is implemented in a clear, testable way. The `require_valid_token=False` pattern for discovery endpoints is appropriately scoped and not an auth bypass -- callers downstream check `identity.is_authenticated` before privileged operations.

**v2/v3 compat shim design is clean.** `version_compat.py` uses a registry pattern that makes it easy to add per-tool transforms. The `needs_v2_compat(None) -> True` default (conservative compatibility for clients that don't declare a version) is an appropriate safety choice. The transform only applies at the transport boundary after serialization, keeping business logic clean.

**`schema_helpers.py` refactoring is correct.** The new `to_brand_reference()`, `to_reporting_webhook()`, and `to_context_object()` helpers follow a consistent pattern. `create_get_products_response()` correctly handles both `Product` objects and dicts.

## Recommendations

1. **Prioritize CRITICAL-1 (`salesagent-goy2`) for Wave 1 creative refactor.** The current approach of patching around the wrong base class with `exclude=True` fields is unsustainable. Define a local `CreativeEntity` schema that is NOT derived from the delivery model. Map DB rows to this entity for `list_creatives` and `sync_creatives`; use the library `Creative` (delivery) only where delivery metrics are actually returned.

2. **Fix CRITICAL-2 (`salesagent-mq3n`) immediately.** The `_get_pricing_options` function has never worked correctly since `pricing_option_id` became a string. This silently degrades delivery metric responses for all media buys.

3. **Sync the ORM model constraint with the DB (HIGH-1).** The two-field XOR in `models.py` is both wrong and misleading. Update it to the three-field version to match `ck_product_authorization` from the migration.

4. **Consider making `GetProductsRequest.brand` accept `BrandReference` directly (MEDIUM-1).** The `dict` override was added for MCP transport flexibility (JSON arrives as dict) but creates fragility for programmatic callers. The correct fix is to use Pydantic's model coercion properly -- pass `BrandReference | dict[str, Any] | None` and let Pydantic handle either, then access `.domain` via a property.
