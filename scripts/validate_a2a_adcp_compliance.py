#!/usr/bin/env python3
"""
Validate that A2A handlers only accept AdCP spec-compliant parameters.

This prevents regressions where legacy non-spec formats are accepted.
"""

import os
import sys

# Add parent directories to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check_create_media_buy_validation():
    """Verify that create_media_buy handler validates AdCP spec parameters."""
    print("🔍 Checking create_media_buy parameter validation...")

    file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "a2a_server", "adcp_a2a_server.py")

    if not os.path.exists(file_path):
        print(f"❌ Source file not found: {file_path}")
        return False

    with open(file_path) as f:
        content = f.read()

    # Check that we validate AdCP spec parameters
    required_checks = [
        '"packages"',  # Must check for packages (not product_ids)
        '"budget"',  # Must check for budget (not total_budget)
        '"start_time"',  # Must check for start_time (not start_date/flight_start_date)
        '"end_time"',  # Must check for end_time (not end_date/flight_end_date)
    ]

    # Find the _handle_create_media_buy_skill function
    if "_handle_create_media_buy_skill" not in content:
        print("❌ create_media_buy handler not found")
        return False

    # Extract the function
    start = content.find("async def _handle_create_media_buy_skill")
    if start == -1:
        print("❌ Could not find create_media_buy handler")
        return False

    # Find the next function definition to get the end
    next_func = content.find("\n    async def ", start + 1)
    if next_func == -1:
        next_func = content.find("\n    def ", start + 1)
    if next_func == -1:
        next_func = len(content)

    function_content = content[start:next_func]

    # Verify all required checks are present
    missing_checks = []
    for check in required_checks:
        if check not in function_content:
            missing_checks.append(check)

    if missing_checks:
        print(f"❌ REGRESSION: Missing AdCP spec parameter validation for: {missing_checks}")
        return False

    # Verify legacy parameters are NOT accepted
    legacy_params = ['"product_ids"', '"total_budget"', '"flight_start_date"', '"flight_end_date"']

    found_legacy = []
    for legacy in legacy_params:
        if f"parameters[{legacy}]" in function_content or f"parameters.get({legacy})" in function_content:
            found_legacy.append(legacy)

    if found_legacy:
        print(f"❌ REGRESSION: Legacy parameters are being used: {found_legacy}")
        print("   A2A handler must ONLY accept AdCP spec format")
        return False

    print("✅ create_media_buy validates AdCP spec parameters correctly")
    return True


def check_adcp_spec_documentation():
    """Verify that handler documentation mentions spec compliance."""
    print("🔍 Checking AdCP spec documentation in handlers...")

    file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "a2a_server", "adcp_a2a_server.py")

    with open(file_path) as f:
        content = f.read()

    # Find create_media_buy handler
    handler_start = content.find("async def _handle_create_media_buy_skill")
    if handler_start == -1:
        print("❌ Handler not found")
        return False

    # Get the docstring
    docstring_start = content.find('"""', handler_start)
    if docstring_start == -1:
        print("❌ No docstring found")
        return False

    docstring_end = content.find('"""', docstring_start + 3)
    docstring = content[docstring_start : docstring_end + 3]

    # Check for important documentation
    required_terms = ["AdCP", "spec", "packages", "budget"]

    missing_terms = []
    for term in required_terms:
        if term.lower() not in docstring.lower():
            missing_terms.append(term)

    if missing_terms:
        print(f"⚠️  Docstring should mention: {missing_terms}")
        print("   (Not critical, but improves clarity)")

    print("✅ Handler has AdCP spec documentation")
    return True


def check_no_legacy_format_in_error_messages():
    """Verify that error messages and examples use spec format, not legacy."""
    print("🔍 Checking that error messages use spec format...")

    file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "a2a_server", "adcp_a2a_server.py")

    with open(file_path) as f:
        content = f.read()

    # Check for legacy format in error messages and examples
    legacy_terms = ['"product_ids"', '"total_budget"', '"flight_start_date"', '"flight_end_date"']

    found_legacy_in_messages = []
    for legacy_term in legacy_terms:
        # Look for these in return statements and examples
        if legacy_term in content:
            # Check if it's in a return statement or example (not in comments or docstrings explaining what NOT to do)
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if legacy_term in line and ("return" in line or "example" in line.lower() or "required_fields" in line):
                    # Skip if this is in a comment explaining what NOT to use
                    if "# ❌" not in line and "Legacy" not in lines[max(0, i - 5) : i + 1]:
                        found_legacy_in_messages.append((i + 1, line.strip(), legacy_term))

    if found_legacy_in_messages:
        print("❌ REGRESSION: Legacy format found in error messages/examples:")
        for line_num, line_content, term in found_legacy_in_messages:
            print(f"   Line {line_num}: {term} in '{line_content[:80]}...'")
        print("   All error messages and examples MUST use AdCP spec format")
        return False

    print("✅ No legacy format in error messages or examples")
    return True


def main():
    """Run all validation checks."""
    print("🚀 Running A2A AdCP Compliance Validation...\n")

    tests = [
        check_create_media_buy_validation,
        check_adcp_spec_documentation,
        check_no_legacy_format_in_error_messages,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ Test {test.__name__} crashed: {e}")
            failed += 1
        print()  # Add spacing

    print(f"📊 Results: {passed} passed, {failed} failed")

    if failed == 0:
        print("🎉 All AdCP compliance validation tests passed!")
        return True
    else:
        print("⚠️  AdCP compliance tests failed - A2A handlers must accept ONLY spec format")
        print("   See https://adcontextprotocol.org/docs/ for spec details")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
