#!/usr/bin/env python3
"""Comprehensive test of all Admin UI pages and submodules."""

import os
from urllib.parse import urljoin

import pytest
import requests

TENANT_ID = "default"

# Track results
results = {"passed": [], "failed": [], "errors": []}


def _check_page(session, path, description, base_url=""):
    """Test a single page/route."""
    url = urljoin(base_url, path)
    print(f"Testing: {description:<50}", end=" ")

    try:
        response = session.get(url, timeout=10)

        if response.status_code == 200:
            # Check for error indicators in content
            if any(
                error in response.text
                for error in [
                    "UndefinedColumn",
                    "UndefinedTable",
                    "Internal Server Error",
                    "AttributeError",
                    "KeyError",
                    "TypeError",
                    "ValueError",
                ]
            ):
                print("❌ ERROR in HTML")
                results["errors"].append(f"{description}: Error text in HTML")
                return False
            else:
                print("✅ OK")
                results["passed"].append(description)
                return True

        elif response.status_code == 302:
            # Redirect is OK (likely to login or another page)
            print(f"↗️  Redirect to {response.headers.get('Location', 'unknown')}")
            results["passed"].append(f"{description} (redirect)")
            return True

        elif response.status_code == 404:
            print("⚠️  404 Not Found")
            results["failed"].append(f"{description}: 404")
            return False

        elif response.status_code == 500:
            print("❌ 500 ERROR!")
            results["failed"].append(f"{description}: 500 Server Error")

            # Try to get error details
            if response.text:
                # Look for specific error patterns
                if "UndefinedColumn" in response.text:
                    error_msg = "Database column missing"
                    # Try to extract column name
                    import re

                    match = re.search(r'column "([^"]+)" does not exist', response.text)
                    if match:
                        error_msg = f"Missing column: {match.group(1)}"
                    results["errors"].append(f"{description}: {error_msg}")
                elif "UndefinedTable" in response.text:
                    error_msg = "Database table missing"
                    match = re.search(r'relation "([^"]+)" does not exist', response.text)
                    if match:
                        error_msg = f"Missing table: {match.group(1)}"
                    results["errors"].append(f"{description}: {error_msg}")
                else:
                    results["errors"].append(f"{description}: Unknown 500 error")
            return False

        else:
            print(f"⚠️  Status {response.status_code}")
            results["failed"].append(f"{description}: HTTP {response.status_code}")
            return False

    except Exception as e:
        print(f"❌ Exception: {str(e)}")
        results["errors"].append(f"{description}: {str(e)}")
        return False


@pytest.mark.slow
@pytest.mark.requires_server
def test_all_admin_pages():
    """Test all pages systematically."""
    base_url = f"http://localhost:{os.environ.get('ADCP_SALES_PORT', '8080')}"

    print(f"\n{'=' * 70}")
    print("COMPREHENSIVE ADMIN UI TEST")
    print(f"{'=' * 70}\n")

    # Create session
    session = requests.Session()

    # First authenticate using test mode
    print("1. AUTHENTICATION")
    print("-" * 40)

    auth_data = {
        "email": "test_super_admin@example.com",
        "password": "test123",
        "tenant_id": "",
    }

    try:
        response = session.post(f"{base_url}/admin/test/auth", json=auth_data)
    except (requests.ConnectionError, requests.Timeout):
        pytest.skip(f"Server not running at {base_url}")

    if response.status_code in [200, 302]:
        print("✅ Authentication successful\n")
    else:
        pytest.fail(f"Authentication failed: {response.status_code}. Make sure ADCP_AUTH_TEST_MODE=true is set")

    # Test main pages
    print("2. MAIN PAGES")
    print("-" * 40)

    main_pages = [
        (f"/admin/tenant/{TENANT_ID}", "Dashboard"),
        (f"/admin/tenant/{TENANT_ID}/settings", "Settings Main"),
        (f"/admin/tenant/{TENANT_ID}/operations", "Operations"),
        (f"/admin/tenant/{TENANT_ID}/products", "Products List"),
        (f"/admin/tenant/{TENANT_ID}/products/new", "New Product"),
        (f"/admin/tenant/{TENANT_ID}/advertisers", "Advertisers List"),
        (f"/admin/tenant/{TENANT_ID}/advertisers/new", "New Advertiser"),
        (f"/admin/tenant/{TENANT_ID}/formats", "Creative Formats"),
        (f"/admin/tenant/{TENANT_ID}/integrations", "Integrations"),
        (f"/admin/tenant/{TENANT_ID}/api-tokens", "API Tokens"),
        (f"/admin/tenant/{TENANT_ID}/users", "Users"),
        (f"/admin/tenant/{TENANT_ID}/activity", "Activity Log"),
    ]

    for path, description in main_pages:
        _check_page(session, path, description, base_url=base_url)

    print()

    # Test settings sections
    print("3. SETTINGS SECTIONS")
    print("-" * 40)

    settings_sections = [
        "general",
        "ad_server",
        "products",
        "formats",
        "advertisers",
        "integrations",
        "tokens",
        "users",
        "advanced",
    ]

    for section in settings_sections:
        path = f"/admin/tenant/{TENANT_ID}/settings/{section}"
        _check_page(session, path, f"Settings: {section.title()}", base_url=base_url)

    print()

    # Test API endpoints
    print("4. API ENDPOINTS")
    print("-" * 40)

    api_endpoints = [
        (f"/admin/api/tenant/{TENANT_ID}/products", "API: Products List"),
        (f"/admin/api/tenant/{TENANT_ID}/advertisers", "API: Advertisers List"),
        (f"/admin/api/tenant/{TENANT_ID}/formats", "API: Formats List"),
        (f"/admin/api/tenant/{TENANT_ID}/metrics", "API: Metrics"),
        (f"/admin/api/tenant/{TENANT_ID}/activity", "API: Activity"),
        (f"/admin/api/tenant/{TENANT_ID}/config", "API: Config"),
        (f"/admin/api/tenant/{TENANT_ID}/products/suggestions", "API: Product Suggestions"),
        (f"/admin/api/tenant/{TENANT_ID}/products/templates", "API: Product Templates"),
    ]

    for path, description in api_endpoints:
        _check_page(session, path, description, base_url=base_url)

    print()

    # Test special routes
    print("5. SPECIAL ROUTES")
    print("-" * 40)

    special_routes = [
        ("/", "Root"),
        ("/health", "Health Check"),
        ("/admin/login", "Login Page"),
        (f"/admin/tenant/{TENANT_ID}/login", "Tenant Login"),
        ("/admin/tenants", "Tenants List (Super Admin)"),
        ("/admin/test/login", "Test Login Page"),
    ]

    for path, description in special_routes:
        _check_page(session, path, description, base_url=base_url)

    print()

    # Test adapter-specific routes (if any)
    print("6. ADAPTER ROUTES")
    print("-" * 40)

    adapter_routes = [
        (f"/adapters/mock/config/{TENANT_ID}/prod_1", "Mock Adapter Config"),
        (f"/adapters/gam/config/{TENANT_ID}/prod_1", "GAM Adapter Config"),
    ]

    for path, description in adapter_routes:
        _check_page(session, path, description, base_url=base_url)

    # Print summary
    print(f"\n{'=' * 70}")
    print("TEST SUMMARY")
    print(f"{'=' * 70}\n")

    total = len(results["passed"]) + len(results["failed"])

    print(f"✅ Passed: {len(results['passed'])}/{total}")
    print(f"❌ Failed: {len(results['failed'])}/{total}")
    print(f"⚠️  Errors: {len(results['errors'])}")

    if results["failed"]:
        print("\nFailed Pages:")
        for item in results["failed"]:
            print(f"  - {item}")

    if results["errors"]:
        print("\nErrors Found:")
        for item in results["errors"]:
            print(f"  - {item}")

    # Assert results
    if results["failed"] or results["errors"]:
        failed_details = "; ".join(results["failed"][:5] + results["errors"][:5])
        pytest.fail(f"Admin pages have issues: {failed_details}")
    else:
        print("\n✅ ALL TESTS PASSED")


if __name__ == "__main__":
    main()
