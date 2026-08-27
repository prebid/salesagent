"""Guard: every TenantContext field is plumbed all the way from the Tenant column.

A new per-tenant column has to be added in FOUR coordinated places or it silently
reads as its default everywhere:

1. ``Tenant`` column                      (src/core/database/models.py)
2. ``TenantContext`` field                (src/core/tenant_context.py)
3. ``TenantContext.from_orm_model``       (src/core/tenant_context.py)
4. ``serialize_tenant_to_dict``           (src/core/utils/tenant_utils.py)

There is no fifth site: ``config_loader.get_tenant_by_id`` and
``get_tenant_by_virtual_host`` both delegate to ``serialize_tenant_to_dict``, and
``TenantContext.from_dict`` filters to ``model_fields``.

Miss site 3 or 4 and nothing raises -- ``_impl`` reads the field's *default*, so
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
_CONTEXT_ONLY_FIELDS: dict[str, str] = {
    # Tenant stores the encrypted column as `_gemini_api_key` and exposes a
    # `gemini_api_key` property; the context carries the decrypted value.
    "gemini_api_key": "Tenant maps this via the _gemini_api_key column + property",
}

# Keys serialize_tenant_to_dict emits under a DIFFERENT name than the column /
# context field. TenantContext.from_dict already reconciles these.
_SERIALIZER_ALIASES: dict[str, str] = {
    "auto_approve_format_ids": "auto_approve_formats",
}


def _tenant_column_names() -> set[str]:
    from src.core.database.models import Tenant

    return {c.name for c in Tenant.__table__.columns}


def _tenant_context_field_names() -> set[str]:
    from src.core.tenant_context import TenantContext

    return set(TenantContext.model_fields)


def _serializer_keys() -> set[str]:
    """The literal dict keys ``serialize_tenant_to_dict`` returns, read from source.

    Read via AST rather than by calling it: calling would need a Tenant instance,
    and a partially-populated stub would hide exactly the omission being checked.
    """
    src = Path("src/core/utils/tenant_utils.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "serialize_tenant_to_dict":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    return {k.value for k in sub.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    raise AssertionError("serialize_tenant_to_dict not found or returns no dict literal")


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


def test_every_context_field_is_serialized():
    """A field absent here is lost on the dict path (config_loader -> from_dict)."""
    serialized = _serializer_keys()
    expected = {_SERIALIZER_ALIASES.get(f, f) for f in _tenant_context_field_names()} - set(_CONTEXT_ONLY_FIELDS)
    missing = expected - serialized
    assert not missing, (
        f"TenantContext fields missing from serialize_tenant_to_dict: {sorted(missing)}. "
        "These are dropped on the dict path used by config_loader.get_tenant_by_id."
    )


class TestMatcherModelsTheForm:
    """Self-tests: the readers extract real names, so a green result is meaningful."""

    def test_reads_a_known_column(self):
        assert "capability_declarations" in _tenant_column_names()

    def test_reads_a_known_from_orm_kwarg(self):
        assert "capability_declarations" in _from_orm_model_kwargs()

    def test_reads_a_known_serializer_key(self):
        assert "capability_declarations" in _serializer_keys()

    def test_readers_are_not_trivially_empty(self):
        # A parse failure that returned an empty set would make every assertion
        # above vacuously pass.
        assert len(_tenant_column_names()) > 10
        assert len(_from_orm_model_kwargs()) > 10
        assert len(_serializer_keys()) > 10
