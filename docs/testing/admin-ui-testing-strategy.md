# Admin UI Testing Strategy

## Two-Level Testing Approach

We use a **two-level testing strategy** for Admin UI pages:

### Level 1: Smoke Tests (Infrastructure)
**Purpose**: Verify pages render without crashing
**File**: `tests/integration/test_admin_ui_routes_comprehensive.py`
**Coverage**: 56 tests covering 89 GET routes

```python
def test_list_products(self, authenticated_admin_session, test_tenant_with_data):
    """Smoke test: Verify products page renders."""
    response = authenticated_admin_session.get(f"/tenant/{tenant_id}/products/")
    assert response.status_code == 200  # Just check it doesn't crash
```

**Catches:**
- ✅ Template syntax errors
- ✅ Missing route definitions
- ✅ Import errors
- ✅ Variable name bugs (like `session` vs `db_session`)
- ✅ Authentication issues

**Execution**: ~12 seconds (fast feedback)

---

### Level 2: Data Validation Tests (Correctness)
**Purpose**: Verify correct data is displayed without duplicates
**File**: `tests/integration/test_admin_ui_data_validation.py`
**Coverage**: Growing (currently 6 critical tests)

```python
def test_products_list_no_duplicates_with_pricing_options(self, ...):
    """Data validation: Verify products aren't duplicated when using joinedload()."""
    # Create product with 3 pricing options
    create_product_with_pricing_options(tenant_id, count=3)

    response = authenticated_admin_session.get(f"/tenant/{tenant_id}/products/")
    assert response.status_code == 200

    html = response.data.decode()
    product_count = html.count("Test Product")
    assert product_count == 1, "Product should appear once, not duplicated"
```

**Catches:**
- ✅ SQL query bugs (missing `.unique()`, incorrect joins)
- ✅ ORM relationship bugs (duplicate rows)
- ✅ Data duplication
- ✅ Incorrect counts
- ✅ Missing data
- ✅ Template logic errors

**Execution**: ~20-30 seconds (more data setup)

---

## Which Pages Need Data Validation?

### Priority 1: Critical Pages (MUST have data validation) 🔴

**Pages that show lists of items** (high risk of duplicates):

1. **Products List** ✅ DONE
   - Risk: `joinedload(Product.pricing_options)` without `.unique()`
   - Test: Verify no duplicates when products have multiple pricing options

2. **Principals/Advertisers List** ✅ DONE
   - Risk: Relationships causing duplicate rows
   - Test: Verify each principal appears once

3. **Inventory Browser** ✅ DONE
   - Risk: Ad unit hierarchy joins
   - Test: Verify no duplicate ad units

4. **Media Buys List** ⚠️ TODO
   - Risk: Joins with creatives, packages, principals
   - Test: Verify no duplicate media buys

5. **Dashboard** ✅ PARTIAL
   - Risk: Aggregation queries, metrics calculation
   - Test: Verify accurate counts (media buys, products, revenue)

6. **Workflows List** ⚠️ TODO
   - Risk: Joins with workflow steps, mappings
   - Test: Verify no duplicate workflows

7. **Authorized Properties List** ⚠️ TODO
   - Risk: Property tags, verification status joins
   - Test: Verify no duplicate properties

### Priority 2: Detail Pages (SHOULD have data validation) 🟡

**Pages that show aggregated data or metrics:**

8. **Product Detail/Edit** ⚠️ TODO
   - Risk: Pricing options not loaded correctly
   - Test: Verify all pricing options shown

9. **Principal Detail** ⚠️ TODO
   - Risk: Platform mappings, webhooks not loaded
   - Test: Verify all data present

10. **Media Buy Detail** ⚠️ TODO
    - Risk: Packages, creatives, delivery metrics
    - Test: Verify accurate package/creative assignments

11. **Settings Pages** ⚠️ TODO
    - Risk: Complex config merging (tenant config + defaults)
    - Test: Verify correct settings displayed

### Priority 3: Action Pages (COULD have validation) 🟢

**Pages that trigger actions:**

12. **Create/Edit Forms** ⚠️ TODO
    - Risk: Form pre-population, validation errors
    - Test: Verify form data pre-populated correctly

---

## Implementation Plan

### Phase 1: Critical List Pages (Week 1)
Add data validation for:
- ✅ Products list (DONE)
- ✅ Principals list (DONE)
- ✅ Inventory browser (DONE)
- [ ] Media buys list
- [ ] Workflows list
- [ ] Authorized properties list

**Goal**: Catch ORM/SQL bugs that smoke tests miss

### Phase 2: Detail Pages (Week 2)
Add data validation for:
- [ ] Product detail/edit
- [ ] Principal detail
- [ ] Media buy detail
- [ ] Settings pages

**Goal**: Verify complex data loading

### Phase 3: Dashboard & Metrics (Week 3)
Add data validation for:
- [ ] Dashboard metrics (revenue, counts, charts)
- [ ] Reporting pages
- [ ] Analytics pages

**Goal**: Verify aggregations and calculations

---

## Test Patterns

### Pattern 1: No Duplicates (List Pages)
```python
def test_page_no_duplicates_with_relationships(self, ...):
    """Verify items with relationships aren't duplicated."""
    # Create item with multiple related records
    create_item_with_relations(count=3)

    response = client.get("/page")
    html = response.data.decode()

    # Verify appears exactly once
    assert html.count("Item Name") == 1
```

**Use for**: Products, principals, inventory, media buys

### Pattern 2: Correct Count (List Pages)
```python
def test_page_shows_all_items(self, ...):
    """Verify all items are displayed."""
    # Create 5 items
    items = create_items(count=5)

    response = client.get("/page")
    html = response.data.decode()

    # Verify each appears once
    for item in items:
        assert html.count(item.name) == 1
```

**Use for**: Any list page

### Pattern 3: Accurate Metrics (Dashboard/Metrics)
```python
def test_dashboard_accurate_metrics(self, ...):
    """Verify dashboard shows correct counts."""
    # Create known data
    create_media_buys(count=5, status="live")
    create_media_buys(count=3, status="paused")

    response = client.get("/dashboard")
    html = response.data.decode()

    # Verify metrics
    assert "5 live campaigns" in html or ">5<" in html
    assert "8 total campaigns" in html or ">8<" in html
```

**Use for**: Dashboards, reports, analytics

### Pattern 4: Complete Data (Detail Pages)
```python
def test_detail_page_shows_all_data(self, ...):
    """Verify detail page shows all related data."""
    # Create product with 3 pricing options
    product = create_product()
    create_pricing_options(product.id, count=3)

    response = client.get(f"/product/{product.id}/edit")
    html = response.data.decode()

    # Verify all pricing options present
    assert html.count("pricing-option-row") == 3
```

**Use for**: Edit forms, detail pages

---

## What Makes a Good Data Validation Test?

### ✅ Good Example
```python
def test_products_list_no_duplicates_with_pricing_options(self, ...):
    """
    Regression test for joinedload() without .unique() bug.

    Bug: Products with multiple pricing_options appeared multiple times
    Root cause: Missing .unique() after joinedload(Product.pricing_options)
    Impact: User sees duplicate products in UI
    Fix: Add .unique() before .all()
    """
    # Setup: Create specific scenario that triggers bug
    product = create_product(name="Test Product")
    create_pricing_options(product.id, count=3)  # This causes 3 joined rows

    # Execute: Request the page
    response = client.get("/products")
    html = response.data.decode()

    # Verify: Check correctness, not just status
    count = html.count("Test Product")
    assert count == 1, (
        f"Product appears {count} times (expected 1). "
        f"This indicates joinedload() without .unique() bug."
    )
```

**Good because:**
- ✅ Tests specific bug scenario
- ✅ Clear regression test (documents what broke before)
- ✅ Helpful error message with fix suggestion
- ✅ Validates data, not just status code

### ❌ Bad Example
```python
def test_products_page_works(self, ...):
    """Test products page works."""
    response = client.get("/products")
    assert response.status_code == 200  # Only checks it doesn't crash
```

**Bad because:**
- ❌ Only checks status code (smoke test, not data validation)
- ❌ Doesn't verify correctness
- ❌ Wouldn't catch the joinedload bug
- ❌ No helpful error message

---

## Enforcement

### Pre-commit Hook (Future)
Add data validation coverage check:

```python
# scripts/check_data_validation_coverage.py
CRITICAL_PAGES = [
    "/tenant/<tenant_id>/products",      # Must have data validation
    "/tenant/<tenant_id>/principals",    # Must have data validation
    "/tenant/<tenant_id>/inventory",     # Must have data validation
    "/tenant/<tenant_id>/media-buys",    # Must have data validation
]

def check_coverage():
    for page in CRITICAL_PAGES:
        if not has_data_validation_test(page):
            print(f"❌ Missing data validation test for: {page}")
            return 1
    return 0
```

**Goal**: Ensure critical pages have both smoke tests AND data validation

---

## Benefits

### For Developers
- ✅ Catch SQL/ORM bugs before production
- ✅ Fast feedback (smoke tests in 12s, data tests in 30s)
- ✅ Clear error messages point to exact bug
- ✅ Regression tests document what broke before

### For the Project
- ✅ Higher quality (catches correctness bugs, not just crashes)
- ✅ Fewer production incidents (caught `.unique()` bug)
- ✅ Better test documentation (explains what was tested and why)

### For Production
- ✅ No duplicate data in UI
- ✅ Accurate metrics and counts
- ✅ Correct business logic

---

## See Also
- `tests/integration/test_admin_ui_routes_comprehensive.py` - Smoke tests (56 tests)
- `tests/integration/test_admin_ui_data_validation.py` - Data validation tests (growing)
- `docs/testing/admin-ui-route-testing.md` - Route testing guide
