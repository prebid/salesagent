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
two-layer envelope. Every A2A case drives the REAL wire — ``on_message_send``
→ skill handler — via the harness A2A dispatch (salesagent-klkg closed the
dead-path hole where the get_media_buys case drove ``get_media_buys_raw``,
which has zero production callers).
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
class TestGetMediaBuysA2ASuggestionParity:
    """A2A get_media_buys request-validation must carry a top-level suggestion.

    Drives the REAL A2A wire (``on_message_send`` →
    ``_handle_get_media_buys_skill``) via the harness A2A dispatch. The
    previous version of this test drove ``get_media_buys_raw`` — a wrapper
    with ZERO production callers — so its green was false confidence
    (salesagent-klkg): the real skill handler builds ``GetMediaBuysRequest``
    with no ``adcp_validation_boundary`` and leaks a bare ValidationError
    that ``normalize_to_adcp_error`` flattens into a suggestion-less envelope.
    """

    def test_malformed_media_buy_ids_a2a_envelope_carries_suggestion(self, integration_db):
        """A wrong-typed ``media_buy_ids`` (string instead of array) rejected
        on the A2A wire must produce the AdCP two-layer VALIDATION_ERROR
        envelope WITH a top-level ``suggestion`` (error.json
        @v3.1-04f59d2d5), matching what REST emits for the same input.

        RED today (salesagent-klkg): ``_handle_get_media_buys_skill`` calls
        ``GetMediaBuysRequest.model_validate(params)`` bare, so the envelope
        has code+recovery but NO suggestion.
        """
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness.media_buy_list import MediaBuyListEnv
        from tests.harness.transport import Transport

        with MediaBuyListEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            result = env.call_via(Transport.A2A, media_buy_ids="not-a-list")

            assert result.is_error, (
                f"Malformed media_buy_ids must be rejected on the A2A wire, got success payload: {result.payload!r}"
            )
            result.assert_wire_error(
                "VALIDATION_ERROR",
                recovery="correctable",
                require_suggestion=True,
                message_substr="media_buy_ids",
            )


@pytest.mark.requires_db
class TestListAccountsA2ASuggestionParity:
    """A2A list_accounts request-validation must carry a top-level suggestion."""

    def test_invalid_status_a2a_envelope_carries_suggestion(self, integration_db):
        """An invalid ``status`` rejected on the A2A wire must produce the AdCP
        two-layer VALIDATION_ERROR envelope WITH a top-level ``suggestion`` —
        the same enriched envelope REST emits for the same input
        (``/api/v1/accounts`` wraps construction in ``adcp_validation_boundary``).

        RED today (salesagent-klkg): ``_handle_list_accounts_skill`` constructs
        ``ListAccountsRequest`` bare, so the bare ValidationError reaches
        ``normalize_to_adcp_error`` and the envelope has NO suggestion.
        """
        from tests.harness.account_list import AccountListEnv
        from tests.harness.transport import Transport

        with AccountListEnv() as env:
            env.setup_default_data()

            result = env.call_via(Transport.A2A, status="not_a_status")

            assert result.is_error, (
                f"Invalid status must be rejected on the A2A wire, got success payload: {result.payload!r}"
            )
            result.assert_wire_error(
                "VALIDATION_ERROR",
                recovery="correctable",
                require_suggestion=True,
                message_substr="status",
            )


@pytest.mark.requires_db
class TestSyncAccountsA2ASuggestionParity:
    """A2A sync_accounts request-validation must carry a top-level suggestion."""

    def test_account_missing_brand_a2a_envelope_carries_suggestion(self, integration_db):
        """An account entry missing the required ``brand`` rejected on the A2A
        wire must produce the AdCP two-layer VALIDATION_ERROR envelope WITH a
        top-level ``suggestion`` — parity with ``/api/v1/accounts/sync``.

        RED today (salesagent-klkg): ``_handle_sync_accounts_skill`` constructs
        ``SyncAccountsRequest`` bare.
        """
        from tests.harness.account_sync import AccountSyncEnv
        from tests.harness.transport import Transport

        with AccountSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(Transport.A2A, accounts=[{"operator": "no-brand.example"}])

            assert result.is_error, (
                "An account entry missing brand must be rejected on the A2A wire, "
                f"got success payload: {result.payload!r}"
            )
            result.assert_wire_error(
                "VALIDATION_ERROR",
                recovery="correctable",
                require_suggestion=True,
                message_substr="brand",
            )


@pytest.mark.requires_db
class TestListAuthorizedPropertiesA2ASuggestionParity:
    """A2A list_authorized_properties request-validation must carry a top-level suggestion."""

    def test_invalid_context_a2a_envelope_carries_suggestion(self, integration_db):
        """A wrong-typed ``context`` rejected on the A2A wire must produce the
        AdCP two-layer VALIDATION_ERROR envelope WITH a top-level
        ``suggestion`` — parity with ``/api/v1/authorized-properties``.

        RED before the klkg fix: ``_handle_list_authorized_properties_skill``
        constructed ``ListAuthorizedPropertiesRequest`` bare (the fifth bare
        handler, found by the disease scan; missed by the original finding).
        """
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness.authorized_properties import AuthorizedPropertiesEnv
        from tests.harness.transport import Transport

        with AuthorizedPropertiesEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            PrincipalFactory(tenant=tenant, principal_id="p1")

            result = env.call_via(Transport.A2A, context="not-a-context-object")

            assert result.is_error, (
                f"Wrong-typed context must be rejected on the A2A wire, got success payload: {result.payload!r}"
            )
            result.assert_wire_error(
                "VALIDATION_ERROR",
                recovery="correctable",
                require_suggestion=True,
                message_substr="context",
            )


@pytest.mark.requires_db
class TestListCreativeFormatsA2ASuggestionParity:
    """A2A list_creative_formats request-validation must carry a top-level suggestion."""

    def test_invalid_asset_types_a2a_envelope_carries_suggestion(self, integration_db):
        """An invalid ``asset_types`` member rejected on the A2A wire must
        produce the AdCP two-layer VALIDATION_ERROR envelope WITH a top-level
        ``suggestion`` — parity with ``/api/v1/creative-formats``.

        RED today (salesagent-klkg): ``_handle_list_creative_formats_skill``
        calls ``build_list_creative_formats_request`` bare.
        """
        from tests.harness import CreativeFormatsEnv
        from tests.harness.transport import Transport

        with CreativeFormatsEnv(tenant_id="t1", principal_id="p1") as env:
            result = env.call_via(Transport.A2A, asset_types=INVALID_ASSET_TYPES)

            assert result.is_error, (
                f"Invalid asset_types must be rejected on the A2A wire, got success payload: {result.payload!r}"
            )
            result.assert_wire_error(
                "VALIDATION_ERROR",
                recovery="correctable",
                require_suggestion=True,
                message_substr="asset_types",
            )


@pytest.mark.requires_db
class TestGetProductsRestSuggestionParity:
    """REST get_products request-validation must carry a top-level suggestion."""

    def test_invalid_filters_rest_envelope_carries_suggestion(self, integration_db):
        """An invalid ``filters.delivery_type`` rejected on the REST wire must
        produce the AdCP two-layer VALIDATION_ERROR envelope WITH a top-level
        ``suggestion`` (error.json @v3.1-04f59d2d5).

        The invalid value passes ``GetProductsBody`` (``filters: dict``) and
        fails inside ``ProductFilters`` (delivery_type enum) built by
        ``create_get_products_request`` — the tool-level request-validation
        boundary this invariant governs. The MCP wrapper wraps this helper in
        ``adcp_validation_boundary`` (products.py); the REST route must match.

        RED today (#1417 gap): the ``/api/v1/products`` route calls the helper
        with no boundary; the raw ValidationError reaches the generic
        ``ValueError`` handler and the envelope has NO suggestion.
        """
        from tests.harness import ProductEnv
        from tests.harness.transport import extract_wire_suggestion
        from tests.helpers import assert_envelope_shape

        with ProductEnv(tenant_id="t1", principal_id="p1") as env:
            client = env.get_rest_client()

            response = client.post(
                "/api/v1/products",
                json={"brief": "video ads", "filters": {"delivery_type": "not_a_delivery_type"}},
            )

            assert response.status_code == 400, (
                "Invalid filters.delivery_type must be rejected on the REST wire, got "
                f"{response.status_code}: {response.text[:500]}"
            )
            envelope = response.json()
            assert_envelope_shape(
                envelope,
                "VALIDATION_ERROR",
                recovery="correctable",
                message_substr="delivery_type",
            )
            suggestion = extract_wire_suggestion(envelope)
            assert suggestion, (
                "Expected a non-empty TOP-LEVEL suggestion in the VALIDATION_ERROR "
                f"wire envelope (error.json @v3.1-04f59d2d5), got: {envelope}"
            )


@pytest.mark.requires_db
class TestCreateMediaBuyRestWebhookSuggestionParity:
    """REST create_media_buy object-param coercion must carry a top-level suggestion."""

    def test_invalid_reporting_webhook_rest_envelope_carries_suggestion(self, integration_db):
        """A malformed ``reporting_webhook`` rejected on the REST wire must
        produce the AdCP two-layer VALIDATION_ERROR envelope WITH a top-level
        ``suggestion`` (error.json @v3.1-04f59d2d5).

        The invalid value passes ``CreateMediaBuyBody`` (``dict``) and fails
        ``to_reporting_webhook`` coercion (ReportingWebhook requires url /
        authentication / reporting_frequency) — coerced at the route inside
        ``adcp_validation_boundary`` so the rejection is typed instead of the
        raw-ValidationError leak the un-coerced pass-through produced.
        """
        from tests.harness.media_buy_create import MediaBuyCreateEnv
        from tests.harness.transport import extract_wire_suggestion
        from tests.helpers import assert_envelope_shape

        with MediaBuyCreateEnv() as env:
            env.setup_media_buy_data()
            client = env.get_rest_client()

            response = client.post(
                "/api/v1/media-buys",
                json={
                    "brand": {"domain": "acme.example"},
                    "packages": [],
                    "start_time": "asap",
                    "end_time": "2099-12-31T23:59:59Z",
                    "reporting_webhook": {"not_a_webhook_field": True},
                },
            )

            assert response.status_code == 400, (
                "A malformed reporting_webhook must be rejected on the REST wire, got "
                f"{response.status_code}: {response.text[:500]}"
            )
            envelope = response.json()
            assert_envelope_shape(
                envelope,
                "VALIDATION_ERROR",
                recovery="correctable",
            )
            suggestion = extract_wire_suggestion(envelope)
            assert suggestion, (
                "Expected a non-empty TOP-LEVEL suggestion in the VALIDATION_ERROR "
                f"wire envelope (error.json @v3.1-04f59d2d5), got: {envelope}"
            )


@pytest.mark.requires_db
class TestCreateMediaBuyA2AWebhookSuggestionParity:
    """A2A create_media_buy object-param rejection must carry a top-level suggestion.

    The A2A twin of ``TestCreateMediaBuyRestWebhookSuggestionParity``. The A2A
    skill handler full-request-validates inside ``adcp_validation_boundary``
    BEFORE ``create_media_buy_raw`` runs, which is the only thing standing
    between a malformed ``reporting_webhook`` and the raw wrapper's
    boundary-less ``to_reporting_webhook`` call (salesagent-oygh: the
    raise-capable ``to_*`` coercions depend on every caller pre-validating).
    This test pins that pre-validation: remove the handler's boundary or its
    full-request validation and the envelope loses its suggestion.
    """

    def test_invalid_reporting_webhook_a2a_envelope_carries_suggestion(self, integration_db):
        """A malformed ``reporting_webhook`` rejected on the A2A wire must
        produce the AdCP two-layer VALIDATION_ERROR envelope WITH a top-level
        ``suggestion`` (error.json @v3.1-04f59d2d5) — identical to REST.
        """
        from tests.harness.media_buy_create import MediaBuyCreateEnv
        from tests.harness.transport import Transport

        with MediaBuyCreateEnv() as env:
            env.setup_media_buy_data()

            result = env.call_via(
                Transport.A2A,
                brand={"domain": "acme.example"},
                packages=[{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
                start_time="asap",
                end_time="2099-12-31T23:59:59Z",
                reporting_webhook={"not_a_webhook_field": True},
            )

            assert result.is_error, (
                "A malformed reporting_webhook must be rejected on the A2A wire, "
                f"got success payload: {result.payload!r}"
            )
            result.assert_wire_error(
                "VALIDATION_ERROR",
                recovery="correctable",
                require_suggestion=True,
            )


@pytest.mark.requires_db
class TestListAccountsRestSuggestionParity:
    """REST list_accounts request-validation must carry a top-level suggestion."""

    def test_invalid_status_rest_envelope_carries_suggestion(self, integration_db):
        """An invalid ``status`` rejected on the REST wire must produce the
        AdCP two-layer VALIDATION_ERROR envelope WITH a top-level
        ``suggestion`` (error.json @v3.1-04f59d2d5).

        The invalid value passes ``ListAccountsBody`` (``str``) and fails
        inside ``ListAccountsRequest`` (AccountStatus enum) — the tool-level
        request-validation boundary this invariant governs.

        RED today (#1417 gap): the ``/api/v1/accounts`` route builds the
        request with no boundary; the raw ValidationError reaches the generic
        ``ValueError`` handler and the envelope has NO suggestion.
        """
        from tests.harness.account_list import AccountListEnv
        from tests.harness.transport import extract_wire_suggestion
        from tests.helpers import assert_envelope_shape

        with AccountListEnv() as env:
            env.setup_default_data()
            client = env.get_rest_client()

            response = client.post("/api/v1/accounts", json={"status": "not_a_status"})

            assert response.status_code == 400, (
                f"Invalid status must be rejected on the REST wire, got {response.status_code}: {response.text[:500]}"
            )
            envelope = response.json()
            assert_envelope_shape(
                envelope,
                "VALIDATION_ERROR",
                recovery="correctable",
                message_substr="status",
            )
            suggestion = extract_wire_suggestion(envelope)
            assert suggestion, (
                "Expected a non-empty TOP-LEVEL suggestion in the VALIDATION_ERROR "
                f"wire envelope (error.json @v3.1-04f59d2d5), got: {envelope}"
            )


@pytest.mark.requires_db
class TestSyncAccountsRestSuggestionParity:
    """REST sync_accounts request-validation must carry a top-level suggestion."""

    def test_account_missing_brand_rest_envelope_carries_suggestion(self, integration_db):
        """An account entry missing the required ``brand`` rejected on the
        REST wire must produce the AdCP two-layer VALIDATION_ERROR envelope
        WITH a top-level ``suggestion``.

        The invalid entry passes ``SyncAccountsBody`` (``list[dict]``) and
        fails inside ``SyncAccountsRequest`` (Accounts requires brand).

        RED today (#1417 gap): the ``/api/v1/accounts/sync`` route builds the
        request with no boundary; the raw ValidationError reaches the generic
        ``ValueError`` handler and the envelope has NO suggestion.
        """
        from tests.harness.account_sync import AccountSyncEnv
        from tests.harness.transport import extract_wire_suggestion
        from tests.helpers import assert_envelope_shape

        with AccountSyncEnv() as env:
            env.setup_default_data()
            client = env.get_rest_client()

            response = client.post(
                "/api/v1/accounts/sync",
                json={"accounts": [{"operator": "no-brand.example"}]},
            )

            assert response.status_code == 400, (
                "An account entry missing brand must be rejected on the REST wire, got "
                f"{response.status_code}: {response.text[:500]}"
            )
            envelope = response.json()
            assert_envelope_shape(
                envelope,
                "VALIDATION_ERROR",
                recovery="correctable",
                message_substr="brand",
            )
            suggestion = extract_wire_suggestion(envelope)
            assert suggestion, (
                "Expected a non-empty TOP-LEVEL suggestion in the VALIDATION_ERROR "
                f"wire envelope (error.json @v3.1-04f59d2d5), got: {envelope}"
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
