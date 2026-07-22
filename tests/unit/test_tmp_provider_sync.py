"""Unit tests for TMP Provider package sync service.

Covers:
- _build_package_payload: spec-compliant AvailablePackage payload (seller_agent object)
- sync_packages_for_media_buy: fan-out logic, error isolation, logging
- _resolve_seller_agent_url: env override, tenant virtual_host, None fallback
- _post_packages_sync: auth header selection (bearer only), SSRF guard, 5xx raises

beads: salesagent-tmp-sync
"""

from __future__ import annotations

from unittest import mock
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy.orm.exc import DetachedInstanceError

from src.services.tmp_provider_sync import (
    _build_package_payload,
    _is_local_host,
    _post_packages_sync,
    _resolve_seller_agent_url,
    sync_packages_for_media_buy,
)
from tests.unit._tmp_helpers import _make_sync_uow, _make_tenant_config_uow

# ---------------------------------------------------------------------------
# _build_package_payload tests
# ---------------------------------------------------------------------------


class TestBuildPackagePayload:
    """_build_package_payload emits a spec-compliant AvailablePackage payload.

    Authority: dist/schemas/3.1.0/tmp/available-package.json (AdCP 3.1.0-beta.3).
    The schema has ``additionalProperties: false`` and requires exactly:
    ``package_id``, ``media_buy_id``, ``seller_agent``.
    Optional allowed fields: ``format_ids``, ``catalogs``.
    """

    def test_emits_required_fields(self):
        """Payload contains the three required fields: package_id, media_buy_id, seller_agent."""
        pkg = MagicMock()
        pkg.package_id = "pkg-001"
        pkg.package_config = {}

        result = _build_package_payload("mb-100", pkg, "https://agent.example.com/mcp")

        assert result["package_id"] == "pkg-001"
        assert result["media_buy_id"] == "mb-100"
        assert result["seller_agent"] == {"agent_url": "https://agent.example.com/mcp"}

    def test_seller_agent_is_structured_object(self):
        """seller_agent is a dict with agent_url, not a flat string.

        Per dist/schemas/3.1.0/core/seller-agent-ref.json, seller_agent MUST be
        an object with agent_url — not the legacy flat si_agent_endpoint string.
        """
        pkg = MagicMock()
        pkg.package_id = "pkg-002"
        pkg.package_config = {}

        result = _build_package_payload("mb-200", pkg, "https://agent.example.com/mcp")

        assert isinstance(result["seller_agent"], dict)
        assert "agent_url" in result["seller_agent"]
        assert result["seller_agent"]["agent_url"] == "https://agent.example.com/mcp"

    def test_no_additional_properties(self):
        """Payload contains only schema-allowed keys (additionalProperties: false).

        The schema allows: package_id, media_buy_id, seller_agent, format_ids, catalogs.
        Keys like offering_id, brand, keywords, si_agent_endpoint etc. are forbidden.
        """
        pkg = MagicMock()
        pkg.package_id = "pkg-003"
        pkg.package_config = {
            "product_id": "prod-42",
            "brand": "Acme Corp",
            "keywords": ["shoes"],
        }

        result = _build_package_payload("mb-300", pkg, "https://agent.example.com/mcp")

        allowed_keys = {"package_id", "media_buy_id", "seller_agent", "format_ids", "catalogs"}
        extra_keys = set(result.keys()) - allowed_keys
        assert not extra_keys, f"Payload contains schema-forbidden keys: {extra_keys}"

    def test_handles_none_package_config(self):
        """package_config=None doesn't crash."""
        pkg = MagicMock()
        pkg.package_id = "pkg-004"
        pkg.package_config = None

        result = _build_package_payload("mb-400", pkg, "https://agent.example.com/mcp")

        assert result["package_id"] == "pkg-004"
        assert result["media_buy_id"] == "mb-400"
        assert result["seller_agent"] == {"agent_url": "https://agent.example.com/mcp"}


# ---------------------------------------------------------------------------
# sync_packages_for_media_buy — no valid seller_agent URL
# ---------------------------------------------------------------------------


class TestSyncSkipsWhenNoSellerAgentUrl:
    """sync_packages_for_media_buy skips sync when _resolve_seller_agent_url returns None.

    Per dist/schemas/3.1.0/core/seller-agent-ref.json, agent_url MUST use
    https://. When no valid https URL is available (no ADCP_AGENT_URL, no
    public virtual_host), the function must skip rather than emit a
    spec-invalid binding.
    """

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value=None)
    def test_skips_sync_when_seller_agent_url_is_none(self, mock_resolve, mock_post):
        """No HTTP calls when _resolve_seller_agent_url returns None."""
        sync_packages_for_media_buy("tenant-1", "mb-1")

        mock_post.assert_not_called()

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value=None)
    def test_logs_warning_when_seller_agent_url_is_none(self, mock_resolve, mock_post):
        """A warning is logged when sync is skipped due to missing seller_agent URL."""
        import logging

        with patch.object(logging.getLogger("src.services.tmp_provider_sync"), "warning") as mock_warn:
            sync_packages_for_media_buy("tenant-1", "mb-1")

        assert mock_warn.called
        # The warning message must mention the media_buy_id and tenant
        warning_args = " ".join(str(a) for a in mock_warn.call_args[0])
        assert "mb-1" in warning_args or "tenant-1" in warning_args


# ---------------------------------------------------------------------------
# sync_packages_for_media_buy session-closed invariant
# ---------------------------------------------------------------------------


class TestSellerAgentUrlResolvedBeforeMediaBuyUoW:
    """_resolve_seller_agent_url runs BEFORE MediaBuyUoW opens.

    Regression test for the nested-UoW bug: _resolve_seller_agent_url() opens
    its own TenantConfigUoW. get_db_session() is a scoped session, so calling
    it from inside an already-open MediaBuyUoW block means the inner UoW's
    __exit__ closes/removes the session the outer block is still using.
    """

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value="http://agent/mcp")
    def test_resolve_seller_agent_url_called_before_media_buy_uow_opens(self, mock_resolve, mock_post):
        """_resolve_seller_agent_url() is called before MediaBuyUoW.__enter__()."""
        call_order: list[str] = []

        mock_resolve.side_effect = lambda *_a, **_kw: (
            call_order.append("resolve_seller_agent_url") or "http://agent/mcp"
        )

        mock_mb_cls, _mock_mb_uow, mock_tp_cls, _mock_tp_uow = _make_sync_uow(packages=[])
        mock_mb_cls.return_value.__enter__ = MagicMock(
            side_effect=lambda: (
                call_order.append("media_buy_uow_entered")
                or MagicMock(media_buys=MagicMock(get_packages=MagicMock(return_value=[])))
            )
        )

        with (
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
        ):
            sync_packages_for_media_buy("tenant-1", "mb-1")

        assert call_order == ["resolve_seller_agent_url", "media_buy_uow_entered"]


class TestSyncSessionClosedBeforeHTTP:
    """sync_packages_for_media_buy closes the DB session before making HTTP calls."""

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value="http://agent/mcp")
    def test_session_closed_before_http_calls(self, mock_resolve, mock_post):
        """The TMPProviderUoW session is closed before _post_packages_sync is called."""
        call_order: list[str] = []

        pkg = MagicMock()
        pkg.package_id = "pkg-1"
        pkg.package_config = {"product_id": "prod-1"}

        provider = MagicMock()
        provider.name = "Provider A"
        provider.endpoint = "http://provider-a:3000"
        provider.auth_credentials = None

        mock_mb_cls, mock_mb_uow, mock_tp_cls, mock_tp_uow = _make_sync_uow(packages=[pkg], providers=[provider])
        # Override __exit__ to track session-close order
        mock_mb_cls.return_value.__exit__ = MagicMock(side_effect=lambda *_: call_order.append("mb_session_closed"))
        mock_tp_cls.return_value.__exit__ = MagicMock(side_effect=lambda *_: call_order.append("tp_session_closed"))

        # Track when HTTP call happens
        mock_post.side_effect = lambda *_: call_order.append("http_called")

        with (
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
        ):
            sync_packages_for_media_buy("tenant-1", "mb-1")

        # Both sessions must be closed before the HTTP fan-out
        assert "tp_session_closed" in call_order
        assert "http_called" in call_order
        assert call_order.index("tp_session_closed") < call_order.index("http_called")


class TestProviderMaterializedBeforeSessionCloses:
    """Provider ORM attributes must be read INSIDE the TMPProviderUoW block.

    Regression test for the DetachedInstanceError class of bug: reading
    provider.endpoint / provider.auth_credentials / provider.name AFTER the
    UoW block has exited hits an expired/detached ORM instance under real
    SQLAlchemy (default expire_on_commit=True). A MagicMock provider doesn't
    reproduce this because MagicMock attribute access never raises — so this
    test builds a fake object whose attributes raise DetachedInstanceError
    once the UoW has closed, proving the production code reads them before
    that point.
    """

    class _DetachAfterCloseProvider:
        """Object whose attributes raise DetachedInstanceError once "closed"."""

        def __init__(self, name: str, endpoint: str, auth_credentials: str | None, closed_flag: list[bool]):
            self._name = name
            self._endpoint = endpoint
            self._auth_credentials = auth_credentials
            self._closed_flag = closed_flag

        def _check(self):
            if self._closed_flag[0]:
                raise DetachedInstanceError("Instance is not bound to a Session; attribute access failed")

        @property
        def name(self):
            self._check()
            return self._name

        @property
        def endpoint(self):
            self._check()
            return self._endpoint

        @property
        def auth_credentials(self):
            self._check()
            return self._auth_credentials

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value="http://agent/mcp")
    def test_provider_attributes_read_before_uow_exits(self, mock_resolve, mock_post):
        """Provider fields are captured inside the `with` block, not after."""
        pkg = MagicMock()
        pkg.package_id = "pkg-1"
        pkg.package_config = {"product_id": "prod-1"}

        closed_flag = [False]
        provider = self._DetachAfterCloseProvider("Provider A", "http://provider-a:3000", "secret", closed_flag)

        mock_mb_cls, _mock_mb_uow, mock_tp_cls, mock_tp_uow = _make_sync_uow(packages=[pkg], providers=[provider])

        def _mark_closed(*_args):
            closed_flag[0] = True
            return False

        mock_tp_cls.return_value.__exit__ = MagicMock(side_effect=_mark_closed)

        with (
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
        ):
            # Would raise DetachedInstanceError if provider.endpoint/.auth_credentials
            # were read after the TMPProviderUoW block closed.
            sync_packages_for_media_buy("tenant-1", "mb-1")

        mock_post.assert_called_once_with(
            "http://provider-a:3000",
            mock.ANY,  # payload correctness pinned by TestBuildPackagePayload
            "secret",
        )


# ---------------------------------------------------------------------------
# sync_packages_for_media_buy fan-out tests
# ---------------------------------------------------------------------------


class TestSyncPackagesFanOut:
    """sync_packages_for_media_buy loads packages and fans out to providers."""

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value="http://agent/mcp")
    def test_fans_out_to_all_providers(self, mock_resolve, mock_post):
        """Packages are POSTed to every syncable provider."""
        pkg = MagicMock()
        pkg.package_id = "pkg-1"
        pkg.package_config = {"product_id": "prod-1", "name": "Test"}

        provider1 = MagicMock()
        provider1.name = "Provider A"
        provider1.endpoint = "http://provider-a:3000"
        provider1.auth_credentials = None
        provider2 = MagicMock()
        provider2.name = "Provider B"
        provider2.endpoint = "http://provider-b:3000"
        provider2.auth_credentials = None

        mock_mb_cls, _mb_uow, mock_tp_cls, _tp_uow = _make_sync_uow(packages=[pkg], providers=[provider1, provider2])
        with (
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
        ):
            sync_packages_for_media_buy("tenant-1", "mb-1")

        # Assert call count and that each provider endpoint + auth were used.
        # We deliberately do NOT assert the payload contents here — that would
        # re-invoke _build_package_payload on the same inputs and thread any
        # wiring bug through both sides of the assertion, making it invisible.
        # Payload correctness is covered by TestBuildPackagePayload unit tests.
        assert mock_post.call_count == 2
        called_endpoints = {call.args[0] for call in mock_post.call_args_list}
        called_auths = {call.args[2] for call in mock_post.call_args_list}
        assert called_endpoints == {"http://provider-a:3000", "http://provider-b:3000"}
        assert called_auths == {""}  # both providers have no auth_credentials

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value="http://agent/mcp")
    def test_skips_when_no_packages(self, mock_resolve, mock_post):
        """No HTTP calls when media buy has no packages."""
        mock_mb_cls, _mb_uow, mock_tp_cls, _tp_uow = _make_sync_uow(packages=[])
        with (
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
        ):
            sync_packages_for_media_buy("tenant-1", "mb-1")

        mock_post.assert_not_called()

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value="http://agent/mcp")
    def test_skips_when_no_providers(self, mock_resolve, mock_post):
        """No HTTP calls when tenant has no syncable providers."""
        pkg = MagicMock()
        pkg.package_id = "pkg-1"
        pkg.package_config = {}

        mock_mb_cls, _mb_uow, mock_tp_cls, _tp_uow = _make_sync_uow(packages=[pkg], providers=[])
        with (
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
        ):
            sync_packages_for_media_buy("tenant-1", "mb-1")

        mock_post.assert_not_called()

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value="http://agent/mcp")
    def test_one_provider_failure_does_not_block_others(self, mock_resolve, mock_post):
        """If one provider fails, the others still get called."""
        pkg = MagicMock()
        pkg.package_id = "pkg-1"
        pkg.package_config = {}

        provider1 = MagicMock()
        provider1.name = "Failing Provider"
        provider1.endpoint = "http://fail:3000"
        provider2 = MagicMock()
        provider2.name = "Working Provider"
        provider2.endpoint = "http://ok:3000"

        mock_mb_cls, _mb_uow, mock_tp_cls, _tp_uow = _make_sync_uow(packages=[pkg], providers=[provider1, provider2])
        # First call raises, second succeeds
        mock_post.side_effect = [httpx.ConnectError("refused"), None]

        with (
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
        ):
            # Should not raise — errors are logged and swallowed
            sync_packages_for_media_buy("tenant-1", "mb-1")

        assert mock_post.call_count == 2

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value="http://agent/mcp")
    def test_package_load_failure_returns_early(self, mock_resolve, mock_post):
        """If loading packages fails, no HTTP calls are made."""
        mock_mb_cls, _mb_uow, mock_tp_cls, _tp_uow = _make_sync_uow()
        mock_mb_cls.return_value.__enter__ = MagicMock(side_effect=RuntimeError("DB connection failed"))

        with (
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
        ):
            sync_packages_for_media_buy("tenant-1", "mb-1")

        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# _resolve_seller_agent_url tests
# ---------------------------------------------------------------------------


class TestResolveSellAgentUrl:
    """_resolve_seller_agent_url resolves the seller agent URL for package payloads."""

    def test_env_override_takes_precedence(self):
        """ADCP_AGENT_URL env var overrides tenant-based resolution."""
        with patch.dict("os.environ", {"ADCP_AGENT_URL": "https://custom.agent.com/mcp/"}):
            result = _resolve_seller_agent_url("any-tenant")

        assert result == "https://custom.agent.com/mcp"

    def test_uses_tenant_virtual_host(self):
        """Uses tenant.virtual_host when ADCP_AGENT_URL is not set."""
        import os

        tenant = MagicMock()
        tenant.virtual_host = "tenant.salesagent.example.com"
        tenant.subdomain = "tenant"
        mock_uow_cls = _make_tenant_config_uow(tenant)

        with patch("src.services.tmp_provider_sync.TenantConfigUoW", mock_uow_cls):
            with patch.dict("os.environ", {}, clear=False):
                os.environ.pop("ADCP_AGENT_URL", None)
                result = _resolve_seller_agent_url("test-tenant")

        assert result == "https://tenant.salesagent.example.com/mcp"

    def test_returns_none_when_no_valid_https_url(self):
        """Returns None when tenant has no public virtual_host and ADCP_AGENT_URL is unset.

        The spec requires agent_url to use https://. A local-only deployment
        cannot produce a valid https URL, so None is returned and the caller
        skips the sync rather than emitting a spec-invalid binding.
        """
        import os

        tenant = MagicMock()
        tenant.virtual_host = None
        tenant.subdomain = None
        mock_uow_cls = _make_tenant_config_uow(tenant)

        with patch("src.services.tmp_provider_sync.TenantConfigUoW", mock_uow_cls):
            with patch.dict("os.environ", {}, clear=False):
                os.environ.pop("ADCP_AGENT_URL", None)
                result = _resolve_seller_agent_url("test-tenant")

        assert result is None

    def test_uses_https_for_public_virtual_host(self):
        """A public (non-local) virtual_host resolves to https://."""
        import os

        tenant = MagicMock()
        tenant.virtual_host = "tenant.salesagent.example.com"
        tenant.subdomain = None
        mock_uow_cls = _make_tenant_config_uow(tenant)

        with patch("src.services.tmp_provider_sync.TenantConfigUoW", mock_uow_cls):
            with patch.dict("os.environ", {}, clear=False):
                os.environ.pop("ADCP_AGENT_URL", None)
                result = _resolve_seller_agent_url("test-tenant")

        assert result == "https://tenant.salesagent.example.com/mcp"

    def test_returns_none_for_localhost_virtual_host(self):
        """A localhost virtual_host returns None — cannot produce a valid https URL.

        Per dist/schemas/3.1.0/core/seller-agent-ref.json, agent_url MUST use
        https://. Local dev hosts cannot satisfy this requirement, so None is
        returned and the caller skips the sync.
        """
        import os

        tenant = MagicMock()
        tenant.virtual_host = "tenant.sales-agent.localhost:8001"
        tenant.subdomain = None
        mock_uow_cls = _make_tenant_config_uow(tenant)

        with patch("src.services.tmp_provider_sync.TenantConfigUoW", mock_uow_cls):
            with patch.dict("os.environ", {}, clear=False):
                os.environ.pop("ADCP_AGENT_URL", None)
                result = _resolve_seller_agent_url("test-tenant")

        assert result is None

    def test_does_not_misclassify_public_host_containing_localhost_substring(self):
        """A public host that merely CONTAINS 'localhost' as a substring must get https.

        Regression test for the substring-check bug: "localhost" not in host
        would incorrectly treat "my-localhost-mirror.example.com" as local.
        """
        import os

        tenant = MagicMock()
        tenant.virtual_host = "my-localhost-mirror.example.com"
        tenant.subdomain = None
        mock_uow_cls = _make_tenant_config_uow(tenant)

        with patch("src.services.tmp_provider_sync.TenantConfigUoW", mock_uow_cls):
            with patch.dict("os.environ", {}, clear=False):
                os.environ.pop("ADCP_AGENT_URL", None)
                result = _resolve_seller_agent_url("test-tenant")

        assert result == "https://my-localhost-mirror.example.com/mcp"


# ---------------------------------------------------------------------------
# _is_local_host tests
# ---------------------------------------------------------------------------


class TestIsLocalHost:
    """_is_local_host distinguishes real local dev hosts from public hosts

    that merely contain "localhost" as a substring.
    """

    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "localhost:8001",
            "tenant.localhost",
            "tenant.sales-agent.localhost:8001",
            "127.0.0.1",
            "127.0.0.1:8000",
        ],
    )
    def test_local_hosts_return_true(self, host):
        assert _is_local_host(host) is True

    @pytest.mark.parametrize(
        "host",
        [
            "tenant.salesagent.example.com",
            "my-localhost-mirror.example.com",
            "example.com",
            "localhost.evil.com",
        ],
    )
    def test_public_hosts_return_false(self, host):
        assert _is_local_host(host) is False


# ---------------------------------------------------------------------------
# _post_packages_sync auth header tests
# ---------------------------------------------------------------------------


class TestPostPackagesSyncAuth:
    """_post_packages_sync sends Bearer auth when credentials are provided."""

    def _make_mock_client(self, status_code: int = 200) -> tuple[MagicMock, MagicMock]:
        """Return (mock_client_cls, mock_client) with a response of the given status."""
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                f"Server error {status_code}",
                request=MagicMock(),
                response=MagicMock(status_code=status_code),
            )
            if status_code >= 400
            else None
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        return mock_client, mock_response

    def test_sends_bearer_token_when_auth_credentials_set(self):
        """When auth_credentials is non-empty, Authorization: Bearer header is sent."""
        mock_client, _ = self._make_mock_client(200)

        with patch("src.services.tmp_provider_sync.httpx.Client", return_value=mock_client):
            _post_packages_sync(
                "http://provider:3000",
                [{"package_id": "pkg-1"}],
                auth_credentials="secret-token",
            )

        mock_client.post.assert_called_once_with(
            "http://provider:3000/packages/sync",
            json=[{"package_id": "pkg-1"}],
            headers={"Authorization": "Bearer secret-token"},
        )

    def test_sends_no_auth_headers_when_no_credentials(self):
        """When auth_credentials is empty, no auth headers are sent."""
        mock_client, _ = self._make_mock_client(200)

        with patch("src.services.tmp_provider_sync.httpx.Client", return_value=mock_client):
            _post_packages_sync(
                "http://provider:3000",
                [{"package_id": "pkg-1"}],
                auth_credentials="",
            )

        mock_client.post.assert_called_once_with(
            "http://provider:3000/packages/sync",
            json=[{"package_id": "pkg-1"}],
            headers={},
        )

    def test_follow_redirects_false_prevents_ssrf(self):
        """follow_redirects=False is always passed to prevent SSRF via open-redirect."""
        mock_client, _ = self._make_mock_client(200)

        with patch("src.services.tmp_provider_sync.httpx.Client", return_value=mock_client) as mock_cls:
            _post_packages_sync("http://provider:3000", [{"package_id": "pkg-1"}])

        _, kwargs = mock_cls.call_args
        assert kwargs.get("follow_redirects") is False

    def test_5xx_response_raises_http_status_error(self):
        """A 5xx response from the TMP Provider raises httpx.HTTPStatusError.

        This ensures a silent success is impossible — the caller's except block
        will log the failure and continue to the next provider.
        """
        mock_client, _ = self._make_mock_client(500)

        with patch("src.services.tmp_provider_sync.httpx.Client", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                _post_packages_sync("http://provider:3000", [{"package_id": "pkg-1"}])

    def test_fan_out_uses_provider_auth_credentials(self):
        """sync_packages_for_media_buy passes provider.auth_credentials to _post_packages_sync."""
        pkg = MagicMock()
        pkg.package_id = "pkg-1"
        pkg.package_config = {"product_id": "prod-1"}

        provider = MagicMock()
        provider.name = "Credentialed Provider"
        provider.endpoint = "http://provider:3000"
        provider.auth_credentials = "provider-secret"

        mock_mb_cls, _mb_uow, mock_tp_cls, _tp_uow = _make_sync_uow(packages=[pkg], providers=[provider])
        with (
            patch("src.services.tmp_provider_sync._post_packages_sync") as mock_post,
            patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value="http://agent/mcp"),
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
        ):
            sync_packages_for_media_buy("tenant-1", "mb-1")

        mock_post.assert_called_once_with(
            "http://provider:3000",
            mock.ANY,  # payload correctness pinned by TestBuildPackagePayload
            "provider-secret",
        )
