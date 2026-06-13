"""Operational helpers for Alembic migration graph inspection.

Used by CI scripts, pre-commit hooks, and architecture guard tests.
Operational code lives here — not under ``tests/``.
"""

from __future__ import annotations

import ast
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"


class MigrationParseError(ValueError):
    """Raised when a migration file cannot be parsed or is structurally invalid."""


def get_migration_files() -> list[Path]:
    """Get all migration Python files (excluding __init__.py)."""
    return sorted(f for f in MIGRATIONS_DIR.glob("*.py") if f.name != "__init__.py" and not f.name.startswith("__"))


def parse_migration_tree(path: Path) -> ast.Module:
    """Parse a migration file, surfacing syntax errors instead of swallowing them."""
    source = path.read_text(encoding="utf-8")
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        msg = f"Migration {path.name} failed to parse: {exc}"
        raise MigrationParseError(msg) from exc


def parse_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find a top-level function by name in the AST."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def iter_migration_trees() -> list[tuple[Path, ast.Module]]:
    """Parse every migration file into (path, ast.Module) pairs."""
    return [(path, parse_migration_tree(path)) for path in get_migration_files()]


def is_empty_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function body contains only pass/docstring."""
    for stmt in node.body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        return False
    return True


def extract_revision_info(path: Path) -> tuple[str | None, list[str]]:
    """Extract revision and down_revision from a migration file's AST."""
    tree = parse_migration_tree(path)

    revision = None
    down_revisions: list[str] = []

    for node in ast.iter_child_nodes(tree):
        targets: list[str] = []
        value = None

        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value = node.value

        if not targets or value is None:
            continue

        name = targets[0]

        if name == "revision":
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                revision = value.value

        elif name == "down_revision":
            if isinstance(value, ast.Constant):
                if value.value is None:
                    down_revisions = []
                elif isinstance(value.value, str):
                    down_revisions = [value.value]
            elif isinstance(value, ast.Tuple):
                down_revisions = [
                    elt.value for elt in value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]

    return revision, down_revisions


def get_migration_heads() -> set[str]:
    """Compute the set of head revisions in the migration graph."""
    all_revisions: set[str] = set()
    pointed_to: set[str] = set()

    for path in get_migration_files():
        revision, down_revisions = extract_revision_info(path)
        if revision:
            all_revisions.add(revision)
        pointed_to.update(down_revisions)

    return all_revisions - pointed_to


def _downgrade_parents_for_head(head_revision: str | None = None) -> list[str]:
    """Resolve the head revision and return its down_revisions (parent list)."""
    if head_revision is None:
        heads = get_migration_heads()
        if len(heads) != 1:
            msg = f"Expected exactly 1 migration head, found {sorted(heads)}"
            raise ValueError(msg)
        head_revision = next(iter(heads))

    for path in get_migration_files():
        revision, down_revisions = extract_revision_info(path)
        if revision != head_revision:
            continue
        if not down_revisions:
            msg = f"Cannot downgrade from base revision {head_revision}"
            raise ValueError(msg)
        return list(down_revisions)

    msg = f"Migration file not found for head revision {head_revision}"
    raise ValueError(msg)


def resolve_roundtrip_downgrade_target(head_revision: str | None = None) -> str:
    """Return an explicit Alembic downgrade target one step back from head."""
    # At a merge head, downgrading to any one parent undoes the merge and restores
    # all branch tips; the first parent is a valid single target for `alembic downgrade`.
    return _downgrade_parents_for_head(head_revision)[0]


def expected_heads_after_roundtrip_downgrade(head_revision: str | None = None) -> set[str]:
    """Return alembic_version heads expected after CI roundtrip downgrade from head."""
    # Merge heads restore every branch tip; single-parent heads land on their one parent.
    return set(_downgrade_parents_for_head(head_revision))


def is_merge_migration(tree: ast.Module) -> bool:
    """Check if this is a merge migration (empty upgrade + downgrade is OK)."""
    upgrade = parse_function(tree, "upgrade")
    downgrade = parse_function(tree, "downgrade")

    if upgrade is None or downgrade is None:
        return False

    return is_empty_body(upgrade) and is_empty_body(downgrade)
