#!/usr/bin/env python3
"""Pre-commit hook to verify all Admin UI GET routes have tests.

This script:
1. Extracts all GET routes from admin blueprints
2. Checks if each route has a corresponding test
3. Reports missing tests
4. Exits with error if coverage is incomplete

Usage:
    ./scripts/check_admin_route_coverage.py

Exit codes:
    0: All GET routes have tests
    1: Some GET routes are missing tests
"""

import re
import sys
from pathlib import Path


def extract_routes_from_blueprint(filepath):
    """Extract GET route definitions from a blueprint file."""
    routes = []
    with open(filepath) as f:
        content = f.read()

    # Find all @blueprint.route() decorators
    pattern = r'@(\w+)\.route\(["\']([^"\']+)["\'](?:,\s*methods\s*=\s*\[([^\]]+)\])?\)'
    matches = re.finditer(pattern, content)

    for match in matches:
        blueprint_var = match.group(1)
        path = match.group(2)
        methods = match.group(3)

        if methods:
            methods_list = [m.strip().strip("\"'") for m in methods.split(",")]
        else:
            methods_list = ["GET"]  # Default to GET

        # Only track GET routes
        if "GET" in methods_list:
            routes.append(
                {
                    "file": filepath.name,
                    "path": path,
                    "blueprint": blueprint_var,
                }
            )

    return routes


def extract_test_routes(test_file):
    """Extract routes being tested from a test file."""
    tested_routes = set()

    if not test_file.exists():
        return tested_routes

    with open(test_file) as f:
        content = f.read()

    # Look for .get("...") calls in test files
    pattern = r'\.get\(["\']([^"\']+)["\']'
    matches = re.finditer(pattern, content)

    for match in matches:
        path = match.group(1)
        # Normalize path (remove tenant_id placeholders for comparison)
        normalized = normalize_route_path(path)
        tested_routes.add(normalized)

    return tested_routes


def normalize_route_path(path):
    """Normalize a route path for comparison.

    Converts:
    - /tenant/{tenant_id}/products -> /products
    - /<tenant_id>/products -> /products
    - /tenant/123/products -> /products
    """
    # Remove leading /tenant/{anything}/ or /{tenant_id}/
    path = re.sub(r"^/tenant/[^/]+/", "/", path)
    path = re.sub(r"^/<tenant_id>/", "/", path)

    # Remove tenant_id variables from path
    path = path.replace("/{tenant_id}", "")
    path = path.replace("<tenant_id>", "")

    return path


def should_skip_route(route_path):
    """Determine if a route should be skipped from testing requirements.

    Some routes are:
    - Auth callbacks (OAuth redirects)
    - POST-only operations embedded in GET routes
    - Dynamic routes that can't be easily tested
    """
    skip_patterns = [
        "/auth/google/callback",
        "/auth/gam/callback",
        "/test/auth",  # Test-only route
    ]

    for pattern in skip_patterns:
        if pattern in route_path:
            return True

    return False


def main():
    """Main checking function."""
    # Find all blueprint files
    admin_dir = Path("src/admin/blueprints")
    if not admin_dir.exists():
        print(f"❌ Error: {admin_dir} not found")
        return 1

    # Extract all GET routes
    all_routes = []
    for filepath in admin_dir.glob("*.py"):
        if filepath.name.startswith("__"):
            continue
        routes = extract_routes_from_blueprint(filepath)
        all_routes.extend(routes)

    print(f"📊 Found {len(all_routes)} GET routes in admin blueprints")

    # Filter out routes that should be skipped
    testable_routes = [r for r in all_routes if not should_skip_route(r["path"])]
    print(f"📋 {len(testable_routes)} routes should have tests")

    # Extract tested routes from test files
    test_dir = Path("tests/integration")
    test_files = [
        test_dir / "test_admin_ui_pages.py",
        test_dir / "test_admin_ui_routes_comprehensive.py",
    ]

    all_tested = set()
    for test_file in test_files:
        tested = extract_test_routes(test_file)
        all_tested.update(tested)

    print(f"✅ Found {len(all_tested)} routes with tests")

    # Check coverage
    missing_tests = []
    for route in testable_routes:
        path = route["path"]
        normalized = normalize_route_path(path)

        # Check if this route or its normalized version is tested
        if path not in all_tested and normalized not in all_tested:
            # Also check if any test includes this route pattern
            found = False
            for tested_path in all_tested:
                if normalized in tested_path or tested_path in normalized:
                    found = True
                    break

            if not found:
                missing_tests.append(route)

    if missing_tests:
        print(f"\n❌ Missing tests for {len(missing_tests)} routes:")
        print("=" * 80)
        for route in sorted(missing_tests, key=lambda x: (x["file"], x["path"])):
            print(f"  {route['file']:30} {route['path']}")

        print("\n" + "=" * 80)
        print(f"Coverage: {len(testable_routes) - len(missing_tests)}/{len(testable_routes)} routes")
        print("\nPlease add tests for these routes to:")
        print("  - tests/integration/test_admin_ui_routes_comprehensive.py")
        print("\nOr if a route truly cannot be tested, add it to should_skip_route()")
        return 1

    print(f"\n✅ All {len(testable_routes)} testable GET routes have tests!")
    print(f"Coverage: 100% ({len(testable_routes)}/{len(testable_routes)} routes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
