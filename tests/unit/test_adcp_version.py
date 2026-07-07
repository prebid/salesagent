"""Unit tests for src/core/adcp_version.py — SDK-derived AdCP version."""

import adcp
import pytest

from src.core.adcp_version import adcp_major_version, validate_adcp_major_version
from src.core.exceptions import AdCPVersionUnsupportedError


def test_major_version_derived_from_sdk_spec_pin():
    """The advertised major is parsed from the SDK's spec version, not hardcoded."""
    expected = int(adcp.get_adcp_spec_version().split(".", 1)[0])
    assert adcp_major_version() == expected


def test_major_version_is_current_pinned_major():
    """Repo pins AdCP 3.x (see docs/adcp-spec-version.md). Guards a stray cross-major bump."""
    assert adcp_major_version() == 3


class TestValidateAdcpMajorVersion:
    """adcp_major_version negotiation — reject unsupported majors (#1512 Tier 2)."""

    def test_supported_major_passes(self):
        validate_adcp_major_version({"adcp_major_version": adcp_major_version(), "brief": "ads"})

    def test_absent_claim_passes(self):
        validate_adcp_major_version({"brief": "ads"})

    def test_unsupported_major_raises_version_unsupported(self):
        with pytest.raises(AdCPVersionUnsupportedError) as exc:
            validate_adcp_major_version({"adcp_major_version": 99})
        assert exc.value.error_code == "VERSION_UNSUPPORTED"
        assert "99" in str(exc.value)
        assert str(adcp_major_version()) in str(exc.value)

    def test_older_major_raises(self):
        with pytest.raises(AdCPVersionUnsupportedError):
            validate_adcp_major_version({"adcp_major_version": 2})

    def test_non_integer_claim_deferred_to_schema_validation(self):
        # A malformed value is not our job to reject-as-unsupported; let schema catch it.
        validate_adcp_major_version({"adcp_major_version": "not-a-number"})


class TestA2ADispatchMajorValidation:
    """A2A dispatch rejects an unsupported major with parity to the MCP path (#1512)."""

    @pytest.mark.asyncio
    async def test_a2a_dispatch_rejects_unsupported_major(self):
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()
        # get_adcp_capabilities is a discovery skill (no identity required); the
        # version check runs before dispatch, so it raises regardless of handler.
        with pytest.raises(AdCPVersionUnsupportedError):
            await handler._handle_explicit_skill("get_adcp_capabilities", {"adcp_major_version": 99}, None)


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
