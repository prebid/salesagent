# Link Validation in CI

## Overview
Automatic link validation tests run as part of the integration test suite in GitHub Actions.

## What Gets Validated
- **10 major admin pages**: Dashboard, Settings, Products, Principals, Media Buys, Workflows, Inventory, Authorized Properties, Property Tags, Creative Review
- **~150+ internal links** across all pages
- **All `<a href>`, `<img src>`, `<link href>`, `<script src>` attributes**

## When It Runs
- ✅ Every push to `main` or `develop`
- ✅ Every pull request to `main`
- ✅ Manual workflow dispatch

## CI Configuration
```yaml
# .github/workflows/test.yml
integration-tests:
  runs-on: ubuntu-latest
  services:
    postgres:
      image: postgres:15
  steps:
    - name: Run integration tests
      run: |
        uv run pytest tests/integration/ -v --cov=. -m "not requires_server and not skip_ci"
```

## Tests Included
From `tests/integration/test_link_validation.py`:
- `test_dashboard_links_are_valid`
- `test_settings_page_links_are_valid`
- `test_products_page_links_are_valid`
- `test_principals_page_links_are_valid`
- `test_media_buys_page_links_are_valid`
- `test_workflows_page_links_are_valid`
- `test_inventory_page_links_are_valid`
- `test_authorized_properties_page_links_are_valid`
- `test_property_tags_page_links_are_valid`
- `test_creative_review_page_links_are_valid`

From `tests/integration/test_admin_ui_routes_comprehensive.py`:
- `test_all_dashboard_links_valid`
- `test_all_settings_links_valid`
- `test_all_products_page_links_valid`

## What Happens on Failure
If a link is broken, CI will fail with a clear error:

```
test_dashboard_links_are_valid FAILED

Broken links found on /tenant/123:

  [404] /tenant/123/creatives/review (line 42, <a href=...>)
       Error: Status 404

Total: 1 broken link
```

## Performance Impact
- **Execution time**: ~3 seconds for all link validation tests
- **Minimal overhead**: Uses HEAD requests (faster than GET)
- **Smart filtering**: Only validates internal links

## Benefits
- ✅ Catches broken links before production
- ✅ Detects missing blueprint registrations
- ✅ Validates template URL correctness
- ✅ No manual testing required
- ✅ Clear error messages with line numbers

## Example: What This Would Have Caught
**PR #421 Issue**: Creative review page returned 404 because `creatives_bp` blueprint wasn't registered.

**With link validation**: CI would have failed with:
```
[404] /tenant/123/creatives/review (line 42, <a href=...>)
```

This would have been caught before merging to main.

## No Special Setup Required
The tests run automatically with the existing integration test infrastructure:
- Uses `authenticated_admin_session` fixture
- Uses `test_tenant_with_data` fixture
- Requires PostgreSQL (provided by CI)
- No additional configuration needed
