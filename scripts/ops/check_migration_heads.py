#!/usr/bin/env python3
"""Check for and optionally fix Alembic multiple heads.

This script detects when multiple migration heads exist and can automatically
create a merge migration to resolve them.

Usage:
    python check_migration_heads.py              # Check only (exit 1 if multiple heads)
    python check_migration_heads.py --fix        # Auto-create merge migration
    python check_migration_heads.py --quiet      # Suppress output unless error
"""

import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command


def get_heads(alembic_cfg: Config) -> list[str]:
    """Get list of current migration heads."""
    script = ScriptDirectory.from_config(alembic_cfg)
    heads = script.get_revisions("heads")
    return [head.revision for head in heads]


def create_merge_migration(alembic_cfg: Config, heads: list[str]) -> str:
    """Create a merge migration for multiple heads.

    Args:
        alembic_cfg: Alembic configuration
        heads: List of head revision IDs to merge

    Returns:
        str: Revision ID of the created merge migration
    """
    # Create merge message
    message = "Merge migration heads"

    # Create merge migration
    print(f"Creating merge migration for heads: {', '.join(heads)}")
    result = command.merge(alembic_cfg, message=message, revisions=",".join(heads))

    print(f"✅ Created merge migration: {result}")
    return result


def check_and_fix_heads(fix: bool = False, quiet: bool = False) -> tuple[bool, str | None]:
    """Check for multiple heads and optionally fix.

    Args:
        fix: If True, automatically create merge migration
        quiet: If True, suppress non-error output

    Returns:
        tuple: (has_multiple_heads, merge_revision_id or error_message)
    """
    # Get project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent
    alembic_ini_path = project_root / "alembic.ini"

    if not alembic_ini_path.exists():
        return False, "alembic.ini not found"

    # Create Alembic configuration
    alembic_cfg = Config(str(alembic_ini_path))

    # Get current heads
    try:
        heads = get_heads(alembic_cfg)
    except Exception as e:
        return False, f"Error getting heads: {e}"

    if len(heads) <= 1:
        if not quiet:
            if len(heads) == 0:
                print("✅ No migration heads found")
            else:
                print(f"✅ Single migration head: {heads[0]}")
        return False, None

    # Multiple heads detected
    if not quiet:
        print(f"⚠️  Multiple migration heads detected: {', '.join(heads)}")

    if fix:
        try:
            merge_rev = create_merge_migration(alembic_cfg, heads)
            return True, merge_rev
        except Exception as e:
            return True, f"Error creating merge migration: {e}"
    else:
        return True, "Multiple heads detected (use --fix to auto-merge)"


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Check for Alembic multiple heads")
    parser.add_argument("--fix", action="store_true", help="Automatically create merge migration")
    parser.add_argument("--quiet", action="store_true", help="Suppress non-error output")
    args = parser.parse_args()

    has_multiple_heads, message = check_and_fix_heads(fix=args.fix, quiet=args.quiet)

    if has_multiple_heads:
        if not args.quiet:
            print(f"\n❌ {message}")
            print("\nTo fix:")
            print("  python scripts/ops/check_migration_heads.py --fix")
            print("\nOr manually:")
            print("  uv run alembic merge -m 'Merge migration heads' head")
        sys.exit(1)
    elif message and not args.quiet:
        # Error occurred
        print(f"❌ {message}")
        sys.exit(1)

    # Success
    if args.fix and not args.quiet:
        print("\n✅ Migration heads merged successfully")
    sys.exit(0)


if __name__ == "__main__":
    main()
