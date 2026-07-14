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
   alias such as ``DBCreative``/``CreativeModel``/``DBAssignment``) that
   compares ``creative_id`` (via ``==``, ``filter_by`` kwargs, or ``.in_()``)
   without also comparing ``principal_id`` **in the same query chain**
   (per-QUERY granularity: a scoped query cannot credit an unscoped sibling
   in the same function).

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

from tests.unit._architecture_helpers import assert_violations_match_allowlist, parse_module

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
_ADMIN_CALL_ALLOWLIST: set[tuple[str, str]] = {
    # GATE-PUSH: pushes an admin-APPROVED creative to an existing buy; the
    # owning principal is re-derived from the creative row itself.
    ("src/core/tools/media_buy_create.py", "push_creative_to_existing_buy"),
}


def _model_aliases(tree: ast.Module) -> dict[str, str]:
    """Map local names (incl. ``import ... as`` aliases) to Creative-family models."""
    aliases: dict[str, str] = {name: name for name in _CREATIVE_MODELS}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _CREATIVE_MODELS and alias.asname:
                    aliases[alias.asname] = alias.name
    return aliases


def _is_model_attr(expr: ast.expr, aliases: dict[str, str]) -> bool:
    """True for ``<CreativeFamilyAlias>.<attr>`` attribute access."""
    return isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id in aliases


def _chain_parts(node: ast.Call) -> tuple[ast.Call | None, list[ast.Call]]:
    """Unwrap ``select(X).where(...).filter(...)`` chains.

    Returns (select_call, [filter-stage calls]) — select_call is None when the
    chain does not bottom out in a ``select(...)`` call.
    """
    parts: list[ast.Call] = []
    base: ast.expr = node
    while (
        isinstance(base, ast.Call)
        and isinstance(base.func, ast.Attribute)
        and base.func.attr in {"where", "filter", "filter_by"}
    ):
        parts.append(base)
        base = base.func.value
    if isinstance(base, ast.Call) and isinstance(base.func, ast.Name) and base.func.id == "select":
        return base, parts
    return None, parts


def _chain_base(node: ast.expr) -> tuple[ast.expr, list[ast.Call]]:
    """Unwrap a ``<base>.where(...).filter(...)`` chain to its base expression.

    Unlike ``_chain_parts`` this returns whatever the chain bottoms out in —
    a ``select(...)`` call, a plain ``ast.Name`` (accumulator style), or
    anything else — so callers can fold ``stmt = stmt.where(...)`` statements.
    """
    parts: list[ast.Call] = []
    base: ast.expr = node
    while (
        isinstance(base, ast.Call)
        and isinstance(base.func, ast.Attribute)
        and base.func.attr in {"where", "filter", "filter_by"}
    ):
        parts.append(base)
        base = base.func.value
    return base, parts


def _accumulated_chains(tree: ast.Module) -> list[tuple[ast.Call, list[ast.Call]]]:
    """Merge accumulator-style queries into single (select_call, parts) chains.

    Tracks simple ``name = select(...)...`` assignments per function scope and
    folds subsequent ``name = name.where/filter/filter_by(...)`` statements into
    the same chain, so the query is graded on its FULL accumulated filter set
    (an unscoped accumulator is flagged; principal_id pinned in a later
    statement is credited).
    """
    scopes: list[ast.AST] = [tree]
    scopes.extend(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    merged: list[tuple[ast.Call, list[ast.Call]]] = []

    def _local_assigns(scope: ast.AST) -> list[ast.Assign]:
        """Assign statements in *scope*, not descending into nested functions."""
        found: list[ast.Assign] = []
        stack: list[ast.AST] = list(ast.iter_child_nodes(scope))
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(node, ast.Assign):
                found.append(node)
            stack.extend(ast.iter_child_nodes(node))
        return sorted(found, key=lambda n: n.lineno)

    for scope in scopes:
        acc: dict[str, tuple[ast.Call, list[ast.Call]]] = {}
        for stmt in _local_assigns(scope):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                continue
            target = stmt.targets[0].id
            base, parts = _chain_base(stmt.value)
            if isinstance(base, ast.Call) and isinstance(base.func, ast.Name) and base.func.id == "select":
                acc[target] = (base, list(parts))
            elif isinstance(base, ast.Name) and base.id in acc and parts:
                sel, prev = acc[base.id]
                acc[target] = (sel, prev + parts)
            else:
                acc.pop(target, None)
        merged.extend(chain for chain in acc.values() if chain[1])
    return merged


def _attrs_compared_in_chain(parts: list[ast.Call], aliases: dict[str, str]) -> set[str]:
    """Collect model attrs pinned by a single query chain's filter stages.

    Counts ``Model.attr == value`` comparisons (excluding pure JOIN conditions
    where both sides are model attrs), ``filter_by(attr=...)`` kwargs, and
    ``Model.attr.in_(...)`` membership filters.
    """
    names: set[str] = set()
    for part in parts:
        if isinstance(part.func, ast.Attribute) and part.func.attr == "filter_by":
            for kw in part.keywords:
                if kw.arg:
                    names.add(kw.arg)
            continue
        for arg in part.args:
            for node in ast.walk(arg):
                if isinstance(node, ast.Compare):
                    sides = [node.left, *node.comparators]
                    if all(_is_model_attr(s, aliases) for s in sides):
                        continue  # JOIN condition, not a lookup filter
                    for expr in sides:
                        if _is_model_attr(expr, aliases):
                            names.add(expr.attr)  # type: ignore[union-attr]
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "in_"
                    and _is_model_attr(node.func.value, aliases)
                ):
                    names.add(node.func.value.attr)  # type: ignore[union-attr]
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


def find_unscoped_creative_queries(tree: ast.Module) -> list[tuple[int, str]]:
    """Return (lineno, description) per QUERY comparing creative_id without principal_id."""
    aliases = _model_aliases(tree)
    funcs = _enclosing_function_map(tree)
    violations: list[tuple[int, str]] = []
    seen_selects: set[int] = set()

    # Accumulator-style queries first: their merged filter set replaces the
    # grading of the bare initial chain (which would otherwise miss filters
    # added by later ``stmt = stmt.where(...)`` statements — in either
    # direction: unscoped accumulators hid, later-scoped ones false-flagged).
    chains: list[tuple[ast.Call, list[ast.Call]]] = list(_accumulated_chains(tree))

    # Then inline chains, longest first so subchains of a processed chain skip.
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    inline: list[tuple[ast.Call, list[ast.Call]]] = []
    for call in calls:
        select_call, parts = _chain_parts(call)
        if select_call is not None:
            inline.append((select_call, parts))
    inline.sort(key=lambda c: -len(c[1]))
    chains.extend(inline)

    for select_call, parts in chains:
        if id(select_call) in seen_selects:
            continue
        seen_selects.add(id(select_call))
        if not (select_call.args and isinstance(select_call.args[0], ast.Name) and select_call.args[0].id in aliases):
            continue
        compared = _attrs_compared_in_chain(parts, aliases)
        if "creative_id" in compared and "principal_id" not in compared:
            func_name = _function_at(funcs, select_call.lineno)
            violations.append((select_call.lineno, func_name))
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
            rel = str(path.relative_to(_REPO_ROOT))
            for _lineno, func in find_unscoped_creative_queries(parse_module(path)):
                found.add((rel, func))
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
            rel = str(path.relative_to(_REPO_ROOT))
            for _lineno, func in find_admin_calls(parse_module(path)):
                found.add((rel, func))
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


class TestDetectorMetaTests:
    """Exercise the LIVE detectors on synthetic modules (#1430 guard meta-drift
    discipline: meta-tests must pin the detectors the guard actually runs, incl.
    alias, .in_(), filter_by, per-query granularity, and the admin-call detector)."""

    @pytest.mark.arch_guard
    def test_detector_catches_tenant_only_lookup(self):
        tree = ast.parse(
            "from src.core.database.models import Creative\n"
            "def get_thing(self, creative_id):\n"
            "    return self._session.scalars(select(Creative).where(\n"
            "        Creative.tenant_id == self._tenant_id,\n"
            "        Creative.creative_id == creative_id,\n"
            "    )).first()\n"
        )
        assert [(f, "creative_id") for _, f in find_unscoped_creative_queries(tree)] == [("get_thing", "creative_id")]

    @pytest.mark.arch_guard
    def test_detector_catches_aliased_in_bulk_load(self):
        """DBCreative alias + .in_() bulk form — the shape that evaded the old detector."""
        tree = ast.parse(
            "from src.core.database.models import Creative as DBCreative\n"
            "def load(session, tenant_id, ids):\n"
            "    stmt = select(DBCreative).where(\n"
            "        DBCreative.tenant_id == tenant_id,\n"
            "        DBCreative.creative_id.in_(ids),\n"
            "    )\n"
            "    return session.scalars(stmt).all()\n"
        )
        assert [f for _, f in find_unscoped_creative_queries(tree)] == ["load"]

    @pytest.mark.arch_guard
    def test_detector_catches_filter_by_form(self):
        tree = ast.parse(
            "from src.core.database.models import Creative\n"
            "def get_thing(session, creative_id, tenant_id):\n"
            "    return session.scalars(select(Creative).filter_by(\n"
            "        creative_id=creative_id, tenant_id=tenant_id)).first()\n"
        )
        assert [f for _, f in find_unscoped_creative_queries(tree)] == ["get_thing"]

    @pytest.mark.arch_guard
    def test_detector_is_per_query_not_per_function(self):
        """A principal-scoped query must NOT credit an unscoped sibling in the same function."""
        tree = ast.parse(
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
        assert [f for _, f in find_unscoped_creative_queries(tree)] == ["two_queries"]

    @pytest.mark.arch_guard
    def test_detector_passes_scoped_and_non_creative_models(self):
        tree = ast.parse(
            "from src.core.database.models import Creative\n"
            "def get_thing(self, creative_id, principal_id):\n"
            "    return self._session.scalars(select(Creative).where(\n"
            "        Creative.tenant_id == self._tenant_id,\n"
            "        Creative.creative_id == creative_id,\n"
            "        Creative.principal_id == principal_id,\n"
            "    )).first()\n"
            "def other_model(session, x):\n"
            "    return session.scalars(select(Product).where(Product.creative_id == x)).all()\n"
        )
        assert find_unscoped_creative_queries(tree) == []

    @pytest.mark.arch_guard
    def test_detector_ignores_join_conditions(self):
        tree = ast.parse(
            "from src.core.database.models import Creative, CreativeAssignment\n"
            "def joined(session, pid, tid):\n"
            "    return session.scalars(select(Creative).where(\n"
            "        Creative.creative_id == CreativeAssignment.creative_id,\n"
            "        Creative.tenant_id == tid,\n"
            "        Creative.principal_id == pid,\n"
            "    )).all()\n"
        )
        assert find_unscoped_creative_queries(tree) == []

    @pytest.mark.arch_guard
    def test_detector_catches_unscoped_accumulator_query(self):
        """Accumulator style (stmt = select(...); stmt = stmt.where(...)) must be graded
        as ONE merged query — the shape the old detector silently dropped."""
        tree = ast.parse(
            "from src.core.database.models import Creative\n"
            "def accumulate(session, tid, ids):\n"
            "    stmt = select(Creative).where(Creative.tenant_id == tid)\n"
            "    stmt = stmt.where(Creative.creative_id.in_(ids))\n"
            "    return session.scalars(stmt).all()\n"
        )
        assert [f for _, f in find_unscoped_creative_queries(tree)] == ["accumulate"]

    @pytest.mark.arch_guard
    def test_detector_merges_accumulated_principal_scope(self):
        """principal_id pinned in a LATER accumulation statement scopes the whole
        query — the merged grading must not flag it (no false positive)."""
        tree = ast.parse(
            "from src.core.database.models import Creative\n"
            "def accumulate(session, tid, pid, ids):\n"
            "    stmt = select(Creative).filter_by(tenant_id=tid)\n"
            "    stmt = stmt.where(Creative.creative_id.in_(ids))\n"
            "    stmt = stmt.where(Creative.principal_id == pid)\n"
            "    return session.scalars(stmt).all()\n"
        )
        assert find_unscoped_creative_queries(tree) == []

    @pytest.mark.arch_guard
    def test_admin_call_detector(self):
        tree = ast.parse(
            "def buyer_path(uow, ids):\n"
            "    return uow.creatives.admin_get_by_ids(ids)\n"
            "def fine(uow, ids, pid):\n"
            "    return uow.creatives.get_by_ids(ids, pid)\n"
        )
        assert [f for _, f in find_admin_calls(tree)] == ["buyer_path"]
