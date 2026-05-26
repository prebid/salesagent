"""Unit tests for the boundary-layer additions in ``core/platforms/_delegate``:

* ``_check_major_version`` — reject unknown ``adcp_major_version`` with
  spec-canonical ``VERSION_UNSUPPORTED`` envelope (issue #348C).
* ``_maybe_raise_legacy_errors`` — translate the legacy success-shaped
  ``{"errors": [...]}`` wrapper into a framework ``AdcpError`` raise so the
  dispatcher emits the AdCP 3.0.11 ``adcp_error`` envelope (issue #349.1).

Verified end-to-end via the ``error_compliance / unsupported_major_version``
and ``schema_validation / reversed_dates`` storyboard probes — the probe
artifacts are in ``.context/probe-verify/``. The unit tests below pin the
helpers' contracts so future refactors don't silently regress the wire shape.
"""

from __future__ import annotations

import json

import pytest
from adcp.decisioning import AdcpError
from pydantic import BaseModel, field_validator

from core.platforms._delegate import (
    _check_major_version,
    _maybe_raise_legacy_errors,
    _resolve_requested_version,
    _to_wire,
    _translate_validation_error,
)
from src.core.schemas import CreateMediaBuySuccess, MediaBuyStatus, UpdateMediaBuySuccess
from src.core.schemas._base import CreateMediaBuyResult


class TestCheckMajorVersion:
    """``_check_major_version`` enforces version negotiation per AdCP 3.0."""

    def test_allows_none(self) -> None:
        """Omitted version → seller assumes its highest supported version."""
        _check_major_version({"adcp_major_version": None})  # no raise

    def test_allows_missing_attribute(self) -> None:
        """Request without the field → no version assertion → no raise."""

        class NoVersion:
            pass

        _check_major_version(NoVersion())  # no raise

    def test_allows_supported_version(self) -> None:
        """Major version 3 is in the supported set → no raise."""
        _check_major_version({"adcp_major_version": 3})  # no raise

    def test_rejects_unsupported_version_on_dict(self) -> None:
        """Buyer sending v99 on a dict payload gets VERSION_UNSUPPORTED."""
        with pytest.raises(AdcpError) as exc:
            _check_major_version({"adcp_major_version": 99})
        assert exc.value.code == "VERSION_UNSUPPORTED"
        assert exc.value.field == "adcp_major_version"

    def test_rejects_unsupported_version_on_attribute(self) -> None:
        """Buyer sending v2 on a typed request gets VERSION_UNSUPPORTED."""

        class TypedReq:
            adcp_major_version = 2

        with pytest.raises(AdcpError) as exc:
            _check_major_version(TypedReq())
        assert exc.value.code == "VERSION_UNSUPPORTED"

    def test_ignores_non_int_value(self) -> None:
        """Mock objects / malformed values must NOT trigger VERSION_UNSUPPORTED.

        The check fires before request-coercion runs, so a test that hands
        the delegate a MagicMock for ``ctx`` would have every attribute
        truthy — including ``adcp_major_version``. Treat non-int as
        "field not present" rather than version 0/1/-1.
        """

        class MockShaped:
            adcp_major_version = object()  # not int, not None

        _check_major_version(MockShaped())  # no raise

    def test_ignores_bool_value(self) -> None:
        """``bool`` is an ``int`` subclass — reject True/False explicitly so
        ``adcp_major_version: True`` doesn't read as version 1.
        """

        class BoolShaped:
            adcp_major_version = True

        _check_major_version(BoolShaped())  # no raise


class TestRequestedVersionResolution:
    """The delegate follows the SDK's release-precision negotiation contract."""

    def test_omitted_version_defaults_to_legacy_3_0(self) -> None:
        assert _resolve_requested_version({}) == "3.0"

    def test_explicit_3_1_beta_is_preserved(self) -> None:
        assert _resolve_requested_version({"adcp_version": "3.1.0-beta.3"}) == "3.1-beta.3"

    def test_unsupported_version_raises_wire_error(self) -> None:
        with pytest.raises(AdcpError) as exc:
            _resolve_requested_version({"adcp_version": "4.0"})
        assert exc.value.code == "VERSION_UNSUPPORTED"
        assert exc.value.field == "adcp_version"


class TestRequestedVersionWireAdaptation:
    """Media-buy success responses adapt status fields to the requested release."""

    def test_create_media_buy_3_0_uses_legacy_status_field(self) -> None:
        response = CreateMediaBuyResult(
            response=CreateMediaBuySuccess(
                media_buy_id="mb_1",
                packages=[],
                media_buy_status=MediaBuyStatus.pending_creatives,
            ),
            status="completed",
        )

        wire = _to_wire(response, requested_adcp_version="3.0", tool_name="create_media_buy")

        assert wire["status"] == "pending_creatives"
        assert "media_buy_status" not in wire

    def test_create_media_buy_3_1_keeps_envelope_and_lifecycle_fields(self) -> None:
        response = CreateMediaBuyResult(
            response=CreateMediaBuySuccess(
                media_buy_id="mb_1",
                packages=[],
                media_buy_status=MediaBuyStatus.pending_creatives,
            ),
            status="completed",
        )

        wire = _to_wire(response, requested_adcp_version="3.1-beta.3", tool_name="create_media_buy")

        assert wire["status"] == "completed"
        assert wire["media_buy_status"] == "pending_creatives"

    def test_update_media_buy_3_0_uses_legacy_status_field(self) -> None:
        response = UpdateMediaBuySuccess(
            media_buy_id="mb_1",
            affected_packages=[],
            media_buy_status=MediaBuyStatus.canceled,
        )

        wire = _to_wire(response, requested_adcp_version="3.0", tool_name="update_media_buy")

        assert wire["status"] == "canceled"
        assert "media_buy_status" not in wire


class TestMaybeRaiseLegacyErrors:
    """``_maybe_raise_legacy_errors`` promotes the legacy ``errors=[...]``
    wrapper to a framework :class:`AdcpError` raise so the dispatcher emits
    the spec ``adcp_error`` envelope — but only when the wire carries
    ``status="failed"``, so partial-success responses (e.g.
    ``GetMediaBuyDeliveryResponse`` returning per-buy errors alongside
    valid deliveries) pass through unchanged.
    """

    def test_no_errors_field_is_passthrough(self) -> None:
        """A normal success response is left alone."""
        _maybe_raise_legacy_errors({"media_buy_id": "mb_1", "status": "active"})

    def test_empty_errors_array_is_passthrough(self) -> None:
        """An empty errors list — treat as no error."""
        _maybe_raise_legacy_errors({"errors": []})

    def test_partial_success_without_failed_status_is_passthrough(self) -> None:
        """A response carrying ``errors=[...]`` alongside success-shape data
        but no ``status="failed"`` is NOT promoted — preserves
        ``GetMediaBuyDeliveryResponse`` partial-failure semantics.
        """
        _maybe_raise_legacy_errors(
            {
                "errors": [{"code": "invalid_date_range", "message": "bad dates"}],
                "media_buy_deliveries": [],
                "aggregated_totals": {"impressions": 0},
            }
        )

    def test_first_error_promoted_to_raise(self) -> None:
        """A legacy ``errors=[{code, message}]`` wrapper with ``status="failed"``
        raises ``AdcpError``.
        """
        with pytest.raises(AdcpError) as exc:
            _maybe_raise_legacy_errors(
                {
                    "errors": [{"code": "VALIDATION_ERROR", "message": "end before start"}],
                    "status": "failed",
                }
            )
        assert exc.value.code == "VALIDATION_ERROR"
        # ``args[0]`` is the message string AdcpError carries as its
        # exception text.
        assert "end before start" in exc.value.args[0]

    def test_lowercase_legacy_code_uppercased(self) -> None:
        """Some impls emit lowercase legacy codes — normalize to the spec
        enum so buyer-side ``STANDARD_ERROR_CODES`` switches match.
        """
        with pytest.raises(AdcpError) as exc:
            _maybe_raise_legacy_errors(
                {
                    "errors": [{"code": "validation_error", "message": "bad input"}],
                    "status": "failed",
                }
            )
        assert exc.value.code == "VALIDATION_ERROR"

    def test_authentication_error_maps_to_auth_required(self) -> None:
        """The legacy ``authentication_error`` string maps to ``AUTH_REQUIRED``
        — the only auth code in the AdCP 3.0 enum that covers both
        missing and rejected credentials. Locking the mapping prevents
        a future regression to ``AUTH_TOKEN_INVALID`` (which is NOT in
        the spec enum and would surface as "unknown code" to buyer
        agents walking ``STANDARD_ERROR_CODES``).
        """
        with pytest.raises(AdcpError) as exc:
            _maybe_raise_legacy_errors(
                {
                    "errors": [{"code": "authentication_error", "message": "principal not found"}],
                    "status": "failed",
                }
            )
        assert exc.value.code == "AUTH_REQUIRED"

    def test_field_and_details_preserved(self) -> None:
        """When the legacy entry carries ``field`` and ``details``, both
        ride along onto the raised ``AdcpError`` so the dispatcher echoes
        them on the wire envelope.
        """
        with pytest.raises(AdcpError) as exc:
            _maybe_raise_legacy_errors(
                {
                    "errors": [
                        {
                            "code": "VALIDATION_ERROR",
                            "message": "bad",
                            "field": "packages.0.budget",
                            "details": {"limit": 100},
                        }
                    ],
                    "status": "failed",
                }
            )
        assert exc.value.field == "packages.0.budget"
        assert exc.value.details == {"limit": 100}


class TestTranslateValidationErrorIsJSONSafe:
    """Regression: ``_translate_validation_error`` must strip pydantic's
    non-JSON-safe ``ctx`` / ``input`` / ``url`` fields from ``errors()``.

    Pydantic's ``ValidationError.errors()`` includes ``ctx={"error":
    ValueError(...)}`` when a ``@field_validator`` raises a Python
    exception. Forwarding that into ``AdcpError.details`` crashes the
    framework dispatcher's wire serializer with
    ``PydanticSerializationError: Unable to serialize unknown type:
    <class 'ValueError'>`` — and the buyer sees HTTP 500 instead of the
    spec ``INVALID_REQUEST`` envelope (#355).
    """

    @staticmethod
    def _trigger_validation_error_with_ctx() -> Exception:
        """Build a pydantic ValidationError whose ``errors()`` contains a
        ``ctx['error']`` ValueError instance — mirrors the real
        ``UpdateMediaBuyRequest.budget`` rejection that surfaced #355.
        """

        class _Model(BaseModel):
            field: int

            @field_validator("field", mode="before")
            @classmethod
            def _reject(cls, v):  # noqa: ANN001, ANN206
                raise ValueError(f"rejected {v}")

        try:
            _Model(field=1)
        except Exception as exc:
            return exc
        raise AssertionError("expected ValidationError")

    def test_details_round_trip_through_json(self) -> None:
        """The full :class:`AdcpError` produced by the translator must be
        JSON-serializable, including ``details.errors``. This is what the
        dispatcher passes to the wire serializer.
        """
        exc = self._trigger_validation_error_with_ctx()
        adcp_err = _translate_validation_error(exc)
        # The dispatcher's wire path roughly does this — fail loudly if it
        # would crash. AdcpError exposes message via ``args[0]``.
        json.dumps(
            {
                "code": adcp_err.code,
                "message": adcp_err.args[0] if adcp_err.args else None,
                "field": adcp_err.field,
                "details": adcp_err.details,
            }
        )

    def test_details_contains_no_class_or_exception_objects(self) -> None:
        """Belt-and-suspenders: the translator must not forward any
        Python class object or exception instance in ``details``. Walk
        the structure and assert primitives only.
        """
        exc = self._trigger_validation_error_with_ctx()
        adcp_err = _translate_validation_error(exc)
        assert adcp_err.details is not None

        def _walk(obj):
            if isinstance(obj, dict):
                for v in obj.values():
                    _walk(v)
            elif isinstance(obj, list | tuple):
                for v in obj:
                    _walk(v)
            else:
                assert isinstance(obj, str | int | float | bool | type(None)), (
                    f"non-JSON-primitive in details: type={type(obj).__name__} repr={obj!r}"
                )

        _walk(adcp_err.details)

    def test_field_path_still_populated(self) -> None:
        """Stripping ``ctx``/``input``/``url`` must not also drop ``loc``
        — the buyer needs the field path to repair the offending value.
        """
        exc = self._trigger_validation_error_with_ctx()
        adcp_err = _translate_validation_error(exc)
        msg = adcp_err.args[0] if adcp_err.args else ""
        assert adcp_err.field == "field"
        assert "field" in msg
        assert "rejected" in msg
