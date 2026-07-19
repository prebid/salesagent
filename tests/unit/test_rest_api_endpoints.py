"""Tests for REST API /api/v1/* endpoints (all handlers except get_products).

Validates that each REST transport endpoint:
- Route exists (not 404)
- Returns 200 with valid mock data
- Auth-optional endpoints work without auth

beads: salesagent-b61l.15
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from adcp.types import AccountReference as LibraryAccountReference
from adcp.types import ContextObject, PushNotificationConfig, ReportingWebhook
from starlette.testclient import TestClient

from src.app import app
from src.core.resolved_identity import ResolvedIdentity
from tests.helpers import assert_envelope_shape

client = TestClient(app)

_MOCK_IDENTITY = ResolvedIdentity(
    principal_id="test-principal",
    tenant_id="default",
    tenant={"tenant_id": "default"},
    auth_token="test-token",
    protocol="rest",
)


# ---------------------------------------------------------------------------
# Discovery endpoints (auth-optional)
# ---------------------------------------------------------------------------


class TestCapabilitiesProtocolsQuery:
    """REST GET /capabilities must accept and honor the ``protocols`` filter (#1546).

    Before the fix the route had no ``protocols`` param at all — REST could not
    filter. These exercise the real wire path (no impl mock): the auth-optional,
    no-tenant path returns the filtered supported_protocols.
    """

    def test_no_protocols_returns_full_set(self):
        response = client.get("/api/v1/capabilities")
        assert response.status_code == 200
        assert response.json()["supported_protocols"] == ["media_buy"]

    def test_repeated_protocols_param_filters(self):
        response = client.get("/api/v1/capabilities?protocols=media_buy")
        assert response.status_code == 200
        assert response.json()["supported_protocols"] == ["media_buy"]

    def test_csv_protocols_param_intersects(self):
        # media_buy supported, signals not -> only media_buy survives.
        response = client.get("/api/v1/capabilities?protocols=media_buy,signals")
        assert response.status_code == 200
        assert response.json()["supported_protocols"] == ["media_buy"]

    def test_only_unsupported_protocol_is_validation_error(self):
        response = client.get("/api/v1/capabilities?protocols=signals")
        assert response.status_code == 400
        assert_envelope_shape(response.json(), "VALIDATION_ERROR", recovery="correctable")

    def test_unknown_protocol_is_validation_error(self):
        response = client.get("/api/v1/capabilities?protocols=marketing")
        assert response.status_code == 400
        assert_envelope_shape(response.json(), "VALIDATION_ERROR", recovery="correctable")

    def test_valid_idempotency_key_is_inert(self):
        response = client.get("/api/v1/capabilities?idempotency_key=valid-read-key-0001")
        assert response.status_code == 200
        assert response.json()["supported_protocols"] == ["media_buy"]

    def test_malformed_idempotency_key_is_validation_error(self):
        response = client.get("/api/v1/capabilities?idempotency_key=short")
        assert response.status_code == 400
        assert_envelope_shape(
            response.json(),
            "VALIDATION_ERROR",
            recovery="correctable",
            message_substr="idempotency_key is too short",
        )


_STANDARD_READ_REST_CASES = (
    pytest.param(
        "get_products",
        "/api/v1/products",
        {"brief": "ads"},
        "src.core.tools.products._get_products_impl",
        False,
        id="get-products",
    ),
    pytest.param(
        "list_creative_formats",
        "/api/v1/creative-formats",
        {},
        "src.core.tools.creative_formats.list_creative_formats_raw",
        False,
        id="list-creative-formats",
    ),
    pytest.param(
        "get_media_buy_delivery",
        "/api/v1/media-buys/delivery",
        {},
        "src.core.tools.media_buy_delivery.get_media_buy_delivery_raw",
        True,
        id="get-media-buy-delivery",
    ),
    pytest.param(
        "list_creatives",
        "/api/v1/creatives",
        {},
        "src.core.tools.creatives.listing.list_creatives_raw",
        True,
        id="list-creatives",
    ),
    pytest.param(
        "list_accounts",
        "/api/v1/accounts",
        {},
        "src.core.tools.accounts.list_accounts_raw",
        True,
        id="list-accounts",
    ),
)


class TestStandardReadIdempotencyRestBoundary:
    """REST reads preserve auth/version precedence and consume inert keys."""

    @pytest.mark.parametrize(
        ("tool_name", "url", "body", "core_target", "auth_required"),
        _STANDARD_READ_REST_CASES,
    )
    @pytest.mark.parametrize(
        "key_fields",
        ({}, {"idempotency_key": "valid-read-key-0001"}),
        ids=("omitted-grace", "valid-supplied"),
    )
    def test_omitted_or_valid_key_reaches_each_read_handler(
        self,
        tool_name,
        url,
        body,
        core_target,
        auth_required,
        key_fields,
    ):
        from src.routes.api_v1 import validate_standard_read_idempotency_key as real_validator

        headers = {"Authorization": "Bearer test-token"} if auth_required else {}
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY),
            patch(core_target) as mock_core,
            patch(
                "src.routes.api_v1.validate_standard_read_idempotency_key",
                wraps=real_validator,
            ) as validate_key,
        ):
            mock_core.return_value = {}
            response = client.post(url, json={**body, **key_fields}, headers=headers)

        assert response.status_code == 200, response.text
        if key_fields:
            validate_key.assert_called_once_with(tool_name, key_fields)
        else:
            validate_key.assert_not_called()

    @pytest.mark.parametrize(
        ("tool_name", "url", "body", "core_target", "auth_required"),
        _STANDARD_READ_REST_CASES,
    )
    @pytest.mark.parametrize(
        ("idempotency_key", "message_substr"),
        ((None, "must be a string"), ("short", "too short")),
        ids=("explicit-null", "malformed"),
    )
    def test_supplied_invalid_key_rejects_before_each_read_handler(
        self,
        tool_name,
        url,
        body,
        core_target,
        auth_required,
        idempotency_key,
        message_substr,
    ):
        from src.routes.api_v1 import validate_standard_read_idempotency_key as real_validator

        headers = {"Authorization": "Bearer test-token"} if auth_required else {}
        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY),
            patch(core_target) as mock_core,
            patch(
                "src.routes.api_v1.validate_standard_read_idempotency_key",
                wraps=real_validator,
            ) as validate_key,
        ):
            response = client.post(
                url,
                json={**body, "idempotency_key": idempotency_key},
                headers=headers,
            )

        assert response.status_code == 400
        assert_envelope_shape(
            response.json(),
            "VALIDATION_ERROR",
            recovery="correctable",
            message_substr=message_substr,
        )
        validate_key.assert_called_once_with(tool_name, {"idempotency_key": idempotency_key})
        mock_core.assert_not_called()

    def test_authentication_precedes_malformed_read_key(self):
        with patch("src.core.tools.accounts.list_accounts_raw") as mock_core:
            response = client.post(
                "/api/v1/accounts",
                json={"idempotency_key": None},
            )

        assert response.status_code == 401
        assert_envelope_shape(response.json(), "AUTH_REQUIRED", recovery="correctable")
        mock_core.assert_not_called()

    def test_version_precedes_malformed_read_key(self):
        with patch("src.core.tools.products._get_products_impl") as mock_core:
            response = client.post(
                "/api/v1/products",
                json={"brief": "ads", "adcp_version": "4.0", "idempotency_key": None},
            )

        assert response.status_code == 400
        assert_envelope_shape(response.json(), "VERSION_UNSUPPORTED", recovery="correctable")
        mock_core.assert_not_called()

    @patch("src.core.tools.properties.list_authorized_properties_raw")
    def test_local_read_tolerates_and_excludes_envelope_key(self, mock_core):
        """The inherited field does not add standard validation to a local tool."""
        mock_core.return_value = {}

        response = client.post(
            "/api/v1/authorized-properties",
            json={"idempotency_key": None},
        )

        assert response.status_code == 200, response.text
        mock_core.assert_called_once_with(req=None, identity=None)


# ---------------------------------------------------------------------------
# Auth-required endpoints
# ---------------------------------------------------------------------------


class TestCreateMediaBuyEndpoint:
    """Verify POST /api/v1/media-buys endpoint."""

    def test_requires_auth(self):
        """create_media_buy requires authentication."""
        response = client.post(
            "/api/v1/media-buys",
            json={"packages": []},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Runtime scalar-forwarding oracles (#1417)
#
# The body-completeness guard proves each REST scalar is DECLARED on the *_raw
# wrapper signature; it does NOT prove the route actually forwards the request
# value. These TestClient tests patch the *_raw wrapper and assert the sentinel
# value the buyer sent reaches the wrapper — one test per non-echoed scalar.
# ---------------------------------------------------------------------------

# field -> (wire value, value the route must forward to the raw wrapper).
# Object params are coerced to SDK types at the route (#1417), so the
# forwarded value is the typed model, not the wire dict; ext stays a raw dict.
_CREATE_WEBHOOK_WIRE = {
    "url": "https://example.com/hook",
    "authentication": {"schemes": ["Bearer"], "credentials": "e9kw-credential-value-of-32-chars"},
    "reporting_frequency": "daily",
}
_CREATE_PNC_WIRE = {"url": "https://example.com/push"}
_CREATE_CONTEXT_WIRE = {"conversation_id": "conv-e9kw"}
_CREATE_FORWARDED_SCALARS = {
    "reporting_webhook": (_CREATE_WEBHOOK_WIRE, ReportingWebhook.model_validate(_CREATE_WEBHOOK_WIRE)),
    "push_notification_config": (_CREATE_PNC_WIRE, PushNotificationConfig.model_validate(_CREATE_PNC_WIRE)),
    "context": (_CREATE_CONTEXT_WIRE, ContextObject.model_validate(_CREATE_CONTEXT_WIRE)),
    "ext": ({"e9kw_marker": "create-value"}, {"e9kw_marker": "create-value"}),
}

_UPDATE_FORWARDED_SCALARS = {
    "pacing": "even",
    "daily_budget": 1234.5,
}


class TestCreateMediaBuyScalarForwarding:
    """Each non-echoed create scalar reaches create_media_buy_raw at runtime."""

    @pytest.mark.parametrize(
        ("field", "wire_value", "expected"),
        [(f, w, e) for f, (w, e) in _CREATE_FORWARDED_SCALARS.items()],
        ids=list(_CREATE_FORWARDED_SCALARS),
    )
    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    @patch("src.core.tools.media_buy_create.create_media_buy_raw", new_callable=AsyncMock)
    def test_scalar_forwards_to_raw(self, mock_raw, mock_resolve, field, wire_value, expected):
        mock_raw.return_value = MagicMock(model_dump=lambda **kw: {})
        body = {
            "packages": [],
            "start_time": "2026-01-01T00:00:00Z",
            "end_time": "2026-02-01T00:00:00Z",
            "idempotency_key": "create-scalar-forward-0001",
            field: wire_value,
        }
        response = client.post("/api/v1/media-buys", json=body, headers={"Authorization": "Bearer test-token"})

        assert response.status_code == 200, response.text
        assert mock_raw.call_args.kwargs[field] == expected, (
            f"REST create route did not forward {field!r} to create_media_buy_raw"
        )


class TestUpdateMediaBuyScalarForwarding:
    """Each non-echoed update scalar reaches update_media_buy_raw at runtime."""

    @pytest.mark.parametrize(
        ("field", "value"), list(_UPDATE_FORWARDED_SCALARS.items()), ids=list(_UPDATE_FORWARDED_SCALARS)
    )
    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    @patch("src.core.tools.media_buy_update.update_media_buy_raw")
    def test_scalar_forwards_to_raw(self, mock_raw, mock_resolve, field, value):
        mock_raw.return_value = MagicMock(model_dump=lambda **kw: {})
        body = {"idempotency_key": "update-scalar-forward-0001", field: value}
        response = client.put("/api/v1/media-buys/mb_e9kw", json=body, headers={"Authorization": "Bearer test-token"})

        assert response.status_code == 200, response.text
        assert mock_raw.call_args.kwargs[field] == value, (
            f"REST update route did not forward {field!r} to update_media_buy_raw"
        )


_REQUIRED_IDEMPOTENCY_REST_CASES = [
    pytest.param(
        "post",
        "/api/v1/media-buys",
        {"packages": []},
        "src.core.tools.media_buy_create._create_media_buy_impl",
        id="create-media-buy",
    ),
    pytest.param(
        "put",
        "/api/v1/media-buys/mb-rest-boundary",
        {"paused": True},
        "src.core.tools.media_buy_update._update_media_buy_impl",
        id="update-media-buy",
    ),
    pytest.param(
        "post",
        "/api/v1/creatives/sync",
        {"creatives": []},
        "src.core.tools.creatives.sync_wrappers.sync_creatives_raw",
        id="sync-creatives",
    ),
    pytest.param(
        "post",
        "/api/v1/accounts/sync",
        {"accounts": []},
        "src.core.tools.accounts.sync_accounts_raw",
        id="sync-accounts",
    ),
]


class TestRequiredIdempotencyRestBoundary:
    """REST advertises required keys while preserving MCP/A2A error parity."""

    @pytest.mark.parametrize(("method", "url", "body", "core_target"), _REQUIRED_IDEMPOTENCY_REST_CASES)
    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    def test_omitted_key_is_validation_error_before_core(self, mock_resolve, method, url, body, core_target):
        with patch(core_target) as mock_core:
            response = client.request(method, url, json=body, headers={"Authorization": "Bearer test-token"})

        assert response.status_code == 400
        assert_envelope_shape(
            response.json(),
            "VALIDATION_ERROR",
            recovery="correctable",
            message_substr="idempotency_key",
        )
        mock_core.assert_not_called()

    @pytest.mark.parametrize(("method", "url", "body", "core_target"), _REQUIRED_IDEMPOTENCY_REST_CASES)
    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    def test_numeric_key_is_validation_error_before_core(self, mock_resolve, method, url, body, core_target):
        with patch(core_target) as mock_core:
            response = client.request(
                method,
                url,
                json={**body, "idempotency_key": 123},
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 400
        assert_envelope_shape(
            response.json(),
            "VALIDATION_ERROR",
            recovery="correctable",
            message_substr="idempotency_key must be a string",
        )
        mock_core.assert_not_called()


class TestGetMediaBuyDeliveryEndpoint:
    """Verify POST /api/v1/media-buys/delivery endpoint."""

    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    @patch("src.core.transport_helpers.enrich_identity_with_account")
    @patch("src.core.tools.media_buy_delivery.get_media_buy_delivery_raw")
    def test_account_is_coerced_before_enriching_identity(self, mock_impl, mock_enrich, mock_resolve):
        enriched_identity = _MOCK_IDENTITY.model_copy(update={"account_id": "acct-1"})
        mock_enrich.return_value = enriched_identity
        mock_impl.return_value = MagicMock(model_dump=lambda **kw: {"media_buys": []})

        response = client.post(
            "/api/v1/media-buys/delivery",
            json={
                "media_buy_ids": ["mb1"],
                "account": {"brand": {"domain": "example.com"}, "operator": "op-1", "sandbox": False},
            },
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        expected_account = LibraryAccountReference.model_validate(
            {"brand": {"domain": "example.com"}, "operator": "op-1", "sandbox": False}
        )
        mock_enrich.assert_called_once_with(_MOCK_IDENTITY, expected_account)
        assert mock_impl.call_args.kwargs["identity"] is enriched_identity

    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    @patch("src.core.transport_helpers.enrich_identity_with_account")
    @patch("src.core.tools.media_buy_delivery.get_media_buy_delivery_raw")
    def test_malformed_account_returns_validation_error(self, mock_impl, mock_enrich, mock_resolve):
        response = client.post(
            "/api/v1/media-buys/delivery",
            json={"media_buy_ids": ["mb1"], "account": {}},
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 400
        assert_envelope_shape(response.json(), "VALIDATION_ERROR", recovery="correctable")
        mock_enrich.assert_not_called()
        mock_impl.assert_not_called()
