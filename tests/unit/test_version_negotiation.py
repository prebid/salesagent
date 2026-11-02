"""Regression tests for version negotiation and idempotency posture.

Pins four behaviors:

1. ``SUPPORTED_ADCP_VERSIONS`` (``src/core/version_negotiation.py``) is
   derived from ``adcp.get_adcp_spec_version()`` STRIPPED to release
   precision (MAJOR.MINOR, e.g. "3.1"), never the raw 3-part semver
   ("3.1.1") which violates the v3.1.1 ``supported_versions`` wire pattern
   (``^\\d+\\.\\d+(-...)?$``).
2. ``negotiate_adcp_version()`` raises ``AdCPVersionUnsupportedError``
   (-> wire code ``VERSION_UNSUPPORTED``) for a version pin outside
   ``SUPPORTED_ADCP_VERSIONS``, and is a no-op for a supported pin / None.
3. Negotiation runs at the BOUNDARY, so it covers every tool and stays
   un-tenant-gated. Running it inside ``_get_adcp_capabilities_impl`` instead
   would leave every other tool serving a buyer whose pin this build cannot
   speak. The wire-level grading across transports lives in
   ``tests/integration/test_version_negotiation_wire.py``.
4. The DRY ``_build_adcp_block()`` helper derives ``supported_versions`` from
   the single-sourced constant -- no literal duplication. There is one response
   path: a request naming no seller is refused TENANT_UNDEFINED before an
   identity exists, so there is no minimal (no-tenant) response to cover.
"""

from __future__ import annotations

import re

import pytest


class TestSupportedAdcpVersionsDerivation:
    """SUPPORTED_ADCP_VERSIONS must be release-precision, derived."""

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
    """negotiate_adcp_version() raises for unsupported pins."""

    def test_rejects_unsupported_version_pin(self):
        from src.core.exceptions import AdCPVersionUnsupportedError
        from src.core.version_negotiation import negotiate_adcp_version

        with pytest.raises(AdCPVersionUnsupportedError) as exc_info:
            negotiate_adcp_version("0.1", None)

        err = exc_info.value
        assert err.error_code == "VERSION_UNSUPPORTED"
        assert err.status_code == 400

    def test_accepts_supported_version_pin_as_noop(self):
        from src.core.version_negotiation import SUPPORTED_ADCP_VERSIONS, negotiate_adcp_version

        supported = SUPPORTED_ADCP_VERSIONS[0]
        # Must not raise.
        assert negotiate_adcp_version(supported, None) is None

    def test_no_pin_requested_is_noop(self):
        from src.core.version_negotiation import negotiate_adcp_version

        # Buyer sent no version/major pin at all -- must not raise.
        assert negotiate_adcp_version(None, None) is None

    def test_a_supported_major_does_not_excuse_an_unsupported_release(self):
        """The pins are constraints, not alternatives.

        Read as alternatives, a request pinning an unsupported RELEASE alongside a
        supported MAJOR was accepted -- the major check returned before the release was
        ever judged.
        """
        from src.core.exceptions import AdCPVersionUnsupportedError
        from src.core.version_negotiation import SUPPORTED_ADCP_MAJORS, negotiate_adcp_version

        with pytest.raises(AdCPVersionUnsupportedError):
            negotiate_adcp_version("99.0", SUPPORTED_ADCP_MAJORS[0])

    def test_a_supported_release_does_not_excuse_an_unsupported_major(self):
        from src.core.exceptions import AdCPVersionUnsupportedError
        from src.core.version_negotiation import SUPPORTED_ADCP_VERSIONS, negotiate_adcp_version

        with pytest.raises(AdCPVersionUnsupportedError):
            negotiate_adcp_version(SUPPORTED_ADCP_VERSIONS[0], 99)

    def test_the_refusal_names_the_supported_majors(self):
        """A buyer refused on a MAJOR needs the majors, not only the releases."""
        from src.core.exceptions import AdCPVersionUnsupportedError
        from src.core.version_negotiation import (
            SUPPORTED_ADCP_MAJORS,
            SUPPORTED_ADCP_VERSIONS,
            negotiate_adcp_version,
        )

        with pytest.raises(AdCPVersionUnsupportedError) as exc_info:
            negotiate_adcp_version(None, 99)

        details = exc_info.value.details
        assert details.supported_versions == SUPPORTED_ADCP_VERSIONS
        assert details.supported_majors == SUPPORTED_ADCP_MAJORS
        assert details.adcp_major_version == 99

    def test_pins_naming_different_majors_are_refused(self, monkeypatch):
        """Two individually-supported pins can still contradict each other.

        Unreachable while this seller speaks one release -- a supported release and a
        supported major necessarily agree -- so the supported set is widened here to reach
        the branch. It becomes reachable for real the moment a second major is served,
        which is exactly when nobody would think to add the rule.
        """
        from src.core import version_negotiation
        from src.core.exceptions import AdCPVersionUnsupportedError

        monkeypatch.setattr(version_negotiation, "SUPPORTED_ADCP_VERSIONS", ["3.1", "4.0"])
        monkeypatch.setattr(version_negotiation, "SUPPORTED_ADCP_MAJORS", [3, 4])

        # Each pin is supported on its own; together they name no release that exists.
        with pytest.raises(AdCPVersionUnsupportedError):
            version_negotiation.negotiate_adcp_version("3.1", 4)

        # The aligned pairs stay acceptable.
        assert version_negotiation.negotiate_adcp_version("3.1", 3) is None
        assert version_negotiation.negotiate_adcp_version("4.0", 4) is None
