"""Tests for the FreeWheel HTTP transport.

Covers bearer auth, content-type negotiation (v3 XML vs v4 JSON), and
status-code -> exception mapping. Uses an injected mock session so no
network calls happen.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from src.adapters.freewheel._transport import (
    FreeWheelAuthError,
    FreeWheelForbiddenError,
    FreeWheelNotFoundError,
    FreeWheelServerError,
    FreeWheelTransport,
    FreeWheelValidationError,
)
from tests.helpers.adapter_test_helpers import stub_http_response as _stub_response


class TestBearerAuth:
    def test_authorization_header_set(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b"{}", text="{}")
        FreeWheelTransport(api_token="tok-abc", session=session).get_json("/x")

        headers = session.request.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer tok-abc"

    def test_no_credentials_rejected(self):
        with pytest.raises(ValueError, match="requires one of"):
            FreeWheelTransport()

    def test_username_without_password_rejected(self):
        with pytest.raises(ValueError):
            FreeWheelTransport(username="u")

    def test_password_without_username_rejected(self):
        with pytest.raises(ValueError):
            FreeWheelTransport(password="p")


class TestPasswordGrant:
    """OAuth2 password grant: mint via /auth/token, cache, refresh on 401."""

    def _mint_response(self, token: str = "minted-tok", expires_in: int = 604800) -> MagicMock:
        mock = MagicMock()
        mock.status_code = 200
        mock.ok = True
        mock.content = b'{"access_token": "...", "expires_in": ...}'
        mock.text = f'{{"access_token": "{token}", "expires_in": {expires_in}}}'
        mock.json.return_value = {"access_token": token, "expires_in": expires_in}
        return mock

    def test_first_call_mints_token_via_auth_token_endpoint(self):
        session = MagicMock()
        session.post.return_value = self._mint_response("minted-1")
        session.request.return_value = _stub_response(200, content=b"{}", text="{}")

        FreeWheelTransport(username="user@example.com", password="hunter2", session=session).get_json("/x")

        # /auth/token was POSTed with password-grant body. URL is positional;
        # data is a kwarg in our transport.
        post_call = session.post.call_args
        assert post_call.args[0].endswith("/auth/token")
        assert post_call.kwargs["data"] == {
            "grant_type": "password",
            "username": "user@example.com",
            "password": "hunter2",
        }
        # The subsequent GET used the minted token as the bearer
        assert session.request.call_args.kwargs["headers"]["Authorization"] == "Bearer minted-1"

    def test_token_is_cached_across_calls(self):
        session = MagicMock()
        session.post.return_value = self._mint_response("cached-tok")
        session.request.return_value = _stub_response(200, content=b"{}", text="{}")

        transport = FreeWheelTransport(username="u", password="p", session=session)
        transport.get_json("/a")
        transport.get_json("/b")
        transport.get_json("/c")

        # Single mint, multiple requests
        assert session.post.call_count == 1
        assert session.request.call_count == 3

    def test_401_triggers_refresh_and_retry(self):
        session = MagicMock()
        session.post.return_value = self._mint_response("fresh-tok")
        # First request 401s, second (after refresh) succeeds
        session.request.side_effect = [
            _stub_response(401, content=b"stale", text="stale"),
            _stub_response(200, content=b"{}", text="{}"),
        ]

        FreeWheelTransport(username="u", password="p", session=session).get_json("/x")

        # Two mints (initial + refresh after 401), two requests (original + retry)
        assert session.post.call_count == 2
        assert session.request.call_count == 2

    def test_mint_failure_raises_auth_error(self, caplog):
        session = MagicMock()
        bad_resp = MagicMock()
        bad_resp.status_code = 401
        bad_resp.ok = False
        bad_resp.content = b'{"error":"invalid_grant"}'
        bad_resp.text = '{"error":"invalid_grant"}'
        session.post.return_value = bad_resp

        transport = FreeWheelTransport(username="u", password="wrong", session=session)
        with caplog.at_level(logging.WARNING, logger="src.adapters.freewheel._transport"):
            with pytest.raises(FreeWheelAuthError, match="/auth/token rejected"):
                transport.get_json("/x")
        assert "phase=auth_token status=401" in caplog.text

    def test_api_token_does_not_trigger_mint(self):
        """When api_token is provided, no /auth/token call should ever happen,
        even on 401 — the caller is expected to manage rotation."""
        session = MagicMock()
        session.request.return_value = _stub_response(401, content=b"stale", text="stale")
        transport = FreeWheelTransport(api_token="static-tok", session=session)

        with pytest.raises(FreeWheelAuthError):
            transport.get_json("/x")
        assert session.post.call_count == 0
        # Only one attempt — no refresh+retry on static-token mode
        assert session.request.call_count == 1


class TestClientCredentialsGrant:
    """OAuth2 client_credentials grant (FreeWheel API-Access): mint from a
    separate token-service host with Basic auth, cache, refresh on 401."""

    TOKEN_URL = "https://token.apiaccess.freewheel.tv/oauth2/token"
    SANDBOX_BASE = "https://api.sandbox.freewheel.tv"

    def _mint_response(self, token: str = "cc-tok", expires_in: int = 3599) -> MagicMock:
        mock = MagicMock()
        mock.status_code = 200
        mock.ok = True
        mock.content = b'{"access_token": "...", "expires_in": ...}'
        mock.text = f'{{"access_token": "{token}", "expires_in": {expires_in}}}'
        mock.json.return_value = {"access_token": token, "expires_in": expires_in}
        return mock

    def test_first_call_mints_via_token_service_with_basic_auth(self):
        import base64

        session = MagicMock()
        session.post.return_value = self._mint_response("cc-1")
        session.request.return_value = _stub_response(200, content=b"{}", text="{}")

        FreeWheelTransport(
            client_id="cid",
            client_secret="csecret",
            token_url=self.TOKEN_URL,
            base_url=self.SANDBOX_BASE,
            session=session,
        ).get_json("/services/v4/sites")

        # Mint POSTs grant_type=client_credentials with HTTP Basic to the token service.
        post_call = session.post.call_args
        assert post_call.args[0] == self.TOKEN_URL
        assert post_call.kwargs["data"] == {"grant_type": "client_credentials"}
        expected_basic = base64.b64encode(b"cid:csecret").decode()
        assert post_call.kwargs["headers"]["Authorization"] == f"Basic {expected_basic}"
        # The data request used the minted token as the bearer.
        assert session.request.call_args.kwargs["headers"]["Authorization"] == "Bearer cc-1"

    def test_token_service_host_is_decoupled_from_data_host(self):
        session = MagicMock()
        session.post.return_value = self._mint_response()
        session.request.return_value = _stub_response(200, content=b"{}", text="{}")

        FreeWheelTransport(
            client_id="cid",
            client_secret="csecret",
            token_url=self.TOKEN_URL,
            base_url=self.SANDBOX_BASE,
            session=session,
        ).get_json("/services/v4/sites")

        # Token mint hits token.apiaccess.*; data hits api.sandbox.* — never conflated.
        assert session.post.call_args.args[0] == self.TOKEN_URL
        data_url = session.request.call_args.kwargs["url"]
        assert data_url.startswith(self.SANDBOX_BASE)
        assert "token.apiaccess" not in data_url

    def test_token_is_cached_across_calls(self):
        session = MagicMock()
        session.post.return_value = self._mint_response("cached-cc")
        session.request.return_value = _stub_response(200, content=b"{}", text="{}")

        transport = FreeWheelTransport(client_id="c", client_secret="s", session=session)
        transport.get_json("/a")
        transport.get_json("/b")

        assert session.post.call_count == 1
        assert session.request.call_count == 2

    def test_401_triggers_refresh_and_retry(self):
        session = MagicMock()
        session.post.return_value = self._mint_response("fresh-cc")
        session.request.side_effect = [
            _stub_response(401, content=b"stale", text="stale"),
            _stub_response(200, content=b"{}", text="{}"),
        ]

        FreeWheelTransport(client_id="c", client_secret="s", session=session).get_json("/x")

        # Re-mint + retry, because client_credentials is a mint mode (has_mint=True).
        assert session.post.call_count == 2
        assert session.request.call_count == 2

    def test_mint_failure_raises_auth_error(self, caplog):
        session = MagicMock()
        bad = MagicMock()
        bad.status_code = 401
        bad.ok = False
        bad.content = b'{"error":"invalid_client"}'
        bad.text = '{"error":"invalid_client"}'
        session.post.return_value = bad

        transport = FreeWheelTransport(client_id="c", client_secret="wrong", session=session)
        with caplog.at_level(logging.WARNING, logger="src.adapters.freewheel._transport"):
            with pytest.raises(FreeWheelAuthError):
                transport.get_json("/x")
        assert "phase=client_credentials status=401" in caplog.text


class TestContentTypeNegotiation:
    def test_v3_path_sends_accept_xml(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, text="<root/>", content=b"<root/>")
        FreeWheelTransport(api_token="t", session=session).get_xml("/services/v3/advertisers")

        headers = session.request.call_args.kwargs["headers"]
        assert headers["accept"] == "application/xml"

    def test_v4_path_sends_accept_json(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b"{}", text="{}")
        FreeWheelTransport(api_token="t", session=session).get_json("/services/v4/sites")

        headers = session.request.call_args.kwargs["headers"]
        assert headers["accept"] == "application/json"

    def test_post_xml_sets_content_type(self):
        session = MagicMock()
        session.request.return_value = _stub_response(
            200, text="<campaign><id>1</id></campaign>", content=b"<campaign><id>1</id></campaign>"
        )
        FreeWheelTransport(api_token="t", session=session).post_xml(
            "/services/v3/campaign", "<campaign><name>x</name></campaign>"
        )

        headers = session.request.call_args.kwargs["headers"]
        assert headers["Content-Type"] == "application/xml"
        assert headers["accept"] == "application/xml"

    def test_put_xml_uses_put_method(self):
        session = MagicMock()
        session.request.return_value = _stub_response(
            200, text="<campaign><id>1</id></campaign>", content=b"<campaign><id>1</id></campaign>"
        )
        FreeWheelTransport(api_token="t", session=session).put_xml(
            "/services/v3/campaign/1", "<campaign><description>x</description></campaign>"
        )

        call = session.request.call_args.kwargs
        assert call["method"] == "PUT"
        assert call["headers"]["Content-Type"] == "application/xml"


class TestStatusMapping:
    @pytest.mark.parametrize(
        "status,exc",
        [
            (401, FreeWheelAuthError),
            (403, FreeWheelForbiddenError),
            (404, FreeWheelNotFoundError),
            (400, FreeWheelValidationError),
            (422, FreeWheelValidationError),
            (500, FreeWheelServerError),
            (503, FreeWheelServerError),
        ],
    )
    def test_status_maps_to_exception(self, status, exc):
        session = MagicMock()
        session.request.return_value = _stub_response(status, text="upstream error", content=b"upstream error")
        transport = FreeWheelTransport(api_token="t", session=session)

        with pytest.raises(exc) as excinfo:
            transport.get_json("/services/v4/sites")
        assert excinfo.value.status_code == status
        assert excinfo.value.body == "upstream error"

    def test_status_failure_is_logged(self, caplog):
        session = MagicMock()
        session.request.return_value = _stub_response(403, text="missing role", content=b"missing role")
        transport = FreeWheelTransport(api_token="t", session=session)

        with caplog.at_level(logging.WARNING, logger="src.adapters.freewheel._transport"):
            with pytest.raises(FreeWheelForbiddenError):
                transport.get_json("/services/v4/sites")

        assert "FreeWheel API request failed" in caplog.text
        assert "path=/services/v4/sites status=403" in caplog.text
        assert "missing role" in caplog.text

    def test_2xx_does_not_raise(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b'{"x":1}', text='{"x":1}')
        session.request.return_value.json.return_value = {"x": 1}
        result = FreeWheelTransport(api_token="t", session=session).get_json("/services/v4/sites")
        assert result == {"x": 1}


class TestQueryParams:
    def test_query_string_built_from_kwargs(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b"{}", text="{}")
        FreeWheelTransport(api_token="t", session=session).get_json("/services/v4/sites", page=2, per_page=50)

        url = session.request.call_args.kwargs["url"]
        # Order of params can vary; check both are present.
        assert "page=2" in url
        assert "per_page=50" in url

    def test_no_query_params_no_query_string(self):
        session = MagicMock()
        session.request.return_value = _stub_response(200, content=b"{}", text="{}")
        FreeWheelTransport(api_token="t", session=session).get_json("/services/v4/sites")

        url = session.request.call_args.kwargs["url"]
        assert "?" not in url
