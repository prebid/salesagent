"""Guard: Business logic functions must not call session.add() directly.

session.add() in business logic is a repository pattern violation — ORM writes
belong in repository classes (src/core/database/repositories/), not in _impl
functions, admin blueprints, or context managers that have moved to UoW.

This complements test_architecture_repository_pattern.py (which guards against
get_db_session() in _impl functions). Where that guard checks SESSION CREATION,
this guard checks SESSION WRITES.

Scanning approach: AST — detect session.add() calls in the same IMPL_FILES
scope as the existing get_db_session() guard. Pre-existing violations are
allowlisted; new code fails immediately.

beads: beads-bou.3 (guard: session.add() in production code outside repositories)
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Files in the "business logic" scope — same list as the get_db_session() guard.
# Only these files are scanned; admin UI setup code and services are out of scope.
IMPL_FILES = [
    "src/core/tools/media_buy_create.py",
    "src/core/tools/media_buy_update.py",
    "src/core/tools/media_buy_delivery.py",
    "src/core/tools/media_buy_list.py",
    "src/core/tools/products.py",
    "src/core/tools/capabilities.py",
    "src/core/tools/creative_formats.py",
    "src/core/tools/properties.py",
    "src/core/tools/creatives/listing.py",
    "src/core/tools/creatives/_sync.py",
    "src/core/tools/creatives/_assignments.py",
    "src/core/tools/creatives/_workflow.py",
    "src/core/tools/performance.py",
    "src/core/tools/signals.py",
    "src/core/tools/task_management.py",
    "src/core/context_manager.py",
    "src/admin/blueprints/creatives.py",
]

# Session-like variable names the detector recognises.
# Covers: session, db_session, db, and attribute access chains like uow.session.add()
_SESSION_VAR_NAMES = {"session", "db_session", "db", "s"}
_SESSION_ATTR_NAMES = {"session", "db_session"}

# Pre-existing violations: (file_path, function_name)
# These existed before the guard was created. Allowlist shrinks as the
# repository pattern migration progresses.
# FIXME(beads-bou.3): each entry below should be migrated to a repository write method
PRODUCTION_SESSION_ADD_ALLOWLIST: set[tuple[str, str]] = {
    # media_buy_create.py — composite UoW + repository methods needed for packages,
    # assignments, workflows (tracked in PR #1097 remaining work)
    ("src/core/tools/media_buy_create.py", "_create_media_buy_impl"),
    # media_buy_update.py — repository methods for update queries
    ("src/core/tools/media_buy_update.py", "_update_media_buy_impl"),
    # media_buy_delivery.py — DeliveryRepository + audit log write
    ("src/core/tools/media_buy_delivery.py", "_get_media_buy_delivery_impl"),
    # creatives/_assignments.py — CreativeAssignmentRepository write methods needed
    ("src/core/tools/creatives/_assignments.py", "_process_assignments"),
    # creatives/_workflow.py — workflow write methods needed
    ("src/core/tools/creatives/_workflow.py", "_create_sync_workflow_steps"),
    # admin/blueprints/creatives.py — three functions with inline session writes
    # approve_creative and reject_creative: human_review records (pre-existing)
    ("src/admin/blueprints/creatives.py", "approve_creative"),
    ("src/admin/blueprints/creatives.py", "reject_creative"),
    # _create_review_record: should use creative_repo.create_review() — tracked in beads-02u
    # FIXME(beads-02u): replace db_session.add(review_record) with creative_repo.create_review()
    ("src/admin/blueprints/creatives.py", "_create_review_record"),
    # context_manager.py — context/workflow step writes before WorkflowRepository existed
    ("src/core/context_manager.py", "create_context"),
    ("src/core/context_manager.py", "create_workflow_step"),
    ("src/core/context_manager.py", "link_workflow_to_object"),
}


def _find_session_add_in_production(file_path: str) -> list[tuple[str, str, int]]:
    """Find functions that call session.add() directly.

    Detects:
    - session.add(x), db_session.add(x), db.add(x), s.add(x)
    - uow.session.add(x), wf_uow.session.add(x)  (attribute chains)

    Returns list of (file_path, function_name, line_number).
    """
    source_path = ROOT / file_path
    if not source_path.exists():
        return []

    tree = ast.parse(source_path.read_text())
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            if not (isinstance(func, ast.Attribute) and func.attr == "add"):
                continue

            val = func.value
            # Direct: session.add(), db_session.add(), db.add()
            if isinstance(val, ast.Name) and val.id in _SESSION_VAR_NAMES:
                violations.append((file_path, node.name, child.lineno))
                break
            # Chained: uow.session.add(), wf_uow.session.add()
            if isinstance(val, ast.Attribute) and val.attr in _SESSION_ATTR_NAMES:
                violations.append((file_path, node.name, child.lineno))
                break

    return violations


class TestProductionCodeNoInlineSessionAdd:
    """Business logic must not call session.add() outside repository classes.

    ORM writes belong in repository create_*() methods. Calling session.add()
    directly in _impl functions, admin blueprints, or context managers bypasses
    the repository layer and makes writes untestable in isolation.
    """

    def test_no_new_session_add_in_production(self):
        """No business logic function calls session.add() outside the allowlist."""
        all_violations = []
        for file_path in IMPL_FILES:
            all_violations.extend(_find_session_add_in_production(file_path))

        new_violations = [
            (f, fn, line) for f, fn, line in all_violations if (f, fn) not in PRODUCTION_SESSION_ADD_ALLOWLIST
        ]

        if new_violations:
            msg_lines = [
                "New session.add() calls in business logic (use repository write methods instead):",
                "",
            ]
            for f, fn, line in new_violations:
                msg_lines.append(f"  {f}:{line} in {fn}()")
            msg_lines.append("")
            msg_lines.append(
                "Fix: Move the write to a repository.create_*() or repository.add_*() method. "
                "See CLAUDE.md Pattern #3 for the repository pattern."
            )
            raise AssertionError("\n".join(msg_lines))

    def test_allowlist_entries_still_exist(self):
        """Every allowlisted violation must still exist (stale entry detection).

        When you fix a violation, remove it from the allowlist — this test enforces that.
        """
        all_violations = set()
        for file_path in IMPL_FILES:
            for f, fn, _line in _find_session_add_in_production(file_path):
                all_violations.add((f, fn))

        stale = PRODUCTION_SESSION_ADD_ALLOWLIST - all_violations
        if stale:
            msg_lines = [
                "Stale allowlist entries (violation was fixed — remove from PRODUCTION_SESSION_ADD_ALLOWLIST):",
                "",
            ]
            for f, fn in sorted(stale):
                msg_lines.append(f"    ({f!r}, {fn!r}),")
            raise AssertionError("\n".join(msg_lines))
