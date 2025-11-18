#!/usr/bin/env python3
"""Pre-commit hook to suggest factory usage in test files.

This hook detects manual object construction patterns in test files that
could use factory functions instead, promoting consistency and reducing boilerplate.
"""

import re
import sys
from pathlib import Path

# Pattern detection rules
PATTERNS = [
    {
        "name": "Database Product construction",
        "pattern": r"Product\(\s*tenant_id=",
        "factory": "create_test_db_product()",
        "message": "Use create_test_db_product() for database Product records",
        "example": """
# Instead of:
product = Product(
    tenant_id="test_tenant",
    product_id="test",
    name="Test",
    format_ids=[...],
    targeting_template={},
    delivery_type="guaranteed",
    property_tags=["all_inventory"],
)

# Use:
from tests.helpers.adcp_factories import create_test_db_product

product = create_test_db_product(
    tenant_id="test_tenant",
    product_id="test",
    name="Test",
    # Other fields use sensible defaults
)
""",
    },
    {
        "name": "PackageRequest construction",
        "pattern": r"PackageRequest\(\s*(product_id|buyer_ref|budget|pricing_option_id)=",
        "factory": "create_test_package_request()",
        "message": "Use create_test_package_request() for PackageRequest objects",
        "example": """
# Instead of:
pkg = PackageRequest(
    product_id="prod_1",
    buyer_ref="pkg_1",
    budget=5000.0,
    pricing_option_id="default",
)

# Use:
from tests.helpers.adcp_factories import create_test_package_request

pkg = create_test_package_request(
    product_id="prod_1",
    buyer_ref="pkg_1",
    budget=5000.0,
    pricing_option_id="default",
)
""",
    },
    {
        "name": "Package construction",
        "pattern": r"Package\(\s*package_id=",
        "factory": "create_test_package()",
        "message": "Use create_test_package() for Package response objects",
        "example": """
# Instead of:
package = Package(
    package_id="pkg_001",
    status="active",
    products=["prod_1"],
)

# Use:
from tests.helpers.adcp_factories import create_test_package

package = create_test_package(
    package_id="pkg_001",
    status="active",
    products=["prod_1"],
)
""",
    },
    {
        "name": "CreativeAsset construction",
        "pattern": r"CreativeAsset\(\s*(creative_id|name|format_id)=",
        "factory": "create_test_creative_asset()",
        "message": "Use create_test_creative_asset() for CreativeAsset objects",
        "example": """
# Instead of:
creative = CreativeAsset(
    creative_id="c1",
    name="Banner",
    format_id=FormatId(...),
    assets={"primary": {"url": "..."}},
)

# Use:
from tests.helpers.adcp_factories import create_test_creative_asset

creative = create_test_creative_asset(
    creative_id="c1",
    name="Banner",
    format_id="display_300x250",
)
""",
    },
    {
        "name": "FormatId construction",
        "pattern": r"FormatId\(\s*(agent_url|id)=",
        "factory": "create_test_format_id()",
        "message": "Use create_test_format_id() for FormatId objects",
        "example": """
# Instead of:
format_id = FormatId(
    agent_url="https://creative.example.com",
    id="display_300x250",
)

# Use:
from tests.helpers.adcp_factories import create_test_format_id

format_id = create_test_format_id("display_300x250")
""",
    },
    {
        "name": "BrandManifest construction",
        "pattern": r"BrandManifest\(\s*name=",
        "factory": "create_test_brand_manifest()",
        "message": "Use create_test_brand_manifest() for BrandManifest objects",
        "example": """
# Instead of:
brand = BrandManifest(
    name="Nike",
    promoted_offering="Air Jordan",
)

# Use:
from tests.helpers.adcp_factories import create_test_brand_manifest

brand = create_test_brand_manifest(
    name="Nike",
    promoted_offering="Air Jordan",
)
""",
    },
]


def check_file(filepath: Path) -> list[dict]:
    """Check a single file for manual construction patterns.

    Args:
        filepath: Path to the file to check

    Returns:
        List of issues found (empty if no issues)
    """
    # Only check test files
    if not str(filepath).startswith("tests/"):
        return []

    # Skip factory file itself
    if "adcp_factories.py" in str(filepath):
        return []

    content = filepath.read_text()
    issues = []

    for pattern_rule in PATTERNS:
        pattern = re.compile(pattern_rule["pattern"], re.MULTILINE)
        matches = list(pattern.finditer(content))

        if not matches:
            continue

        # Check if factory is already imported
        factory_name = pattern_rule["factory"].replace("()", "")
        factory_imported = re.search(rf"from tests\.helpers\.adcp_factories import .*{factory_name}", content)

        if factory_imported:
            # Factory is imported, so this is likely already using it
            continue

        # Count occurrences
        for match in matches:
            # Find line number
            line_num = content[: match.start()].count("\n") + 1

            issues.append(
                {
                    "file": str(filepath),
                    "line": line_num,
                    "pattern": pattern_rule["name"],
                    "factory": pattern_rule["factory"],
                    "message": pattern_rule["message"],
                    "example": pattern_rule["example"],
                }
            )

    return issues


def main():
    """Main entry point for pre-commit hook."""
    files = [Path(arg) for arg in sys.argv[1:]]
    all_issues = []

    for filepath in files:
        if not filepath.exists():
            continue
        issues = check_file(filepath)
        all_issues.extend(issues)

    if not all_issues:
        return 0

    # Group issues by file
    issues_by_file = {}
    for issue in all_issues:
        if issue["file"] not in issues_by_file:
            issues_by_file[issue["file"]] = []
        issues_by_file[issue["file"]].append(issue)

    # Print suggestions
    print("\n⚠️  Factory Usage Suggestions:")
    print("=" * 80)
    print()
    print("The following test files could benefit from using factory functions:")
    print()

    for filepath, issues in issues_by_file.items():
        print(f"📁 {filepath}")
        print()

        # Group by pattern
        patterns = {}
        for issue in issues:
            pattern = issue["pattern"]
            if pattern not in patterns:
                patterns[pattern] = []
            patterns[pattern].append(issue)

        for pattern, pattern_issues in patterns.items():
            count = len(pattern_issues)
            first_issue = pattern_issues[0]
            lines = [str(i["line"]) for i in pattern_issues]

            print(f"  🔍 {pattern} ({count} occurrence{'s' if count > 1 else ''})")
            print(f"     Lines: {', '.join(lines)}")
            print(f"     Suggestion: {first_issue['message']}")
            print(f"     Factory: {first_issue['factory']}")
            print()

        # Print example for first issue
        first_issue = issues[0]
        print("  📝 Example:")
        for line in first_issue["example"].strip().split("\n"):
            print(f"     {line}")
        print()
        print("-" * 80)
        print()

    print("💡 TIP: Run `pytest tests/helpers/README.md` to see full factory documentation")
    print()
    print("ℹ️  This is a suggestion only - the hook will not fail your commit.")
    print("   Consider refactoring to use factories for consistency and maintainability.")
    print()

    # Don't fail the commit - just warn
    return 0


if __name__ == "__main__":
    sys.exit(main())
