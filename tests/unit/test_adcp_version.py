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
