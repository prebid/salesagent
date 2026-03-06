#!/usr/bin/env python3
"""Pre-commit hook to detect tenant context ordering bugs.

This hook checks that all MCP tool implementations use authentication
(identity.principal_id, identity.tenant, or get_principal_from_context)
BEFORE calling get_current_tenant().

Prevents regression of the bug fixed in update_media_buy where
get_current_tenant() was called before tenant context was established.
"""

import re
import sys
from pathlib import Path


def check_file(file_path: Path) -> list[str]:
    """Check a single file for tenant context ordering issues.

    Args:
        file_path: Path to file to check

    Returns:
        List of error messages (empty if no issues)
    """
    content = file_path.read_text()
    errors = []

    # Find all function definitions
    # Look for def _*_impl( or async def _*_impl( functions (implementation functions)
    impl_pattern = re.compile(r"(?:async\s+)?def\s+_(\w+)_impl\s*\(", re.MULTILINE)

    for match in impl_pattern.finditer(content):
        func_name = match.group(1)
        func_start = match.start()

        # Find the end of this function (next function definition or end of file)
        next_func = re.search(r"\n(?:async\s+)?def\s+", content[func_start + len(match.group(0)) :])
        func_end = func_start + len(match.group(0)) + next_func.start() if next_func else len(content)

        func_body = content[func_start:func_end]

        # Check if function uses get_current_tenant()
        tenant_match = re.search(r"get_current_tenant\s*\(", func_body)
        if not tenant_match:
            continue  # No tenant usage, skip

        # Check if function has auth call before tenant call
        auth_patterns = [
            r"identity\.principal_id",
            r"identity\.tenant",
            r"get_principal_from_context\s*\(",
        ]

        auth_pos = None
        auth_pattern_used = None
        for pattern in auth_patterns:
            auth_match = re.search(pattern, func_body)
            if auth_match:
                if auth_pos is None or auth_match.start() < auth_pos:
                    auth_pos = auth_match.start()
                    auth_pattern_used = pattern.replace(r"\s*\(", "")

        tenant_pos = tenant_match.start()

        if auth_pos is None:
            # Uses get_current_tenant() but no auth pattern - potential bug
            line_num = content[:func_start].count("\n") + 1
            errors.append(
                f"{file_path}:{line_num}: "
                f"Function '_{func_name}_impl' calls get_current_tenant() "
                f"but does not access identity.principal_id or identity.tenant first. "
                f"This will cause 'No tenant context set' errors."
            )
        elif auth_pos > tenant_pos:
            # Auth call comes after tenant call - definitely a bug!
            line_num = content[:func_start].count("\n") + 1
            errors.append(
                f"{file_path}:{line_num}: "
                f"BUG: Function '_{func_name}_impl' calls get_current_tenant() "
                f"BEFORE {auth_pattern_used}(). This causes 'No tenant context set' errors. "
                f"FIX: Move the auth call to before get_current_tenant()."
            )

    return errors


def main():
    """Main entry point for pre-commit hook."""
    # Get list of files to check from arguments
    files_to_check = [Path(f) for f in sys.argv[1:]]

    # Only check tool implementation files
    tool_files = [
        f
        for f in files_to_check
        if f.suffix == ".py" and "/tools/" in str(f) and f.name not in ["__init__.py", "tool_context.py"]
    ]

    if not tool_files:
        return 0  # No tool files to check

    all_errors = []
    for file_path in tool_files:
        errors = check_file(file_path)
        all_errors.extend(errors)

    if all_errors:
        print("❌ Tenant context ordering errors detected:")
        print()
        for error in all_errors:
            print(f"  {error}")
        print()
        print("CRITICAL: All tool _impl functions must access identity.principal_id")
        print("or identity.tenant BEFORE calling get_current_tenant().")
        print()
        print("Correct pattern:")
        print("  1. identity = resolve_identity(...)  # At transport boundary")
        print("  2. principal_id = identity.principal_id  # In _impl")
        print("  3. tenant = get_current_tenant()  # Now safe")
        print()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
