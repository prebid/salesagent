#!/usr/bin/env python3
"""Analyze all admin routes and categorize them for testing."""

import re
from pathlib import Path


def extract_routes_from_file(filepath):
    """Extract route definitions from a blueprint file."""
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
            methods = [m.strip().strip("\"'") for m in methods.split(",")]
        else:
            methods = ["GET"]  # Default to GET if not specified

        routes.append({"file": filepath.name, "blueprint": blueprint_var, "path": path, "methods": methods})

    return routes


def categorize_routes(routes):
    """Categorize routes for testing purposes."""
    categories = {
        "testable_get": [],  # GET routes that can be tested
        "auth_required": [],  # Auth endpoints
        "api_endpoints": [],  # API/JSON endpoints
        "post_only": [],  # POST/PUT/DELETE only
        "requires_data": [],  # Need specific data setup
        "not_testable": [],  # Can't easily test
    }

    for route in routes:
        # Authentication routes
        if "/auth/" in route["path"] or "/login" in route["path"] or "/logout" in route["path"]:
            categories["auth_required"].append(route)
            continue

        # API endpoints (return JSON)
        if "/api/" in route["path"] or route["path"].startswith("/api/"):
            categories["api_endpoints"].append(route)
            continue

        # Only POST/PUT/DELETE
        if "GET" not in route["methods"]:
            categories["post_only"].append(route)
            continue

        # GET routes with specific IDs that need data
        if "<" in route["path"] and (
            "delete" in route["path"]
            or "edit" in route["path"]
            or "update" in route["path"]
            or "approve" in route["path"]
            or "reject" in route["path"]
        ):
            categories["requires_data"].append(route)
            continue

        # Testable GET routes
        if "GET" in route["methods"]:
            categories["testable_get"].append(route)
        else:
            categories["not_testable"].append(route)

    return categories


def main():
    """Main analysis function."""
    admin_dir = Path("src/admin/blueprints")

    all_routes = []
    for filepath in admin_dir.glob("*.py"):
        if filepath.name.startswith("__"):
            continue
        routes = extract_routes_from_file(filepath)
        all_routes.extend(routes)

    print(f"📊 Total routes found: {len(all_routes)}\n")

    categories = categorize_routes(all_routes)

    for category, routes in categories.items():
        print(f"\n{category.upper().replace('_', ' ')} ({len(routes)} routes):")
        print("=" * 80)
        for route in sorted(routes, key=lambda x: (x["file"], x["path"])):
            methods_str = ",".join(route["methods"])
            print(f"  {route['file']:30} {methods_str:20} {route['path']}")

    print("\n\n📈 SUMMARY:")
    print(f"  Testable GET routes: {len(categories['testable_get'])}")
    print(f"  Auth routes: {len(categories['auth_required'])}")
    print(f"  API endpoints: {len(categories['api_endpoints'])}")
    print(f"  POST-only routes: {len(categories['post_only'])}")
    print(f"  Requires data setup: {len(categories['requires_data'])}")
    print(f"  Not easily testable: {len(categories['not_testable'])}")


if __name__ == "__main__":
    main()
