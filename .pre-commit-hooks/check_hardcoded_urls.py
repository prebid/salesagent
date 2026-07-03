#!/usr/bin/env python3
"""
Pre-commit hook to detect hardcoded URLs in JavaScript code.

Prevents bugs where JavaScript uses absolute paths like '/auth/login' or '/api/...'
instead of 'scriptRoot + /auth/login', which breaks when deployed behind a reverse
proxy with a URL prefix (e.g., /admin).

See CLAUDE.md section "JavaScript URL Handling - MANDATORY" for the correct pattern.
"""

import re
import sys
from pathlib import Path

# Patterns that indicate hardcoded URLs (common violations)
HARDCODED_URL_PATTERNS = [
    # Login/auth redirects without scriptRoot
    (r"window\.location\.href\s*=\s*['\"]/(auth|tenant)/", "Login redirect should use scriptRoot variable"),
    # API fetch calls without scriptRoot (both quoted strings and template literals)
    (r"fetch\s*\(\s*['\"`]/(api)/", "API fetch should use scriptRoot variable"),
    # Direct assignment of auth/api URLs (both quoted strings and template literals)
    (r"(const|let|var)\s+\w+\s*=\s*['\"`]/(auth|api|tenant)/", "URL variable should use scriptRoot prefix"),
]

# Exceptions that are allowed (correct patterns)
ALLOWED_PATTERNS = [
    r"scriptRoot\s*\+\s*['\"]/(auth|api|tenant)/",  # Correct: scriptRoot + '/auth/login'
    r"`\$\{scriptRoot\}/(auth|api|tenant)/",  # Correct: `${scriptRoot}/api/...`
    r"url_for\(['\"]",  # Python url_for() - correct
    r"//.*hardcoded",  # Comment mentioning hardcoded (documentation)
    r"❌.*hardcoded",  # Documentation showing wrong pattern
]


def check_file(filepath: Path) -> list[tuple[int, str, str]]:
    """
    Check a file for hardcoded URLs.

    Returns:
        List of (line_number, line_content, reason) tuples for violations found
    """
    violations = []

    try:
        content = filepath.read_text()
        lines = content.split("\n")

        for line_num, line in enumerate(lines, start=1):
            # Skip lines with allowed patterns
            if any(re.search(pattern, line) for pattern in ALLOWED_PATTERNS):
                continue

            # Check for hardcoded URL patterns
            for pattern, reason in HARDCODED_URL_PATTERNS:
                if re.search(pattern, line):
                    violations.append((line_num, line.strip(), reason))
                    break

    except Exception as e:
        print(f"Error reading {filepath}: {e}", file=sys.stderr)

    return violations


def main(filenames: list[str]) -> int:
    """
    Check all provided files for hardcoded URLs.

    Returns:
        0 if no violations, 1 if violations found or no files provided
    """
    if not filenames:
        print("check_hardcoded_urls: no files provided", file=sys.stderr)
        return 1

    all_violations = []

    for filename in filenames:
        filepath = Path(filename)
        if not filepath.exists():
            continue

        violations = check_file(filepath)
        if violations:
            all_violations.append((filepath, violations))

    if all_violations:
        print("❌ Found hardcoded URLs in JavaScript code!")
        print("\nJavaScript URLs must use 'scriptRoot' variable to support proxy deployments.")
        print("See CLAUDE.md section 'JavaScript URL Handling - MANDATORY' for the pattern.\n")

        for filepath, violations in all_violations:
            print(f"\n{filepath}:")
            for line_num, line, reason in violations:
                print(f"  Line {line_num}: {reason}")
                print(f"    {line}")

        print("\n✅ Correct pattern:")
        print("  const scriptRoot = '{{ request.script_root }}' || '';")
        print("  const url = scriptRoot + '/auth/login';")
        print("  fetch(scriptRoot + '/api/formats/list');")

        print("\n❌ Wrong pattern:")
        print("  window.location.href = '/auth/login';")
        print("  fetch('/api/formats/list');")

        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
