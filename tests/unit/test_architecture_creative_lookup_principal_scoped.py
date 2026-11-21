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
   column projection such as ``select(Creative.creative_id)``) that
   compares ``creative_id`` (via ``==``, ``filter_by`` kwargs, or ``.in_()``)
   without also comparing ``principal_id`` **in the same query chain**
   (per-QUERY granularity: a scoped query cannot credit an unscoped sibling
   in the same function). The model and ``select`` resolve by import, as the
   query's own scope sees them (``orm_references`` in ``_architecture_helpers``);
   the chain is walked by the shared ``select_chain``, and only its filter
   stages (``where`` / ``filter`` / ``filter_by``) count as comparisons, so a
   join's ON clause never scopes a query.

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
    SELECT_CHAIN_METHODS,
    SELECT_FILTER_METHODS,
    assert_violations_match_allowlist,
    call_callee_name,
    iter_scopes,
    orm_references,
    parse_module,
    query_bindings,
    rel,
    scope_bindings,
    scope_nodes,
    select_chain,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CREATIVE_REPO = _REPO_ROOT / "src" / "core" / "database" / "repositories" / "creative.py"
_TOOLS_DIR = _REPO_ROOT / "src" / "core" / "tools"

# ORM models whose PK/FK carries principal_id — lookups by creative_id on any
# of them are principal-ambiguous unless the query also pins principal_id.
_CREATIVE_MODELS = {"Creative", "CreativeReview", "CreativeAssignment"}

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


def _model_attr(expr: ast.expr, references: dict[int, str]) -> str | None:
    """The attribute of ``<Creative-family class>.<attr>``, the class resolved by import, else None."""
    if isinstance(expr, ast.Attribute) and references.get(id(expr.value)) in _CREATIVE_MODELS:
        return expr.attr
    return None


def _creative_query_chains(
    tree: ast.Module, relpath: str, references: dict[int, str]
) -> list[tuple[ast.Call, list[ast.Call]]]:
    """Every query on a Creative-family model, once: ``(select call, its filter stages)``.

    Per scope (``iter_scopes``). Accumulator-style queries come first: a simple
    ``name = select(...)...`` assignment and every later ``name = name.where(...)``
    are folded into one chain, so the query is graded on its FULL accumulated filter
    set (an unscoped accumulator is flagged; principal_id pinned in a later statement
    is credited). Only a name the scope binds by plain assignment alone is folded
    (``scope_bindings``): for a parameter or a loop target the fold cannot know which
    binding a read sees, and its inline ``select(...)`` chain is still graded on its
    own. Then the inline chains, longest first, so a sub-chain of a graded chain is
    skipped. Chains are walked by ``select_chain``; only the filter stages are kept.
    *references* is ``orm_references(relpath, tree)``.
    """
    chains: list[tuple[ast.Call, list[ast.Call]]] = []
    for scope, _imports in iter_scopes(tree, query_bindings(relpath, tree)):
        nodes = list(scope_nodes(scope))
        foldable = scope_bindings(scope)
        accumulators: dict[str, tuple[ast.Call, list[ast.Call]]] = {}
        for stmt in (n for n in nodes if isinstance(n, ast.Assign)):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                continue
            target = stmt.targets[0].id
            if target not in foldable:
                continue
            base, calls = select_chain(stmt.value, SELECT_CHAIN_METHODS)
            if isinstance(base, ast.Call) and id(base) in references:
                accumulators[target] = (base, calls)
            elif isinstance(base, ast.Name) and base.id in accumulators and calls:
                select_call, earlier = accumulators[base.id]
                accumulators[target] = (select_call, calls + earlier)
            else:
                accumulators.pop(target, None)
        inline = [
            (base, calls)
            for base, calls in (select_chain(n, SELECT_CHAIN_METHODS) for n in nodes if isinstance(n, ast.Call))
            if isinstance(base, ast.Call) and id(base) in references
        ]
        inline.sort(key=lambda chain: -len(chain[1]))
        seen: set[int] = set()
        for select_call, calls in [*accumulators.values(), *inline]:
            if id(select_call) in seen or references[id(select_call)] not in _CREATIVE_MODELS:
                continue
            seen.add(id(select_call))
            chains.append((select_call, [c for c in calls if call_callee_name(c) in SELECT_FILTER_METHODS]))
    return chains


def _attrs_compared_in_chain(parts: list[ast.Call], references: dict[int, str]) -> set[str]:
    """Collect model attrs pinned by a single query chain's filter stages.

    Counts ``Model.attr == value`` comparisons (excluding pure JOIN conditions
    where both sides are model attrs), ``filter_by(attr=...)`` kwargs, and
    ``Model.attr.in_(...)`` membership filters.
    """
    names: set[str] = set()
    for part in parts:
        if call_callee_name(part) == "filter_by":
            for kw in part.keywords:
                if kw.arg:
                    names.add(kw.arg)
            continue
        for arg in part.args:
            for node in ast.walk(arg):
                if isinstance(node, ast.Compare):
                    sides = [_model_attr(side, references) for side in (node.left, *node.comparators)]
                    if all(sides):
                        continue  # JOIN condition, not a lookup filter
                    names.update(attr for attr in sides if attr)
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "in_"
                    and (attr := _model_attr(node.func.value, references))
                ):
                    names.add(attr)
    return names


def _enclosing_function_map(tree: ast.Module) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, set[int]]]:
    """Map each function to the set of line numbers it spans (innermost wins by order)."""
    funcs = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            funcs.append((node, set(range(node.lineno, end + 1))))
    return funcs


def _function_at(funcs: list, lineno: int) -> str:
    """Name of the innermost function containing *lineno* (module level -> '<module>')."""
    best: tuple[int, str] | None = None
    for node, span in funcs:
        if lineno in span:
            if best is None or len(span) < best[0]:
                best = (len(span), node.name)
    return best[1] if best else "<module>"


def find_unscoped_creative_queries(tree: ast.Module, relpath: str) -> list[tuple[int, str]]:
    """Return (lineno, function) per QUERY comparing creative_id without principal_id.

    Accumulator-style queries are graded on their merged filter set, which replaces the
    grading of the bare initial chain (that would miss filters added by later
    ``stmt = stmt.where(...)`` statements -- in either direction: unscoped accumulators
    hid, later-scoped ones false-flagged).
    """
    references = orm_references(relpath, tree)
    funcs = _enclosing_function_map(tree)
    violations: list[tuple[int, str]] = []
    for select_call, filters in _creative_query_chains(tree, relpath, references):
        compared = _attrs_compared_in_chain(filters, references)
        if "creative_id" in compared and "principal_id" not in compared:
            violations.append((select_call.lineno, _function_at(funcs, select_call.lineno)))
    return violations


def find_admin_calls(tree: ast.Module) -> list[tuple[int, str]]:
    """Return (lineno, function) for admin_* repository calls (tools scan)."""
    funcs = _enclosing_function_map(tree)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _ADMIN_REPO_METHODS
        ):
            hits.append((node.lineno, _function_at(funcs, node.lineno)))
    return hits


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
    def test_admin_call_detector(self):
        tree = ast.parse(
            "def buyer_path(uow, ids):\n"
            "    return uow.creatives.admin_get_by_ids(ids)\n"
            "def fine(uow, ids, pid):\n"
            "    return uow.creatives.get_by_ids(ids, pid)\n"
        )
        assert [f for _, f in find_admin_calls(tree)] == ["buyer_path"]
