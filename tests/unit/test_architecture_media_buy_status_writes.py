"""Guard: no direct MediaBuy status/approval writes outside the repository.

Every media-buy status transition MUST flow through ``MediaBuyRepository``
(``update_status`` / ``apply_status_transition``, or ``update_fields`` for the
staged status change inside ``update_media_buy``). Those methods bump the AdCP
3.1.0-beta.3 ``revision`` optimistic-concurrency counter and stamp
``approved_at`` / ``approved_by`` in one place. A direct
``media_buy.status = ...`` / ``media_buy.approved_at = ...`` /
``media_buy.approved_by = ...`` assignment in production code skips the bump, so
``revision`` silently goes stale and ``confirmed_at`` reports the wrong instant —
the exact class of bug #1544 fixed across the admin approve/reject routes, the
flight-date scheduler, and creative-sync assignment.

The scan covers Name targets (``mb.status = ...``) as well as attribute and
subscript targets (``self.mb.status = ...``, ``media_buys[i].status = ...``): a
bypass hiding behind a non-Name target is exactly where a regression would land.

Precedent: the no-raw-select guards (test_architecture_no_raw_select.py).

GitHub PR #1544 (structural guard — no direct MediaBuy status/approval writes)
"""

import ast

from tests.unit._architecture_helpers import assert_violations_match_allowlist, repo_root, safe_parse

# The repository module is the ONLY place allowed to write these attributes.
REPOSITORY_FILE = "src/core/database/repositories/media_buy.py"

# Attributes whose write must go through the repository bump/stamp seam.
# approved_by is guarded alongside approved_at: update_status stamps both, so a
# direct ``mb.approved_by = ...`` is the same bypass class.
GUARDED_ATTRS = {"status", "approved_at", "approved_by"}

# Names conventionally bound to a MediaBuy ORM row (singular) or a collection of
# them (plural, for subscript targets like ``media_buys[i].status = ...``).
# Restricting to these keeps the guard precise: other models expose ``.status``
# too (workflow ``step``, ``creative``), so a blanket ``.status =`` scan would be
# noise. The bypass bugs all wrote through one of these names.
MEDIA_BUY_VAR_NAMES = {
    # singular row bindings
    "media_buy",
    "media_buy_obj",
    "mb",
    "mb_obj",
    "buy",
    # collection bindings — subscript targets index into one of these
    "media_buys",
    "mbs",
    "buys",
}

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


def _nearest_ref_name(value: ast.expr) -> str | None:
    """Identifier naming the object whose attribute is being written, or None.

    Resolves the segment closest to the guarded attribute so the guard catches
    non-``Name`` targets without losing precision:
      * ``mb.status``               → value is ``Name('mb')``            → "mb"
      * ``self.mb.status``          → value is ``Attribute(attr='mb')``  → "mb"
      * ``media_buys[i].status``    → value is ``Subscript(Name(...))``  → "media_buys"
    Only the nearest identifier is returned (``mb.other.status`` → "other"),
    so an unrelated attribute of a media buy is not falsely flagged.
    """
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    if isinstance(value, ast.Subscript):
        return _nearest_ref_name(value.value)
    return None


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
                if target.attr not in GUARDED_ATTRS:
                    continue
                ref = _nearest_ref_name(target.value)
                if ref in MEDIA_BUY_VAR_NAMES:
                    violations.add((rel_path, ref, target.attr))

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
            "AdCP 3.1.0-beta.3 revision counter bumps and approved_at/approved_by "
            "are stamped. See #1544."
        ),
    )


def _detector_hits(snippet: str) -> set[tuple[str, str]]:
    """Run the target matcher over a snippet, returning (ref_name, attr) hits."""
    tree = ast.parse(snippet)
    return {
        (_nearest_ref_name(t.value), t.attr)
        for node in ast.walk(tree)
        for t in _attribute_write_targets(node)
        if t.attr in GUARDED_ATTRS and _nearest_ref_name(t.value) in MEDIA_BUY_VAR_NAMES
    }


def test_guard_detects_a_direct_status_write():
    """The detector flags a synthetic direct ``media_buy.status = ...`` write."""
    assert ("media_buy", "status") in _detector_hits("def f(media_buy):\n    media_buy.status = 'active'\n")


def test_guard_detects_approved_by_and_non_name_targets():
    """approved_by, attribute targets, and subscript targets are all caught."""
    # approved_by write
    assert ("mb", "approved_by") in _detector_hits("def f(mb):\n    mb.approved_by = 'x'\n")
    # attribute target: self.media_buy.status = ...
    assert ("media_buy", "status") in _detector_hits(
        "class C:\n    def f(self):\n        self.media_buy.status = 'active'\n"
    )
    # subscript target into a collection: media_buys[i].approved_at = ...
    assert ("media_buys", "approved_at") in _detector_hits(
        "def f(media_buys, i, ts):\n    media_buys[i].approved_at = ts\n"
    )


def test_guard_ignores_unrelated_attribute_of_media_buy():
    """A nested attribute (``mb.other.status``) is NOT the media-buy status write."""
    # nearest identifier is 'other', not a media-buy name → not flagged.
    assert _detector_hits("def f(mb):\n    mb.other.status = 'x'\n") == set()
