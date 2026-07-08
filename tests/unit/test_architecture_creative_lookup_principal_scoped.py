"""Guard: buyer-path single-creative lookups must be principal-scoped.

Disease pattern (PR #1430 review, cross-principal FK-500/leak): the creatives
PK is composite ``(creative_id, tenant_id, principal_id)``. A buyer-path
lookup that filters tenant-only matches ANOTHER principal's row, so a
cross-principal reference passes existence gates (then violates the FK on
insert) and leaks the other principal's fields into the requester's errors.

Rule: in ``src/core/database/repositories/creative.py``, any method that
selects ``Creative`` and compares ``Creative.creative_id`` must ALSO compare
``Creative.principal_id`` — unless the method name starts with ``admin_``
(the seller-side admin UI is tenant-scoped by design).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import iter_call_expressions, parse_module

_CREATIVE_REPO = Path(__file__).resolve().parents[2] / "src" / "core" / "database" / "repositories" / "creative.py"


def _is_model_attr(expr: ast.expr) -> bool:
    """True for ``<SomeModel>.<attr>`` (capitalized Name attribute access)."""
    return isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id[:1].isupper()


def _attr_names_compared(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Collect ``Creative.<attr>`` names used as lookup filters within *func*.

    Counts ``Creative.<attr> == <value>`` comparisons — EXCLUDING join
    conditions where both sides are model attributes (e.g.
    ``Creative.creative_id == CreativeAssignment.creative_id``) — plus
    ``filter_by(<attr>=...)`` keyword filters.
    """
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            if all(_is_model_attr(s) for s in sides):
                continue  # JOIN condition, not a lookup filter
            for expr in sides:
                if _is_model_attr(expr) and isinstance(expr.value, ast.Name) and expr.value.id == "Creative":
                    names.add(expr.attr)
    for call in iter_call_expressions(func, name="filter_by"):
        for kw in call.keywords:
            if kw.arg:
                names.add(kw.arg)
    return names


def find_tenant_only_creative_lookups(tree: ast.Module) -> list[tuple[int, str]]:
    """Return (lineno, method) for non-admin methods comparing creative_id without principal_id."""
    violations: list[tuple[int, str]] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if func.name.startswith("admin_"):
            continue
        selects_creative = any(
            call.args and isinstance(call.args[0], ast.Name) and call.args[0].id == "Creative"
            for call in iter_call_expressions(func, name="select")
        )
        if not selects_creative:
            continue
        compared = _attr_names_compared(func)
        if "creative_id" in compared and "principal_id" not in compared:
            violations.append((func.lineno, func.name))
    return violations


class TestCreativeLookupPrincipalScoped:
    @pytest.mark.arch_guard
    def test_no_tenant_only_buyer_path_creative_lookup(self):
        violations = find_tenant_only_creative_lookups(parse_module(_CREATIVE_REPO))
        assert not violations, (
            "Buyer-path Creative lookups must compare Creative.principal_id (composite PK "
            "— tenant-only matching enables cross-principal FK-500/leak, PR #1430 review):\n"
            + "\n".join(f"  creative.py:{line} {name}" for line, name in violations)
            + "\nAdmin-scoped lookups must be named admin_*."
        )


class TestDetectorMetaTests:
    @pytest.mark.arch_guard
    def test_detector_catches_tenant_only_lookup(self):
        tree = ast.parse(
            "def get_thing(self, creative_id):\n"
            "    return self._session.scalars(select(Creative).where(\n"
            "        Creative.tenant_id == self._tenant_id,\n"
            "        Creative.creative_id == creative_id,\n"
            "    )).first()\n"
        )
        assert find_tenant_only_creative_lookups(tree) == [(1, "get_thing")]

    @pytest.mark.arch_guard
    def test_detector_passes_scoped_and_admin(self):
        tree = ast.parse(
            "def get_thing(self, creative_id, principal_id):\n"
            "    return self._session.scalars(select(Creative).where(\n"
            "        Creative.tenant_id == self._tenant_id,\n"
            "        Creative.creative_id == creative_id,\n"
            "        Creative.principal_id == principal_id,\n"
            "    )).first()\n"
            "def admin_get_thing(self, creative_id):\n"
            "    return self._session.scalars(select(Creative).where(\n"
            "        Creative.creative_id == creative_id,\n"
            "    )).first()\n"
        )
        assert find_tenant_only_creative_lookups(tree) == []
