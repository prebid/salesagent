"""Unit tests for src/core/adcp_version.py — SDK-derived AdCP version."""

import adcp
import pytest

from src.core.adcp_version import (
    adcp_build_version,
    adcp_major_version,
    supported_adcp_versions,
    validate_adcp_version_pins,
)
from src.core.exceptions import AdCPVersionUnsupportedError


def test_major_version_derived_from_sdk_spec_pin():
    """The advertised major is parsed from the SDK's spec version, not hardcoded."""
    expected = int(adcp.get_adcp_spec_version().split(".", 1)[0])
    assert adcp_major_version() == expected


def test_major_version_is_current_pinned_major():
    """Repo pins AdCP 3.x (see docs/adcp-spec-version.md). Guards a stray cross-major bump."""
    assert adcp_major_version() == 3


def test_supported_versions_derived_from_sdk_spec_pin():
    """supported_versions is the release-precision (MAJOR.MINOR) form of the SDK pin."""
    major, minor = adcp.get_adcp_spec_version().split(".", 2)[:2]
    assert supported_adcp_versions() == (f"{major}.{minor}",)


def test_build_version_is_full_sdk_semver():
    """build_version is the advisory full-semver spec pin (version-unsupported.json)."""
    assert adcp_build_version() == adcp.get_adcp_spec_version()


def test_malformed_sdk_spec_pin_raises_typed_configuration_error(monkeypatch):
    """A malformed SDK spec pin surfaces as a typed AdCPConfigurationError, not a bare ValueError.

    ``supported_adcp_versions()`` runs on the buyer request path
    (``validate_adcp_version_pins`` -> ``_supported_majors``). A broken seller
    deployment (unparseable spec pin) must surface as a typed 500 like every
    other server-side failure, never escape as an untyped unpack/int ValueError.
    """
    from src.core import adcp_version as av
    from src.core.exceptions import AdCPConfigurationError

    av._spec_major_minor.cache_clear()
    monkeypatch.setattr(adcp, "get_adcp_spec_version", lambda: "banana")
    try:
        # Both siblings degrade the same way, and the request-path entry point does too.
        for call in (av.adcp_major_version, av.supported_adcp_versions):
            with pytest.raises(AdCPConfigurationError):
                call()
        with pytest.raises(AdCPConfigurationError):
            av.validate_adcp_version_pins({"adcp_major_version": 99})
    finally:
        av._spec_major_minor.cache_clear()


class TestValidateAdcpVersionPins:
    """Version negotiation per core/version-envelope.json (v3.1.0-beta.3, #1512 Tier 2)."""

    def test_supported_major_passes(self):
        validate_adcp_version_pins({"adcp_major_version": adcp_major_version(), "brief": "ads"})

    def test_supported_release_string_passes(self):
        validate_adcp_version_pins({"adcp_version": supported_adcp_versions()[0], "brief": "ads"})

    def test_absent_claim_passes(self):
        validate_adcp_version_pins({"brief": "ads"})

    def test_unsupported_major_raises_version_unsupported(self):
        with pytest.raises(AdCPVersionUnsupportedError) as exc:
            validate_adcp_version_pins({"adcp_major_version": 99})
        assert exc.value.error_code == "VERSION_UNSUPPORTED"
        assert "99" in str(exc.value)
        assert str(adcp_major_version()) in str(exc.value)

    def test_unsupported_release_string_raises_version_unsupported(self):
        """A string adcp_version pin with a future major triggers the same rejection.

        version-envelope.json: the release-precision string is the primary pin;
        the seller "returns VERSION_UNSUPPORTED on cross-major mismatch".
        """
        with pytest.raises(AdCPVersionUnsupportedError) as exc:
            validate_adcp_version_pins({"adcp_version": "4.0"})
        assert exc.value.error_code == "VERSION_UNSUPPORTED"

    def test_details_payload_matches_version_unsupported_schema(self):
        """Details carry the REQUIRED supported_versions plus the schema's optional fields.

        error-details/version-unsupported.json: supported_versions REQUIRED
        (minItems 1); supported_majors deprecated-but-emitted through 3.x;
        build_version advisory; the buyer's pin echoed via the version envelope.
        """
        with pytest.raises(AdCPVersionUnsupportedError) as exc:
            validate_adcp_version_pins({"adcp_version": "4.0"})
        details = exc.value.details
        assert details["supported_versions"] == list(supported_adcp_versions())
        assert len(details["supported_versions"]) >= 1
        assert details["supported_majors"] == [adcp_major_version()]
        assert details["build_version"] == adcp_build_version()
        assert details["adcp_version"] == "4.0"
        assert exc.value.suggestion is not None
        assert "supported_versions" in exc.value.suggestion

    def test_details_payload_validates_against_sdk_details_model(self):
        """Cross-check: the emitted details parse as the SDK's VersionUnsupportedDetails."""
        from adcp.types.generated_poc.error_details.version_unsupported import VersionUnsupportedDetails

        with pytest.raises(AdCPVersionUnsupportedError) as exc:
            validate_adcp_version_pins({"adcp_major_version": 4})
        parsed = VersionUnsupportedDetails.model_validate(exc.value.details)
        assert [v.root for v in parsed.supported_versions] == list(supported_adcp_versions())

    def test_major_pin_echoed_in_details(self):
        with pytest.raises(AdCPVersionUnsupportedError) as exc:
            validate_adcp_version_pins({"adcp_major_version": 4})
        assert exc.value.details["adcp_major_version"] == 4

    def test_below_native_major_rejected(self):
        """A pin below the supported major set is rejected like one above it.

        get_adcp_capabilities.mdx: "the seller validates against its
        major_versions and returns VERSION_UNSUPPORTED if not in range" — the
        membership test is symmetric. This build serves 3.x-shaped responses
        only (the legacy v2-compat layer covers one tool on a subset of
        transports), so accepting a 2-pin while advertising major_versions
        [3] would be self-inconsistent.
        """
        for pin in ({"adcp_major_version": 2}, {"adcp_version": "2.0"}):
            with pytest.raises(AdCPVersionUnsupportedError) as exc:
                validate_adcp_version_pins(pin)
            assert exc.value.error_code == "VERSION_UNSUPPORTED"
            assert exc.value.details["supported_versions"] == list(supported_adcp_versions())

    def test_recovery_is_correctable(self):
        """enums/error-code.json enumMetadata: VERSION_UNSUPPORTED is correctable
        ("re-pin to a release in supported_versions and retry")."""
        with pytest.raises(AdCPVersionUnsupportedError) as exc:
            validate_adcp_version_pins({"adcp_major_version": 99})
        assert exc.value.recovery == "correctable"

    def test_request_context_echoed_on_error(self):
        """The request's context object rides on the error so the envelope echoes it.

        error-compliance storyboard (unsupported_major_version probe) grades
        field_present: context and an unchanged context.correlation_id on the
        error response.
        """
        from src.core.exceptions import build_two_layer_error_envelope

        request_context = {"correlation_id": "corr-123", "conversation_id": "conv-9"}
        with pytest.raises(AdCPVersionUnsupportedError) as exc:
            validate_adcp_version_pins({"adcp_version": "4.0", "context": request_context})
        envelope = build_two_layer_error_envelope(exc.value)
        assert envelope["context"]["correlation_id"] == "corr-123"

    def test_oversized_pin_echo_truncated(self):
        """The echoed pin is buyer-controlled and unbounded — reflect at most 64 chars."""
        huge_pin = "999." + "x" * 5000
        with pytest.raises(AdCPVersionUnsupportedError) as exc:
            validate_adcp_version_pins({"adcp_version": huge_pin})
        assert len(exc.value.details["adcp_version"]) == 64

    def test_same_major_unknown_release_downshifts_without_error(self):
        """version-envelope.json: same-major pins downshift to the served release."""
        validate_adcp_version_pins({"adcp_version": f"{adcp_major_version()}.99"})

    def test_unparseable_claim_tolerated(self):
        # A malformed pin carries no negotiable major; it is stripped with the
        # negotiation envelope rather than misreported as VERSION_UNSUPPORTED.
        validate_adcp_version_pins({"adcp_major_version": "not-a-number"})
        validate_adcp_version_pins({"adcp_version": "not-a-version"})


class TestRESTVersionNegotiation:
    """REST boundary rejects unsupported pins with parity to MCP/A2A (#1512).

    The router-level dependency reads the RAW body/query pin, so the *Body
    models' local ``adcp_version`` defaults never trigger a rejection.
    """

    @staticmethod
    def _client():
        from starlette.testclient import TestClient

        from src.app import app

        return TestClient(app, raise_server_exceptions=False)

    def _assert_version_unsupported(self, response):
        assert response.status_code == 400
        envelope = response.json()
        assert envelope["adcp_error"]["code"] == "VERSION_UNSUPPORTED"
        assert envelope["errors"][0]["details"]["supported_versions"] == list(supported_adcp_versions())

    def test_rest_body_pin_rejected(self):
        response = self._client().post("/api/v1/products", json={"brief": "ads", "adcp_version": "4.0"})
        self._assert_version_unsupported(response)

    def test_rest_body_major_pin_rejected(self):
        response = self._client().post("/api/v1/products", json={"brief": "ads", "adcp_major_version": 4})
        self._assert_version_unsupported(response)

    def test_rest_query_pin_rejected_on_get(self):
        response = self._client().get("/api/v1/capabilities", params={"adcp_version": "4.0"})
        self._assert_version_unsupported(response)


class TestA2ADispatchMajorValidation:
    """A2A dispatch rejects an unsupported pin with parity to the MCP path (#1512)."""

    @pytest.mark.asyncio
    async def test_a2a_dispatch_rejects_unsupported_major(self):
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()
        # get_adcp_capabilities is a discovery skill (no identity required); the
        # version check runs before dispatch, so it raises regardless of handler.
        with pytest.raises(AdCPVersionUnsupportedError):
            await handler._handle_explicit_skill("get_adcp_capabilities", {"adcp_major_version": 99}, None)

    @pytest.mark.asyncio
    async def test_a2a_dispatch_rejects_unsupported_release_string(self):
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()
        with pytest.raises(AdCPVersionUnsupportedError):
            await handler._handle_explicit_skill("get_adcp_capabilities", {"adcp_version": "4.0"}, None)


class TestA2ADispatchStripsEnvelope:
    """A2A dispatch strips negotiation + envelope framing before a handler's strict
    ``model_validate``, so a conformant SDK client is not rejected with extra_forbidden.

    Regression for #1512: every AdCP SDK client injects adcp_version /
    adcp_major_version, and may attach standard envelope framing (``ext``). The
    strict request models (GetMediaBuysRequest, ...) use extra="forbid" in
    dev/CI. Before the fix the A2A path validated the major but left these fields
    in ``parameters``, so ``GetMediaBuysRequest.model_validate(params)`` raised
    extra_forbidden — the MCP middleware strips them, the A2A path did not.
    """

    @pytest.mark.asyncio
    async def test_supported_major_and_envelope_do_not_reject(self):
        from unittest.mock import patch

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from tests.factories.principal import PrincipalFactory

        identity = PrincipalFactory.make_identity(
            principal_id="p1", tenant_id="t1", tenant={"tenant_id": "t1"}, protocol="a2a"
        )
        handler = AdCPRequestHandler()

        # SDK-injected negotiation fields (supported major) + standard envelope
        # framing the GetMediaBuysRequest model does not declare.
        parameters = {
            "adcp_version": "3.1",
            "adcp_major_version": adcp_major_version(),
            "ext": {"vendor": "acme"},
        }

        captured: dict = {}

        def _fake_impl(req, *, identity, include_snapshot):
            # The handler reached model_validate without extra_forbidden and passed
            # a typed request — proof the negotiation + envelope fields were stripped.
            captured["req_type"] = type(req).__name__
            return {"media_buys": []}

        with (
            patch("src.core.tools.media_buy_list._get_media_buys_impl", side_effect=_fake_impl),
            patch.object(AdCPRequestHandler, "_serialize_for_a2a", staticmethod(lambda result: dict(result))),
        ):
            result = await handler._handle_explicit_skill("get_media_buys", parameters, identity)

        assert captured["req_type"] == "GetMediaBuysRequest"
        assert result == {"media_buys": []}

    @pytest.mark.asyncio
    async def test_dropped_negotiation_fields_logged_at_debug(self, caplog):
        """A2A logs the dropped negotiation fields at DEBUG, parity with the MCP middleware.

        The audit trail for a stripped negotiation field must exist on every
        transport, not just MCP — otherwise a silently-dropped field is invisible
        at triage time (#1546).
        """
        import logging
        from unittest.mock import patch

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from tests.factories.principal import PrincipalFactory

        identity = PrincipalFactory.make_identity(
            principal_id="p1", tenant_id="t1", tenant={"tenant_id": "t1"}, protocol="a2a"
        )
        handler = AdCPRequestHandler()
        parameters = {"adcp_version": "3.1", "adcp_major_version": adcp_major_version()}

        with (
            patch("src.core.tools.media_buy_list._get_media_buys_impl", return_value={"media_buys": []}),
            patch.object(AdCPRequestHandler, "_serialize_for_a2a", staticmethod(lambda result: dict(result))),
            caplog.at_level(logging.DEBUG, logger="src.a2a_server.adcp_a2a_server"),
        ):
            await handler._handle_explicit_skill("get_media_buys", parameters, identity)

        drop_logs = [r.message for r in caplog.records if "Dropped AdCP negotiation fields" in r.message]
        assert drop_logs, "A2A did not log the dropped negotiation fields at DEBUG"
        assert "adcp_version" in drop_logs[0] and "adcp_major_version" in drop_logs[0]
