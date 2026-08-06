"""Unit tests for NestedModelSerializerMixin's top-level exclude_none default.

Pins the fix for the MCP null-field-leak bug: FastMCP serializes ToolResult's
structured_content via pydantic_core.to_jsonable_python, which triggers the
mixin's @model_serializer(mode="wrap") hook but never calls the class's own
model_dump() Python-method override (where the adcp library base's
exclude_none=True default lives). The mixin must therefore apply that same
default itself for its own top-level fields, not just for nested children.
"""

from adcp.types import BrandReference

from src.core.schemas.account import SyncAccountsResponse, SyncResponseAccount


def _make_response(**overrides):
    account = SyncResponseAccount(
        brand=BrandReference(domain="acme.com"),
        operator="example.com",
        action="created",
        status="active",
    )
    kwargs = {"accounts": [account]}
    kwargs.update(overrides)
    return SyncAccountsResponse(**kwargs)


class TestNestedModelSerializerMixinTopLevelExcludeNone:
    def test_pydantic_core_path_omits_unset_top_level_none_fields(self):
        """The pydantic_core wrap-serializer path (what FastMCP's
        to_jsonable_python triggers) must omit unset optional top-level
        fields, matching this class's documented exclude_none=True default."""
        import pydantic_core

        response = _make_response()
        data = pydantic_core.to_jsonable_python(response)

        null_keys = {k for k, v in data.items() if v is None}
        assert not null_keys, f"pydantic_core dump leaked null top-level keys: {sorted(null_keys)}"

    def test_bare_model_dump_still_omits_none_fields(self):
        """The class's own .model_dump() method call must keep excluding
        None by default — this must not regress."""
        response = _make_response()
        data = response.model_dump()

        null_keys = {k for k, v in data.items() if v is None}
        assert not null_keys, f".model_dump() leaked null top-level keys: {sorted(null_keys)}"

    def test_exclude_none_false_cannot_be_honored_through_the_mixin(self):
        """Known limitation, pinned deliberately: since @model_serializer
        intercepts EVERY .model_dump() call on this class (not just the bare
        pydantic_core path FastMCP uses), the mixin's wrap hook cannot
        distinguish a caller's explicit exclude_none=False from Pydantic's
        own unset default — both present as info.exclude_none=False. The fix
        treats them identically (always exclude None for its own top-level
        fields), matching this codebase's documented default everywhere else.
        No production call site needs nulls preserved through this path
        (confirmed: the only real exclude_none=False site in the codebase is
        Package.model_dump_internal, a distinct DB-storage method untouched
        by this mixin)."""
        response = _make_response()
        data = response.model_dump(exclude_none=False)

        assert "dry_run" not in data

    def test_nested_account_still_omits_its_own_none_fields(self):
        """Nested list items (already correct pre-fix) keep excluding None —
        this fix must not regress the existing correct behavior."""
        import pydantic_core

        response = _make_response()
        data = pydantic_core.to_jsonable_python(response)

        account_data = data["accounts"][0]
        null_keys = {k for k, v in account_data.items() if v is None}
        assert not null_keys, f"nested account dump leaked null keys: {sorted(null_keys)}"
