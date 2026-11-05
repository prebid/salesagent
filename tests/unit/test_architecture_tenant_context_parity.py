"""Guard: every TenantContext field is plumbed all the way from the Tenant column.

A new per-tenant column has to be added in THREE coordinated places or it silently
reads as its default everywhere:

1. ``Tenant`` column                      (src/core/database/models.py)
2. ``TenantContext`` field                (src/core/tenant_context.py)
3. ``TenantContext.from_orm_model``       (src/core/tenant_context.py)

There is no fourth site: ``TenantContext.load`` goes through
``config_loader.get_tenant_context``, which builds the context with ``from_orm_model``.
``serialize_tenant_to_dict`` used to be that path, and was graded here as a fourth site;
it no longer feeds the context, and it deliberately leaves out ``ai_config``, a credential
the dict would carry to the Slack notifier.

Miss site 3 and nothing raises -- ``_impl`` reads the field's *default*, so
the failure surfaces as a scenario quietly asserting the wrong value, which in a
heavily-xfailed suite reads as "still not implemented" rather than "wired wrong".
That is the shape this guard exists to make loud, discovered while adding
``capability_declarations`` (#1592 T1a).

Deliberately NOT a value test: it compares the three NAME SETS structurally, so it
catches the next column too, not just the one that motivated it.
"""

import ast
from pathlib import Path

# Fields that intentionally exist on TenantContext without a same-named Tenant
# column. Each needs a reason; this list may only shrink.
_CONTEXT_ONLY_FIELDS: dict[str, str] = {}


def _tenant_column_names() -> set[str]:
    from src.core.database.models import Tenant

    return {c.name for c in Tenant.__table__.columns}


def _tenant_context_field_names() -> set[str]:
    from src.core.tenant_context import TenantContext

    return set(TenantContext.model_fields)


def _from_orm_model_kwargs() -> set[str]:
    """Keyword names passed to the ``cls(...)`` call inside ``from_orm_model``."""
    src = Path("src/core/tenant_context.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "from_orm_model":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and sub.keywords:
                    return {kw.arg for kw in sub.keywords if kw.arg}
    raise AssertionError("from_orm_model not found or makes no keyword call")


def test_every_context_field_has_a_tenant_column():
    missing = _tenant_context_field_names() - _tenant_column_names() - set(_CONTEXT_ONLY_FIELDS)
    assert not missing, (
        f"TenantContext fields with no matching Tenant column: {sorted(missing)}. "
        "Add the column, or record the exemption in _CONTEXT_ONLY_FIELDS with a reason."
    )


def test_every_context_field_is_populated_by_from_orm_model():
    """A field absent here reads as its DEFAULT on every request -- silently."""
    missing = _tenant_context_field_names() - _from_orm_model_kwargs()
    assert not missing, (
        f"TenantContext fields never populated by from_orm_model: {sorted(missing)}. "
        "These would silently read as their default on every tenant-resolved request."
    )


class TestMatcherModelsTheForm:
    """Self-tests: the readers extract real names, so a green result is meaningful."""

    def test_reads_a_known_column(self):
        assert "capability_declarations" in _tenant_column_names()

    def test_reads_a_known_from_orm_kwarg(self):
        assert "capability_declarations" in _from_orm_model_kwargs()

    def test_readers_are_not_trivially_empty(self):
        # A parse failure that returned an empty set would make every assertion
        # above vacuously pass.
        assert len(_tenant_column_names()) > 10
        assert len(_from_orm_model_kwargs()) > 10
