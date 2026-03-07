"""Schema-layer unit tests for property list constraints.

These tests verify schema shape, field presence, and validation at the
Pydantic schema layer -- no database or transport required.

Every test method has a ``Covers: <obligation-id>`` tag in its docstring.
"""

from __future__ import annotations

import pytest
from adcp.types import (
    CreatePropertyListRequest,
    CreatePropertyListResponse,
    DeletePropertyListRequest,
    GetPropertyListRequest,
    GetPropertyListResponse,
    ListPropertyListsResponse,
    PropertyList,
    UpdatePropertyListRequest,
    UpdatePropertyListResponse,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# CONSTR-PROPERTY-LIST-AUTH-TOKEN-01
# auth_token returned once in create response, absent from get/list/update/delete.
# ---------------------------------------------------------------------------


class TestPropertyListAuthToken:
    """Auth token field presence and absence."""

    def test_create_response_contains_auth_token(self):
        """CreatePropertyListResponse includes auth_token as a required field.

        Covers: CONSTR-PROPERTY-LIST-AUTH-TOKEN-01
        """
        assert "auth_token" in CreatePropertyListResponse.model_fields
        field = CreatePropertyListResponse.model_fields["auth_token"]
        assert field.is_required(), "auth_token must be required on create response"

    def test_auth_token_absent_from_get_update_delete_list(self):
        """auth_token is NOT present on get/update/delete/list responses.

        Covers: CONSTR-PROPERTY-LIST-AUTH-TOKEN-01
        """
        for cls in [
            GetPropertyListResponse,
            UpdatePropertyListResponse,
            ListPropertyListsResponse,
        ]:
            assert "auth_token" not in cls.model_fields, f"auth_token must not appear in {cls.__name__}"

    def test_create_response_roundtrip_with_auth_token(self):
        """CreatePropertyListResponse serializes auth_token correctly.

        Covers: CONSTR-PROPERTY-LIST-AUTH-TOKEN-01
        """
        token = "a" * 64  # realistic token length
        resp = CreatePropertyListResponse(
            auth_token=token,
            list=PropertyList(list_id="pl-001", name="Test List"),
        )
        data = resp.model_dump()
        assert data["auth_token"] == token
        assert len(data["auth_token"]) >= 32


# ---------------------------------------------------------------------------
# CONSTR-PROPERTY-LIST-LIST-ID-01
# System-assigned unique identifier. Required for get/update/delete.
# ---------------------------------------------------------------------------


class TestPropertyListListId:
    """list_id required on get/update/delete request schemas."""

    @pytest.mark.parametrize(
        "cls",
        [
            GetPropertyListRequest,
            UpdatePropertyListRequest,
            DeletePropertyListRequest,
        ],
        ids=["get", "update", "delete"],
    )
    def test_list_id_required_on_operations(self, cls):
        """list_id is a required field on get/update/delete requests.

        Covers: CONSTR-PROPERTY-LIST-LIST-ID-01
        """
        assert "list_id" in cls.model_fields
        assert cls.model_fields["list_id"].is_required(), f"list_id must be required on {cls.__name__}"

    @pytest.mark.parametrize(
        "cls",
        [
            GetPropertyListRequest,
            UpdatePropertyListRequest,
            DeletePropertyListRequest,
        ],
        ids=["get", "update", "delete"],
    )
    def test_list_id_missing_raises_validation_error(self, cls):
        """Omitting list_id from get/update/delete raises ValidationError.

        Covers: CONSTR-PROPERTY-LIST-LIST-ID-01
        """
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

    def test_name_required_on_create(self):
        """name is required on CreatePropertyListRequest.

        Covers: CONSTR-PROPERTY-LIST-NAME-01
        """
        assert "name" in CreatePropertyListRequest.model_fields
        assert CreatePropertyListRequest.model_fields["name"].is_required()

    def test_create_without_name_raises_validation_error(self):
        """Omitting name from create request raises ValidationError.

        Covers: CONSTR-PROPERTY-LIST-NAME-01
        """
        with pytest.raises(ValidationError) as exc_info:
            CreatePropertyListRequest()  # type: ignore[call-arg]
        errors = exc_info.value.errors()
        name_errors = [e for e in errors if "name" in e["loc"]]
        assert name_errors, "Expected name validation error"

    def test_create_with_valid_name(self):
        """CreatePropertyListRequest accepts a valid name string.

        Covers: CONSTR-PROPERTY-LIST-NAME-01
        """
        req = CreatePropertyListRequest(name="Sports Inventory")
        assert req.name == "Sports Inventory"


# ---------------------------------------------------------------------------
# CONSTR-PROPERTY-LIST-RESOLVE-01
# resolve=true (default) evaluates filters; resolve=false returns metadata only.
# ---------------------------------------------------------------------------


class TestPropertyListResolve:
    """resolve flag on GetPropertyListRequest."""

    def test_resolve_defaults_to_true(self):
        """resolve defaults to True on GetPropertyListRequest.

        Covers: CONSTR-PROPERTY-LIST-RESOLVE-01
        """
        req = GetPropertyListRequest(list_id="pl-001")
        assert req.resolve is True

    def test_resolve_false_accepted(self):
        """resolve=False is accepted on GetPropertyListRequest.

        Covers: CONSTR-PROPERTY-LIST-RESOLVE-01
        """
        req = GetPropertyListRequest(list_id="pl-001", resolve=False)
        assert req.resolve is False


# ---------------------------------------------------------------------------
# CONSTR-PROPERTY-LIST-WEBHOOK-URL-01
# webhook_url only on update (not create). URI format when set.
# ---------------------------------------------------------------------------


class TestPropertyListWebhookUrl:
    """webhook_url field presence and validation."""

    def test_webhook_url_not_in_create_schema(self):
        """webhook_url is not a field on CreatePropertyListRequest.

        Covers: CONSTR-PROPERTY-LIST-WEBHOOK-URL-01
        """
        assert "webhook_url" not in CreatePropertyListRequest.model_fields

    def test_webhook_url_rejected_in_create_request(self):
        """Passing webhook_url to CreatePropertyListRequest raises ValidationError.

        Covers: CONSTR-PROPERTY-LIST-WEBHOOK-URL-01
        """
        with pytest.raises(ValidationError) as exc_info:
            CreatePropertyListRequest(
                name="Test List",
                webhook_url="https://example.com/webhook",  # type: ignore[call-arg]
            )
        errors = exc_info.value.errors()
        assert any("webhook_url" in str(e["loc"]) for e in errors)

    def test_webhook_url_present_in_update_schema(self):
        """webhook_url is a valid field on UpdatePropertyListRequest.

        Covers: CONSTR-PROPERTY-LIST-WEBHOOK-URL-01
        """
        assert "webhook_url" in UpdatePropertyListRequest.model_fields

    def test_valid_webhook_url_accepted_on_update(self):
        """UpdatePropertyListRequest accepts a valid URL for webhook_url.

        Covers: CONSTR-PROPERTY-LIST-WEBHOOK-URL-01
        """
        req = UpdatePropertyListRequest(
            list_id="pl-001",
            webhook_url="https://example.com/webhook",
        )
        assert str(req.webhook_url) == "https://example.com/webhook"

    def test_invalid_webhook_url_rejected_on_update(self):
        """UpdatePropertyListRequest rejects an invalid URL for webhook_url.

        Covers: CONSTR-PROPERTY-LIST-WEBHOOK-URL-01
        """
        with pytest.raises(ValidationError):
            UpdatePropertyListRequest(
                list_id="pl-001",
                webhook_url="not-a-valid-url",
            )
