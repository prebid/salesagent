#!/usr/bin/env python3
"""
Pre-commit hook to ensure GAM client initialization supports both OAuth and service account auth.

This hook catches patterns where AdManagerClient is created but only one auth method is checked.
Prevents regressions like the one where targeting values endpoint only supported OAuth.
"""

import re
import sys
from pathlib import Path

# Files/patterns that are intentionally OAuth-only (setup/testing)
ALLOWED_OAUTH_ONLY = [
    "test_gam_connection",  # API endpoint that tests OAuth tokens
    "detect_gam_network",  # Auto-detect network from OAuth token
    "test_",  # Test files can use either auth method
]

# Patterns that suggest GAM client creation
GAM_CLIENT_PATTERNS = [
    r"ad_manager\.AdManagerClient\(",
    r"AdManagerClient\(",
]

# Anti-patterns: Checking only for refresh_token without service_account
OAUTH_ONLY_PATTERNS = [
    r"if\s+not\s+.*gam_refresh_token",  # Checking only refresh token
    r"oauth2\.GoogleRefreshTokenClient\(",  # Creating OAuth client
]

# Good patterns: Checking for both auth methods
BOTH_AUTH_PATTERNS = [
    r"gam_service_account",  # Mentions service account
    r"service_account_json",  # Mentions service account JSON
    r"auth_method",  # Checks auth method
    r"GAMAuthManager",  # Uses the auth manager (supports both)
]


def is_allowed_oauth_only(file_path: str, content: str) -> bool:
    """Check if this file/function is allowed to be OAuth-only."""
    for allowed in ALLOWED_OAUTH_ONLY:
        if allowed in file_path or allowed in content:
            return True
    return False


def check_file(file_path: str) -> list[str]:
    """Check a single file for GAM auth issues."""
    errors = []

    try:
        content = Path(file_path).read_text()
    except Exception as e:
        print(f"Warning: Could not read {file_path}: {e}", file=sys.stderr)
        return errors

    # Check if file creates GAM clients
    creates_gam_client = any(re.search(pattern, content) for pattern in GAM_CLIENT_PATTERNS)

    if not creates_gam_client:
        return errors  # No GAM clients, nothing to check

    # Check if it's intentionally OAuth-only
    if is_allowed_oauth_only(file_path, content):
        return errors  # Allowed to be OAuth-only

    # Check if it uses OAuth-only patterns
    uses_oauth_only = any(re.search(pattern, content) for pattern in OAUTH_ONLY_PATTERNS)

    if not uses_oauth_only:
        return errors  # No OAuth-only patterns found

    # Check if it also mentions service accounts
    mentions_service_account = any(re.search(pattern, content, re.IGNORECASE) for pattern in BOTH_AUTH_PATTERNS)

    if not mentions_service_account:
        errors.append(
            f"{file_path}: Creates GAM client but only checks for OAuth refresh token.\n"
            f"  This file should support BOTH OAuth and service account authentication.\n"
            f"  See src/admin/routers/inventory.py:get_targeting_values() for example.\n"
            f"  Check for EITHER gam_refresh_token OR gam_service_account_json."
        )

    return errors


def main():
    """Check all staged Python files for GAM auth issues."""
    import subprocess

    # Get list of staged Python files
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"], capture_output=True, text=True, check=True
        )
        files = [f for f in result.stdout.strip().split("\n") if f.endswith(".py")]
    except subprocess.CalledProcessError:
        print("Error: Could not get list of staged files", file=sys.stderr)
        return 1

    if not files:
        return 0  # No Python files staged

    all_errors = []
    for file_path in files:
        if Path(file_path).exists():
            errors = check_file(file_path)
            all_errors.extend(errors)

    if all_errors:
        print("❌ GAM Authentication Support Check Failed:", file=sys.stderr)
        print(file=sys.stderr)
        for error in all_errors:
            print(error, file=sys.stderr)
            print(file=sys.stderr)
        print("💡 Tip: Production endpoints must support BOTH OAuth and service account auth.", file=sys.stderr)
        print("   Only setup/testing endpoints (in api.py, gam.py) can be OAuth-only.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
