"""Guard: no direct MediaBuy status/approval writes outside the repository.

Every media-buy status transition MUST flow through ``MediaBuyRepository``
(``update_status`` / ``apply_status_transition``, or ``update_fields`` for the
staged status change inside ``update_media_buy``). Those methods bump the AdCP
3.1.0-beta.3 ``revision`` optimistic-concurrency counter and stamp
``approved_at`` / ``approved_by`` in one place. A direct
``media_buy.status = ...`` / ``.approved_at = ...`` / ``.approved_by = ...``
assignment in production code skips the bump, so ``revision`` silently goes
stale and ``confirmed_at`` reports the wrong instant — the exact class of bug
#1544 fixed across the admin approve/reject routes, the flight-date scheduler,
and creative-sync assignment.

**Type-scoped, not name-scoped.** ``.status``/``.approved_at``/``.approved_by``
are shared attribute names (a ``WorkflowStep`` and a ``Creative`` also carry
``.status``; ``Creative`` also carries ``.approved_at``/``.approved_by``), so the
guard cannot flag every write to those names. Instead it flags writes on
bindings it can prove are a ``MediaBuy``:

  * conventional single-row names (``mb``, ``media_buy``, ``buy``, …) and
    collection names for subscript targets (``media_buys[i].status``);
  * ANY local bound from a MediaBuy source — ``MediaBuy(...)``, a
    ``…​.media_buys.<method>(...)`` repository call, or a MediaBuy-specific
    repository method (``create_from_request``, ``apply_status_transition``,
    ``bump_revision``, …) — so a write through a freshly-named binding like
    ``created_mb.status = ...`` is caught even though the name is not on any
    list;

and it covers ``setattr(mb, "status", ...)`` in addition to direct assignment.
The property enforced is "no MediaBuy status/approval write bypasses the seam,"
not "no write through these eight names."

The type inference is deliberately conservative, not exhaustive: a MediaBuy
loaded through a raw ``select(MediaBuy)`` and bound to a novel name is
invisible to this guard. That blind spot requires code the no-raw-select
guard (test_architecture_no_raw_select.py) already rejects outside
repositories, so the two guards compose to close it.

Precedent: the no-raw-select guards (test_architecture_no_raw_select.py).

GitHub PR #1544 (structural guard — no direct MediaBuy status/approval writes)
"""

import ast

from tests.unit._architecture_helpers import assert_violations_match_allowlist, repo_root, safe_parse

# The repository module is the ONLY place allowed to write these attributes.
REPOSITORY_FILE = "src/core/database/repositories/media_buy.py"

# Attributes whose write must go through the repository bump/stamp seam.
GUARDED_ATTRS = {"status", "approved_at", "approved_by"}

# Conventional single-row bindings — always treated as MediaBuy-typed.
MEDIA_BUY_SINGULAR_NAMES = {"media_buy", "media_buy_obj", "mb", "mb_obj", "buy"}

# Collection bindings — subscript targets (``media_buys[i].status``) index one.
MEDIA_BUY_COLLECTION_NAMES = {"media_buys", "mbs", "buys"}

# Repository methods that RETURN a MediaBuy and are defined ONLY on
# MediaBuyRepository (no other repo declares them), so a local bound from one is
# MediaBuy-typed regardless of the receiver expression. Generic names shared
# with other repos (get_by_id, create, update_status, update_fields) are
# deliberately excluded — those are matched via the ``.media_buys`` receiver.
MEDIA_BUY_SPECIFIC_METHODS = {
    "create_from_request",
    "apply_status_transition",
    "bump_revision",
    "update_status_or_raise",
    "update_fields_or_raise",
    "bump_revision_or_raise",
    "find_by_idempotency_key",
    "get_by_id_or_idempotency_key",
    "get_by_id_or_raise",
}

# Pre-existing violations: (relative_file_path, binding_name, attribute).
# Empty — #1544 routed every production write through the repository. It may
# only ever shrink; a new entry means a new bypass was introduced.
ALLOWLIST: set[tuple[str, str, str]] = set()


def _attr_chain_contains(node: ast.expr, name: str) -> bool:
    """True if the attribute/name access chain of *node* contains ``name``."""
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        if cur.attr == name:
            return True
        cur = cur.value
    return isinstance(cur, ast.Name) and cur.id == name


def _rhs_is_media_buy(rhs: ast.expr) -> bool:
    """True if *rhs* is an expression that yields a single MediaBuy instance."""
    if not isinstance(rhs, ast.Call):
        return False
    func = rhs.func
    if isinstance(func, ast.Name):
        return func.id in ("MediaBuy", "MediaBuyFactory")
    if isinstance(func, ast.Attribute):
        if func.attr in MEDIA_BUY_SPECIFIC_METHODS:
            return True
        # Repository access via a Unit of Work: ``<uow>.media_buys.<method>(...)``.
        if _attr_chain_contains(func.value, "media_buys"):
            return True
        # MediaBuyFactory.create_sync(...) / .build(...) etc.
        if _attr_chain_contains(func, "MediaBuyFactory"):
            return True
    return False


def _media_buy_typed_locals(tree: ast.AST) -> set[str]:
    """Local names bound (anywhere in the file) from a MediaBuy source."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _rhs_is_media_buy(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and _rhs_is_media_buy(node.value)
            and isinstance(node.target, ast.Name)
        ):
            names.add(node.target.id)
    return names


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


def _detect(tree: ast.AST) -> set[tuple[str, str]]:
    """(binding, attr) pairs where a MediaBuy status/approval write happens."""
    media_buy_typed = _media_buy_typed_locals(tree)
    write_bindings = MEDIA_BUY_SINGULAR_NAMES | MEDIA_BUY_COLLECTION_NAMES | media_buy_typed
    setattr_bindings = MEDIA_BUY_SINGULAR_NAMES | media_buy_typed

    hits: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        # Direct / subscript / attribute assignment: <binding>.<attr> = ...
        for target in _attribute_write_targets(node):
            if target.attr in GUARDED_ATTRS:
                ref = _nearest_ref_name(target.value)
                if ref in write_bindings:
                    hits.add((ref, target.attr))
        # setattr(<binding>, "<attr>", ...) — the target is resolved through
        # _nearest_ref_name so attribute receivers (setattr(self.media_buy,
        # "status", ...)) are caught like their direct-assignment twins.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in GUARDED_ATTRS
        ):
            ref = _nearest_ref_name(node.args[0])
            if ref in setattr_bindings:
                hits.add((ref, node.args[1].value))
    return hits


def _iter_guarded_src_trees():
    """Yield ``(rel_path, tree)`` for the src files subject to the seam guards.

    One definition of the guarded surface — skips the repository module (the
    allowed seam) and test code under any ``tests/`` directory (fixtures
    legitimately construct rows directly). Consumed by both ``_find_*``
    walkers below.
    """
    repo = repo_root()
    for py_file in (repo / "src").rglob("*.py"):
        rel_path = str(py_file.relative_to(repo))
        if rel_path == REPOSITORY_FILE or "/tests/" in rel_path:
            continue
        tree = safe_parse(py_file)
        if tree is None:
            continue
        yield rel_path, tree


def _find_direct_media_buy_status_writes() -> set[tuple[str, str, str]]:
    """Find MediaBuy ``.status`` / ``.approved_at`` / ``.approved_by`` writes in src/.

    Returns a set of (file_path, binding_name, attribute).
    """
    violations: set[tuple[str, str, str]] = set()
    for rel_path, tree in _iter_guarded_src_trees():
        for binding, attr in _detect(tree):
            violations.add((rel_path, binding, attr))
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


# ---------------------------------------------------------------------------
# Companion guard: no discarded returns from None-tolerant MediaBuy mutators.
#
# ``update_status`` / ``update_fields`` / ``bump_revision`` return ``None``
# when the buy is not found in this tenant. A call whose return value is
# DISCARDED (a bare expression statement) silently skips the write for a
# vanished buy while the caller proceeds to report success — the round-6
# class on GitHub PR #1544 (four admin transition sites plus the
# creative-approval activation). Callers must use the ``*_or_raise`` variant
# or bind and check the return.
# ---------------------------------------------------------------------------

# Mutators whose ``None`` return means "row not found; nothing was written".
NONE_TOLERANT_MUTATORS = {"update_status", "update_fields", "bump_revision"}

# Pre-existing violations: (relative_file_path, receiver_name, method).
DISCARDED_MUTATION_ALLOWLIST: set[tuple[str, str, str]] = set()


def _media_buy_repo_locals(tree: ast.AST) -> set[str]:
    """Local names bound (anywhere in the file) from ``MediaBuyRepository(...)``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "MediaBuyRepository"
        ):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _detect_discarded_mutations(tree: ast.AST) -> set[tuple[str, str]]:
    """(receiver, method) pairs where a None-tolerant mutator's return is discarded.

    A receiver is MediaBuy-typed when its attribute chain contains
    ``media_buys`` (UoW access) or it is a local bound from
    ``MediaBuyRepository(...)``.
    """
    repo_locals = _media_buy_repo_locals(tree)
    hits: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
            continue
        func = node.value.func
        if not (isinstance(func, ast.Attribute) and func.attr in NONE_TOLERANT_MUTATORS):
            continue
        receiver = func.value
        if _attr_chain_contains(receiver, "media_buys") or (
            isinstance(receiver, ast.Name) and receiver.id in repo_locals
        ):
            hits.add((_nearest_ref_name(receiver) or "<expr>", func.attr))
    return hits


def _find_discarded_media_buy_mutations() -> set[tuple[str, str, str]]:
    """Discarded None-tolerant MediaBuy mutator calls in src/ (outside the repository)."""
    violations: set[tuple[str, str, str]] = set()
    for rel_path, tree in _iter_guarded_src_trees():
        for receiver, method in _detect_discarded_mutations(tree):
            violations.add((rel_path, receiver, method))
    return violations


def test_no_discarded_none_tolerant_media_buy_mutations():
    """Production code must not discard the return of a None-tolerant MediaBuy mutator."""
    found = _find_discarded_media_buy_mutations()
    assert_violations_match_allowlist(
        found,
        DISCARDED_MUTATION_ALLOWLIST,
        fix_hint=(
            "The return of update_status()/update_fields()/bump_revision() is None "
            "when the buy is not found — discarding it silently skips the write. "
            "Use the *_or_raise variant, or bind the return and handle None. "
            "See GitHub PR #1544 (round-6 sweep)."
        ),
    )


def _detector_hits(snippet: str) -> set[tuple[str, str]]:
    """Run the type-scoped detector over a snippet."""
    return _detect(ast.parse(snippet))


def test_guard_detects_a_direct_status_write():
    """The detector flags a synthetic direct ``media_buy.status = ...`` write."""
    assert ("media_buy", "status") in _detector_hits("def f(media_buy):\n    media_buy.status = 'active'\n")


def test_guard_detects_approved_by_and_non_name_targets():
    """approved_by, attribute targets, and subscript targets are all caught."""
    assert ("mb", "approved_by") in _detector_hits("def f(mb):\n    mb.approved_by = 'x'\n")
    assert ("media_buy", "status") in _detector_hits(
        "class C:\n    def f(self):\n        self.media_buy.status = 'active'\n"
    )
    assert ("media_buys", "approved_at") in _detector_hits(
        "def f(media_buys, i, ts):\n    media_buys[i].approved_at = ts\n"
    )


def test_guard_detects_write_through_inferred_binding_not_on_the_name_list():
    """A binding NOT on any name list is caught when it is bound from a MediaBuy source.

    This is the round-3 hole: ``created_mb`` (from ``.media_buys.create_from_request``)
    or a row from ``apply_status_transition`` is a MediaBuy, so a status write
    through it must be flagged even though the name is novel.
    """
    assert ("created_mb", "status") in _detector_hits(
        "def f(uow, req):\n    created_mb = uow.media_buys.create_from_request(req)\n    created_mb.status = 'active'\n"
    )
    assert ("row", "approved_at") in _detector_hits(
        "def f(mb, ts):\n    row = MediaBuyRepository.apply_status_transition(mb, 'active')\n    row.approved_at = ts\n"
    )
    assert ("fresh", "status") in _detector_hits(
        "def f():\n    fresh = MediaBuy(status='draft')\n    fresh.status = 'active'\n"
    )


def test_guard_detects_setattr_bypass():
    """``setattr(mb, 'status', ...)`` is the same bypass as ``mb.status = ...``."""
    assert ("mb", "status") in _detector_hits("def f(mb):\n    setattr(mb, 'status', 'active')\n")
    assert ("created_mb", "approved_by") in _detector_hits(
        "def f(uow, req):\n"
        "    created_mb = uow.media_buys.create_from_request(req)\n"
        "    setattr(created_mb, 'approved_by', 'admin')\n"
    )


def _discard_hits(snippet: str) -> set[tuple[str, str]]:
    """Run the discarded-mutation detector over a snippet."""
    return _detect_discarded_mutations(ast.parse(snippet))


def test_guard_detects_discarded_mutator_returns():
    """A bare mutator call on a UoW or a MediaBuyRepository local is flagged."""
    assert ("media_buys", "update_status") in _discard_hits(
        "def f(uow, mb_id):\n    uow.media_buys.update_status(mb_id, 'active')\n"
    )
    assert ("repo", "update_fields") in _discard_hits(
        "def f(session, tid, mb_id):\n"
        "    repo = MediaBuyRepository(session, tid)\n"
        "    repo.update_fields(mb_id, budget=1)\n"
    )
    assert ("media_buys", "bump_revision") in _discard_hits(
        "def f(uow, mb_id):\n    uow.media_buys.bump_revision(mb_id)\n"
    )


def test_guard_ignores_bound_or_raising_mutator_calls():
    """Bound returns, *_or_raise variants, and non-MediaBuy repos are NOT flagged."""
    # return value bound — the caller can (and must) check it
    assert _discard_hits("def f(uow, mb_id):\n    row = uow.media_buys.update_status(mb_id, 'failed')\n") == set()
    # the raising variant already surfaces the vanished row
    assert _discard_hits("def f(uow, mb_id):\n    uow.media_buys.update_status_or_raise(mb_id, 'active')\n") == set()
    # another repository's update_status is out of scope
    assert _discard_hits("def f(uow, step_id):\n    uow.workflows.update_status(step_id, 'approved')\n") == set()


def test_guard_ignores_non_media_buy_status_writes():
    """Writes to a ``.status`` on a non-MediaBuy binding are NOT flagged."""
    # nested attribute of a media buy — nearest identifier is 'other'
    assert _detector_hits("def f(mb):\n    mb.other.status = 'x'\n") == set()
    # a Creative from the creatives repo carries .status/.approved_at too
    assert (
        _detector_hits(
            "def f(uow):\n"
            "    creative = uow.creatives.get_by_id('c1')\n"
            "    creative.status = 'approved'\n"
            "    creative.approved_at = None\n"
        )
        == set()
    )
    # a workflow step's status is not a media-buy write
    assert _detector_hits("def f(step):\n    step.status = 'approved'\n") == set()
