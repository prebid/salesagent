#!/usr/bin/env python3
"""
Pre-commit hook to flag defensive RootModel unwrapping patterns.

Checks for:
1. hasattr(x, "root") — defensive guard against known types
2. hasattr(x, 'root') — same with single quotes

These patterns indicate type ambiguity that shouldn't exist in typed code.
Use direct .root access when the type is known, or model_dump() for serialization.

Allowed exceptions:
- model_validator(mode="before") pre-validators where input types are mixed
- a2a-sdk types (TextPart/DataPart) that are genuinely polymorphic
- Lines with # noqa: rootmodel comment

Usage:
    pre-commit run check-rootmodel-access --all-files
"""

import re
import sys

PATTERN = re.compile(r"""hasattr\([^,]+,\s*["']root["']\)""")

# Files with legitimate uses (pre-validators with mixed input types, a2a-sdk types)
ALLOWED_FILES = {
    "src/a2a_server/adcp_a2a_server.py",  # a2a-sdk TextPart/DataPart — genuinely polymorphic
}


def check_file(filepath: str) -> list[str]:
    """Check a file for defensive RootModel unwrapping patterns."""
    errors = []

    # Skip allowed files
    for allowed in ALLOWED_FILES:
        if filepath.endswith(allowed):
            return []

    try:
        with open(filepath) as f:
            lines = f.readlines()
    except Exception:
        return []

    for i, line in enumerate(lines, 1):
        # Skip lines with noqa comment
        if "# noqa: rootmodel" in line:
            continue

        if PATTERN.search(line):
            errors.append(f"{filepath}:{i}: hasattr(x, 'root') — use direct .root access or model_dump()")

    return errors


def main() -> int:
    errors = []
    for filepath in sys.argv[1:]:
        # Only check Python files in src/ and tests/
        if not filepath.endswith(".py"):
            continue
        if not (filepath.startswith("src/") or filepath.startswith("tests/")):
            continue
        errors.extend(check_file(filepath))

    if errors:
        print("RootModel defensive access patterns found:")
        print("  Use direct .root access (type is known) or model_dump() for serialization.")
        print("  Add '# noqa: rootmodel' comment if the pattern is genuinely needed.")
        print()
        for error in errors:
            print(f"  {error}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
