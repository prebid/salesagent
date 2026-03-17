"""Guard: every alembic migration must have non-empty upgrade() and downgrade().

A migration with an empty downgrade is unrecoverable in production. A migration
with an empty upgrade is dead code that clutters the migration chain.

Merge migrations (empty upgrade + empty downgrade) are exempt — they only
reconcile branch heads and contain no schema changes.

This guard also checks that downgrade() reverses the structural changes made by
upgrade() — specifically, that if upgrade() creates/drops tables, constraints,
or columns, the downgrade() references the same tables.

beads: salesagent-t735
"""

import ast

from tests.unit._migration_helpers import (
    MIGRATIONS_DIR,
    get_migration_files,
    is_empty_body,
    is_merge_migration,
    parse_function,
)

# Alembic operations that modify schema structure
SCHEMA_OPS = {
    "create_table",
    "drop_table",
    "add_column",
    "drop_column",
    "create_index",
    "drop_index",
    "create_foreign_key",
    "drop_constraint",
    "create_primary_key",
    "create_unique_constraint",
    "alter_column",
    "create_check_constraint",
}

# Pre-existing violations — allowlists shrink as violations are fixed.
# FIXME(salesagent-t735): These legacy migrations have incomplete downgrades.
KNOWN_EMPTY_DOWNGRADE = {
    # Legacy: data migration (adds default values), no structural revert needed
    "017_handle_partial_schemas.py",
    # Legacy: fixes JSON encoding, no structural revert
    "e81e275c9b29_fix_price_guidance_json_encoding.py",
}

KNOWN_DOWNGRADE_COVERAGE_GAPS = {
    # Legacy: upgrade creates index but downgrade doesn't drop it
    "015_workflow_improvements.py",
    # Legacy: upgrade creates indexes/FKs but downgrade drops tables (indexes go with them)
    "020_fix_tasks_schema_properly_fix_tasks_schema_properly.py",
    # Legacy: upgrade adds column to tenants but downgrade doesn't revert
    "ebcb8dda247a_add_naming_templates_to_tenants.py",
}


def _extract_table_names(node: ast.FunctionDef) -> set[str]:
    """Extract table names referenced in op.XXX() calls."""
    tables = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "op" and func.attr in SCHEMA_OPS:
                # First string argument is usually the table name
                for arg in child.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        tables.add(arg.value)
                        break
    return tables


class TestMigrationCompleteness:
    """Every non-merge migration must have non-empty upgrade() and downgrade()."""

    def test_non_merge_migrations_have_upgrade(self):
        """Every non-merge migration must define a non-empty upgrade() function."""
        missing = []
        empty = []

        for path in get_migration_files():
            source = path.read_text()
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:
                continue

            if is_merge_migration(tree):
                continue

            func = parse_function(tree, "upgrade")
            if func is None:
                missing.append(path.name)
            elif is_empty_body(func):
                empty.append(path.name)

        violations = []
        if missing:
            violations.append(f"Missing upgrade(): {', '.join(missing)}")
        if empty:
            violations.append(f"Empty upgrade() (not a merge migration): {', '.join(empty)}")

        assert not violations, "Migration completeness violations:\n" + "\n".join(f"  {v}" for v in violations)

    def test_non_merge_migrations_have_downgrade(self):
        """Every non-merge migration must define a non-empty downgrade() function."""
        missing = []
        empty = []

        for path in get_migration_files():
            if path.name in KNOWN_EMPTY_DOWNGRADE:
                continue

            source = path.read_text()
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:
                continue

            if is_merge_migration(tree):
                continue

            func = parse_function(tree, "downgrade")
            if func is None:
                missing.append(path.name)
            elif is_empty_body(func):
                empty.append(path.name)

        violations = []
        if missing:
            violations.append(f"Missing downgrade(): {', '.join(missing)}")
        if empty:
            violations.append(f"Empty downgrade() (not a merge migration): {', '.join(empty)}")

        assert not violations, "Migration completeness violations:\n" + "\n".join(f"  {v}" for v in violations)

    def test_downgrade_covers_upgrade_tables(self):
        """downgrade() must reference the same tables as upgrade().

        If upgrade() touches table X (create, alter, add column, etc.),
        downgrade() should also reference table X to reverse the change.
        """
        gaps = []

        for path in get_migration_files():
            if path.name in KNOWN_DOWNGRADE_COVERAGE_GAPS:
                continue

            source = path.read_text()
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:
                continue

            if is_merge_migration(tree):
                continue

            upgrade = parse_function(tree, "upgrade")
            downgrade = parse_function(tree, "downgrade")

            if upgrade is None or downgrade is None:
                continue
            if is_empty_body(upgrade) or is_empty_body(downgrade):
                continue

            up_tables = _extract_table_names(upgrade)
            down_tables = _extract_table_names(downgrade)

            missing_in_down = up_tables - down_tables
            if missing_in_down:
                gaps.append(f"{path.name}: upgrade touches {missing_in_down} but downgrade does not")

        assert not gaps, (
            "Migration downgrade coverage gaps:\n"
            + "\n".join(f"  {g}" for g in gaps)
            + "\n\nEvery table modified in upgrade() should be referenced in downgrade()."
        )

    def test_known_empty_downgrades_still_exist(self):
        """Stale allowlist detection for KNOWN_EMPTY_DOWNGRADE."""
        stale = []
        for name in KNOWN_EMPTY_DOWNGRADE:
            path = MIGRATIONS_DIR / name
            if not path.exists():
                stale.append(f"{name} (file deleted)")
                continue

            source = path.read_text()
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:
                continue

            downgrade = parse_function(tree, "downgrade")
            if downgrade is not None and not is_empty_body(downgrade):
                stale.append(f"{name} (downgrade added — remove from allowlist)")

        assert not stale, "Stale entries in KNOWN_EMPTY_DOWNGRADE:\n" + "\n".join(f"  {s}" for s in stale)

    def test_known_downgrade_gaps_still_exist(self):
        """Stale allowlist detection — remove entries when fixed."""
        stale = []
        for name in KNOWN_DOWNGRADE_COVERAGE_GAPS:
            path = MIGRATIONS_DIR / name
            if not path.exists():
                stale.append(name)
                continue

            source = path.read_text()
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:
                continue

            upgrade = parse_function(tree, "upgrade")
            downgrade = parse_function(tree, "downgrade")
            if upgrade is None or downgrade is None:
                continue

            up_tables = _extract_table_names(upgrade)
            down_tables = _extract_table_names(downgrade)
            if not (up_tables - down_tables):
                stale.append(f"{name} (gap fixed — remove from allowlist)")

        assert not stale, "Stale entries in KNOWN_DOWNGRADE_COVERAGE_GAPS:\n" + "\n".join(f"  {s}" for s in stale)
