"""Structural guard: typed AdCP request construction must be validation-boundary
protected.

Disease pattern this guards against: a transport wrapper (MCP tool, A2A skill
handler, REST route) constructs a typed ``*Request(...)`` Pydantic model with
one or more keyword arguments, OUTSIDE an ``adcp_validation_boundary(...)``
context manager. A raw Pydantic ``ValidationError`` then leaks past the
transport boundary untranslated — the buyer loses the real error code/
recovery/suggestion the boundary exists to attach (see
``src/core/validation_helpers.py::adcp_validation_boundary``).

Scope: this guard covers the CONCRETE, automatable half of the disease
(missing boundary protection around a non-trivial construction) — not the
"reimplements the builder instead of calling it" half, which needs semantic
judgment a structural scan can't make reliably and is tracked separately
(GH #1885).

Zero-argument constructions (``XRequest()``) are exempt -- nothing for a
boundary to protect (no ``ValidationError`` can occur).
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit._architecture_helpers import assert_violations_match_allowlist, repo_root

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_FILES = [
    *sorted((_REPO_ROOT / "src" / "core" / "tools").glob("*.py")),
    *sorted((_REPO_ROOT / "src" / "core" / "tools" / "creatives").glob("*.py")),
    _REPO_ROOT / "src" / "a2a_server" / "adcp_a2a_server.py",
    _REPO_ROOT / "src" / "routes" / "api_v1.py",
]

#: (relpath, enclosing_def, form): reason. Pre-existing / deferred instances,
#: each tracked by a filed GitHub issue.
#: Ratchet like EXPECTED_LEDGER -- shrinks as those issues land, never grows.
ALLOWLIST_DEFERRED: frozenset[tuple[str, str, str, str]] = frozenset(
    {
        # MCP wrapper for list_authorized_properties has NO adcp_validation_boundary
        # at all (unlike its A2A/REST siblings, which are already boundary-wrapped --
        # just missing a shared builder, a dedup concern out of THIS guard's scope).
        # This is the genuine boundary-protection gap. (list_accounts/sync_accounts
        # in accounts.py had the same gap -- already fixed.)
        (
            "src/core/tools/properties.py",
            "list_authorized_properties",
            "ListAuthorizedPropertiesRequest",
            "FIXME(#1882)",
        ),
    }
)

#: Legitimate false positives -- checked, not disease instances.
ALLOWLIST_NOT_APPLICABLE: frozenset[tuple[str, str, str]] = frozenset(
    {
        # Internal admin-approval replay reconstruction, not a transport
        # boundary -- no live buyer input.
        ("src/core/tools/media_buy_create.py", "execute_approved_media_buy", "CreateMediaBuyRequest"),
    }
)


class _BoundaryTracker(ast.NodeVisitor):
    """Track whether the current node is lexically inside an
    ``adcp_validation_boundary(...)`` ``with`` block, and find unprotected
    ``*Request(...)`` calls with >=1 keyword/positional argument.
    """

    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self.violations: list[tuple[str, str, str]] = []
        self._boundary_depth = 0
        self._enclosing_def = "<module>"

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        prev = self._enclosing_def
        self._enclosing_def = node.name
        self.generic_visit(node)
        self._enclosing_def = prev

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_With(self, node: ast.With) -> None:  # noqa: N802
        is_boundary = any(_is_adcp_validation_boundary_call(item.context_expr) for item in node.items)
        if is_boundary:
            self._boundary_depth += 1
        self.generic_visit(node)
        if is_boundary:
            self._boundary_depth -= 1

    visit_AsyncWith = visit_With  # noqa: N815

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if (
            name
            and name.endswith("Request")
            and (node.args or node.keywords)
            and self._boundary_depth == 0
            and not _is_exempt_scope(self._enclosing_def)
        ):
            form = ast.unparse(node.func)
            self.violations.append((self.relpath, self._enclosing_def, form))
        self.generic_visit(node)


def _is_exempt_scope(enclosing_def: str) -> bool:
    """True for scopes where a bare *Request(...) construction is the safe,
    canonical pattern rather than a disease instance:

    - Builder functions themselves (build_X_request / _build_X_request /
      create_get_X_request) -- these ARE the construction seam; their
      CALLERS wrap the call site in adcp_validation_boundary, not the
      builder's own body (see creative_formats.py::build_list_creative_formats_request).
    - _impl functions -- transport-agnostic business logic layer (Pattern #5);
      boundary protection is a transport-wrapper concern, not theirs.
    """
    return (
        enclosing_def.startswith("build_")
        or enclosing_def.startswith("_build_")
        or enclosing_def.startswith("create_get_")
        or enclosing_def.endswith("_impl")
    )


def _is_adcp_validation_boundary_call(expr: ast.expr) -> bool:
    call = expr
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    return name == "adcp_validation_boundary"


def _find_unprotected_constructions() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for path in _SCAN_FILES:
        if not path.exists() or path.name.startswith("test_"):
            continue
        relpath = str(path.relative_to(_REPO_ROOT))
        tracker = _BoundaryTracker(relpath)
        tracker.visit(ast.parse(path.read_text()))
        found.extend(tracker.violations)
    return found


def test_request_construction_is_boundary_protected() -> None:
    """Every non-trivial *Request(...) construction in a transport file is
    either wrapped in adcp_validation_boundary, or pinned as a known,
    ticket-tracked gap.
    """
    actual = set(_find_unprotected_constructions())
    deferred_triples = {(f, d, form) for f, d, form, _ticket in ALLOWLIST_DEFERRED}
    pinned = deferred_triples | ALLOWLIST_NOT_APPLICABLE

    assert_violations_match_allowlist(
        actual,
        pinned,
        fix_hint=(
            "Wrap the construction in `with adcp_validation_boundary(context=...):` "
            "(see src/core/tools/creative_formats.py for the reference pattern), or "
            "add a reasoned entry to ALLOWLIST_DEFERRED (with a filed ticket) or "
            "ALLOWLIST_NOT_APPLICABLE (not a transport boundary) in this file."
        ),
    )


def test_deferred_entries_carry_their_citation_at_the_source() -> None:
    """The ticket in each ALLOWLIST_DEFERRED entry must appear at the source file.

    ``test_request_construction_is_boundary_protected`` destructures the ticket out
    and throws it away (``for f, d, form, _ticket in``), so until now the fourth
    element was decorative: an entry could cite any string and nothing checked that a
    reader arriving at the code would find it. That is exactly what happened —
    ``src/core/tools/properties.py`` carried no FIXME at all while this file claimed
    ``FIXME(#1882)`` tracked it, so the gap was invisible from the code and visible
    only from the guard that permits it.

    CLAUDE.md's rule is that every allowlisted violation carries a
    ``# FIXME(#<gh-issue>)`` at the source location, and a GitHub issue rather than a
    beads id, because outside contributors cannot resolve a beads id.
    """
    root = repo_root()
    missing = [
        (rel_path, ticket)
        for rel_path, _def_name, _form, ticket in sorted(ALLOWLIST_DEFERRED)
        if ticket not in (root / rel_path).read_text(encoding="utf-8")
    ]
    assert not missing, (
        "these ALLOWLIST_DEFERRED entries cite a ticket that does not appear in the file "
        f"they defer: {missing}. Add `# {missing[0][1]}: <why, and what removes it>` at the "
        "construction site, so a reader of the CODE learns the gap is tracked — not only a "
        "reader of the guard that permits it."
    )


def test_positive_bare_construction_outside_boundary_is_detected() -> None:
    """Meta-test: a bare *Request(...) construction with args, no boundary, is caught."""
    src = """
def some_wrapper():
    req = FooRequest(x=1, y=2)
"""
    tracker = _BoundaryTracker("fake.py")
    tracker.visit(ast.parse(src))
    assert tracker.violations == [("fake.py", "some_wrapper", "FooRequest")]


def test_negative_boundary_protected_construction_is_not_flagged() -> None:
    """Meta-test: the same construction inside adcp_validation_boundary is excluded."""
    src = """
def some_wrapper():
    with adcp_validation_boundary(context="foo request"):
        req = FooRequest(x=1, y=2)
"""
    tracker = _BoundaryTracker("fake.py")
    tracker.visit(ast.parse(src))
    assert tracker.violations == []


def test_negative_zero_arg_construction_is_not_flagged() -> None:
    """Meta-test: a zero-argument construction has nothing to validate -- excluded
    even with no boundary, matching capabilities.py's legitimate zero-arg exemption.
    """
    src = """
def some_wrapper():
    req = FooRequest()
"""
    tracker = _BoundaryTracker("fake.py")
    tracker.visit(ast.parse(src))
    assert tracker.violations == []


def test_regex_slip_attribute_call_form_is_still_caught() -> None:
    """Meta-test: module.FooRequest(...) (attribute access, not a bare Name) is
    still detected -- the detector reads node.func.attr, not just node.func.id,
    so an import-qualified construction can't dodge the scan.
    """
    src = """
def some_wrapper():
    req = schemas_module.FooRequest(x=1)
"""
    tracker = _BoundaryTracker("fake.py")
    tracker.visit(ast.parse(src))
    assert tracker.violations == [("fake.py", "some_wrapper", "schemas_module.FooRequest")]
