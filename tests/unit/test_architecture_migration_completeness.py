"""Guard: every alembic migration must have non-empty upgrade() and downgrade().

A migration with an empty downgrade is unrecoverable in production. A migration
with an empty upgrade is dead code that clutters the migration chain.

Merge migrations (empty upgrade + empty downgrade) are exempt — they only
reconcile branch heads and contain no schema changes.

This guard also checks that downgrade() reverses the structural changes made by
upgrade() — specifically, that if upgrade() creates/drops tables, constraints,
or columns, the downgrade() references the same tables.

"""

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

from scripts.ci.migration_helpers import (
    KNOWN_EMPTY_DOWNGRADE,
    MIGRATIONS_DIR,
    get_migration_files,
    is_downgrade_exempt,
    is_empty_body,
    is_merge_migration,
    iter_migration_trees,
    parse_function,
)
from tests.unit._architecture_helpers import iter_call_expressions, rel

_HOOKS_DIR = Path(__file__).resolve().parents[2] / ".pre-commit-hooks"


def _load_hook(name: str):
    """Import a pre-commit hook script by path (hooks are not an importable package)."""
    path = _HOOKS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


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

# KNOWN_EMPTY_DOWNGRADE is imported from scripts.ci.migration_helpers — the
# pre-push hook enforces the same policy and cannot import from tests/, so the
# allowlist lives there and both paths read the one copy. Its two legacy
# migrations have incomplete downgrades; FIXME(#2107) tracks them. The
# stale-entry tests below stay here: they are the ratchet, not the policy.

KNOWN_DOWNGRADE_COVERAGE_GAPS = {
    # Legacy: upgrade creates index but downgrade doesn't drop it
    "015_workflow_improvements.py",
    # Legacy: upgrade creates indexes/FKs but downgrade drops tables (indexes go with them)
    "020_fix_tasks_schema_properly_fix_tasks_schema_properly.py",
    # Legacy: upgrade adds column to tenants but downgrade doesn't revert
    "ebcb8dda247a_add_naming_templates_to_tenants.py",
}


def _extract_table_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Extract table names referenced in op.XXX() calls."""
    tables = set()
    for child in iter_call_expressions(node):
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

    @pytest.mark.arch_guard
    def test_non_merge_migrations_have_upgrade(self):
        """Every non-merge migration must define a non-empty upgrade() function."""
        missing = []
        empty = []

        for path, tree in iter_migration_trees():
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

    @pytest.mark.arch_guard
    def test_non_merge_migrations_have_downgrade(self):
        """Every non-merge migration must define a non-empty downgrade() function."""
        missing = []
        empty = []

        for path, tree in iter_migration_trees():
            if is_downgrade_exempt(path):
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

    @pytest.mark.arch_guard
    def test_downgrade_covers_upgrade_tables(self):
        """downgrade() must reference the same tables as upgrade().

        If upgrade() touches table X (create, alter, add column, etc.),
        downgrade() should also reference table X to reverse the change.
        """
        gaps = []

        for path, tree in iter_migration_trees():
            if path.name in KNOWN_DOWNGRADE_COVERAGE_GAPS:
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

    @pytest.mark.arch_guard
    def test_known_empty_downgrades_still_exist(self):
        """Stale allowlist detection for KNOWN_EMPTY_DOWNGRADE."""
        stale = []
        for name in KNOWN_EMPTY_DOWNGRADE:
            path = MIGRATIONS_DIR / name
            if not path.exists():
                stale.append(f"{name} (file deleted)")
                continue

            source = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:
                continue

            downgrade = parse_function(tree, "downgrade")
            if downgrade is not None and not is_empty_body(downgrade):
                stale.append(f"{name} (downgrade added — remove from allowlist)")

        assert not stale, "Stale entries in KNOWN_EMPTY_DOWNGRADE:\n" + "\n".join(f"  {s}" for s in stale)

    @pytest.mark.arch_guard
    def test_known_downgrade_gaps_still_exist(self):
        """Stale allowlist detection — remove entries when fixed."""
        stale = []
        for name in KNOWN_DOWNGRADE_COVERAGE_GAPS:
            path = MIGRATIONS_DIR / name
            if not path.exists():
                stale.append(name)
                continue

            source = path.read_text(encoding="utf-8")
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


class TestPrePushHookAgreesWithGuard:
    """The pre-push hook enforces the same policy as this guard (#1613).

    `.pre-commit-config.yaml` runs `.pre-commit-hooks/check_migration_completeness.py`
    at the pre-push stage over every changed `alembic/versions/*.py`. It must reach
    the SAME verdict this guard reaches on the same file — otherwise the tree is
    green under `make quality` and red at push time, which is how a latent trap
    ships to every contributor.
    """

    @pytest.mark.arch_guard
    def test_hook_exits_zero_on_the_current_migration_tree(self, monkeypatch):
        """The hook's EXIT CODE — what actually fails a push — must be 0 on this tree.

        Asserted at `main()`, not at `check_migration_file()`: `main()` also filters
        argv down to `alembic/versions/*.py` and turns the error list into the return
        code pre-commit reads. A fix that satisfied only the per-file check while
        `main()` still returned 1 would leave the push-time gate red with this test green.
        """
        hook = _load_hook("check_migration_completeness")
        repo_root = Path(__file__).resolve().parents[2]
        migrations = [rel(p, repo_root) for p in get_migration_files()]

        monkeypatch.setattr(sys, "argv", ["check_migration_completeness.py", *migrations])
        monkeypatch.chdir(repo_root)
        rc = hook.main()

        # Recomputed only to make the failure message name the offending files.
        errors = [e for path in get_migration_files() for e in hook.check_migration_file(path)]
        assert rc == 0, (
            "The pre-push hook rejects migrations this guard accepts:\n"
            + "\n".join(f"  {e}" for e in errors)
            + "\n\nThe two enforcement paths must share one policy and one allowlist."
        )

    @pytest.mark.arch_guard
    def test_hook_still_rejects_an_unallowlisted_empty_downgrade(self, tmp_path):
        """Parity must not be bought by weakening the hook."""
        hook = _load_hook("check_migration_completeness")

        offender = tmp_path / "9999_new_migration_with_no_downgrade.py"
        offender.write_text(
            '"""New migration."""\n\n\ndef upgrade():\n    op.create_table("widgets")\n\n\ndef downgrade():\n    pass\n',
            encoding="utf-8",
        )

        errors = hook.check_migration_file(offender)

        assert any("downgrade() is empty" in e for e in errors), (
            f"Hook accepted a new migration with an empty downgrade(): {errors}"
        )
