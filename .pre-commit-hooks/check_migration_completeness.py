"""Pre-commit hook: warn when migration files are staged without Docker validation.

When alembic migration files are staged for commit, this hook reminds the
developer to run the full test suite with Docker to validate the migration
runs against a real PostgreSQL database.

`make quality` and `create_all()` tests do NOT exercise alembic migrations.
Only `./run_all_tests.sh ci` catches migration bugs.
"""

import ast
import sys
from pathlib import Path


def check_migration_file(path: Path) -> list[str]:
    """Check a single migration file for structural issues.

    Returns list of error messages (empty = OK).
    """
    errors = []
    source = path.read_text()

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        errors.append(f"{path}: SyntaxError: {e}")
        return errors

    # Find upgrade() and downgrade() functions
    functions = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("upgrade", "downgrade"):
            functions[node.name] = node

    if "upgrade" not in functions:
        errors.append(f"{path}: missing upgrade() function")

    if "downgrade" not in functions:
        errors.append(f"{path}: missing downgrade() function")

    # Check for non-empty bodies (not just `pass` or docstring-only)
    for name, node in functions.items():
        body = node.body
        # Filter out docstrings and pass statements
        meaningful = [
            stmt
            for stmt in body
            if not (isinstance(stmt, ast.Pass))
            and not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
        ]
        if not meaningful:
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

    # Always warn to run Docker validation
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
