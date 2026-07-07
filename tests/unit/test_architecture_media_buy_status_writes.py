"""Guard: no direct MediaBuy.status / .approved_at writes outside the repository.

Every media-buy status transition MUST flow through ``MediaBuyRepository``
(``update_status`` / ``apply_status_transition``, or ``update_fields`` for the
staged status change inside ``update_media_buy``). Those methods bump the AdCP
GA ``revision`` optimistic-concurrency counter and stamp ``approved_at`` in one
place. A direct ``media_buy.status = ...`` / ``media_buy.approved_at = ...``
assignment in production code skips the bump, so ``revision`` silently goes
stale and ``confirmed_at`` reports the wrong instant — the exact class of bug
#1544 fixed across the admin approve/reject routes, the flight-date scheduler,
and creative-sync assignment.

Precedent: the no-raw-select guards (test_architecture_no_raw_select.py).

beads: #1544 (structural guard — no direct MediaBuy status/approval writes)
"""

import ast

from tests.unit._architecture_helpers import assert_violations_match_allowlist, repo_root, safe_parse

# The repository module is the ONLY place allowed to write these attributes.
REPOSITORY_FILE = "src/core/database/repositories/media_buy.py"

# Attributes whose write must go through the repository bump/stamp seam.
GUARDED_ATTRS = {"status", "approved_at"}

# Variable names conventionally bound to a MediaBuy ORM row. Restricting to
# these keeps the guard precise: other models expose ``.status`` too
# (workflow ``step``, ``creative``), so a blanket ``.status =`` scan would be
# noise. The bypass bugs all wrote through one of these names.
MEDIA_BUY_VAR_NAMES = {"media_buy", "media_buy_obj", "mb", "mb_obj", "buy"}

# Pre-existing violations: (relative_file_path, variable_name, attribute).
# Empty — #1544 routed every production write through the repository. It may
# only ever shrink; a new entry means a new bypass was introduced.
ALLOWLIST: set[tuple[str, str, str]] = set()


def _attribute_write_targets(node: ast.AST) -> list[ast.Attribute]:
    """Attribute targets assigned by an Assign / AnnAssign / AugAssign node."""
    if isinstance(node, ast.Assign):
        return [t for t in node.targets if isinstance(t, ast.Attribute)]
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        return [node.target] if isinstance(node.target, ast.Attribute) else []
    return []


def _find_direct_media_buy_status_writes() -> set[tuple[str, str, str]]:
    """Find ``<media_buy_var>.status`` / ``.approved_at`` writes in src/.

    Skips the repository module (the allowed seam) and test code under any
    ``tests/`` directory (fixtures legitimately construct rows directly).
    Returns a set of (file_path, variable_name, attribute).
    """
    repo = repo_root()
    violations: set[tuple[str, str, str]] = set()

    for py_file in (repo / "src").rglob("*.py"):
        rel_path = str(py_file.relative_to(repo))
        if rel_path == REPOSITORY_FILE or "/tests/" in rel_path:
            continue

        tree = safe_parse(py_file)
        if tree is None:
            continue

        for node in ast.walk(tree):
            for target in _attribute_write_targets(node):
                if (
                    target.attr in GUARDED_ATTRS
                    and isinstance(target.value, ast.Name)
                    and target.value.id in MEDIA_BUY_VAR_NAMES
                ):
                    violations.add((rel_path, target.value.id, target.attr))

    return violations


def test_no_direct_media_buy_status_writes_outside_repository():
    """Production code must route media-buy status/approval writes through the repository."""
    found = _find_direct_media_buy_status_writes()
    assert_violations_match_allowlist(
        found,
        ALLOWLIST,
        fix_hint=(
            "Route the transition through MediaBuyRepository.update_status() "
            "(or apply_status_transition() for an already-loaded cross-tenant row, "
            "or stage it into update_media_buy's pending_field_updates) so the "
            "AdCP GA revision counter bumps and approved_at is stamped. See #1544."
        ),
    )


def test_guard_detects_a_direct_status_write():
    """The detector flags a synthetic direct ``media_buy.status = ...`` write."""
    snippet = "def f(media_buy):\n    media_buy.status = 'active'\n"
    tree = ast.parse(snippet)
    hits = [
        (t.value.id, t.attr)
        for node in ast.walk(tree)
        for t in _attribute_write_targets(node)
        if isinstance(t.value, ast.Name) and t.attr in GUARDED_ATTRS and t.value.id in MEDIA_BUY_VAR_NAMES
    ]
    assert ("media_buy", "status") in hits
