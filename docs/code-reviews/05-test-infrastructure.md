# Code Review: Test Infrastructure

Reviewing: new and modified test files for the adcp 3.2.0 to 3.6.0 migration (branch `KonstantinMirin/adcp-v3-upgrade`, `git diff main..HEAD`).

## Summary

289 files changed in the migration. This review examines 24 test/infrastructure files: 15 new unit test files (~7,500 lines), `tox.ini` (new), and 8 modified test support files. The overall architecture of the new test layer is sound -- the behavioral snapshot pattern (per-tool `_behavioral.py` files calling `_impl()` directly) is correct and the tox migration from bash parallelism is an improvement. However, there are three confirmed coverage gaps for the three known bugs, one structural issue that locks in wrong behavior, and one broken factory function.

## Files Reviewed

1. `tests/unit/test_adcp_36_schema_upgrade.py` (147 lines)
2. `tests/unit/test_product_adcp36_fields_missing.py` (91 lines)
3. `tests/unit/test_brand_manifest_to_brand_migration.py` (129 lines)
4. `tests/unit/test_brand_migration.py` (165 lines)
5. `tests/unit/test_brand_manifest_removed.py` (134 lines)
6. `tests/unit/test_delivery_behavioral.py` (1037 lines)
7. `tests/unit/test_create_media_buy_behavioral.py` (978 lines)
8. `tests/unit/test_get_products_behavioral.py` (968 lines)
9. `tests/unit/test_sync_creatives_behavioral.py` (577 lines)
10. `tests/unit/test_update_media_buy_behavioral.py` (717 lines)
11. `tests/unit/test_response_shapes.py` (902 lines)
12. `tests/unit/test_a2a_transport_contract.py` (536 lines)
13. `tests/unit/test_error_format_consistency.py` (561 lines)
14. `tests/unit/test_auth_consistency.py` (421 lines)
15. `tests/unit/test_get_media_buys.py` (491 lines)
16. `tests/conftest.py`
17. `tests/unit/conftest.py`
18. `tests/integration/conftest.py`
19. `tests/e2e/conftest.py`
20. `tox.ini`
21. `tests/unit/test_adcp_contract.py`
22. `tests/unit/test_list_creatives_serialization.py`
23. `tests/unit/test_creative_response_serialization.py`
24. `tests/helpers/adcp_factories.py`

---

## Test Coverage Gaps

### Known Bug: salesagent-goy2 (Creative wrong base class)

**Covered by tests?** No -- existing tests confirm the wrong workaround, not the fix

**Which tests?**
- `tests/unit/test_list_creatives_serialization.py` -- verifies `name`, `assets`, `status`, `created_date`, `updated_date` are absent from `model_dump()`
- `tests/unit/test_adcp_contract.py::TestAdCPContract::test_list_creatives_response_adcp_compliance` -- verifies `creative_id` present, `principal_id` absent
- `tests/unit/test_adcp_36_schema_upgrade.py::TestCreativeVariantsBoundary` -- verifies `variants` is required, `Creative(creative_id="c1", variants=[])` is valid

**Gap:**

The documentation at `docs/test-obligations/BR-UC-006-sync-creatives.md` is unambiguous:

> Current state: `salesagent.schemas.Creative` extends `adcp.types.Creative` which resolves to `adcp.types.generated_poc.creative.get_creative_delivery_response.Creative` (the delivery Creative).
> Correct base class: `adcp.types.generated_poc.media_buy.list_creatives_response.Creative` (13 fields: `account`, `assets`, `assignments`, `catalogs`, `created_date`, `creative_id`, `format_id`, `name`, `performance`, `status`, `sub_assets`, `tags`, `updated_date`).

The existing tests in `test_list_creatives_serialization.py` assert that `name`, `status`, `created_date`, `updated_date` are excluded via `exclude=True`. But per the bug report, these are **required fields** in the listing Creative -- they should be **present** in `model_dump()` output. The tests are asserting the workaround behavior as correct.

`test_adcp_36_schema_upgrade.py::TestCreativeVariantsBoundary` further cements the wrong base class: it asserts `variants` is a required field of `Creative` and that `Creative(creative_id="c1", variants=[])` is valid. `variants` belongs to the delivery Creative only; the listing Creative has no `variants` field. When salesagent-goy2 is fixed, the entire `TestCreativeVariantsBoundary` class (7 tests) will require inversion.

There is no test that asserts:
1. `Creative.__mro__` contains `list_creatives_response.Creative`, not `get_creative_delivery_response.Creative`
2. `model_dump()` includes `name`, `status`, `created_date`, `updated_date` (required listing fields)
3. `model_dump()` excludes `variants`, `variant_count`, `totals`, `media_buy_id` (delivery-only fields that must not appear in listing responses)

### Known Bug: salesagent-mq3n (PricingOption delivery lookup)

**Covered by tests?** No

**Which tests?** None that exercise `_get_pricing_options` without mocking it.

**Gap:**

The bug is in `src/core/tools/media_buy_delivery.py`, lines 645-649:

```python
def _get_pricing_options(pricing_option_ids: list[Any | None]) -> dict[str, PricingOption]:
    with get_db_session() as session:
        statement = select(PricingOption).where(PricingOption.id.in_(pricing_option_ids))
        pricing_options = session.scalars(statement).all()
        return {str(pricing_option.id): pricing_option for pricing_option in pricing_options}
```

The `pricing_option_ids` list contains string values extracted from `buy.raw_request.get("pricing_option_id")` (line 161). `PricingOption.id` is an integer primary key. The query `PricingOption.id.in_(["cpm_usd_fixed"])` will never match `id=1` in PostgreSQL (no implicit string-to-integer coercion in SQLAlchemy's `in_()` filter), so `_get_pricing_options` always returns `{}` in production.

Every test in `test_delivery_behavioral.py` uses `_standard_patches()` which always mocks `_get_pricing_options` at line 166-169:

```python
"pricing_options": patch(
    f"{_PATCH_PREFIX}._get_pricing_options",
    return_value=pricing_options or {},
),
```

The mock returns `{}` by default. This means the downstream code at line 311:

```python
pricing_option = pricing_options.get(pricing_option_id) if pricing_option_id is not None else None
```

always gets `None`, silently skipping the CPC click calculation at lines 324-327. The unit tests never exercise the actual DB query path, so the string-vs-integer mismatch is invisible to the entire unit suite. No integration test in the reviewed files specifically tests this code path either.

### Known Bug: salesagent-7gnv (MediaBuy field drops)

**Covered by tests?** Partially -- schema field presence confirmed, but response field propagation through `_impl()` is not tested

**Which tests?**
- `tests/unit/test_adcp_contract.py::TestSchemaMatchesLibrary::test_all_request_schemas_match_library` (line 143-145) -- confirms `CreateMediaBuyRequest` fields match the library, so `buyer_campaign_ref`, `ext`, `account_id` exist in the schema
- `tests/unit/test_response_shapes.py::TestCreateMediaBuyResponseShape` -- tests `media_buy_id`, `buyer_ref`, `packages`, and `workflow_step_id` exclusion

**Gap:**

The test obligations at `docs/test-obligations/UC-002-create-media-buy.md` (lines 234-291) define six scenarios for salesagent-7gnv, all at P1/P2 priority:
- `buyer_campaign_ref` accepted at request boundary and propagated through to `list_media_buys` response (P1)
- `buyer_campaign_ref` appears in `CreateMediaBuySuccess` JSON (P1)
- `ext` field accepted and propagated to response (P1)
- `ext` field roundtrip through create and list (P1)
- `creative_deadline` in `CreateMediaBuySuccess` response (P2)
- `sandbox` flag in response (P3)

None of these appear in `test_create_media_buy_behavioral.py` or `test_response_shapes.py`. The `TestCreateMediaBuyResponseShape` test constructs `CreateMediaBuySuccess` manually with only `media_buy_id`, `buyer_ref`, and `packages` -- it never populates or checks `buyer_campaign_ref` or `ext`.

For UC-003, `docs/test-obligations/UC-003-update-media-buy.md` defines two P0 scenarios (lines 107-118): `buyer_ref` in the update response matches `buyer_campaign_ref` from the request, and `ext` fields roundtrip through `UpdateMediaBuyRequest` -> `UpdateMediaBuySuccess`. Neither appears in `test_update_media_buy_behavioral.py`.

---

## Critical Issues

### Issue 1: `TestCreativeVariantsBoundary` encodes the wrong base class contract as the desired state

**Confidence: 85**

**File:** `tests/unit/test_adcp_36_schema_upgrade.py`, lines 16-93

The 7-test class `TestCreativeVariantsBoundary` asserts that `Creative` requires `variants` and accepts `variants=[]`. This is the contract of the delivery Creative (`get_creative_delivery_response.Creative`), not the listing Creative that the spec requires. When salesagent-goy2 is fixed (rebasing `Creative` onto `list_creatives_response.Creative`), the listing Creative has no `variants` field at all -- every assertion in this class will need to be inverted.

`test_creative_with_empty_variants_is_valid` (line 26) will become invalid. `test_creative_without_variants_is_rejected` (line 19) will need to become `test_creative_accepts_name_without_variants`.

This class is not labeled as "documenting current wrong behavior" -- it reads as the expected post-fix behavior, which it is not.

```python
# Currently asserts this (delivery Creative contract):
def test_creative_without_variants_is_rejected(self):
    with pytest.raises(ValidationError, match="variants"):
        Creative(creative_id="c1")  # fails because variants is required

# After goy2 fix, this should be valid (listing Creative has no variants field):
# Creative(creative_id="c1", name="Test Ad", status="approved")
```

**Fix:** Add a comment block:
```python
# FIXME(salesagent-goy2): This class describes current (wrong) behavior where
# Creative extends get_creative_delivery_response.Creative.
# After the fix (rebasing onto list_creatives_response.Creative), variants is
# NOT a field of listing Creative and these tests must be inverted.
```
Alternatively, replace this class with an `xfail` class that asserts the correct post-fix contract, and a separate class that documents the current workaround behavior.

### Issue 2: `adcp_factories.py` exports a `create_test_brand_manifest()` factory that returns the wrong type for adcp 3.6.0

**Confidence: 80**

**File:** `tests/helpers/adcp_factories.py`, lines 14, 497-523

```python
# Line 14
from adcp import BrandManifest, Format, Property

# Lines 497-523
def create_test_brand_manifest(
    name: str = "Test Brand",
    tagline: str | None = None,
    **kwargs,
) -> BrandManifest:
    ...
    return BrandManifest(**manifest_kwargs)
```

`BrandManifest` is still importable from `adcp` in 3.6.0 (it is re-exported via `src/core/schemas.py` as `LibraryBrandManifest`), so this does not cause an import error at test collection time. However, `CreateMediaBuyRequest.brand` in adcp 3.6.0 expects a `BrandReference` object (with `domain` field), not a `BrandManifest`. Any test that calls `create_test_brand_manifest()` and passes the result as `brand=` will fail validation with an uninformative error. The function is dead code in the 3.6.0 context (none of the 15 new test files call it), but it remains discoverable and will mislead future developers.

**Guideline reference:** CLAUDE.md Critical Pattern #1 -- "Extend library schemas; never duplicate." The factory produces the pre-3.6.0 type for a field that no longer accepts it.

**Fix:** Remove `create_test_brand_manifest()` and its `BrandManifest` import from line 14, or replace with:
```python
from adcp.types import BrandReference  # adcp 3.6.0

def create_test_brand_reference(domain: str = "test.com", brand_id: str | None = None) -> BrandReference:
    kwargs = {"domain": domain}
    if brand_id:
        kwargs["brand_id"] = brand_id
    return BrandReference(**kwargs)
```

---

## High Issues

### Issue 3: `_get_pricing_options` is unconditionally mocked -- the mq3n code path is permanently invisible to the unit suite

**Confidence: 90**

**File:** `tests/unit/test_delivery_behavioral.py`, lines 127-174 (`_standard_patches`)

All 40+ test cases in `test_delivery_behavioral.py` go through `_standard_patches()`, which always patches `_get_pricing_options` to return `{}`. This makes `pricing_option` always `None` in the delivery loop (line 311 of `media_buy_delivery.py`), silently skipping click calculations for CPC pricing. The bug (string IDs queried against integer PK) is never exposed.

There is no test in any suite that calls `_get_pricing_options(["cpm_usd_fixed"])` against a real or simulated DB and verifies the return value.

**Fix:** Add a direct test of `_get_pricing_options`:

```python
def test_get_pricing_options_with_string_ids():
    """Verify _get_pricing_options returns results when queried with string pricing_option_ids."""
    from src.core.tools.media_buy_delivery import _get_pricing_options

    mock_po = MagicMock()
    mock_po.id = 1  # integer PK
    mock_po.pricing_model = "cpc"

    mock_session = MagicMock()
    mock_session.scalars.return_value.all.return_value = [mock_po]

    with patch("src.core.tools.media_buy_delivery.get_db_session") as mock_db:
        mock_db.return_value.__enter__.return_value = mock_session
        result = _get_pricing_options(["1"])  # string "1" vs integer id=1

    # This will reveal whether the query succeeds or returns empty
    assert "1" in result, "String ID lookup must find integer PK=1"
```

### Issue 4: Multi-buy partial failure semantics are untested

**Confidence: 82**

**File:** `tests/unit/test_delivery_behavioral.py`, lines 376-479 (`TestDeliveryImplAdapterError`)

Both error tests use a single media buy. The implementation at `src/core/tools/media_buy_delivery.py:243-259` does an early return on the first adapter exception, discarding any previously collected delivery data from earlier buys in the same request. This is a behavior worth documenting, but it is untested.

Neither test covers:
- Multi-buy request where one buy fails (partial success semantics)
- Whether the first successful buy's data is lost in the error case
- Whether `aggregated_totals` reflects only successful buys or is always zeroed

**Fix:** Add a test case with two target buys where the first succeeds and the second raises, asserting whether the response contains the first buy's data or an empty `media_buy_deliveries` list.

---

## Medium Issues

### Issue 5: `_PatchContext` patches `get_db_session` at the source module rather than the consumer

**Confidence: 80**

**File:** `tests/unit/test_create_media_buy_behavioral.py`, line 176

```python
self._p_db = patch("src.core.database.database_session.get_db_session")
```

Python mock patching convention: patch where the name is *used*, not where it is *defined*. If `media_buy_create.py` imports `get_db_session` at the top of the module (which it does), the reference is already bound. Patching the source module affects only new imports after the patch is applied. The comment "Patched at source because media_buy_create.py uses local imports" is internally contradictory -- local imports are caught by patching the consumer, not the source.

This currently works because the tests are passing, which means Python is finding the patched version. However, it will silently break if `media_buy_create.py` caches the reference at import time rather than calling `get_db_session()` each time.

**Fix:** Change to `patch("src.core.tools.media_buy_create.get_db_session")`.

### Issue 6: `coverage --fail-under=30` in `tox.ini` provides no regression signal

**Confidence: 82**

**File:** `tox.ini`, line 94

```ini
coverage report --fail-under=30
```

A 30% threshold will pass even if large portions of the codebase lose coverage. With 15 new test files and the existing integration suite, the actual coverage is likely substantially higher. This gate provides no protection against coverage regressions from refactoring or dead code.

**Guideline reference:** CLAUDE.md Quality Rules -- "Never skip tests to make CI pass."

**Fix:** Run `make test-cov` after the current suite passes, read the actual percentage from `coverage.json`, and set `--fail-under` to (actual - 5)%.

---

## Test Quality Assessment

**Mock usage:** All new behavioral test files are within the 10-mock-per-file limit from CLAUDE.md. The `_standard_patches()` pattern in `test_delivery_behavioral.py` consolidates 6 patches into a reusable helper, which is clean. The concern is not count but completeness -- `_get_pricing_options` should not be in the standard patch set since it contains the mq3n bug.

**Test isolation:** Good. No shared mutable state between test classes. All DB mocks use context managers properly.

**Fixture patterns:** The `_PatchContext` class in `test_create_media_buy_behavioral.py` is a sound pattern. It is slightly more complex than a pytest fixture but provides useful per-test customization (products, currency_limit, adapter_config). The `_make_identity()` / `_make_mock_media_buy()` / `_make_adapter_response()` factory functions in `test_delivery_behavioral.py` are clear and reusable.

**Test data realism:** The `adcp_factories.py` helpers (`create_test_product`, `create_test_cpm_pricing_option`) construct valid AdCP-compliant objects and are used consistently across the new test files. The factories correctly use the discriminated union format required by adcp 3.6.0. No issues with test data realism in the new behavioral tests.

**tox.ini:** The migration from bash parallelism to tox is well-structured. The five environments (`unit`, `integration`, `integration_v2`, `e2e`, `ui`) map correctly to test directories. The `DATABASE_URL =` override in `[testenv:unit]` correctly prevents unit tests from accidentally connecting to a database. Coverage separation by `COVERAGE_FILE = {toxworkdir}/.coverage.{envname}` and combine step are implemented correctly.

**`test_product_adcp36_fields_missing.py`:** The `test_adcp_36_fields_exist_in_database` test is intentionally failing to track technical debt (6 new adcp 3.6.0 Product fields have no DB columns). This is a valid tracking pattern. Confirm this test is excluded from the CI gate or marked `xfail` to avoid blocking the unit suite.

**Behavioral snapshot tests (5 files, ~4,000 lines total):** These are well-structured. Direct `_impl()` calls, explicit `ResolvedIdentity` construction, minimal mock surface. The `test_sync_creatives_behavioral.py` coverage of BR-RULE-040 status transitions is particularly thorough. The `test_get_media_buys.py` coverage of status computation, filtering, and creative approval mapping is complete.

---

## Summary Table

| Bug | Covered? | Strongest Existing Test | Gap |
|-----|----------|------------------------|-----|
| salesagent-goy2 (Creative wrong base class) | No | `test_list_creatives_serialization.py` (tests the workaround) | No `issubclass` test; field exclusion tests confirm wrong behavior |
| salesagent-mq3n (delivery string vs int PK) | No | None -- `_get_pricing_options` always mocked | Entire function is invisible to unit suite |
| salesagent-7gnv (MediaBuy drops fields) | Partial | `test_adcp_contract.py` schema field presence | No `_impl()` roundtrip for `buyer_campaign_ref`, `ext`, `creative_deadline` |

---

## Recommendations

1. **Add a `test_creative_extends_correct_base_class()` test** that uses `issubclass()` to assert `Creative` inherits from `list_creatives_response.Creative`, not `get_creative_delivery_response.Creative`. This is the only test that would actually catch salesagent-goy2.

2. **Add a unit test for `_get_pricing_options()`** that does not mock the function, to expose the string-vs-integer PK comparison bug for salesagent-mq3n.

3. **Add response-shape roundtrip tests** for `buyer_campaign_ref`, `ext`, and `creative_deadline` in `test_create_media_buy_behavioral.py` and `test_update_media_buy_behavioral.py` to cover the salesagent-7gnv scenarios defined in `docs/test-obligations/`.

4. **Remove `create_test_brand_manifest()`** from `tests/helpers/adcp_factories.py` and its `BrandManifest` import, replacing with `create_test_brand_reference()` that returns a `BrandReference` with `domain`.

5. **Add a `FIXME(salesagent-goy2)` comment block** to `TestCreativeVariantsBoundary` in `test_adcp_36_schema_upgrade.py` making clear these tests describe the current wrong behavior and must be inverted after the fix.

6. **Confirm `test_product_adcp36_fields_missing.py::test_adcp_36_fields_exist_in_database`** is either `xfail` or excluded from the default CI run, since it is expected to fail until the DB migration is added.

7. **Raise `coverage --fail-under`** in `tox.ini` from 30% to reflect actual current coverage.
