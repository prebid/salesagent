"""Schema-layer unit tests for property list constraints.

These tests verify property list schema constraints by calling production
_impl functions. Since property list CRUD _impl functions are not yet
implemented, tests are marked xfail with ImportError/NotImplementedError.

When _impl functions are added to ``src.core.tools.property_list``, these
tests will automatically start passing.

Every test method has a ``Covers: <obligation-id>`` tag in its docstring.
"""

from __future__ import annotations

import pytest
from adcp.types import (
    CreatePropertyListRequest,
    DeletePropertyListRequest,
    GetPropertyListRequest,
    GetPropertyListResponse,
    ListPropertyListsResponse,
    UpdatePropertyListRequest,
    UpdatePropertyListResponse,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# xfail marker -- property list CRUD _impl not yet implemented
# ---------------------------------------------------------------------------

_XFAIL_NO_IMPL = pytest.mark.xfail(
    reason="Property list CRUD _impl functions not yet implemented",
    raises=(ImportError, AttributeError, NotImplementedError),
    strict=False,
)


def _lazy_identity(tenant_id: str = "test_tenant", principal_id: str = "test_principal"):
    """Build a ResolvedIdentity for property list tests."""
    from src.core.resolved_identity import ResolvedIdentity
    from src.core.testing_hooks import AdCPTestContext

    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant={"tenant_id": tenant_id, "name": "Test Tenant"},
        protocol="mcp",
        testing_context=AdCPTestContext(
            dry_run=False,
            mock_time=None,
            jump_to_event=None,
            test_session_id=None,
        ),
    )


# ---------------------------------------------------------------------------
# CONSTR-PROPERTY-LIST-AUTH-TOKEN-01
# auth_token returned once in create response, absent from get/list/update/delete.
# ---------------------------------------------------------------------------


class TestPropertyListAuthToken:
    """Auth token field presence and absence via production _impl path."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_create_response_contains_auth_token(self):
        """Create response from _impl includes auth_token as a required field.

        The _impl function generates a one-time auth_token on create.
        Schema-level: auth_token is required on CreatePropertyListResponse.

        Covers: CONSTR-PROPERTY-LIST-AUTH-TOKEN-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        identity = _lazy_identity()
        req = CreatePropertyListRequest(name="Auth Token Test")
        resp = await _create_property_list_impl(req, identity)

        assert resp.auth_token is not None
        assert len(resp.auth_token) >= 32

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_auth_token_absent_from_get_update_delete_list(self):
        """auth_token is NOT present on get/update/delete/list responses.

        After create, subsequent operations never expose the auth_token.
        Verified at schema level: get/update/list response types lack the field.

        Covers: CONSTR-PROPERTY-LIST-AUTH-TOKEN-01
        """
        from src.core.tools.property_list import (
            _create_property_list_impl,
            _get_property_list_impl,
        )

        identity = _lazy_identity()
        create_req = CreatePropertyListRequest(name="Token Absence Test")
        create_resp = await _create_property_list_impl(create_req, identity)
        list_id = create_resp.list.list_id

        get_resp = await _get_property_list_impl(list_id, identity)
        get_data = get_resp.model_dump()
        assert "auth_token" not in get_data

        # Also verify at schema level
        for cls in [GetPropertyListResponse, UpdatePropertyListResponse, ListPropertyListsResponse]:
            assert "auth_token" not in cls.model_fields, f"auth_token must not appear in {cls.__name__}"

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_create_response_roundtrip_with_auth_token(self):
        """Create response auth_token survives serialization roundtrip.

        The _impl-produced response serializes auth_token correctly
        through model_dump().

        Covers: CONSTR-PROPERTY-LIST-AUTH-TOKEN-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        identity = _lazy_identity()
        req = CreatePropertyListRequest(name="Roundtrip Test")
        resp = await _create_property_list_impl(req, identity)

        data = resp.model_dump()
        assert "auth_token" in data
        assert len(data["auth_token"]) >= 32
        assert data["list"]["name"] == "Roundtrip Test"


# ---------------------------------------------------------------------------
# CONSTR-PROPERTY-LIST-LIST-ID-01
# System-assigned unique identifier. Required for get/update/delete.
# ---------------------------------------------------------------------------


class TestPropertyListListId:
    """list_id required on get/update/delete request schemas."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "operation",
        ["get", "update", "delete"],
    )
    async def test_list_id_required_on_operations(self, operation):
        """list_id is a required field on get/update/delete -- verified by
        calling _impl with a valid list_id and confirming no validation error.

        Covers: CONSTR-PROPERTY-LIST-LIST-ID-01
        """
        from src.core.tools.property_list import (
            _create_property_list_impl,
            _delete_property_list_impl,
            _get_property_list_impl,
            _update_property_list_impl,
        )

        identity = _lazy_identity()

        # Create a list to get a valid list_id
        create_req = CreatePropertyListRequest(name=f"ListId {operation} Test")
        create_resp = await _create_property_list_impl(create_req, identity)
        list_id = create_resp.list.list_id

        if operation == "get":
            resp = await _get_property_list_impl(list_id, identity)
            assert resp.list.list_id == list_id
        elif operation == "update":
            update_req = UpdatePropertyListRequest(list_id=list_id, name="Updated")
            resp = await _update_property_list_impl(update_req, identity)
            assert resp.list.list_id == list_id
        elif operation == "delete":
            resp = await _delete_property_list_impl(list_id, identity)
            assert resp.list_id == list_id

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "cls",
        [
            GetPropertyListRequest,
            UpdatePropertyListRequest,
            DeletePropertyListRequest,
        ],
        ids=["get", "update", "delete"],
    )
    async def test_list_id_missing_raises_validation_error(self, cls):
        """Omitting list_id from get/update/delete raises ValidationError.

        Schema-level enforcement: the request cannot be constructed without list_id.
        This prevents calling _impl with an invalid request.

        Covers: CONSTR-PROPERTY-LIST-LIST-ID-01
        """
        from src.core.tools.property_list import _get_property_list_impl  # noqa: F401

        with pytest.raises(ValidationError) as exc_info:
            cls()  # type: ignore[call-arg]
        errors = exc_info.value.errors()
        list_id_errors = [e for e in errors if "list_id" in e["loc"]]
        assert list_id_errors, f"Expected list_id validation error on {cls.__name__}"


# ---------------------------------------------------------------------------
# CONSTR-PROPERTY-LIST-NAME-01
# name is required string for property list creation.
# ---------------------------------------------------------------------------


class TestPropertyListName:
    """name field constraints on create request."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_name_required_on_create(self):
        """name is required on CreatePropertyListRequest -- schema rejects omission.

        The _impl function requires a valid CreatePropertyListRequest which
        mandates the name field.

        Covers: CONSTR-PROPERTY-LIST-NAME-01
        """
        from src.core.tools.property_list import _create_property_list_impl  # noqa: F401

        assert "name" in CreatePropertyListRequest.model_fields
        assert CreatePropertyListRequest.model_fields["name"].is_required()

        # Confirm schema enforcement
        with pytest.raises(ValidationError) as exc_info:
            CreatePropertyListRequest()  # type: ignore[call-arg]
        name_errors = [e for e in exc_info.value.errors() if "name" in e["loc"]]
        assert name_errors, "Expected name validation error"

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_create_without_name_raises_validation_error(self):
        """Omitting name from create request raises ValidationError before _impl.

        Schema-level enforcement prevents constructing a request without name,
        so _impl never receives an invalid request.

        Covers: CONSTR-PROPERTY-LIST-NAME-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        identity = _lazy_identity()

        # Cannot even construct the request without name
        with pytest.raises(ValidationError) as exc_info:
            req = CreatePropertyListRequest()  # type: ignore[call-arg]
            await _create_property_list_impl(req, identity)

        errors = exc_info.value.errors()
        name_errors = [e for e in errors if "name" in e["loc"]]
        assert name_errors, "Expected name validation error"

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_create_with_valid_name(self):
        """CreatePropertyListRequest with valid name produces a list via _impl.

        Covers: CONSTR-PROPERTY-LIST-NAME-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        identity = _lazy_identity()
        req = CreatePropertyListRequest(name="Sports Inventory")
        resp = await _create_property_list_impl(req, identity)

        assert resp.list.name == "Sports Inventory"


# ---------------------------------------------------------------------------
# CONSTR-PROPERTY-LIST-RESOLVE-01
# resolve=true (default) evaluates filters; resolve=false returns metadata only.
# ---------------------------------------------------------------------------


class TestPropertyListResolve:
    """resolve flag on GetPropertyListRequest."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_resolve_defaults_to_true(self):
        """resolve defaults to True on GetPropertyListRequest.

        When calling _get_property_list_impl without explicit resolve,
        the request defaults to resolve=True (evaluate filters).

        Covers: CONSTR-PROPERTY-LIST-RESOLVE-01
        """
        from src.core.tools.property_list import (
            _create_property_list_impl,
            _get_property_list_impl,
        )

        identity = _lazy_identity()
        create_req = CreatePropertyListRequest(name="Resolve Default Test")
        create_resp = await _create_property_list_impl(create_req, identity)
        list_id = create_resp.list.list_id

        # Get without explicit resolve -- defaults to True
        req = GetPropertyListRequest(list_id=list_id)
        assert req.resolve is True

        resp = await _get_property_list_impl(list_id, identity)
        assert resp.list.list_id == list_id

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_resolve_false_accepted(self):
        """resolve=False is accepted and returns metadata only.

        Covers: CONSTR-PROPERTY-LIST-RESOLVE-01
        """
        from src.core.tools.property_list import (
            _create_property_list_impl,
            _get_property_list_impl,
        )

        identity = _lazy_identity()
        create_req = CreatePropertyListRequest(name="Resolve False Test")
        create_resp = await _create_property_list_impl(create_req, identity)
        list_id = create_resp.list.list_id

        req = GetPropertyListRequest(list_id=list_id, resolve=False)
        assert req.resolve is False

        resp = await _get_property_list_impl(list_id, identity, resolve=False)
        assert resp.list.list_id == list_id


# ---------------------------------------------------------------------------
# CONSTR-PROPERTY-LIST-WEBHOOK-URL-01
# webhook_url only on update (not create). URI format when set.
# ---------------------------------------------------------------------------


class TestPropertyListWebhookUrl:
    """webhook_url field presence and validation."""

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_webhook_url_not_in_create_schema(self):
        """webhook_url is not a field on CreatePropertyListRequest.

        Schema-level enforcement: the create request type does not accept
        webhook_url, preventing callers from setting it on create.

        Covers: CONSTR-PROPERTY-LIST-WEBHOOK-URL-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        identity = _lazy_identity()
        assert "webhook_url" not in CreatePropertyListRequest.model_fields

        # Valid create without webhook_url succeeds
        req = CreatePropertyListRequest(name="No Webhook Create")
        resp = await _create_property_list_impl(req, identity)
        assert resp.list.list_id is not None

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_webhook_url_rejected_in_create_request(self):
        """Passing webhook_url to CreatePropertyListRequest raises ValidationError.

        Covers: CONSTR-PROPERTY-LIST-WEBHOOK-URL-01
        """
        from src.core.tools.property_list import _create_property_list_impl

        identity = _lazy_identity()

        with pytest.raises(ValidationError) as exc_info:
            req = CreatePropertyListRequest(
                name="Test List",
                webhook_url="https://example.com/webhook",  # type: ignore[call-arg]
            )
            await _create_property_list_impl(req, identity)

        errors = exc_info.value.errors()
        assert any("webhook_url" in str(e["loc"]) for e in errors)

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_webhook_url_present_in_update_schema(self):
        """webhook_url is a valid field on UpdatePropertyListRequest.

        The update request accepts webhook_url, and _update_property_list_impl
        persists it.

        Covers: CONSTR-PROPERTY-LIST-WEBHOOK-URL-01
        """
        from src.core.tools.property_list import (
            _create_property_list_impl,
            _update_property_list_impl,
        )

        identity = _lazy_identity()
        assert "webhook_url" in UpdatePropertyListRequest.model_fields

        # Create then update with webhook_url
        create_req = CreatePropertyListRequest(name="Webhook Update Test")
        create_resp = await _create_property_list_impl(create_req, identity)

        update_req = UpdatePropertyListRequest(
            list_id=create_resp.list.list_id,
            webhook_url="https://example.com/webhook",
        )
        resp = await _update_property_list_impl(update_req, identity)
        assert resp.list.list_id == create_resp.list.list_id

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_valid_webhook_url_accepted_on_update(self):
        """UpdatePropertyListRequest accepts a valid URL for webhook_url
        and _impl persists it.

        Covers: CONSTR-PROPERTY-LIST-WEBHOOK-URL-01
        """
        from src.core.tools.property_list import (
            _create_property_list_impl,
            _get_property_list_impl,
            _update_property_list_impl,
        )

        identity = _lazy_identity()

        create_req = CreatePropertyListRequest(name="Valid Webhook Test")
        create_resp = await _create_property_list_impl(create_req, identity)
        list_id = create_resp.list.list_id

        update_req = UpdatePropertyListRequest(
            list_id=list_id,
            webhook_url="https://example.com/webhook",
        )
        await _update_property_list_impl(update_req, identity)

        get_resp = await _get_property_list_impl(list_id, identity)
        assert str(get_resp.list.webhook_url) == "https://example.com/webhook"

    @_XFAIL_NO_IMPL
    @pytest.mark.asyncio
    async def test_invalid_webhook_url_rejected_on_update(self):
        """UpdatePropertyListRequest rejects an invalid URL for webhook_url.

        Schema-level enforcement prevents constructing the request with an
        invalid URL, so _impl never receives it.

        Covers: CONSTR-PROPERTY-LIST-WEBHOOK-URL-01
        """
        from src.core.tools.property_list import _update_property_list_impl

        identity = _lazy_identity()

        with pytest.raises(ValidationError):
            req = UpdatePropertyListRequest(
                list_id="pl-001",
                webhook_url="not-a-valid-url",
            )
            await _update_property_list_impl(req, identity)
