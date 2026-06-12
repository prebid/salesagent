"""Tests for the synchronous credential probes used at provision time.

``probe_adapter_connection()`` is the gate that turns "bad credentials" into
a 400 at provision rather than an eternally-pending inventory sync. These
tests pin the contract for each adapter type:

- Auth rejection → ``ProbeResult(success=False, error_code="invalid_credentials")``
- Permission denied (valid creds, wrong account) → ``error_code="permission_denied"``
- Wrong network identifier (e.g. typo) → ``error_code="network_not_found"``
- Transport failure / fallback → ``error_code="connection_failed"``
- Success → ``ProbeResult(success=True)``
- Missing required config → fail with ``invalid_config`` and no HTTP call

The probes themselves call into live adapter clients; tests mock those at
the call boundary so the behavior under each HTTP outcome is exercised.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.admin.services.adapter_connection_tester import (
    CONNECTION_FAILED,
    CUSTOMER_REBINDS_ACCOUNT,
    CUSTOMER_ROTATES_TOKEN,
    INVALID_CONFIG,
    INVALID_CREDENTIALS,
    NETWORK_NOT_FOUND,
    PERMISSION_DENIED,
    UPSTREAM_UNAVAILABLE,
    VENDOR_ENABLES_ROLE,
    _classify_gam_message,
    preview_adapter,
    probe_adapter_connection,
)


class TestGAMFaultClassification:
    """``_classify_gam_message`` turns a GAM SOAP fault string into
    ``(error_code, remediation, gam_extra)`` so callers don't have to grep
    English error text. The ``gam_extra`` block ends up nested under
    ``vendor_fault.gam``."""

    def test_network_not_found_classified_as_typo(self):
        msg = "Authentication failed: [AuthenticationError.NETWORK_NOT_FOUND @ ; trigger:'3312659540']"
        code, remediation, extra = _classify_gam_message(msg)
        assert code == NETWORK_NOT_FOUND
        # Typos are unambiguous — no remediation hint needed; the code
        # already implies "fix the network_code".
        assert remediation is None
        assert extra["service"] == "AuthenticationError"
        assert extra["reason"] == "NETWORK_NOT_FOUND"
        assert extra["trigger"] == "3312659540"

    def test_not_allowed_classified_as_permission_denied(self):
        msg = "Authentication failed: [AuthenticationError.NOT_ALLOWED @ network]"
        code, remediation, extra = _classify_gam_message(msg)
        assert code == PERMISSION_DENIED
        # Service account isn't in this network — customer rebinds.
        assert remediation == CUSTOMER_REBINDS_ACCOUNT
        assert extra["reason"] == "NOT_ALLOWED"

    def test_no_networks_to_access_classified_as_permission_denied(self):
        msg = "[AuthenticationError.NO_NETWORKS_TO_ACCESS @ ]"
        code, remediation, extra = _classify_gam_message(msg)
        assert code == PERMISSION_DENIED
        assert remediation == CUSTOMER_REBINDS_ACCOUNT
        assert extra["reason"] == "NO_NETWORKS_TO_ACCESS"

    def test_authentication_failed_classified_as_invalid_credentials(self):
        msg = "[AuthenticationError.AUTHENTICATION_FAILED @ ]"
        code, remediation, extra = _classify_gam_message(msg)
        assert code == INVALID_CREDENTIALS
        assert remediation == CUSTOMER_ROTATES_TOKEN
        assert extra["reason"] == "AUTHENTICATION_FAILED"

    def test_unparseable_message_falls_back_to_connection_failed(self):
        code, remediation, extra = _classify_gam_message("DNS lookup failed for adsapi.google.com")
        assert code == CONNECTION_FAILED
        assert remediation is None
        assert extra == {}

    def test_unknown_reason_falls_back_to_connection_failed(self):
        msg = "[ServerError.SOAP_FAULT @ ; trigger:'something']"
        code, remediation, extra = _classify_gam_message(msg)
        assert code == CONNECTION_FAILED
        # No remediation — we don't know what GAM is complaining about.
        assert remediation is None
        # Fault block is still populated for diagnostics.
        assert extra["service"] == "ServerError"
        assert extra["reason"] == "SOAP_FAULT"

    def test_multi_fault_prefers_classifiable_reason(self):
        """Real GAM responses often prepend a generic ``ServerError`` wrapper
        in front of the diagnostic ``AuthenticationError`` entry. The
        classifier must scan past the wrapper to find the typed reason."""
        msg = "Server fault: [ServerError.SOAP_FAULT @ ] [AuthenticationError.NETWORK_NOT_FOUND @ ; trigger:'12345']"
        code, remediation, extra = _classify_gam_message(msg)
        assert code == NETWORK_NOT_FOUND
        # Picks the classifiable entry, not the first one.
        assert extra["reason"] == "NETWORK_NOT_FOUND"
        assert extra["trigger"] == "12345"


class TestFreeWheelProbe:
    """FreeWheel probe is two-call: token_info (auth) + list_sites (binding)."""

    def _config(self, **overrides):
        base = {"api_token": "tok", "environment": "production"}
        base.update(overrides)
        return base

    def test_missing_credentials_fails_with_invalid_config(self):
        result = probe_adapter_connection("freewheel", {"environment": "production"})
        assert result.success is False
        assert result.error_code == INVALID_CONFIG
        assert "username + password" in result.error_message or "api_token" in result.error_message

    def test_client_credentials_skips_token_info_and_probes_inventory(self):
        """API-Access client_credentials tokens 401 on /auth/token/info, so the
        probe must validate via list_sites only — never call token_info."""
        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            result = probe_adapter_connection(
                "freewheel",
                {"client_id": "cid", "client_secret": "sec", "environment": "sandbox"},
            )
        assert result.success is True
        client.token_info.assert_not_called()
        client.inventory.list_sites.assert_called_once_with(per_page=1)

    def test_auth_rejection_classified_as_invalid_credentials(self):
        from src.adapters.freewheel._transport import FreeWheelAuthError

        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.side_effect = FreeWheelAuthError("bad token", status_code=401)
            result = probe_adapter_connection("freewheel", self._config())
        assert result.success is False
        assert result.error_code == INVALID_CREDENTIALS
        assert "auth rejected" in result.error_message

    def test_inventory_403_classified_as_permission_denied(self):
        from src.adapters.freewheel._transport import FreeWheelForbiddenError

        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.return_value = {"sub": "user@example.com"}
            client.inventory.list_sites.side_effect = FreeWheelForbiddenError("no inventory scope", status_code=403)
            result = probe_adapter_connection("freewheel", self._config())
        assert result.success is False
        assert result.error_code == PERMISSION_DENIED
        # 403 on the inventory probe = wrong-publisher binding; customer
        # remediates by getting a token for the right account.
        assert result.remediation == CUSTOMER_REBINDS_ACCOUNT
        assert "cannot read inventory" in result.error_message
        assert "publisher" in result.error_message
        fault = result.details["vendor_fault"]
        assert fault["vendor"] == "freewheel"
        assert fault["phase"] == "list_sites"
        assert fault["endpoint"] == "/services/v4/sites"
        assert fault["vendor_status"] == 403

    def test_inventory_404_classified_as_permission_denied(self):
        """404 on the inventory probe is the role/scope-gap signature:
        bearer authenticated but the configured publisher has no
        accessible inventory route. UIs need PERMISSION_DENIED + a
        VENDOR_ENABLES_ROLE remediation so the "contact your rep" copy
        fires without grepping the message."""
        from src.adapters.freewheel._transport import FreeWheelNotFoundError

        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.return_value = {"sub": "user@example.com"}
            client.inventory.list_sites.side_effect = FreeWheelNotFoundError(
                "no sites", status_code=404, body="Not Found"
            )
            result = probe_adapter_connection("freewheel", self._config())
        assert result.success is False
        assert result.error_code == PERMISSION_DENIED
        assert result.remediation == VENDOR_ENABLES_ROLE
        assert "404" in result.error_message
        fault = result.details["vendor_fault"]
        assert fault["vendor"] == "freewheel"
        assert fault["phase"] == "list_sites"
        assert fault["endpoint"] == "/services/v4/sites"
        assert fault["vendor_status"] == 404

    def test_inventory_5xx_classified_as_upstream_unavailable(self):
        """5xx from the inventory endpoint = we reached FreeWheel but it's
        unhealthy. Splits from connection_failed (DNS/TLS/no-response) so
        UIs can render "transient — retry in a moment" vs "check your
        network config"."""
        from src.adapters.freewheel._transport import FreeWheelError

        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.return_value = {"sub": "user@example.com"}
            client.inventory.list_sites.side_effect = FreeWheelError(
                "internal error", status_code=503, body="Service Unavailable"
            )
            result = probe_adapter_connection("freewheel", self._config())
        assert result.success is False
        assert result.error_code == UPSTREAM_UNAVAILABLE
        fault = result.details["vendor_fault"]
        assert fault["vendor"] == "freewheel"
        assert fault["vendor_status"] == 503

    def test_token_info_404_stays_connection_failed(self):
        """Asymmetric counterpart to ``test_inventory_404_classified_as_permission_denied``.
        A 404 from ``/auth/token/info`` is a host/route misconfiguration
        (the auth gateway hostname is wrong) — not a role gap. It must
        stay CONNECTION_FAILED so the embedder's "check your base URL"
        copy fires instead of the "contact your rep" copy."""
        from src.adapters.freewheel._transport import FreeWheelNotFoundError

        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.side_effect = FreeWheelNotFoundError("no such route", status_code=404, body="Not Found")
            result = probe_adapter_connection("freewheel", self._config())
        assert result.success is False
        assert result.error_code == CONNECTION_FAILED
        assert result.remediation is None
        fault = result.details["vendor_fault"]
        assert fault["vendor"] == "freewheel"
        assert fault["phase"] == "token_info"
        assert fault["endpoint"] == "/auth/token/info"
        assert fault["vendor_status"] == 404

    def test_token_info_failure_attaches_fault_details(self):
        """All FreeWheel fail paths must emit a vendor_fault block so the
        embedder's typed-code UI can branch on machine-readable fields."""
        from src.adapters.freewheel._transport import FreeWheelAuthError

        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.side_effect = FreeWheelAuthError("token revoked", status_code=401)
            result = probe_adapter_connection("freewheel", self._config())
        # Invalid creds → rotate the token.
        assert result.remediation == CUSTOMER_ROTATES_TOKEN
        fault = result.details["vendor_fault"]
        assert fault["vendor"] == "freewheel"
        assert fault["phase"] == "token_info"
        assert fault["endpoint"] == "/auth/token/info"
        assert fault["vendor_status"] == 401

    def test_transport_failure_classified_as_connection_failed(self):
        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.side_effect = ConnectionError("DNS")
            result = probe_adapter_connection("freewheel", self._config())
        assert result.success is False
        assert result.error_code == CONNECTION_FAILED
        assert "transport failure" in result.error_message

    def test_happy_path_returns_success(self):
        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.return_value = {"sub": "user@example.com"}
            client.inventory.list_sites.return_value = MagicMock()
            result = probe_adapter_connection("freewheel", self._config())
        assert result.success is True
        assert result.error_code is None
        assert result.error_message is None


class TestBroadstreetProbe:
    """Broadstreet probe is one call: get_network() validates auth + binding."""

    def test_missing_network_id_fails_with_invalid_config(self):
        result = probe_adapter_connection("broadstreet", {"api_key": "k"})
        assert result.success is False
        assert result.error_code == INVALID_CONFIG
        assert "network_id" in result.error_message

    def test_missing_api_key_fails_with_invalid_config(self):
        result = probe_adapter_connection("broadstreet", {"network_id": "123"})
        assert result.success is False
        assert result.error_code == INVALID_CONFIG
        assert "api_key" in result.error_message

    def test_401_classified_as_invalid_credentials(self):
        from src.adapters.broadstreet.client import BroadstreetAPIError

        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.side_effect = BroadstreetAPIError("unauthorized", status_code=401)
            result = probe_adapter_connection("broadstreet", {"network_id": "123", "api_key": "wrong"})
        assert result.success is False
        assert result.error_code == INVALID_CREDENTIALS
        assert "auth rejected" in result.error_message

    def test_403_classified_as_permission_denied(self):
        from src.adapters.broadstreet.client import BroadstreetAPIError

        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.side_effect = BroadstreetAPIError("forbidden", status_code=403)
            result = probe_adapter_connection("broadstreet", {"network_id": "123", "api_key": "wrong"})
        assert result.success is False
        assert result.error_code == PERMISSION_DENIED
        assert result.remediation == CUSTOMER_REBINDS_ACCOUNT
        assert "network access denied" in result.error_message
        fault = result.details["vendor_fault"]
        assert fault["vendor"] == "broadstreet"
        assert fault["phase"] == "get_network"
        assert fault["vendor_status"] == 403

    def test_5xx_classified_as_upstream_unavailable(self):
        from src.adapters.broadstreet.client import BroadstreetAPIError

        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.side_effect = BroadstreetAPIError("server error", status_code=500)
            result = probe_adapter_connection("broadstreet", {"network_id": "123", "api_key": "k"})
        assert result.success is False
        assert result.error_code == UPSTREAM_UNAVAILABLE
        fault = result.details["vendor_fault"]
        assert fault["vendor"] == "broadstreet"
        assert fault["vendor_status"] == 500

    def test_wrong_network_id_classified_as_network_not_found(self):
        from src.adapters.broadstreet.client import BroadstreetAPIError

        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.side_effect = BroadstreetAPIError("not found", status_code=404)
            result = probe_adapter_connection("broadstreet", {"network_id": "999999", "api_key": "k"})
        assert result.success is False
        assert result.error_code == NETWORK_NOT_FOUND
        assert "not found" in result.error_message
        assert "999999" in result.error_message

    def test_happy_path_returns_success(self):
        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.return_value = {"id": 123, "name": "Net"}
            result = probe_adapter_connection("broadstreet", {"network_id": "123", "api_key": "k"})
        assert result.success is True
        assert result.error_code is None


class TestSpringServeProbe:
    """SpringServe probe is one transport.probe() call — status code drives
    the outcome. Auth-mint failures from the password grant raise rather
    than returning a status code."""

    def _config(self, **overrides):
        base = {"api_token": "tok"}
        base.update(overrides)
        return base

    def test_missing_credentials_fails_with_invalid_config(self):
        result = probe_adapter_connection("springserve", {})
        assert result.success is False
        assert result.error_code == INVALID_CONFIG
        assert "email + password" in result.error_message or "api_token" in result.error_message

    def test_auth_mint_failure_classified_as_invalid_credentials(self):
        from src.adapters.springserve._transport import SpringServeAuthError

        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client.probe.side_effect = SpringServeAuthError("bad creds", status_code=401)
            result = probe_adapter_connection("springserve", {"email": "a@b.com", "password": "x"})
        assert result.success is False
        assert result.error_code == INVALID_CREDENTIALS
        assert "auth rejected" in result.error_message

    def test_403_classified_as_permission_denied(self):
        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client.probe.return_value = (403, "Forbidden")
            result = probe_adapter_connection("springserve", self._config())
        assert result.success is False
        assert result.error_code == PERMISSION_DENIED
        # 403 typically means the token is for the wrong account; customer
        # rebinds rather than asking the vendor to enable a role.
        assert result.remediation == CUSTOMER_REBINDS_ACCOUNT
        assert "cannot read supply inventory" in result.error_message
        assert "publisher" in result.error_message
        fault = result.details["vendor_fault"]
        assert fault["vendor"] == "springserve"
        assert fault["phase"] == "supply_probe"
        assert fault["endpoint"] == "/supply_tags"
        assert fault["vendor_status"] == 403

    def test_404_on_supply_probe_classified_as_permission_denied(self):
        """SpringServe surfaces "your account lacks the supply API role" as a
        404 on the supply endpoint (auth succeeded, route hidden from the
        bearer). Treat as PERMISSION_DENIED + VENDOR_ENABLES_ROLE so UIs
        render the "contact your SpringServe rep" copy instead of the
        generic "could not connect" copy."""
        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client.probe.return_value = (404, "Not Found")
            result = probe_adapter_connection("springserve", self._config())
        assert result.success is False
        assert result.error_code == PERMISSION_DENIED
        assert result.remediation == VENDOR_ENABLES_ROLE
        assert "404" in result.error_message
        assert "supply API role" in result.error_message
        fault = result.details["vendor_fault"]
        assert fault["vendor"] == "springserve"
        assert fault["phase"] == "supply_probe"
        assert fault["endpoint"] == "/supply_tags"
        assert fault["vendor_status"] == 404
        assert fault["vendor_message"] == "Not Found"

    def test_5xx_on_supply_probe_classified_as_upstream_unavailable(self):
        """5xx from /supply_tags = SpringServe is unhealthy. Distinct from
        connection_failed (we never reached them) — retry-eligible."""
        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client.probe.return_value = (502, "Bad Gateway")
            result = probe_adapter_connection("springserve", self._config())
        assert result.success is False
        assert result.error_code == UPSTREAM_UNAVAILABLE
        fault = result.details["vendor_fault"]
        assert fault["vendor"] == "springserve"
        assert fault["vendor_status"] == 502

    def test_auth_failure_attaches_fault_details(self):
        """All SpringServe fail paths must emit a vendor_fault block.

        Phase/endpoint are always ``supply_probe`` / ``/supply_tags`` —
        ``client.probe()`` always targets the supply endpoint, and any
        token mint that happens is an internal implementation detail of
        the transport. Don't label exceptions with a speculative ``/auth``
        endpoint that may or may not have actually been hit."""
        from src.adapters.springserve._transport import SpringServeAuthError

        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client.probe.side_effect = SpringServeAuthError("invalid", status_code=401)
            result = probe_adapter_connection("springserve", self._config())
        assert result.remediation == CUSTOMER_ROTATES_TOKEN
        fault = result.details["vendor_fault"]
        assert fault["vendor"] == "springserve"
        assert fault["phase"] == "supply_probe"
        assert fault["endpoint"] == "/supply_tags"
        assert fault["vendor_status"] == 401

    def test_bare_exception_does_not_leak_str_exc(self):
        """``requests.ConnectionError`` and similar untyped exceptions
        embed the full URL (including internal hostnames) in ``str(exc)``.
        The fault block must include ONLY the exception class name for
        these paths — never the stringified message."""

        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client.probe.side_effect = ConnectionError(
                "HTTPSConnectionPool(host='internal-host.staging.local', port=443): "
                "Max retries exceeded with url: /supply_tags"
            )
            result = probe_adapter_connection("springserve", self._config())
        fault = result.details["vendor_fault"]
        assert fault["vendor_message"] == "ConnectionError"
        assert "internal-host" not in fault["vendor_message"]
        # error_message on the envelope also must not leak the URL.
        assert "internal-host" not in result.error_message

    def test_happy_path_returns_success(self):
        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client.probe.return_value = (200, "[]")
            result = probe_adapter_connection("springserve", self._config())
        assert result.success is True
        assert result.error_code is None
        client.probe.assert_called_once_with("GET", "/supply_tags?per_page=1")


class TestRoutingTable:
    """The dispatch in probe_adapter_connection covers every adapter the
    discriminated AdapterConfig union accepts. Adding a new adapter to the
    schema without updating this dispatch is a real (and previously latent)
    bug — this guard catches it by deriving the adapter list directly from
    the schema's discriminated union, so a hardcoded list can't fall out
    of sync."""

    def test_all_adapter_types_are_routed(self):
        from typing import get_args

        from src.admin.api_schemas.tenant_management import AdapterConfig

        # AdapterConfig is Annotated[Union[...], Field(discriminator="type")].
        # Unwrap to get the union, then pull each member's "type" Literal.
        union = get_args(AdapterConfig)[0]
        adapter_types = [get_args(m.model_fields["type"].annotation)[0] for m in get_args(union)]
        assert adapter_types, "Schema introspection returned no adapter types — check AdapterConfig union shape"

        for adapter_type in adapter_types:
            result = probe_adapter_connection(adapter_type, {})
            err = result.error_message or ""
            assert "Unsupported adapter_type" not in err, (
                f"{adapter_type!r} (declared in AdapterConfig union) fell through to the "
                f"unsupported-type branch in probe_adapter_connection — add a probe for it."
            )

    def test_all_adapter_types_have_preview(self):
        """Same guard for ``preview_adapter`` — the Storefront's inline
        preview UI must work for every adapter the schema accepts. A new
        adapter without a preview path returns ``ok=False`` with an
        Unsupported error, breaking the pre-commit UX."""
        from typing import get_args

        from src.admin.api_schemas.tenant_management import AdapterConfig

        union = get_args(AdapterConfig)[0]
        adapter_types = [get_args(m.model_fields["type"].annotation)[0] for m in get_args(union)]

        for adapter_type in adapter_types:
            preview = preview_adapter(adapter_type, {})
            err = preview.error or ""
            assert "Unsupported adapter_type" not in err, (
                f"{adapter_type!r} (declared in AdapterConfig union) fell through to the "
                f"unsupported-type branch in preview_adapter — add a _preview_{adapter_type}()."
            )


class TestGAMProbeClassification:
    """End-to-end: a GAM-flavored exception bubbles up through ``_test_gam``
    and gets classified into the right ``error_code``. This is the
    user-visible contract from #467."""

    def _gam_config(self):
        return {"network_code": "12345", "service_account_json": "{}"}

    def test_network_not_found_surface_through_probe(self):
        with patch("src.adapters.gam.client.GAMClientManager") as mock_mgr:
            mock_mgr.return_value.test_connection.side_effect = Exception(
                "[AuthenticationError.NETWORK_NOT_FOUND @ ; trigger:'12345']"
            )
            result = probe_adapter_connection("google_ad_manager", self._gam_config())
        assert result.success is False
        assert result.error_code == NETWORK_NOT_FOUND
        # No remediation hint: code "network_not_found" is unambiguous.
        assert result.remediation is None
        fault = result.details["vendor_fault"]
        assert fault["vendor"] == "gam"
        assert fault["phase"] == "get_current_network"
        assert fault["endpoint"] == "NetworkService.getCurrentNetwork"
        # GAM SOAP specifics live under the vendor-discriminated nested block.
        assert fault["gam"]["reason"] == "NETWORK_NOT_FOUND"
        assert fault["gam"]["trigger"] == "12345"

    def test_not_allowed_surface_through_probe(self):
        with patch("src.adapters.gam.client.GAMClientManager") as mock_mgr:
            mock_mgr.return_value.test_connection.side_effect = Exception("[AuthenticationError.NOT_ALLOWED @ network]")
            result = probe_adapter_connection("google_ad_manager", self._gam_config())
        assert result.success is False
        assert result.error_code == PERMISSION_DENIED
        # Service account not in this network — customer can rebind.
        assert result.remediation == CUSTOMER_REBINDS_ACCOUNT
        fault = result.details["vendor_fault"]
        assert fault["vendor"] == "gam"
        assert fault["gam"]["reason"] == "NOT_ALLOWED"

    def test_unhealthy_status_surfaces_through_probe(self):
        """When ``test_connection`` returns UNHEALTHY (rather than raising),
        the message is still classified."""
        from src.adapters.gam.utils.health_check import HealthCheckResult, HealthStatus

        with patch("src.adapters.gam.client.GAMClientManager") as mock_mgr:
            mock_mgr.return_value.test_connection.return_value = HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                check_name="authentication",
                message="Authentication failed: [AuthenticationError.NETWORK_NOT_FOUND @ ; trigger:'X']",
                details={},
                duration_ms=0,
            )
            result = probe_adapter_connection("google_ad_manager", self._gam_config())
        assert result.success is False
        assert result.error_code == NETWORK_NOT_FOUND


class TestFreeWheelPreview:
    def test_missing_credentials_returns_inline_error(self):
        preview = preview_adapter("freewheel", {"environment": "production"})
        assert preview.ok is False
        assert preview.error_code == INVALID_CONFIG
        assert "username + password" in (preview.error or "") or "api_token" in (preview.error or "")

    def test_auth_rejection_surfaces_inline(self):
        from src.adapters.freewheel._transport import FreeWheelAuthError

        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.side_effect = FreeWheelAuthError("bad", status_code=401)
            preview = preview_adapter("freewheel", {"api_token": "tok"})
        assert preview.ok is False
        assert preview.error_code == INVALID_CREDENTIALS
        assert "auth rejected" in preview.error

    def test_happy_path_surfaces_user_name_as_network_name(self):
        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.return_value = {"user_id": 42, "user_name": "alice@example.com"}
            client.inventory.list_sites.return_value = MagicMock()
            preview = preview_adapter("freewheel", {"api_token": "tok"})
        assert preview.ok is True
        assert preview.network_name == "alice@example.com"
        assert preview.inventory_reachable is True

    def test_inventory_unreachable_is_non_fatal(self):
        """If token is valid but inventory probe fails, preview still
        returns ok=True — preview is a soft check, not the provision
        gate. Inventory_reachable flag tells the UI to warn but allow."""
        with patch("src.adapters.freewheel.client.FreeWheelClient") as mock_cls:
            client = mock_cls.return_value
            client.token_info.return_value = {"user_name": "u"}
            client.inventory.list_sites.side_effect = Exception("scope missing")
            preview = preview_adapter("freewheel", {"api_token": "tok"})
        assert preview.ok is True
        assert preview.inventory_reachable is False


class TestBroadstreetPreview:
    def test_happy_path_returns_network_name(self):
        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.return_value = {"id": "nw1", "name": "Acme Publishers"}
            preview = preview_adapter("broadstreet", {"network_id": "nw1", "api_key": "k"})
        assert preview.ok is True
        assert preview.network_name == "Acme Publishers"
        assert preview.network_code == "nw1"

    def test_wrong_network_classified_as_network_not_found(self):
        from src.adapters.broadstreet.client import BroadstreetAPIError

        with patch("src.adapters.broadstreet.client.BroadstreetClient") as mock_cls:
            client = mock_cls.return_value
            client.get_network.side_effect = BroadstreetAPIError("not found", status_code=404)
            preview = preview_adapter("broadstreet", {"network_id": "nw1", "api_key": "k"})
        assert preview.ok is False
        assert preview.error_code == NETWORK_NOT_FOUND
        assert "not found" in preview.error


class TestSpringServePreview:
    def test_happy_path_returns_email_as_network_name(self):
        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client.probe.return_value = (200, "[]")
            preview = preview_adapter("springserve", {"email": "ops@pub.com", "password": "x"})
        assert preview.ok is True
        assert preview.network_name == "ops@pub.com"
        assert preview.inventory_reachable is True
        client.probe.assert_called_once_with("GET", "/supply_tags?per_page=1")

    def test_auth_failure_surfaces_inline(self):
        from src.adapters.springserve._transport import SpringServeAuthError

        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client.probe.side_effect = SpringServeAuthError("bad", status_code=401)
            preview = preview_adapter("springserve", {"api_token": "tok"})
        assert preview.ok is False
        assert preview.error_code == INVALID_CREDENTIALS
        assert preview.remediation == CUSTOMER_ROTATES_TOKEN
        assert "auth rejected" in preview.error
        fault = preview.details["vendor_fault"]
        assert fault["vendor"] == "springserve"
        assert fault["phase"] == "supply_probe"
        assert fault["endpoint"] == "/supply_tags"

    def test_404_on_supply_probe_surfaces_as_permission_denied(self):
        with patch("src.adapters.springserve.client.SpringServeClient") as mock_cls:
            client = mock_cls.return_value
            client.probe.return_value = (404, "Not Found")
            preview = preview_adapter("springserve", {"api_token": "tok"})
        assert preview.ok is False
        assert preview.error_code == PERMISSION_DENIED
        assert preview.remediation == VENDOR_ENABLES_ROLE
        assert "supply API role" in preview.error
        fault = preview.details["vendor_fault"]
        assert fault["vendor"] == "springserve"
        assert fault["phase"] == "supply_probe"
        assert fault["vendor_status"] == 404
