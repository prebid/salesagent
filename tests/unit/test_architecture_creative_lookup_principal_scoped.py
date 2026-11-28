"""Guard: buyer-path creative lookups must be principal-scoped.

Disease pattern (PR #1430 review, cross-principal FK-500/leak; guard
extended in the #1430 re-review round): the creatives PK is composite ``(creative_id, tenant_id,
principal_id)``. A buyer-path lookup that filters tenant-only matches ANOTHER
principal's row, so a cross-principal reference passes existence gates (then
violates the composite FK on insert) and leaks the other principal's fields
into the requester's errors.

Two detectors, scanned over ``src/core/database/repositories/creative.py``
AND every module under ``src/core/tools/``:

1. **Query detector** — any query selecting a Creative-family model
   (``Creative``, ``CreativeReview``, ``CreativeAssignment``, under ANY import
   alias such as ``DBCreative``/``CreativeModel``/``DBAssignment``, whole or as a
   column projection such as ``select(Creative.creative_id)``, through ``select``
   or a legacy ``<session>.query(...)``) that
   filters ``creative_id`` (``==``, a ``filter_by`` keyword, or ``.in_()``)
   without also filtering ``principal_id`` **in the same query**
   (per-QUERY granularity: a scoped query cannot credit an unscoped sibling
   in the same function). The queries are the ones ``iter_queries`` in
   ``_architecture_helpers`` reads, the model and ``select`` resolved by import
   as the query's own scope sees them, and the columns are the ones
   ``filtered_columns`` reads off the filter stages (``where`` / ``filter`` /
   ``filter_by``) -- the answers the filter_by and uniqueness guards read -- so a
   join's ON clause, a comparison of two mapped columns and ``principal_id != pid``
   never scope a query.

2. **Admin-call detector** — any ``admin_get_by_id`` / ``admin_get_by_ids`` /
   ``admin_list_all`` repository call inside ``src/core/tools/``: buyer-facing
   tool code must use principal-scoped repository methods; admin-side flows
   are pinned by the explicit allowlist below, not a name convention (the old
   ``admin_*`` exemption is exactly how the update_media_buy hole hid).

Allowlists are explicit ``(relpath, function)`` pairs and can only shrink.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    assert_violations_match_allowlist,
    filtered_columns,
    iter_call_expressions,
    iter_queries,
    mapped,
    parse_module,
    rel,
    walk_with_enclosing_function,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CREATIVE_REPO = _REPO_ROOT / "src" / "core" / "database" / "repositories" / "creative.py"
_TOOLS_DIR = _REPO_ROOT / "src" / "core" / "tools"

# ORM models whose PK/FK carries principal_id — lookups by creative_id on any
# of them are principal-ambiguous unless the query also pins principal_id.
# Checked against the mapper where the detector runs (``mapped``), so a rename fails.
_CREATIVE_MODELS = ("Creative", "CreativeReview", "CreativeAssignment")

_ADMIN_REPO_METHODS = {"admin_get_by_id", "admin_get_by_ids", "admin_list_all"}

# Explicit admin-by-design allowlist for the QUERY detector: tenant-scoped
# lookups whose callers are the seller-side admin surface (Admin UI / GATE-PUSH
# approve flow), never buyer input. Shrink-only.
_QUERY_ALLOWLIST: set[tuple[str, str]] = {
    ("src/core/database/repositories/creative.py", "admin_get_by_id"),
    ("src/core/database/repositories/creative.py", "admin_get_by_ids"),
    # CreativeReview lookup; callers are the admin AI-review blueprint + the
    # GATE-PUSH flow. Naming drift (no admin_ prefix) — rename is a follow-up.
    ("src/core/database/repositories/creative.py", "get_prior_ai_review"),
    # CreativeAssignmentRepository: tenant-scoped assignment reads for the
    # admin surface (creative approval fan-out).
    ("src/core/database/repositories/creative.py", "get_by_creative"),
}

# Explicit allowlist for the ADMIN-CALL detector: tools-layer functions that
# are genuinely seller-side (reached from creative approval, not buyer input).
_ADMIN_CALL_ALLOWLIST: set[tuple[str, str]] = set()


def find_unscoped_creative_queries(tree: ast.Module, relpath: str) -> list[tuple[int, str]]:
    """Return (lineno, function) per QUERY filtering creative_id without principal_id.

    One row per query ``iter_queries`` reads on a Creative-family class, under the function
    that holds it. An accumulator (``stmt = select(...)`` and every later
    ``stmt = stmt.where(...)``) is ONE query on its whole filter set, so an unscoped
    accumulator is flagged and principal_id pinned in a later statement is credited; a chain
    on a folded name (``session.scalars(stmt.where(...))``) is graded on the fold's filters
    with its own on top. Opaque or not, a fold is graded on the calls it folded: a rebind it
    cannot read (``s = wrap(s)``) receives the statement with those filters already applied.
    A column counts on any Creative-family class (``filtered_columns``).
    """
    creative = mapped(*_CREATIVE_MODELS)
    violations: list[tuple[int, str]] = []
    for query in iter_queries(tree, relpath):
        if query.entity not in creative:
            continue
        compared = {column for owner, column in filtered_columns(query) if owner in creative}
        if "creative_id" in compared and "principal_id" not in compared:
            violations.append((query.root.lineno, query.holder))
    return violations


def find_admin_calls(tree: ast.Module) -> list[tuple[int, str]]:
    """Return (lineno, function) for admin_* repository calls (tools scan).

    A node walk, so the function comes from ``walk_with_enclosing_function``: the name
    ``iter_scopes`` gives the scope that owns the call.
    """
    enclosing = {id(node): function for node, function in walk_with_enclosing_function(tree)}
    return [
        (call.lineno, enclosing[id(call)])
        for call in iter_call_expressions(tree)
        if isinstance(call.func, ast.Attribute) and call.func.attr in _ADMIN_REPO_METHODS
    ]


def _scanned_files() -> list[Path]:
    return [_CREATIVE_REPO, *sorted(_TOOLS_DIR.rglob("*.py"))]


class TestCreativeLookupPrincipalScoped:
    @pytest.mark.arch_guard
    def test_no_unscoped_creative_query(self):
        """Found violations must EXACTLY match the allowlist (new ones rejected, fixed ones removed)."""
        found: set[tuple[str, str]] = set()
        for path in _scanned_files():
            rel_path = rel(path, _REPO_ROOT)
            for _lineno, func in find_unscoped_creative_queries(parse_module(path), rel_path):
                found.add((rel_path, func))
        assert_violations_match_allowlist(
            found,
            _QUERY_ALLOWLIST,
            fix_hint=(
                "Creative-family lookups comparing creative_id must ALSO compare principal_id "
                "in the SAME query (composite PK — tenant-only matching enables the "
                "cross-principal FK-500/leak, PR #1430 review). "
                "Admin-by-design lookups belong in _QUERY_ALLOWLIST (shrink-only)."
            ),
        )

    @pytest.mark.arch_guard
    def test_no_admin_repo_calls_in_buyer_tools(self):
        found: set[tuple[str, str]] = set()
        for path in sorted(_TOOLS_DIR.rglob("*.py")):
            rel_path = rel(path, _REPO_ROOT)
            for _lineno, func in find_admin_calls(parse_module(path)):
                found.add((rel_path, func))
        assert_violations_match_allowlist(
            found,
            _ADMIN_CALL_ALLOWLIST,
            fix_hint=(
                "admin_* repository lookups are principal-agnostic and MUST NOT be called "
                "from buyer-facing tool code (this is how the update_media_buy cross-principal "
                "hole hid behind the old admin_* name exemption — #1430 re-review). "
                "Genuinely seller-side tool flows belong in _ADMIN_CALL_ALLOWLIST (shrink-only)."
            ),
        )


#: What every real module that queries carries; the snippets below import their own models.
_SELECT_IMPORT = "from sqlalchemy import select\n"


def _flagged(source: str) -> list[str]:
    """The functions the LIVE query detector flags in *source*, given the ``select`` import."""
    tree = ast.parse(_SELECT_IMPORT + source)
    return [func for _, func in find_unscoped_creative_queries(tree, "src/core/tools/x.py")]


class TestDetectorMetaTests:
    """Exercise the LIVE detectors on synthetic modules (#1430 guard meta-drift
    discipline: meta-tests must pin the detectors the guard actually runs, incl.
    alias, .in_(), filter_by, per-query granularity, and the admin-call detector)."""

    @pytest.mark.arch_guard
    def test_detector_catches_tenant_only_lookup(self):
        source = (
            "from src.core.database.models import Creative\n"
            "def get_thing(self, creative_id):\n"
            "    return self._session.scalars(select(Creative).where(\n"
            "        Creative.tenant_id == self._tenant_id,\n"
            "        Creative.creative_id == creative_id,\n"
            "    )).first()\n"
        )
        assert _flagged(source) == ["get_thing"]

    @pytest.mark.arch_guard
    def test_detector_catches_aliased_in_bulk_load(self):
        """DBCreative alias + .in_() bulk form — the shape that evaded the old detector."""
        source = (
            "from src.core.database.models import Creative as DBCreative\n"
            "def load(session, tenant_id, ids):\n"
            "    stmt = select(DBCreative).where(\n"
            "        DBCreative.tenant_id == tenant_id,\n"
            "        DBCreative.creative_id.in_(ids),\n"
            "    )\n"
            "    return session.scalars(stmt).all()\n"
        )
        assert _flagged(source) == ["load"]

    @pytest.mark.arch_guard
    def test_detector_catches_filter_by_form(self):
        source = (
            "from src.core.database.models import Creative\n"
            "def get_thing(session, creative_id, tenant_id):\n"
            "    return session.scalars(select(Creative).filter_by(\n"
            "        creative_id=creative_id, tenant_id=tenant_id)).first()\n"
        )
        assert _flagged(source) == ["get_thing"]

    @pytest.mark.arch_guard
    def test_detector_catches_a_column_projection(self):
        """select(Creative.creative_id) addresses Creative; the spelling match read a model named creative_id."""
        source = (
            "from src.core.database.models import Creative\n"
            "def ids(session, tid, cid):\n"
            "    return session.scalars(select(Creative.creative_id).where(\n"
            "        Creative.tenant_id == tid, Creative.creative_id == cid)).all()\n"
        )
        assert _flagged(source) == ["ids"]

    @pytest.mark.arch_guard
    def test_detector_walks_the_whole_chain(self):
        """A filter after order_by belongs to the same query; the old walker stopped at order_by."""
        source = (
            "from src.core.database.models import Creative\n"
            "def latest(session, tid, cid):\n"
            "    return session.scalars(select(Creative).order_by(Creative.created_at).where(\n"
            "        Creative.tenant_id == tid, Creative.creative_id == cid)).first()\n"
        )
        assert _flagged(source) == ["latest"]

    @pytest.mark.arch_guard
    def test_a_join_condition_does_not_scope_the_query(self):
        """principal_id in a join's ON clause filters nothing: only where/filter/filter_by count."""
        source = (
            "from src.core.database.models import Creative, Principal\n"
            "def joined(session, tid, cid):\n"
            "    return session.scalars(select(Creative)\n"
            "        .join(Principal, Principal.principal_id == Creative.principal_id)\n"
            "        .where(Creative.tenant_id == tid, Creative.creative_id == cid)).first()\n"
        )
        assert _flagged(source) == ["joined"]

    @pytest.mark.arch_guard
    def test_detector_resolves_by_import_not_by_spelling(self):
        """A Pydantic Creative and a parameter named select are not an ORM query."""
        source = (
            "from src.core.schemas import Creative\n"
            "def pydantic(cid):\n"
            "    return select(Creative).where(Creative.creative_id == cid)\n"
            "def shadowed(select, cid):\n"
            "    from src.core.database.models import Creative as DBCreative\n"
            "    return select(DBCreative).where(DBCreative.creative_id == cid)\n"
        )
        assert _flagged(source) == []

    @pytest.mark.arch_guard
    def test_detector_is_per_query_not_per_function(self):
        """A principal-scoped query must NOT credit an unscoped sibling in the same function."""
        source = (
            "from src.core.database.models import Creative\n"
            "def two_queries(session, cid, tid, pid):\n"
            "    scoped = session.scalars(select(Creative).where(\n"
            "        Creative.tenant_id == tid,\n"
            "        Creative.principal_id == pid,\n"
            "        Creative.creative_id == cid,\n"
            "    )).first()\n"
            "    unscoped = session.scalars(select(Creative).where(\n"
            "        Creative.tenant_id == tid,\n"
            "        Creative.creative_id == cid,\n"
            "    )).first()\n"
            "    return scoped, unscoped\n"
        )
        assert _flagged(source) == ["two_queries"]

    @pytest.mark.arch_guard
    def test_detector_passes_scoped_and_non_creative_models(self):
        source = (
            "from src.core.database.models import Creative, Product\n"
            "def get_thing(self, creative_id, principal_id):\n"
            "    return self._session.scalars(select(Creative).where(\n"
            "        Creative.tenant_id == self._tenant_id,\n"
            "        Creative.creative_id == creative_id,\n"
            "        Creative.principal_id == principal_id,\n"
            "    )).first()\n"
            "def other_model(session, x):\n"
            "    return session.scalars(select(Product).where(Product.creative_id == x)).all()\n"
        )
        assert _flagged(source) == []

    @pytest.mark.arch_guard
    def test_detector_ignores_join_conditions(self):
        source = (
            "from src.core.database.models import Creative, CreativeAssignment\n"
            "def joined(session, pid, tid):\n"
            "    return session.scalars(select(Creative).where(\n"
            "        Creative.creative_id == CreativeAssignment.creative_id,\n"
            "        Creative.tenant_id == tid,\n"
            "        Creative.principal_id == pid,\n"
            "    )).all()\n"
        )
        assert _flagged(source) == []

    @pytest.mark.arch_guard
    def test_detector_catches_unscoped_accumulator_query(self):
        """Accumulator style (stmt = select(...); stmt = stmt.where(...)) must be graded
        as ONE merged query — the shape the old detector silently dropped."""
        source = (
            "from src.core.database.models import Creative\n"
            "def accumulate(session, tid, ids):\n"
            "    stmt = select(Creative).where(Creative.tenant_id == tid)\n"
            "    stmt = stmt.where(Creative.creative_id.in_(ids))\n"
            "    return session.scalars(stmt).all()\n"
        )
        assert _flagged(source) == ["accumulate"]

    @pytest.mark.arch_guard
    def test_detector_merges_accumulated_principal_scope(self):
        """principal_id pinned in a LATER accumulation statement scopes the whole
        query — the merged grading must not flag it (no false positive)."""
        source = (
            "from src.core.database.models import Creative\n"
            "def accumulate(session, tid, pid, ids):\n"
            "    stmt = select(Creative).filter_by(tenant_id=tid)\n"
            "    stmt = stmt.where(Creative.creative_id.in_(ids))\n"
            "    stmt = stmt.where(Creative.principal_id == pid)\n"
            "    return session.scalars(stmt).all()\n"
        )
        assert _flagged(source) == []

    @pytest.mark.arch_guard
    def test_detector_grades_the_filters_folded_before_an_opaque_rebind(self):
        """``s = wrap(s)`` receives the statement with the folded filters already applied, so
        they are graded: principal_id pinned before it scopes the query, and creative_id
        without principal_id still flags. The fold used to be dropped there, and the first
        line was graded alone."""
        source = (
            "from src.core.database.models import Creative\n"
            "def f(session, cid, p):\n"
            "    s = select(Creative).where(Creative.creative_id == cid)\n"
            "    s = s.where(Creative.principal_id == p)\n"
            "    s = wrap(s)\n"
            "    return session.scalars(s).all()\n"
        )
        assert _flagged(source) == []
        assert _flagged(source.replace("    s = s.where(Creative.principal_id == p)\n", "")) == ["f"]

    @pytest.mark.arch_guard
    def test_detector_grades_a_chain_on_a_folded_name_with_the_folded_filters(self):
        """``stmt.where(...)`` inside a call is a query of its own: the fold's filters with its
        own on top. It used to be dropped because its base is a name, not a statement root."""
        source = (
            "from src.core.database.models import Creative\n"
            "def f(session, tid, cid):\n"
            "    stmt = select(Creative).where(Creative.tenant_id == tid)\n"
            "    return session.scalars(stmt.where(Creative.creative_id == cid)).first()\n"
        )
        assert _flagged(source) == ["f"]

    @pytest.mark.arch_guard
    def test_a_principal_inequality_does_not_scope_the_query(self):
        """``principal_id != pid`` pins no principal; only ``==``, ``filter_by`` and ``.in_()`` do."""
        source = (
            "from src.core.database.models import Creative\n"
            "def f(session, tid, cid, pid):\n"
            "    return session.scalars(select(Creative).where(\n"
            "        Creative.tenant_id == tid, Creative.creative_id == cid, Creative.principal_id != pid)).first()\n"
        )
        assert _flagged(source) == ["f"]

    @pytest.mark.arch_guard
    def test_detector_reads_a_legacy_query_root(self):
        """``<session>.query(Creative)`` addresses Creative as ``select(Creative)`` does, inline or
        folded: the root and its class are ``statement_root`` and ``statement_entity``, the answers
        the filter_by guard reads. The detector used to read the class off ``select(...)`` calls only."""
        source = (
            "from src.core.database.models import Creative\n"
            "def inline(s, cid):\n"
            "    return s.query(Creative).filter_by(creative_id=cid).first()\n"
            "def folded(s, cid):\n"
            "    q = s.query(Creative)\n"
            "    q = q.filter_by(creative_id=cid)\n"
            "    return q.first()\n"
            "def scoped(s, cid, pid):\n"
            "    return s.query(Creative).filter_by(creative_id=cid, principal_id=pid).first()\n"
            "def scoped_later(s, cid, pid):\n"
            "    q = s.query(Creative).filter_by(creative_id=cid)\n"
            "    q = q.filter(Creative.principal_id == pid)\n"
            "    return q.first()\n"
        )
        assert _flagged(source) == ["inline", "folded"]

    @pytest.mark.arch_guard
    def test_detector_names_the_function_that_holds_a_nested_scope(self):
        """A query in a comprehension or a lambda is reported under its enclosing function."""
        source = (
            "from src.core.database.models import Creative\n"
            "def listed(session, ids):\n"
            "    return [session.scalars(select(Creative).where(Creative.creative_id == i)).first() for i in ids]\n"
            "def deferred(session, cid):\n"
            "    return lambda: session.scalars(select(Creative).where(Creative.creative_id == cid)).first()\n"
        )
        assert _flagged(source) == ["listed", "deferred"]

    @pytest.mark.arch_guard
    def test_admin_call_detector(self):
        tree = ast.parse(
            "def buyer_path(uow, ids):\n"
            "    return uow.creatives.admin_get_by_ids(ids)\n"
            "def fine(uow, ids, pid):\n"
            "    return uow.creatives.get_by_ids(ids, pid)\n"
            "def deferred(uow, cid):\n"
            "    return lambda: uow.creatives.admin_get_by_id(cid)\n"
        )
        assert [f for _, f in find_admin_calls(tree)] == ["buyer_path", "deferred"]
