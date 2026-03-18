"""Shared helpers for migration guard tests.

DRY extraction: test_architecture_migration_completeness.py,
test_architecture_single_migration_head.py, and the smoke test
(tests/smoke/test_database_migrations.py) share migration directory,
file enumeration, and revision-graph logic.

NOTE: The pre-commit hook (check_migration_completeness.py) has its own
copy of is_merge_migration() because hooks run via ``python script.py``
where the project root is not on sys.path.  Keep both in sync.
"""

import ast
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def get_migration_files() -> list[Path]:
    """Get all migration Python files (excluding __init__.py)."""
    return sorted(f for f in MIGRATIONS_DIR.glob("*.py") if f.name != "__init__.py" and not f.name.startswith("__"))


def parse_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    """Find a top-level function by name in the AST."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def is_empty_body(node: ast.FunctionDef) -> bool:
    """Check if a function body contains only pass/docstring."""
    for stmt in node.body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        return False
    return True


def extract_revision_info(path: Path) -> tuple[str | None, list[str]]:
    """Extract revision and down_revision from a migration file's AST.

    Returns:
        (revision, list_of_down_revisions) where down_revision is normalized
        to a list (empty for None, single-element for string, multi for tuple).
    """
    source = path.read_text()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return None, []

    revision = None
    down_revisions: list[str] = []

    for node in ast.iter_child_nodes(tree):
        # Handle both ast.Assign and ast.AnnAssign
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
    """Compute the set of head revisions in the migration graph.

    A head is a revision that no other revision lists as its down_revision.
    A healthy migration graph has exactly one head.
    """
    all_revisions: set[str] = set()
    pointed_to: set[str] = set()

    for path in get_migration_files():
        revision, down_revisions = extract_revision_info(path)
        if revision:
            all_revisions.add(revision)
        pointed_to.update(down_revisions)

    return all_revisions - pointed_to


def is_merge_migration(tree: ast.Module) -> bool:
    """Check if this is a merge migration (empty upgrade + downgrade is OK).

    Merge migrations reconcile multiple alembic branch heads. They have no
    schema changes — both upgrade() and downgrade() are intentionally empty.

    NOTE: Duplicated in .pre-commit-hooks/check_migration_completeness.py
    (hooks cannot import from tests/).  Keep both in sync.
    """
    upgrade = parse_function(tree, "upgrade")
    downgrade = parse_function(tree, "downgrade")

    if upgrade is None or downgrade is None:
        return False

    return is_empty_body(upgrade) and is_empty_body(downgrade)
