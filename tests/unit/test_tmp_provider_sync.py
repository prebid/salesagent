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

import pytest
from adcp.types import AvailablePackage, SellerAgentReference
from sqlalchemy.orm.exc import DetachedInstanceError

from src.core.security.egress.attempts import OutboundDeliveryFailed
from src.core.security.outbound_http import OperatorEndpoint
from src.services.tmp_provider_sync import (
    AVAILABLE_PACKAGE_SCHEMA,
    _build_package_payload,
    _post_packages_sync,
    _resolve_seller_agent_url,
    sync_packages_for_media_buy,
)
from tests.helpers.pinned_schema import validate_against_pinned_schema
from tests.helpers.tmp_provider_http import make_delivery_failed, make_seam_result
from tests.unit._tmp_helpers import (
    make_create_result,
    make_mock_package,
    make_mock_provider,
    make_sync_uow,
    make_tenant_config_uow,
)

# The only shape ``_resolve_seller_agent_url`` can return besides ``None``: it
# rejects a non-https override and builds the virtual_host branch as
# ``https://{host}/mcp``, so a stub returning ``http://`` drove
# ``_build_package_payload`` with a URL production cannot emit — while the same
# file spent a docstring, an error branch and a SellerAgentReference on the
# https rule (#1197 review).
_SELLER_AGENT_URL = "https://agent.example.com/mcp"

# ---------------------------------------------------------------------------
# _build_package_payload tests
# ---------------------------------------------------------------------------


def _package_wire(package_id: str = "pkg-1") -> dict:
    """The JSON body ``_post_packages_sync`` must produce for :func:`_a_package`."""
    return _a_package(package_id).model_dump(mode="json", exclude_none=True)


def _a_package(package_id: str = "pkg-1") -> AvailablePackage:
    """A minimal valid ``AvailablePackage``, as the fan-out hands to the poster.

    ``_post_packages_sync`` takes models now, not dicts — the single
    ``model_dump`` happens inside it, at the transport edge (#1197 review).
    """
    return AvailablePackage(
        package_id=package_id,
        media_buy_id="mb-1",
        seller_agent=SellerAgentReference(agent_url=_SELLER_AGENT_URL),
    )


class TestBuildPackagePayload:
    """``_build_package_payload`` produces a schema-valid ``AvailablePackage``.

    Graded against the pinned file the module itself declares
    (:data:`src.services.tmp_provider_sync.AVAILABLE_PACKAGE_SCHEMA`) plus the SDK
    codegen, not against a hand-written ``allowed_keys`` set. A restated key set
    could not see a ``seller_agent`` that lost ``agent_url``, a wrong-typed
    ``package_id``, or a non-https ``agent_url`` — the one value constraint this
    module spends a docstring, an error branch and a skip path on. It also failed
    in the wrong direction: a spec bump adding an optional member that production
    correctly emits broke the exact-set assertion for a payload the schema accepts
    (#1197 review).
    """

    @staticmethod
    def _wire(package: AvailablePackage) -> dict:
        """The package as ``_post_packages_sync`` serializes it."""
        return package.model_dump(mode="json", exclude_none=True)

    def _built(self, package_id: str, media_buy_id: str, agent_url: str = _SELLER_AGENT_URL) -> dict:
        pkg = make_mock_package(package_id=package_id)
        built = _build_package_payload(media_buy_id, pkg, agent_url)
        # The builder returns the MODEL — that is what travels through the
        # fan-out, so `Any` never leaves this module (#1197 review).
        assert isinstance(built, AvailablePackage)
        wire = self._wire(built)
        validate_against_pinned_schema(AVAILABLE_PACKAGE_SCHEMA, wire)
        AvailablePackage.model_validate(wire)
        return wire

    def test_carries_the_identity_of_the_package_and_its_media_buy(self):
        """The two values only this function can get wrong."""
        wire = self._built("pkg-001", "mb-100")

        assert wire["package_id"] == "pkg-001"
        assert wire["media_buy_id"] == "mb-100"

    def test_seller_agent_is_the_structured_reference(self):
        """``seller_agent`` is ``{"agent_url": ...}``, not a flat string.

        Per ``adcp/_schemas/3.1/core/seller-agent-ref.json`` — the legacy flat
        ``si_agent_endpoint`` string is not this shape.
        """
        wire = self._built("pkg-002", "mb-200")

        assert wire["seller_agent"] == {"agent_url": _SELLER_AGENT_URL}

    def test_a_non_https_seller_agent_url_cannot_be_built(self):
        """The https rule is enforced by construction, which the key-set form could not see.

        ``seller-agent-ref.json`` requires https for ``agent_url``; callers resolve
        one before calling this (``_resolve_seller_agent_url``) and skip the sync
        when they cannot.
        """
        pkg = make_mock_package(package_id="pkg-005")
        with pytest.raises(ValueError, match="https"):
            _build_package_payload("mb-500", pkg, "http://agent.example.com/mcp")

    def test_package_config_contents_never_reach_the_wire(self):
        """The schema is closed, so nothing from package_config may leak into the body."""
        pkg = make_mock_package(
            package_id="pkg-003",
            package_config={"product_id": "prod-42", "brand": "Acme Corp", "keywords": ["shoes"]},
        )
        wire = self._wire(_build_package_payload("mb-300", pkg, _SELLER_AGENT_URL))

        validate_against_pinned_schema(AVAILABLE_PACKAGE_SCHEMA, wire)
        assert "brand" not in wire
        assert "keywords" not in wire

    def test_handles_none_package_config(self):
        """``package_config=None`` doesn't crash — the body never reads it."""
        pkg = make_mock_package(package_id="pkg-004", package_config=None)
        wire = self._wire(_build_package_payload("mb-400", pkg, _SELLER_AGENT_URL))

        validate_against_pinned_schema(AVAILABLE_PACKAGE_SCHEMA, wire)
        assert wire["package_id"] == "pkg-004"


# ---------------------------------------------------------------------------
# sync_packages_for_media_buy — no valid seller_agent URL
# ---------------------------------------------------------------------------


class TestSyncSkipsWhenNoSellerAgentUrl:
    """sync_packages_for_media_buy skips sync when _resolve_seller_agent_url returns None.

    Per adcp/_schemas/3.1/core/seller-agent-ref.json, agent_url MUST use
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

        # Pinned atomically with the real format string and BOTH args: the
        # previous `"mb-1" in args or "tenant-1" in args` passed on a warning
        # that had dropped the media_buy_id, which is the field an operator
        # needs to find the affected buy (#1197 review).
        mock_warn.assert_called_once_with(
            "[TMP sync] Skipping sync for media_buy=%s tenant=%s — no valid https seller_agent URL. "
            "Set ADCP_AGENT_URL to enable TMP sync.",
            "mb-1",
            "tenant-1",
        )


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
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value=_SELLER_AGENT_URL)
    def test_resolve_seller_agent_url_called_before_media_buy_uow_opens(self, mock_resolve, mock_post):
        """_resolve_seller_agent_url() is called before MediaBuyUoW.__enter__()."""
        call_order: list[str] = []

        mock_resolve.side_effect = lambda *_a, **_kw: call_order.append("resolve_seller_agent_url") or _SELLER_AGENT_URL

        mock_mb_cls, _mock_mb_uow, mock_tp_cls, _mock_tp_uow = make_sync_uow(packages=[])
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
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value=_SELLER_AGENT_URL)
    def test_session_closed_before_http_calls(self, mock_resolve, mock_post):
        """The TMPProviderUoW session is closed before _post_packages_sync is called."""
        call_order: list[str] = []

        pkg = make_mock_package(package_id="pkg-1", package_config={"product_id": "prod-1"})

        provider = MagicMock()
        provider.name = "Provider A"
        provider.endpoint = "https://provider-a:3000"
        provider.auth_credentials = None

        mock_mb_cls, mock_mb_uow, mock_tp_cls, mock_tp_uow = make_sync_uow(packages=[pkg], providers=[provider])
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

        @property
        def auth_type(self):
            self._check()
            return "bearer"

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value=_SELLER_AGENT_URL)
    def test_provider_attributes_read_before_uow_exits(self, mock_resolve, mock_post):
        """Provider fields are captured inside the `with` block, not after."""
        pkg = make_mock_package(package_id="pkg-1", package_config={"product_id": "prod-1"})

        closed_flag = [False]
        provider = self._DetachAfterCloseProvider("Provider A", "https://provider-a:3000", "secret", closed_flag)

        mock_mb_cls, _mock_mb_uow, mock_tp_cls, mock_tp_uow = make_sync_uow(packages=[pkg], providers=[provider])

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
            "https://provider-a:3000",
            mock.ANY,  # payload correctness pinned by TestBuildPackagePayload
            "secret",
            "bearer",  # the provider's scheme, passed through — not mock.ANY
        )


# ---------------------------------------------------------------------------
# sync_packages_for_media_buy fan-out tests
# ---------------------------------------------------------------------------


class TestSyncPackagesFanOut:
    """sync_packages_for_media_buy loads packages and fans out to providers."""

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value=_SELLER_AGENT_URL)
    def test_fans_out_to_all_providers(self, mock_resolve, mock_post):
        """Packages are POSTed to every syncable provider."""
        pkg = make_mock_package(package_id="pkg-1", package_config={"product_id": "prod-1", "name": "Test"})

        provider1 = MagicMock()
        provider1.name = "Provider A"
        provider1.endpoint = "https://provider-a:3000"
        provider1.auth_credentials = None
        provider2 = MagicMock()
        provider2.name = "Provider B"
        provider2.endpoint = "https://provider-b:3000"
        provider2.auth_credentials = None

        mock_mb_cls, _mb_uow, mock_tp_cls, _tp_uow = make_sync_uow(packages=[pkg], providers=[provider1, provider2])
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
        assert called_endpoints == {"https://provider-a:3000", "https://provider-b:3000"}
        assert called_auths == {""}  # both providers have no auth_credentials

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value=_SELLER_AGENT_URL)
    def test_skips_when_no_packages(self, mock_resolve, mock_post):
        """No HTTP calls when media buy has no packages."""
        mock_mb_cls, _mb_uow, mock_tp_cls, _tp_uow = make_sync_uow(packages=[])
        with (
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
        ):
            sync_packages_for_media_buy("tenant-1", "mb-1")

        mock_post.assert_not_called()

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value=_SELLER_AGENT_URL)
    def test_skips_when_no_providers(self, mock_resolve, mock_post):
        """No HTTP calls when tenant has no syncable providers."""
        pkg = make_mock_package(package_id="pkg-1", package_config={})

        mock_mb_cls, _mb_uow, mock_tp_cls, _tp_uow = make_sync_uow(packages=[pkg], providers=[])
        with (
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
        ):
            sync_packages_for_media_buy("tenant-1", "mb-1")

        mock_post.assert_not_called()

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value=_SELLER_AGENT_URL)
    def test_one_provider_failure_does_not_block_others(self, mock_resolve, mock_post):
        """If one provider fails, the others still get called."""
        pkg = make_mock_package(package_id="pkg-1", package_config={})

        provider1 = MagicMock()
        provider1.name = "Failing Provider"
        provider1.endpoint = "https://fail:3000"
        provider2 = MagicMock()
        provider2.name = "Working Provider"
        provider2.endpoint = "https://ok:3000"

        mock_mb_cls, _mb_uow, mock_tp_cls, _tp_uow = make_sync_uow(packages=[pkg], providers=[provider1, provider2])
        # First call raises, second succeeds
        mock_post.side_effect = [make_delivery_failed(None), None]

        with (
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
        ):
            # Should not raise — errors are logged and swallowed
            sync_packages_for_media_buy("tenant-1", "mb-1")

        assert mock_post.call_count == 2

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value=_SELLER_AGENT_URL)
    def test_package_load_failure_returns_early(self, mock_resolve, mock_post):
        """If loading packages fails, no HTTP calls are made."""
        mock_mb_cls, _mb_uow, mock_tp_cls, _tp_uow = make_sync_uow()
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


class TestOneUnreadableCredentialDoesNotSkipTheRest:
    """A provider whose stored credential cannot be decrypted is skipped alone.

    ``TMPProvider.auth_credentials`` decrypts on read and raises
    ``AdCPConfigurationError`` when the current key cannot open the ciphertext —
    a key-rotation state, not a corrupt database. The materialisation used to be
    a list comprehension over the whole provider set, so that single row aborted
    it, was swallowed by the surrounding ``except Exception``, and every OTHER
    provider for the tenant was skipped and logged as a repository failure
    (#1197 review).
    """

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value=_SELLER_AGENT_URL)
    def test_healthy_providers_still_receive_packages(self, mock_resolve, mock_post):
        from src.core.exceptions import AdCPConfigurationError

        pkg = make_mock_package(package_id="pkg-1")

        rotated = make_mock_provider(
            name="Rotated Provider",
            endpoint="http://rotated:3000",
            credential=AdCPConfigurationError("Failed to decrypt auth credentials for TMP provider p1"),
        )
        healthy = make_mock_provider(name="Healthy Provider", endpoint="http://healthy:3000", credential="tok")

        mock_mb_cls, _mb_uow, mock_tp_cls, _tp_uow = make_sync_uow(packages=[pkg], providers=[rotated, healthy])
        with (
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
        ):
            sync_packages_for_media_buy("tenant-1", "mb-1")

        assert mock_post.call_count == 1, "the readable provider must still be synced"
        assert mock_post.call_args_list[0].args[0] == "http://healthy:3000"
        assert mock_post.call_args_list[0].args[2] == "tok"

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value=_SELLER_AGENT_URL)
    def test_the_skipped_provider_is_named_in_the_log(self, mock_resolve, mock_post, caplog):
        """The operator must be able to tell WHICH registration to re-enter."""
        import logging

        from src.core.exceptions import AdCPConfigurationError

        pkg = make_mock_package(package_id="pkg-1")
        rotated = make_mock_provider(
            name="Rotated Provider",
            endpoint="http://rotated:3000",
            credential=AdCPConfigurationError("boom"),
        )

        mock_mb_cls, _mb_uow, mock_tp_cls, _tp_uow = make_sync_uow(packages=[pkg], providers=[rotated])
        with (
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
            caplog.at_level(logging.WARNING, logger="src.services.tmp_provider_sync"),
        ):
            sync_packages_for_media_buy("tenant-1", "mb-1")

        mock_post.assert_not_called()
        assert "Rotated Provider" in caplog.text
        # NOT the repository-failure line: blaming the load for one provider's
        # credential is exactly the misdiagnosis this fix removes.
        assert "Failed to load TMP providers" not in caplog.text


class TestSyncsForOneMediaBuyAreSerialized:
    """Two fires for the same media buy run in order, not as a race.

    "Every provider holds current package data" is only true if the LAST
    operation is the last to POST. ``fire_tmp_sync`` registers each sync in
    ``_active_syncs`` and each one joins its predecessor, so the ordering is a
    property of the code rather than of thread scheduling (#1197 review).
    """

    def test_second_fire_waits_for_the_first(self):
        import threading

        from src.core.schemas._base import CreateMediaBuyResult
        from src.services.tmp_provider_sync import fire_tmp_sync, join_active_syncs
        from tests.harness import make_identity

        order: list[str] = []
        first_may_finish = threading.Event()

        def _fake_sync(tenant_id: str, media_buy_id: str) -> None:
            order.append(f"start-{len(order)}")
            if len(order) == 1:
                # Hold the first sync open; if the second did not serialize
                # behind it, its "start" lands while this one is still blocked.
                first_may_finish.wait(timeout=10)
            order.append("end")

        def _result() -> CreateMediaBuyResult:
            return make_create_result("mb_serialize")

        identity = make_identity(tenant_id="tenant_1")
        with patch("src.services.tmp_provider_sync.sync_packages_for_media_buy", _fake_sync):
            fire_tmp_sync(_result(), identity)
            fire_tmp_sync(_result(), identity)
            first_may_finish.set()
            assert join_active_syncs(timeout=10) == []

        # start, end, start, end — never start, start.
        assert order == ["start-0", "end", "start-2", "end"], order


class TestResolveSellAgentUrl:
    """_resolve_seller_agent_url resolves the seller agent URL for package payloads."""

    def test_env_override_takes_precedence(self):
        """ADCP_AGENT_URL env var overrides tenant-based resolution."""
        with patch.dict("os.environ", {"ADCP_AGENT_URL": "https://custom.agent.com/mcp/"}):
            result = _resolve_seller_agent_url("any-tenant")

        assert result == "https://custom.agent.com/mcp"

    def test_non_https_env_override_is_rejected(self):
        """A non-https ADCP_AGENT_URL override is rejected, not emitted verbatim.

        Per adcp/_schemas/3.1/core/seller-agent-ref.json, agent_url MUST
        use https://. An operator misconfiguring ADCP_AGENT_URL=http://... must
        not produce a spec-invalid binding — the override is ignored and
        resolution falls through to the tenant virtual_host path (which itself
        also requires https, so with no valid tenant host this returns None).
        """
        tenant = MagicMock()
        tenant.virtual_host = None
        tenant.subdomain = None
        mock_uow_cls = make_tenant_config_uow(tenant)

        with patch("src.services.tmp_provider_sync.TenantConfigUoW", mock_uow_cls):
            with patch.dict("os.environ", {"ADCP_AGENT_URL": "http://insecure.agent.com/mcp"}):
                result = _resolve_seller_agent_url("test-tenant")

        assert result is None

    def test_non_https_env_override_falls_through_to_https_virtual_host(self):
        """A rejected non-https override still allows the virtual_host path to succeed."""
        tenant = MagicMock()
        tenant.virtual_host = "tenant.salesagent.example.com"
        tenant.subdomain = None
        mock_uow_cls = make_tenant_config_uow(tenant)

        with patch("src.services.tmp_provider_sync.TenantConfigUoW", mock_uow_cls):
            with patch.dict("os.environ", {"ADCP_AGENT_URL": "http://insecure.agent.com/mcp"}):
                result = _resolve_seller_agent_url("test-tenant")

        assert result == "https://tenant.salesagent.example.com/mcp"

    def test_uses_tenant_virtual_host(self):
        """Uses tenant.virtual_host when ADCP_AGENT_URL is not set."""
        import os

        tenant = MagicMock()
        tenant.virtual_host = "tenant.salesagent.example.com"
        tenant.subdomain = "tenant"
        mock_uow_cls = make_tenant_config_uow(tenant)

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
        mock_uow_cls = make_tenant_config_uow(tenant)

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
        mock_uow_cls = make_tenant_config_uow(tenant)

        with patch("src.services.tmp_provider_sync.TenantConfigUoW", mock_uow_cls):
            with patch.dict("os.environ", {}, clear=False):
                os.environ.pop("ADCP_AGENT_URL", None)
                result = _resolve_seller_agent_url("test-tenant")

        assert result == "https://tenant.salesagent.example.com/mcp"

    def test_returns_none_for_localhost_virtual_host(self):
        """A localhost virtual_host returns None — cannot produce a valid https URL.

        Per adcp/_schemas/3.1/core/seller-agent-ref.json, agent_url MUST use
        https://. Local dev hosts cannot satisfy this requirement, so None is
        returned and the caller skips the sync.
        """
        import os

        tenant = MagicMock()
        tenant.virtual_host = "tenant.sales-agent.localhost:8001"
        tenant.subdomain = None
        mock_uow_cls = make_tenant_config_uow(tenant)

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
        mock_uow_cls = make_tenant_config_uow(tenant)

        with patch("src.services.tmp_provider_sync.TenantConfigUoW", mock_uow_cls):
            with patch.dict("os.environ", {}, clear=False):
                os.environ.pop("ADCP_AGENT_URL", None)
                result = _resolve_seller_agent_url("test-tenant")

        assert result == "https://my-localhost-mirror.example.com/mcp"


# ---------------------------------------------------------------------------
# Local-host predicate
# ---------------------------------------------------------------------------
#
# The predicate itself now lives in src.core.domain_config.is_local_host
# — shared with src/app.py's agent-card scheme choice, which forked from it on
# *.localhost (#1197 review). Its own cases are in
# tests/unit/test_ssrf_url_validator.py::TestIsLocalHost; the seller-URL
# behaviour that depends on it is graded by TestResolveSellAgentUrl above.


# ---------------------------------------------------------------------------
# _post_packages_sync auth header tests
# ---------------------------------------------------------------------------


class TestPostPackagesSyncAuth:
    """_post_packages_sync sends Bearer auth when credentials are provided."""

    def test_sends_bearer_token_when_auth_credentials_set(self):
        """When auth_credentials is non-empty, Authorization: Bearer header is sent."""
        with patch("src.services.tmp_provider_sync.send", return_value=make_seam_result(200)) as seam:
            _post_packages_sync(
                "https://provider:3000",
                [_a_package()],
                auth_credentials="secret-token",
            )

        _, kwargs = seam.call_args
        assert seam.call_args[0][0] == "https://provider:3000/packages/sync"
        assert kwargs["method"] == "POST"
        # The SERIALIZED body, not the model: _post_packages_sync owns the one
        # model_dump in the path, so this asserts what goes on the wire.
        assert kwargs["json"] == [_package_wire()]
        assert kwargs["headers"] == {"Authorization": "Bearer secret-token"}

    def test_sends_no_auth_headers_when_no_credentials(self):
        """When auth_credentials is empty, no auth headers are sent."""
        with patch("src.services.tmp_provider_sync.send", return_value=make_seam_result(200)) as seam:
            _post_packages_sync(
                "https://provider:3000",
                [_a_package()],
                auth_credentials="",
            )

        assert seam.call_args.kwargs["headers"] == {}

    def test_provenance_is_an_operator_endpoint_naming_no_address(self):
        """The seam is told whose URL this is, as a role rather than an address.

        Replaces a ``follow_redirects=False`` assertion: that flag is no longer
        this module's to pass — it is httpx's default inside the seam, and the
        SSRF guard it stood in for is the seam's pinned-IP transport. What IS
        still this call site's decision is the provenance it declares, and an
        ``OperatorEndpoint`` whose name looked like a URL would disclose network
        topology on a refusal (AdCP 3.1.1 security point 6), which its own
        constructor refuses (#1802 migration).
        """
        with patch("src.services.tmp_provider_sync.send", return_value=make_seam_result(200)) as seam:
            _post_packages_sync("https://provider:3000", [_a_package()])

        provenance = seam.call_args.kwargs["provenance"]
        assert isinstance(provenance, OperatorEndpoint)
        assert "://" not in provenance.name

    def test_seam_failure_propagates_to_the_caller(self):
        """A non-2xx the seam refuses to retry reaches the caller as OutboundError.

        Silent success must be impossible: the fan-out's except block logs the
        failure and continues to the next provider, which only happens if this
        raises. The seam raises rather than returning a 500 result, so the
        contract ``raise_for_status()`` used to provide is preserved.
        """
        with patch(
            "src.services.tmp_provider_sync.send",
            side_effect=make_delivery_failed(500),
        ):
            with pytest.raises(OutboundDeliveryFailed):
                _post_packages_sync("https://provider:3000", [_a_package()])

    def test_fan_out_uses_provider_auth_credentials(self):
        """sync_packages_for_media_buy passes provider.auth_credentials to _post_packages_sync."""
        pkg = make_mock_package(package_id="pkg-1", package_config={"product_id": "prod-1"})

        provider = MagicMock()
        provider.name = "Credentialed Provider"
        provider.endpoint = "https://provider:3000"
        provider.auth_credentials = "provider-secret"
        provider.auth_type = "bearer"

        mock_mb_cls, _mb_uow, mock_tp_cls, _tp_uow = make_sync_uow(packages=[pkg], providers=[provider])
        with (
            patch("src.services.tmp_provider_sync._post_packages_sync") as mock_post,
            patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value=_SELLER_AGENT_URL),
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
        ):
            sync_packages_for_media_buy("tenant-1", "mb-1")

        mock_post.assert_called_once_with(
            "https://provider:3000",
            mock.ANY,  # payload correctness pinned by TestBuildPackagePayload
            "provider-secret",
            "bearer",  # the provider's scheme, passed through — not mock.ANY
        )


class TestProviderAuthHeaders:
    """``provider_auth_headers`` — the function that turns a stored scheme into a header.

    It had no direct test: the fan-out assertions used ``mock.ANY`` for
    ``auth_type`` and excused it by citing a class that did not exist, so passing a
    hardcoded scheme would have kept both green (#1197 review).
    """

    def test_bearer_scheme_emits_the_authorization_header(self):
        from src.services._provider_http import provider_auth_headers

        assert provider_auth_headers("bearer", "tok") == {"Authorization": "Bearer tok"}

    def test_no_credential_emits_no_headers(self):
        """An unauthenticated provider is a supported registration."""
        from src.services._provider_http import provider_auth_headers

        assert provider_auth_headers("bearer", "") == {}
        assert provider_auth_headers(None, "") == {}

    def test_a_credential_without_a_scheme_defaults_to_bearer(self):
        """What every previously-stored registration already got."""
        from src.services._provider_http import provider_auth_headers

        assert provider_auth_headers(None, "tok") == {"Authorization": "Bearer tok"}

    def test_an_unimplemented_scheme_raises(self):
        """No silent fallback — that would reinstate "the selected scheme is ignored".

        Unreachable from a write surface (the record types the field from
        ``VALID_AUTH_SCHEMES``), so it is a programming error.
        """
        from src.services._provider_http import provider_auth_headers

        with pytest.raises(ValueError, match="api_key"):
            provider_auth_headers("api_key", "tok")

    def test_the_vocabulary_is_what_the_record_constrains(self):
        """The field and the behaviour cannot disagree — same frozenset."""
        from src.core.schemas.tmp_provider import VALID_AUTH_SCHEMES, _known_auth_scheme

        for scheme in VALID_AUTH_SCHEMES:
            assert _known_auth_scheme(scheme) == scheme
