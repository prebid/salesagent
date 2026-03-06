#!/usr/bin/env python3
"""Pre-commit hook to detect unsafe response attribute access patterns.

This hook prevents bugs like:
    response.message  # ❌ Unsafe - not all response types have .message
    str(response)     # ✅ Safe - works for all response types

The hook detects patterns where we access specific attributes on response objects
that might not exist on all response types, and suggests using str() instead.

Usage:
    python scripts/hooks/check_response_attribute_access.py [files...]

Exit codes:
    0 - No unsafe patterns found
    1 - Unsafe patterns detected
"""

import re
import sys
from pathlib import Path

# Patterns that indicate unsafe attribute access on response objects
# Note: These patterns specifically look for variables named 'response' to avoid false positives
UNSAFE_PATTERNS = [
    # response.message (not all responses have this)
    (
        r"\bresponse\.message(?!\s*=)",  # Specifically 'response' variable
        "response.message",
        "str(response)",
        "Not all response types have a .message attribute. Use str(response) instead.",
    ),
    # response.detail (some responses use this, but not standardized)
    (
        r"\bresponse\.detail(?!\s*=)",
        "response.detail",
        "str(response) or check hasattr() first",
        "Not all response types have a .detail attribute.",
    ),
    # result.message (common alias for response)
    (
        r"\bresult\.message(?!\s*=)",
        "result.message",
        "str(result)",
        "Not all response types have a .message attribute. Use str(result) instead.",
    ),
]

# Exceptions: Files where these patterns are allowed
ALLOWED_FILES = {
    "src/core/schemas/",  # Schema definitions can access their own fields
    "tests/unit/test_all_response_str_methods.py",  # Tests validating __str__ methods
    "scripts/hooks/check_response_attribute_access.py",  # This file
}

# Exceptions: Lines containing these strings are allowed
ALLOWED_LINE_PATTERNS = [
    "hasattr(",  # Checking for attribute existence is safe
    "getattr(",  # Using getattr is safe
    "if response.message",  # Explicit checking is okay
    "response.message is not None",  # Explicit None checking is okay
    "# noqa: response-attribute",  # Explicit override comment
    "def message(",  # Method definitions
    "message:",  # Type hints or dict keys
    '"message"',  # String literal
    "'message'",  # String literal
]


def check_file(file_path: Path) -> list[tuple[int, str, str]]:
    """Check a single file for unsafe response attribute access patterns.

    Args:
        file_path: Path to the file to check

    Returns:
        List of tuples: (line_number, line_content, error_message)
    """
    if not file_path.exists() or not file_path.is_file():
        return []

    # Skip allowed files
    if str(file_path) in ALLOWED_FILES or any(allowed in str(file_path) for allowed in ALLOWED_FILES):
        return []

    # Skip non-Python files
    if file_path.suffix != ".py":
        return []

    errors = []

    try:
        with open(file_path, encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                # Skip allowed line patterns
                if any(pattern in line for pattern in ALLOWED_LINE_PATTERNS):
                    continue

                # Skip comments and docstrings
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue

                # Check each unsafe pattern
                for pattern, unsafe, safe, message in UNSAFE_PATTERNS:
                    if re.search(pattern, line):
                        error_msg = (
                            f"Unsafe attribute access: {unsafe}\n"
                            f"  Suggestion: Use {safe}\n"
                            f"  Reason: {message}\n"
                            f"  To override: Add # noqa: response-attribute"
                        )
                        errors.append((line_num, line.rstrip(), error_msg))

    except Exception as e:
        print(f"Warning: Could not check {file_path}: {e}", file=sys.stderr)

    return errors


def main() -> int:
    """Main entry point.

    Returns:
        Exit code: 0 if no issues, 1 if issues found
    """
    if len(sys.argv) < 2:
        print("Usage: check_response_attribute_access.py [files...]")
        return 0

    files_to_check = [Path(f) for f in sys.argv[1:]]
    all_errors = []

    for file_path in files_to_check:
        errors = check_file(file_path)
        if errors:
            all_errors.extend([(file_path, line_num, line, msg) for line_num, line, msg in errors])

    if all_errors:
        print("❌ Unsafe response attribute access patterns detected:\n")
        for file_path, line_num, line, msg in all_errors:
            print(f"{file_path}:{line_num}")
            print(f"  {line}")
            print(f"  {msg}\n")

        print("💡 Why this matters:")
        print("   Not all response types have the same attributes. For example:")
        print("   - CreateMediaBuyResponse has NO .message field (uses __str__ method)")
        print("   - GetProductsResponse HAS a .message field")
        print("   - Using str(response) works for BOTH patterns safely\n")

        print("✅ Safe patterns:")
        print("   response.message               ❌ Unsafe")
        print("   str(response)                  ✅ Safe")
        print("   getattr(response, 'message')   ✅ Safe")
        print("   hasattr(response, 'message')   ✅ Safe\n")

        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
