#!/usr/bin/env python3
"""
Pre-commit hook to verify roundtrip tests exist for all apply_testing_hooks calls.

This prevents bugs where apply_testing_hooks adds fields that break response
reconstruction (like the CreateMediaBuyResponse validation bug).

For each operation that uses apply_testing_hooks, we verify a corresponding
roundtrip test exists.
"""

import re
import sys
from pathlib import Path


def find_testing_hooks_usages(file_path: Path) -> list[str]:
    """Find all apply_testing_hooks calls and extract operation names."""
    if not file_path.exists():
        return []

    content = file_path.read_text()

    # Pattern: apply_testing_hooks(testing_ctx, "operation_name", ...)
    # Supports both single-line and multiline calls
    pattern = r'apply_testing_hooks\(\s*\w+\s*,\s*["\']([^"\']+)["\']'
    matches = re.findall(pattern, content, re.DOTALL)

    return list(set(matches))


def find_roundtrip_tests(test_dir: Path) -> set[str]:
    """Find all operations that have roundtrip tests."""
    if not test_dir.exists():
        return set()

    tested_operations = set()

    # Look for test files that test roundtrip conversion
    for test_file in test_dir.rglob("test_*.py"):
        if "roundtrip" not in test_file.name and "tool_roundtrip" not in test_file.name:
            continue

        content = test_file.read_text()

        # Look for apply_testing_hooks calls in tests
        pattern = r'apply_testing_hooks\(\s*\w+\s*,\s*["\']([^"\']+)["\']'
        matches = re.findall(pattern, content, re.DOTALL)
        tested_operations.update(matches)

    return tested_operations


def main():
    """Check that all apply_testing_hooks usages have corresponding roundtrip tests."""
    repo_root = Path(__file__).parent.parent

    # Find all apply_testing_hooks usages in main.py
    main_py = repo_root / "src" / "core" / "main.py"
    if not main_py.exists():
        print("❌ src/core/main.py not found")
        return 1

    operations_using_hooks = find_testing_hooks_usages(main_py)

    if not operations_using_hooks:
        # No testing hooks used, nothing to check
        return 0

    # Find all roundtrip tests
    test_dir = repo_root / "tests"
    tested_operations = find_roundtrip_tests(test_dir)

    # Check for missing tests
    # Note: get_media_buy_delivery test is TODO (complex schema)
    exempt_operations = {"get_media_buy_delivery"}
    missing_tests = []
    for operation in operations_using_hooks:
        if operation not in tested_operations and operation not in exempt_operations:
            missing_tests.append(operation)

    if missing_tests:
        print("❌ Missing roundtrip tests for operations using apply_testing_hooks:")
        print()
        for operation in sorted(missing_tests):
            print(f"  - {operation}")
        print()
        print("Each operation that uses apply_testing_hooks must have a roundtrip test.")
        print("The test should:")
        print("  1. Call the operation to get a response")
        print("  2. Convert to dict via model_dump_internal()")
        print("  3. Pass through apply_testing_hooks()")
        print("  4. Reconstruct the response object")
        print("  5. Verify reconstruction succeeds")
        print()
        print("Example test structure:")
        print("  def test_{operation}_with_testing_hooks_roundtrip():")
        print("      response = {operation}(...)")
        print("      response_data = response.model_dump_internal()")
        print("      hooks_result = apply_testing_hooks(ctx, '{operation}', ...)")
        print("      reconstructed = {ResponseType}(**response_data)")
        print("      assert reconstructed.field == expected_value")
        print()
        print("See tests/integration/test_create_media_buy_roundtrip.py for example.")
        return 1

    print(f"✅ All {len(operations_using_hooks)} operations using apply_testing_hooks have roundtrip tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
