"""Regression tests for salesagent-rldj (C4: version negotiation + idempotency posture).

Pins the CORE behavior from the refined implementation plan, steps 1-4:

1. ``SUPPORTED_ADCP_VERSIONS`` (new ``src/core/version_negotiation.py``) is
   derived from ``adcp.get_adcp_spec_version()`` STRIPPED to release
   precision (MAJOR.MINOR, e.g. "3.1"), never the raw 3-part semver
   ("3.1.1") which violates the v3.1.1 ``supported_versions`` wire pattern
   (``^\\d+\\.\\d+(-...)?$``).
2. ``negotiate_adcp_version()`` raises the new ``AdCPVersionUnsupportedError``
   (-> wire code ``VERSION_UNSUPPORTED``) for a version pin outside
   ``SUPPORTED_ADCP_VERSIONS``, and is a no-op for a supported pin / None.
3. ``_get_adcp_capabilities_impl`` calls negotiation FIRST and lets the error
   propagate for a bad ``adcp_version`` pin, even on the no-tenant path.
4. The DRY ``_build_adcp_block()`` helper derives ``supported_versions`` from
   the single-sourced constant on BOTH the minimal (no-tenant) and full
   (tenant-resolved) response paths -- no literal duplication.

Does NOT cover plan step 5 (harness override seam) or step 6 (BDD step
authoring) -- that is separate implementation-atom scope.
"""

from __future__ import annotations

import re

import pytest


class TestSupportedAdcpVersionsDerivation:
    """Plan step 1: SUPPORTED_ADCP_VERSIONS must be release-precision, derived."""

    def test_supported_adcp_versions_are_release_precision(self):
        """Every entry must match the v3.1.1 SupportedVersion wire pattern
        (release precision, i.e. MAJOR.MINOR only -- NOT MAJOR.MINOR.PATCH).

        adcp.get_adcp_spec_version() returns "3.1.1" today (verified via
        direct interpreter check per salesagent-rldj notes) -- a raw
        pass-through would produce "3.1.1", which FAILS this pattern.
        """
        from src.core.version_negotiation import SUPPORTED_ADCP_VERSIONS

        release_precision_pattern = re.compile(r"^\d+\.\d+(-[a-zA-Z0-9.-]+)?$")
        assert len(SUPPORTED_ADCP_VERSIONS) >= 1
        for version in SUPPORTED_ADCP_VERSIONS:
            assert release_precision_pattern.match(version), (
                f"{version!r} is not release-precision (MAJOR.MINOR) -- "
                "did SUPPORTED_ADCP_VERSIONS pass the raw semver through unstripped?"
            )

    def test_supported_adcp_versions_derived_from_installed_sdk_stripped_to_release(self):
        """Pins the EXACT derivation: strip adcp.get_adcp_spec_version() to
        its first two dot-separated components, not a hardcoded literal.
        """
        import adcp

        from src.core.version_negotiation import SUPPORTED_ADCP_VERSIONS

        full_spec_version = adcp.get_adcp_spec_version()
        expected_release = ".".join(full_spec_version.split(".")[:2])

        assert expected_release in SUPPORTED_ADCP_VERSIONS


class TestNegotiateAdcpVersion:
    """Plan step 1: negotiate_adcp_version() raises for unsupported pins."""

    def test_rejects_unsupported_version_pin(self):
        from src.core.exceptions import AdCPVersionUnsupportedError
        from src.core.version_negotiation import negotiate_adcp_version

        with pytest.raises(AdCPVersionUnsupportedError) as exc_info:
            negotiate_adcp_version("0.1", None)

        err = exc_info.value
        assert err.error_code == "VERSION_UNSUPPORTED"
        assert err.status_code == 400
        assert err.recovery == "correctable"

    def test_accepts_supported_version_pin_as_noop(self):
        from src.core.version_negotiation import SUPPORTED_ADCP_VERSIONS, negotiate_adcp_version

        supported = SUPPORTED_ADCP_VERSIONS[0]
        # Must not raise.
        assert negotiate_adcp_version(supported, None) is None

    def test_no_pin_requested_is_noop(self):
        from src.core.version_negotiation import negotiate_adcp_version

        # Buyer sent no version/major pin at all -- must not raise.
        assert negotiate_adcp_version(None, None) is None


class TestCapabilitiesImplVersionNegotiation:
    """Plan step 2: _get_adcp_capabilities_impl negotiates FIRST, before the
    no-tenant branch, and lets AdCPVersionUnsupportedError propagate.
    """

    def test_bad_version_pin_raises_even_without_tenant(self):
        from src.core.config_loader import current_tenant
        from src.core.exceptions import AdCPVersionUnsupportedError
        from src.core.tools.capabilities import (
            _get_adcp_capabilities_impl,
            build_get_adcp_capabilities_request,
        )

        current_tenant.set(None)
        req = build_get_adcp_capabilities_request(adcp_version="0.1")

        with pytest.raises(AdCPVersionUnsupportedError):
            _get_adcp_capabilities_impl(req, None)


class TestBuildAdcpBlockDry:
    """Plan step 3-4: _build_adcp_block() single-sources supported_versions
    across BOTH the no-tenant minimal response and the tenant-resolved full
    response -- no literal Adcp(...) duplication.
    """

    def test_minimal_no_tenant_response_declares_derived_supported_versions(self):
        from src.core.config_loader import current_tenant
        from src.core.tools.capabilities import _get_adcp_capabilities_impl
        from src.core.version_negotiation import SUPPORTED_ADCP_VERSIONS

        current_tenant.set(None)
        response = _get_adcp_capabilities_impl(None, None)

        assert response.adcp.supported_versions is not None
        assert [v.root for v in response.adcp.supported_versions] == SUPPORTED_ADCP_VERSIONS

    def test_full_tenant_response_declares_same_derived_supported_versions(self):
        from src.core.config_loader import current_tenant
        from src.core.tools.capabilities import _get_adcp_capabilities_impl
        from src.core.version_negotiation import SUPPORTED_ADCP_VERSIONS
        from tests.unit.test_get_adcp_capabilities import (
            _make_capabilities_identity,
            _patch_capabilities_deps,
        )

        identity = _make_capabilities_identity(principal_id=None, tenant_id="test-tenant-version-negotiation")
        current_tenant.set(identity.tenant)

        try:
            with _patch_capabilities_deps(adapter=None):
                response = _get_adcp_capabilities_impl(None, identity)

            assert response.adcp.supported_versions is not None
            assert [v.root for v in response.adcp.supported_versions] == SUPPORTED_ADCP_VERSIONS
        finally:
            current_tenant.set(None)
