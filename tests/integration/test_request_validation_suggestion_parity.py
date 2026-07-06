"""Request-validation suggestion parity — TDD red for salesagent-ah98.

Core Invariant under test (ah98): every request-validation rejection, on every
transport, crosses the wire as ONE typed AdCPValidationError produced by the
single shared boundary (``adcp_validation_boundary``), carrying error.json's
TOP-LEVEL ``suggestion`` (AdCP 3.1, pinned ref v3.1-04f59d2d5,
static/schemas/source/core/error.json — "Suggested action to resolve the
error"; graded by the POST-F3 storyboard steps).

Today these paths hand-roll their own ValidationError translation (or none at
all), so the buyer-facing envelope carries NO suggestion:

- ``get_media_buy_delivery_raw`` (src/core/tools/media_buy_delivery.py)
  builds ``GetMediaBuyDeliveryRequest`` with NO boundary — the raw pydantic
  ``ValidationError`` leaks to the generic REST ``ValueError`` handler, which
  emits a suggestion-less VALIDATION_ERROR envelope.
- ``get_media_buys_raw`` (src/core/tools/media_buy_list.py) builds
  ``GetMediaBuysRequest`` with NO boundary — the raw pydantic
  ``ValidationError`` leaks untyped to the caller. (get_media_buys has no
  REST route; the raw wrapper is the boundary surface.)
- the REST ``/api/v1/creative-formats`` route (src/routes/api_v1.py) builds
  ``ListCreativeFormatsRequest`` with NO boundary — same suggestion-less
  envelope via the generic ``ValueError`` handler.

These tests go RED until the shared-boundary fold-in lands. They must not be
skipped, xfailed, or weakened — they ARE the red step.

Wire-first per tests/CLAUDE.md § Error Verification Policy:
``TransportResult.assert_wire_error(..., require_suggestion=True)`` reads the
STRICT top-level suggestion (``extract_wire_suggestion``) from the captured
two-layer envelope. The get_media_buys raw-wrapper test is a no-wire path
(direct raw call, no REST route exists), so it asserts the typed exception's
top-level attributes — the one place the policy allows exception assertions.
"""

import pytest

INVALID_STATUS_FILTER = ["nonexistent_status"]  # rejected by GetMediaBuyDeliveryRequest
INVALID_ASSET_TYPES = ["not_an_asset_type"]  # rejected by ListCreativeFormatsRequest


@pytest.mark.requires_db
class TestGetMediaBuyDeliveryRestSuggestionParity:
    """REST get_media_buy_delivery request-validation must carry a top-level suggestion."""

    def test_invalid_status_filter_rest_envelope_carries_suggestion(self, integration_db):
        """An invalid ``status_filter`` rejected on the REST wire must produce
        the AdCP two-layer VALIDATION_ERROR envelope WITH a top-level
        ``suggestion`` (error.json @v3.1-04f59d2d5).

        RED today: ``get_media_buy_delivery_raw`` builds the request with no
        boundary; the raw ValidationError reaches the generic ``ValueError``
        handler and the 400 envelope has code+recovery but NO suggestion.
        """
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv
        from tests.harness.transport import Transport

        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            result = env.call_via(Transport.REST, status_filter=INVALID_STATUS_FILTER)

            assert result.is_error, (
                f"Invalid status_filter must be rejected on the REST wire, got success payload: {result.payload!r}"
            )
            assert result.envelope["status_code"] == 400
            result.assert_wire_error(
                "VALIDATION_ERROR",
                recovery="correctable",
                require_suggestion=True,
                message_substr="status_filter",
            )


@pytest.mark.requires_db
class TestGetMediaBuysRawWrapperSuggestionParity:
    """get_media_buys_raw must reject invalid requests with a typed, suggestion-carrying error.

    No wire on this path: get_media_buys has no REST route, and the harness A2A
    dispatch calls the raw wrapper directly. Per the Error Verification Policy,
    a no-wire path asserts the typed exception's top-level attributes — which is
    exactly the contract ``adcp_validation_boundary`` produces (message + field
    + suggestion) and the boundary translators serialize verbatim.
    """

    def test_malformed_media_buy_ids_raises_typed_error_with_suggestion(self, integration_db):
        """RED today: the raw wrapper builds ``GetMediaBuysRequest`` with no
        boundary, so a raw ``pydantic.ValidationError`` leaks instead of an
        ``AdCPValidationError`` with a top-level suggestion.

        Uses a wrong-typed ``media_buy_ids`` (string instead of array) because
        that is a request-validation failure ``GetMediaBuysRequest`` actually
        raises. (``status_filter`` is typed ``Any`` on this request and is
        silently accepted — a separate gap, tracked with the
        T-UC-019-partition-status-filter-invalid xfail.)
        """
        from src.core.exceptions import AdCPValidationError
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness.media_buy_list import MediaBuyListEnv
        from tests.harness.transport import Transport

        with MediaBuyListEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            result = env.call_via(Transport.A2A, media_buy_ids="not-a-list")

            assert result.is_error, (
                "Malformed media_buy_ids must be rejected by get_media_buys_raw, "
                f"got success payload: {result.payload!r}"
            )
            error = result.error
            assert isinstance(error, AdCPValidationError), (
                "get_media_buys_raw must translate request-validation failures through "
                "the shared adcp_validation_boundary (typed AdCPValidationError), got "
                f"leaked {type(error).__name__}: {error}"
            )
            assert error.error_code == "VALIDATION_ERROR"
            # STRICT error.json conformance: suggestion is a TOP-LEVEL attribute
            # of the error object, not a details entry (salesagent-9val).
            assert error.suggestion, (
                f"Expected a non-empty top-level suggestion on the validation error, got: {error.to_dict()}"
            )


@pytest.mark.requires_db
class TestListCreativeFormatsRestSuggestionParity:
    """REST list_creative_formats request-validation must carry a top-level suggestion."""

    def test_invalid_asset_types_rest_envelope_carries_suggestion(self, integration_db):
        """An invalid ``asset_types`` member rejected on the REST wire must
        produce the AdCP two-layer VALIDATION_ERROR envelope WITH a top-level
        ``suggestion``.

        The invalid value passes ``ListCreativeFormatsBody`` (``list[str]``)
        and fails inside ``ListCreativeFormatsRequest`` — the tool-level
        request-validation boundary this invariant governs. Raw-body POST via
        ``get_rest_client`` because the harness ``build_rest_body`` serializes
        a typed request, which cannot represent the invalid input.

        RED today: the route builds the request with no boundary; the raw
        ValidationError reaches the generic ``ValueError`` handler and the
        envelope has NO suggestion.
        """
        from tests.harness import CreativeFormatsEnv
        from tests.harness.transport import extract_wire_suggestion
        from tests.helpers import assert_envelope_shape

        with CreativeFormatsEnv(tenant_id="t1", principal_id="p1") as env:
            client = env.get_rest_client()

            response = client.post(
                "/api/v1/creative-formats",
                json={"asset_types": INVALID_ASSET_TYPES},
            )

            assert response.status_code == 400, (
                "Invalid asset_types must be rejected on the REST wire, got "
                f"{response.status_code}: {response.text[:500]}"
            )
            envelope = response.json()
            assert_envelope_shape(
                envelope,
                "VALIDATION_ERROR",
                recovery="correctable",
                message_substr="asset_types",
            )
            suggestion = extract_wire_suggestion(envelope)
            assert suggestion, (
                "Expected a non-empty TOP-LEVEL suggestion in the VALIDATION_ERROR "
                f"wire envelope (error.json @v3.1-04f59d2d5), got: {envelope}"
            )
