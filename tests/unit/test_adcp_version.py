"""Unit tests for src/core/adcp_version.py — SDK-derived AdCP version."""

import adcp
import pytest

from src.core.adcp_version import (
    adcp_build_version,
    adcp_major_version,
    supported_adcp_versions,
    validate_adcp_version_pins,
)
from src.core.exceptions import AdCPConfigurationError, AdCPValidationError, AdCPVersionUnsupportedError


def _request_compat_log_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return only records emitted by the negotiation/envelope logger."""
    return [record.message for record in caplog.records if record.name == "src.core.request_compat"]


def _install_example_testing_policy(adcp_version_module, lease_id: str) -> None:
    """Install the canonical valid snapshot used by lease/control tests."""
    assert adcp_version_module._install_testing_version_policy(
        lease_id=lease_id,
        supported_versions=("3.0", "3.1"),
        build_version="3.1.2+e2e.1",
    )


@pytest.fixture
def testing_version_policy_control(monkeypatch):
    """Provide one owned live-policy lease and guarantee global teardown."""
    from src.core import adcp_version as av

    lease_id = "bdd_policy_lease_0123456789abcdef"
    monkeypatch.setenv("ADCP_TESTING", "true")
    yield av, lease_id
    assert av._reset_testing_version_policy(lease_id=lease_id)


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


class TestTestingVersionPolicyControl:
    """The separate-server E2E setup seam is atomic, leased, and test-only."""

    def test_install_and_owner_reset_restore_sdk_defaults(self, testing_version_policy_control):
        av, lease_id = testing_version_policy_control
        default_versions = av.supported_adcp_versions()
        default_build = av.adcp_build_version()

        _install_example_testing_policy(av, lease_id)
        assert av.supported_adcp_versions() == ("3.0", "3.1")
        assert av.adcp_build_version() == "3.1.2+e2e.1"

        assert av._reset_testing_version_policy(lease_id=lease_id)
        assert av.supported_adcp_versions() == default_versions
        assert av.adcp_build_version() == default_build

    @pytest.mark.parametrize(
        ("candidate_lease", "candidate_versions", "candidate_build"),
        [
            ("bdd_policy_lease_0123456789abcdef", ("3.1.0",), "3.1.2+e2e.1"),
            ("bdd_policy_lease_0123456789abcdef", ("3.1",), "not-semver"),
            ("bad lease", ("3.1",), "3.1.2+e2e.1"),
        ],
        ids=["malformed-release", "malformed-build", "malformed-lease"],
    )
    def test_invalid_update_preserves_installed_snapshot(
        self,
        testing_version_policy_control,
        candidate_lease,
        candidate_versions,
        candidate_build,
    ):
        av, lease_id = testing_version_policy_control
        _install_example_testing_policy(av, lease_id)

        with pytest.raises(AdCPConfigurationError):
            av._install_testing_version_policy(
                lease_id=candidate_lease,
                supported_versions=candidate_versions,
                build_version=candidate_build,
            )

        assert av.supported_adcp_versions() == ("3.0", "3.1")
        assert av.adcp_build_version() == "3.1.2+e2e.1"

    def test_other_lease_cannot_replace_or_reset_snapshot(self, testing_version_policy_control):
        av, lease_id = testing_version_policy_control
        other_lease = "other_policy_lease_fedcba9876543210"
        _install_example_testing_policy(av, lease_id)

        assert not av._install_testing_version_policy(
            lease_id=other_lease,
            supported_versions=("3.1", "3.2"),
            build_version="3.2.0",
        )
        assert not av._reset_testing_version_policy(lease_id=other_lease)
        assert av.supported_adcp_versions() == ("3.0", "3.1")

    def test_install_is_blocked_outside_testing_mode(self, monkeypatch):
        from src.core import adcp_version as av

        monkeypatch.delenv("ADCP_TESTING", raising=False)
        with pytest.raises(PermissionError):
            av._install_testing_version_policy(
                lease_id="bdd_policy_lease_0123456789abcdef",
                supported_versions=("3.1",),
                build_version="3.1.0",
            )


@pytest.mark.parametrize(
    "malformed_pin",
    ["banana", None, 31, "3.1", "3.1.x", "03.01.0", "3.1.0-01", "3.1.0-."],
)
def test_malformed_sdk_spec_pin_raises_typed_configuration_error(monkeypatch, malformed_pin):
    """A malformed SDK spec pin surfaces as a typed AdCPConfigurationError, not a bare ValueError.

    ``supported_adcp_versions()`` runs on the buyer request path
    (``validate_adcp_version_pins`` -> ``_supported_majors``). A broken seller
    deployment (unparseable spec pin) must surface as a typed 500 like every
    other server-side failure, never escape as an untyped unpack/int ValueError.
    """
    from src.core import adcp_version as av
    from src.core.exceptions import AdCPConfigurationError

    av._spec_major_minor.cache_clear()
    monkeypatch.setattr(adcp, "get_adcp_spec_version", lambda: malformed_pin)
    try:
        # Both siblings degrade the same way, and the request-path entry point does too.
        for call in (av.adcp_major_version, av.supported_adcp_versions, av.adcp_build_version):
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
        """Versioning & Governance: a different major is VERSION_UNSUPPORTED."""
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
        """Bound the message without corrupting the schema-valid details echo."""
        huge_pin = "9" * 5000 + ".0"
        with pytest.raises(AdCPVersionUnsupportedError) as exc:
            validate_adcp_version_pins({"adcp_version": huge_pin})
        assert exc.value.details["adcp_version"] == huge_pin
        assert huge_pin not in str(exc.value)

        from adcp.types.generated_poc.error_details.version_unsupported import VersionUnsupportedDetails

        VersionUnsupportedDetails.model_validate(exc.value.details)

    def test_same_major_unknown_release_downshifts_without_error(self):
        """A stable same-major pin downshifts to the highest supported older release."""
        validate_adcp_version_pins({"adcp_version": f"{adcp_major_version()}.99"})

    def test_stable_release_downshifts_across_minor_gap(self, monkeypatch):
        """Resolution need not be adjacent: 3.4 may select the highest stable <= 3.4."""
        from src.core import adcp_version as av

        monkeypatch.setattr(av, "supported_adcp_versions", lambda: ("3.1", "3.3"))
        validate_adcp_version_pins({"adcp_version": "3.4"})

        claimed = av._parse_release_pin("3.4")
        assert claimed is not None
        resolved = av._resolve_release_pin(claimed, av._supported_releases())
        assert resolved is not None
        assert resolved.raw == "3.3"

    def test_stable_release_below_minimum_is_unsupported(self, monkeypatch):
        """A same-major pin older than every seller release cannot downshift upward."""
        from src.core import adcp_version as av

        monkeypatch.setattr(av, "supported_adcp_versions", lambda: ("3.1", "3.3"))
        with pytest.raises(AdCPVersionUnsupportedError) as exc:
            validate_adcp_version_pins({"adcp_version": "3.0"})
        assert exc.value.details["supported_versions"] == ["3.1", "3.3"]

    def test_prerelease_requires_exact_match(self, monkeypatch):
        """Prereleases never range-resolve, even when a stable same-major release exists."""
        from src.core import adcp_version as av

        monkeypatch.setattr(av, "supported_adcp_versions", lambda: ("3.1", "3.2-beta.1"))
        validate_adcp_version_pins({"adcp_version": "3.2-beta.1"})
        with pytest.raises(AdCPVersionUnsupportedError):
            validate_adcp_version_pins({"adcp_version": "3.2-beta.2"})

    def test_stable_exact_match_wins_and_never_downshifts_to_prerelease(self, monkeypatch):
        """A stable release pin selects its exact stable entry when both forms exist."""
        from src.core import adcp_version as av

        monkeypatch.setattr(av, "supported_adcp_versions", lambda: ("3.1-beta", "3.1"))
        claimed = av._parse_release_pin("3.1")
        assert claimed is not None
        resolved = av._resolve_release_pin(claimed, av._supported_releases())
        assert resolved is not None
        assert resolved.raw == "3.1"

    def test_stable_pin_never_downshifts_onto_prerelease(self, monkeypatch):
        """Without a stable exact match, only older stable releases are eligible."""
        from src.core import adcp_version as av

        claimed = av._parse_release_pin("3.1")
        assert claimed is not None

        monkeypatch.setattr(av, "supported_adcp_versions", lambda: ("3.0", "3.1-beta"))
        resolved = av._resolve_release_pin(claimed, av._supported_releases())
        assert resolved is not None
        assert resolved.raw == "3.0"

        monkeypatch.setattr(av, "supported_adcp_versions", lambda: ("3.1-beta",))
        assert av._resolve_release_pin(claimed, av._supported_releases()) is None
        with pytest.raises(AdCPVersionUnsupportedError):
            validate_adcp_version_pins({"adcp_version": "3.1"})

    def test_attacker_sized_numeric_components_resolve_without_bare_value_error(self):
        """Schema-valid digit strings must not trip Python's int conversion guard."""
        huge_digits = "9" * 5000

        # Same-major future release safely downshifts to this seller's release.
        validate_adcp_version_pins({"adcp_version": f"3.{huge_digits}"})

        # A huge different major is still a typed VERSION_UNSUPPORTED rejection.
        with pytest.raises(AdCPVersionUnsupportedError) as exc:
            validate_adcp_version_pins({"adcp_version": f"{huge_digits}.0"})
        assert exc.value.details["adcp_version"] == f"{huge_digits}.0"
        assert f"{huge_digits}.0" not in str(exc.value)

    def test_conflicting_dual_pins_are_unsupported(self):
        """Through 3.x clients may emit both pins, but their major components must agree."""
        with pytest.raises(AdCPVersionUnsupportedError) as exc:
            validate_adcp_version_pins({"adcp_version": "3.1", "adcp_major_version": 4})
        assert exc.value.details["adcp_version"] == "3.1"
        assert exc.value.details["adcp_major_version"] == 4

    def test_agreeing_dual_pins_pass(self):
        validate_adcp_version_pins(
            {
                "adcp_version": supported_adcp_versions()[0],
                "adcp_major_version": adcp_major_version(),
            }
        )

    def test_malformed_pin_rejected_as_validation_error(self):
        """A present-but-unparseable pin is a VALIDATION_ERROR, not silently stripped.

        version-envelope.json constrains adcp_version to ^\\d+\\.\\d+(-...)?$ and
        types adcp_major_version as an integer; the spec defines a fallback only
        for an OMITTED pin. A garbage value is a malformed request, so it must
        surface a typed VALIDATION_ERROR (correctable) rather than be treated as
        absent — which would erase a protocol-version claim the client made.
        """
        malformed_pins = (
            {"adcp_major_version": "3"},
            {"adcp_major_version": 3.0},
            {"adcp_major_version": 3.5},
            {"adcp_major_version": True},
            {"adcp_major_version": 0},
            {"adcp_major_version": 100},
            {"adcp_major_version": None},
            {"adcp_version": "banana"},
            {"adcp_version": "3"},
            {"adcp_version": "3.1.0"},
            {"adcp_version": "3."},
            {"adcp_version": "3.beta"},
            {"adcp_version": None},
        )
        for pin in malformed_pins:
            with pytest.raises(AdCPValidationError) as exc:
                validate_adcp_version_pins(pin)
            assert exc.value.error_code == "VALIDATION_ERROR"
            assert exc.value.recovery == "correctable"

    def test_malformed_pin_echo_truncated(self):
        """The malformed value echoed in the error is capped (buyer-controlled, unbounded)."""
        with pytest.raises(AdCPValidationError) as exc:
            validate_adcp_version_pins({"adcp_version": "z" * 5000})
        assert len(exc.value.details["adcp_version"]) == 64

    def test_malformed_pin_preserves_request_context(self):
        from src.core.exceptions import build_two_layer_error_envelope

        context = {"correlation_id": "corr-malformed"}
        with pytest.raises(AdCPValidationError) as exc:
            validate_adcp_version_pins({"adcp_version": "3.1.0", "context": context})
        assert build_two_layer_error_envelope(exc.value)["context"] == context

    @pytest.mark.parametrize("supported", [(), ("banana",), ("3.1.0",), ("0.1",), (None,)])
    def test_invalid_seller_supported_versions_are_configuration_errors(self, monkeypatch, supported):
        """Bad seller configuration is a typed terminal server error, never a buyer mismatch."""
        from src.core import adcp_version as av

        monkeypatch.setattr(av, "supported_adcp_versions", lambda: supported)
        with pytest.raises(AdCPConfigurationError) as exc:
            validate_adcp_version_pins({"adcp_version": "4.0"})
        assert exc.value.error_code == "CONFIGURATION_ERROR"
        assert exc.value.recovery == "terminal"

    def test_supported_versions_are_read_once_per_validation(self, monkeypatch):
        """The decision and recovery details use one validated configuration snapshot."""
        from src.core import adcp_version as av

        reads: list[int] = []

        def _configured_versions() -> tuple[str, ...]:
            reads.append(1)
            return ("3.1",)

        monkeypatch.setattr(av, "supported_adcp_versions", _configured_versions)
        with pytest.raises(AdCPVersionUnsupportedError) as exc:
            validate_adcp_version_pins({"adcp_version": "4.0"})

        assert reads == [1]
        assert exc.value.details["supported_versions"] == ["3.1"]


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

    def _assert_version_unsupported(self, response, expected_versions=None):
        assert response.status_code == 400
        envelope = response.json()
        assert envelope["adcp_error"]["code"] == "VERSION_UNSUPPORTED"
        expected = list(supported_adcp_versions()) if expected_versions is None else expected_versions
        assert envelope["errors"][0]["details"]["supported_versions"] == expected

    def test_rest_body_pin_rejected(self):
        response = self._client().post(
            "/api/v1/products",
            json={
                "brief": "ads",
                "adcp_version": "4.0",
                "context": {"correlation_id": "rest-version-context"},
            },
        )
        self._assert_version_unsupported(response)
        assert response.json()["context"]["correlation_id"] == "rest-version-context"

    def test_rest_body_major_pin_rejected(self):
        response = self._client().post("/api/v1/products", json={"brief": "ads", "adcp_major_version": 4})
        self._assert_version_unsupported(response)

    def test_rest_json_body_rejects_integral_float_major_pin(self):
        """Only A2A repairs protobuf's integer-as-double loss; JSON remains strict."""
        from tests.helpers.envelope_assertions import assert_envelope_shape

        response = self._client().post("/api/v1/products", json={"brief": "ads", "adcp_major_version": 3.0})

        assert response.status_code == 400
        assert_envelope_shape(response.json(), "VALIDATION_ERROR", recovery="correctable")

    def test_rest_query_pin_rejected_on_get(self):
        response = self._client().get("/api/v1/capabilities", params={"adcp_version": "4.0"})
        self._assert_version_unsupported(response)

    def test_rest_query_major_pin_is_coerced_then_rejected(self):
        """The URL's textual integer representation reaches the strict core as an int."""
        response = self._client().get("/api/v1/capabilities", params={"adcp_major_version": 4})
        self._assert_version_unsupported(response)
        assert response.json()["errors"][0]["details"]["adcp_major_version"] == 4

    def test_rest_query_malformed_major_pin_is_validation_error(self):
        from tests.helpers.envelope_assertions import assert_envelope_shape

        response = self._client().get("/api/v1/capabilities", params={"adcp_major_version": "4.0"})
        assert response.status_code == 400
        assert_envelope_shape(response.json(), "VALIDATION_ERROR", recovery="correctable")

    def test_rest_combines_query_and_body_before_dual_pin_validation(self, monkeypatch):
        """Split-location pins cannot bypass disagreement checks on a multi-major seller."""
        from src.core import adcp_version as av

        monkeypatch.setattr(av, "supported_adcp_versions", lambda: ("3.1", "4.0"))
        response = self._client().post(
            "/api/v1/products",
            params={"adcp_major_version": 3},
            json={"brief": "ads", "adcp_version": "4.0"},
        )

        self._assert_version_unsupported(response, expected_versions=["3.1", "4.0"])
        details = response.json()["errors"][0]["details"]
        assert details["adcp_major_version"] == 3
        assert details["adcp_version"] == "4.0"

    def test_rest_rejects_conflicting_duplicate_pin_locations(self):
        from tests.helpers.envelope_assertions import assert_envelope_shape

        response = self._client().post(
            "/api/v1/products",
            params={"adcp_version": "3.1"},
            json={
                "brief": "ads",
                "adcp_version": "4.0",
                "context": {"correlation_id": "rest-duplicate-context"},
            },
        )

        assert response.status_code == 400
        assert_envelope_shape(response.json(), "VALIDATION_ERROR", recovery="correctable")
        assert response.json()["context"]["correlation_id"] == "rest-duplicate-context"


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

    def test_a2a_restores_integral_protobuf_major_without_mutating_input(self):
        from src.a2a_server.adcp_a2a_server import _restore_a2a_integer_version_pin

        original = {"adcp_major_version": 3.0, "brief": "ads"}

        restored = _restore_a2a_integer_version_pin(original)

        assert restored == {"adcp_major_version": 3, "brief": "ads"}
        assert type(restored["adcp_major_version"]) is int
        assert restored is not original
        assert original["adcp_major_version"] == 3.0

    @pytest.mark.parametrize("major", [3.5, float("inf"), float("nan"), True, "3", None])
    def test_a2a_preserves_non_integral_or_invalid_major_for_core_rejection(self, major):
        from src.a2a_server.adcp_a2a_server import _restore_a2a_integer_version_pin

        original = {"adcp_major_version": major}

        assert _restore_a2a_integer_version_pin(original) is original


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
    async def test_supported_major_and_envelope_do_not_reject(self, caplog):
        import logging
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
            caplog.at_level(logging.DEBUG, logger="src.core.request_compat"),
        ):
            result = await handler._handle_explicit_skill("get_media_buys", parameters, identity)

        assert captured["req_type"] == "GetMediaBuysRequest"
        assert result == {"media_buys": []}
        assert _request_compat_log_messages(caplog) == [
            "Dropped AdCP negotiation fields from get_media_buys: adcp_major_version, adcp_version",
            "Dropped undeclared AdCP envelope fields from get_media_buys: ext",
        ]

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
            caplog.at_level(logging.DEBUG, logger="src.core.request_compat"),
        ):
            await handler._handle_explicit_skill("get_media_buys", parameters, identity)

        assert _request_compat_log_messages(caplog) == [
            "Dropped AdCP negotiation fields from get_media_buys: adcp_major_version, adcp_version"
        ]
