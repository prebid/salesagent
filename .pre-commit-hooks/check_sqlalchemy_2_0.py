#!/usr/bin/env python3
"""
Pre-commit hook to enforce SQLAlchemy 2.0 patterns.

Prevents new code from using legacy session.query() pattern.
Existing legacy patterns are documented and allowed with # legacy-ok comment.

Usage:
    pre-commit run enforce-sqlalchemy-2-0 --all-files
"""

import re
import sys


def check_file(filepath: str) -> list[str]:
    """Check a file for legacy SQLAlchemy patterns.

    Returns list of error messages (empty if file is clean).
    """
    errors = []

    # Read file
    try:
        with open(filepath) as f:
            lines = f.readlines()
    except Exception as e:
        return [f"Error reading {filepath}: {e}"]

    # Pattern to detect legacy session.query()
    legacy_pattern = re.compile(
        r"(session|db_session|Session)\.query\(",
        re.IGNORECASE,
    )

    for line_num, line in enumerate(lines, start=1):
        # Skip if explicitly marked as legacy-ok
        if "# legacy-ok" in line or "# noqa" in line:
            continue

        # Skip test files (we're stricter with production code)
        if "test_" in filepath:
            continue

        # Check for legacy pattern
        if legacy_pattern.search(line):
            errors.append(
                f"{filepath}:{line_num}: Found legacy session.query() pattern\n"
                f"  Line: {line.strip()}\n"
                f"  Use SQLAlchemy 2.0 pattern instead:\n"
                f"    stmt = select(Model).filter_by(...)\n"
                f"    result = session.scalars(stmt).first()  # or .all()\n"
            )

    return errors


def main():
    """Check all provided files."""
    if len(sys.argv) < 2:
        print("Usage: check_sqlalchemy_2_0.py <file1> [file2 ...]")
        sys.exit(0)

    all_errors = []

    for filepath in sys.argv[1:]:
        # Only check Python files in src/ and product_catalog_providers/
        if not filepath.endswith(".py"):
            continue

        if not (filepath.startswith("src/") or filepath.startswith("product_catalog_providers/")):
            continue

        errors = check_file(filepath)
        all_errors.extend(errors)

    if all_errors:
        print("❌ SQLAlchemy 2.0 Pattern Enforcement Failed\n")
        print("Found legacy session.query() patterns in changed files:\n")
        for error in all_errors:
            print(error)

        print("\n📖 See CLAUDE.md for SQLAlchemy 2.0 migration guide")
        print("💡 To mark existing legacy code, add: # legacy-ok")
        sys.exit(1)

    # Success
    sys.exit(0)


if __name__ == "__main__":
    main()
