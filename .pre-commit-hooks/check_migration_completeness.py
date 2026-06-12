"""Pre-commit hook: warn when migration files are staged without Docker validation.

When alembic migration files are staged for commit, this hook reminds the
developer to run the full test suite with Docker to validate the migration
runs against a real PostgreSQL database.

`make quality` and `create_all()` tests do NOT exercise alembic migrations.
Only `./run_all_tests.sh ci` catches migration bugs.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.ci.migration_helpers import (
    MigrationParseError,
    is_empty_body,
    is_merge_migration,
    parse_function,
    parse_migration_tree,
)


def check_migration_file(path: Path) -> list[str]:
    """Check a single migration file for structural issues.

    Returns list of error messages (empty = OK).
    """
    errors = []
    try:
        tree = parse_migration_tree(path)
    except MigrationParseError as exc:
        errors.append(f"{path}: {exc}")
        return errors

    if is_merge_migration(tree):
        return errors

    upgrade = parse_function(tree, "upgrade")
    downgrade = parse_function(tree, "downgrade")

    if upgrade is None:
        errors.append(f"{path}: missing upgrade() function")

    if downgrade is None:
        errors.append(f"{path}: missing downgrade() function")

    for name, node in (("upgrade", upgrade), ("downgrade", downgrade)):
        if node is None:
            continue
        if is_empty_body(node):
            errors.append(f"{path}: {name}() is empty (only pass/docstring) — must contain migration logic")

    return errors


def main() -> int:
    migration_files = [Path(f) for f in sys.argv[1:] if f.startswith("alembic/versions/") and f.endswith(".py")]

    if not migration_files:
        return 0

    errors = []
    for path in migration_files:
        if not path.exists():
            continue
        errors.extend(check_migration_file(path))

    if errors:
        print("Migration file issues found:")
        for error in errors:
            print(f"  {error}")
        return 1

    print(
        "⚠️  Migration files staged. Validate with Docker before pushing:\n"
        "    ./run_all_tests.sh ci\n"
        "\n"
        "  make quality does NOT test migrations (uses create_all, not alembic).\n"
        "  Only ./run_all_tests.sh ci runs alembic upgrade/downgrade against real PostgreSQL."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
