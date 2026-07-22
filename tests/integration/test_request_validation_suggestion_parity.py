"""Request-validation suggestion parity (#1417).

Core Invariant: every request-validation rejection, on every transport,
crosses the wire as ONE typed AdCPValidationError produced by the single
shared boundary (``adcp_validation_boundary``), carrying error.json's
TOP-LEVEL ``suggestion`` (AdCP 3.1, pinned ref v3.1-04f59d2d5,
static/schemas/source/core/error.json — "Suggested action to resolve the
error"; graded by the POST-F3 storyboard steps). A path that constructs its
request outside the boundary leaks a raw pydantic ``ValidationError`` and the
buyer-facing envelope carries NO suggestion.

Each test pins that invariant for one production request-construction site:
remove the site's boundary (or its full-request validation) and the envelope
loses its suggestion. The sites covered (transports in parens):

- ``get_media_buy_delivery`` — GetMediaBuyDeliveryRequest (REST)
- ``get_media_buys`` — ``_handle_get_media_buys_skill`` / GetMediaBuysRequest (A2A)
- ``list_accounts`` — ``_handle_list_accounts_skill`` + ``/api/v1/accounts`` / ListAccountsRequest (A2A, REST)
- ``sync_accounts`` — ``_handle_sync_accounts_skill`` + ``/api/v1/accounts/sync`` / SyncAccountsRequest (A2A, REST)
- ``list_authorized_properties`` — ``_handle_list_authorized_properties_skill`` / ListAuthorizedPropertiesRequest (A2A)
- ``list_creative_formats`` — ``_handle_list_creative_formats_skill`` + ``/api/v1/creative-formats`` / ListCreativeFormatsRequest (A2A, REST)
- ``get_products`` — ``/api/v1/products`` / ``create_get_products_request`` ProductFilters (REST)
- ``create_media_buy`` — ``to_reporting_webhook`` object coercion, ``/api/v1/media-buys`` + A2A handler (REST, A2A)

Wire-first per tests/CLAUDE.md § Error Verification Policy:
``TransportResult.assert_wire_error(..., require_suggestion=True)`` reads the
STRICT top-level suggestion (``extract_wire_suggestion``) from the captured
two-layer envelope. Every A2A case drives the REAL wire — ``on_message_send``
→ skill handler — via the harness A2A dispatch, never a ``*_raw`` wrapper
(those have zero production callers, so their green would be false confidence).
"""

import pytest

from tests.harness._idempotency import fresh_idempotency_key

INVALID_STATUS_FILTER = ["nonexistent_status"]  # rejected by GetMediaBuyDeliveryRequest
INVALID_ASSET_TYPES = ["not_an_asset_type"]  # rejected by ListCreativeFormatsRequest


@pytest.mark.requires_db
class TestRequestValidationContextEchoParity:
    """A valid application context survives pre-tool validation on every wire."""

    @pytest.mark.parametrize(
        ("transport_name", "expected_code"),
        [
            ("A2A", "VALIDATION_ERROR"),
            ("MCP", "VALIDATION_ERROR"),
            ("REST", "INVALID_REQUEST"),
        ],
    )
    def test_malformed_packages_echoes_exact_context(
        self,
        integration_db,
        transport_name: str,
        expected_code: str,
    ) -> None:
        """Validation before request construction still echoes opaque context."""
        from tests.harness.media_buy_create import MediaBuyCreateEnv
        from tests.harness.transport import Transport

        transport = Transport[transport_name]
        application_context = {
            "correlation_id": f"ctx-validation-{transport.value}",
            "nullable": None,
            "nested": {"value": None},
        }
        with MediaBuyCreateEnv() as env:
            env.setup_media_buy_data()
            result = env.call_via(
                transport,
                brand={"domain": "acme.example"},
                packages="not-a-list",
                start_time="asap",
                end_time="2099-12-31T23:59:59Z",
                idempotency_key="context-echo-error-0001",
                context=application_context,
            )

        assert result.is_error, f"{transport.value}: malformed packages unexpectedly succeeded"
        result.assert_wire_error(
            expected_code,
            recovery="correctable",
            require_suggestion=True,
            message_substr="list",
        )
        assert result.wire_error_envelope["context"] == application_context


@pytest.mark.requires_db
class TestMissingIdempotencyKeyParity:
    """One buyer mistake, one wire identity: the canonical missing-key error.

    ``missing_idempotency_key_error`` (src.core.exceptions) is the single home
    for the rejection's message, ``field``, and suggestion. Each transport
    reaches it through a DIFFERENT boundary (A2A skill-handler precedence
    check, MCP TypeAdapter → normalize_to_adcp_error, REST
    RequestValidationError handler) — this parity matrix reddens if any one
    boundary regresses to a transport-local copy or a divergent suggestion.
    """

    CANONICAL_MESSAGE = "idempotency_key is required."
    CANONICAL_SUGGESTION = "Provide a client-generated idempotency_key (16-255 characters, using only [A-Za-z0-9_.:-])."

    @pytest.mark.parametrize("transport_name", ["A2A", "MCP", "REST"])
    def test_missing_key_identical_wire_identity(self, integration_db, transport_name: str) -> None:
        from src.core.exceptions import missing_idempotency_key_error
        from tests.harness._idempotency import OMIT_IDEMPOTENCY_KEY
        from tests.harness.media_buy_create import MediaBuyCreateEnv
        from tests.harness.transport import Transport

        # The factory itself is the source the wire values below must equal —
        # loaded here so drift between the constants and the factory reddens too.
        canonical = missing_idempotency_key_error()
        assert str(canonical) == self.CANONICAL_MESSAGE
        assert canonical.suggestion == self.CANONICAL_SUGGESTION

        transport = Transport[transport_name]
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            result = env.call_via(
                transport,
                brand={"domain": "missing-key-parity.example"},
                packages=[
                    {
                        "product_id": product.product_id,
                        "budget": 5000.0,
                        "pricing_option_id": "cpm_usd_fixed",
                    }
                ],
                start_time=(now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                end_time=(now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                idempotency_key=OMIT_IDEMPOTENCY_KEY,
            )

        assert result.is_error, f"{transport.value}: missing idempotency_key unexpectedly succeeded"
        envelope = result.wire_error_envelope
        assert envelope is not None, f"{transport.value}: missing-key rejection must carry the wire envelope"
        error = envelope["errors"][0]
        assert error.get("message") == self.CANONICAL_MESSAGE, (
            f"{transport.value}: message diverged: {error.get('message')!r}"
        )
        assert error.get("field") == "idempotency_key", f"{transport.value}: field diverged: {error.get('field')!r}"
        assert error.get("suggestion") == self.CANONICAL_SUGGESTION, (
            f"{transport.value}: suggestion diverged: {error.get('suggestion')!r}"
        )
        assert error.get("code") == "VALIDATION_ERROR"


@pytest.mark.requires_db
class TestGetMediaBuyDeliveryRestSuggestionParity:
    """REST get_media_buy_delivery request-validation must carry a top-level suggestion."""

    def test_invalid_status_filter_rest_envelope_carries_suggestion(self, integration_db):
        """An invalid ``status_filter`` rejected on the REST wire must produce
        the AdCP two-layer VALIDATION_ERROR envelope WITH a top-level
        ``suggestion`` (error.json @v3.1-04f59d2d5).

        Pins that ``get_media_buy_delivery`` builds ``GetMediaBuyDeliveryRequest``
        inside ``adcp_validation_boundary``; without it the raw ValidationError
        reaches the generic ``ValueError`` handler and the 400 envelope has
        code+recovery but NO suggestion.
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
    (#1417): the real skill handler builds ``GetMediaBuysRequest``
    with no ``adcp_validation_boundary`` and leaks a bare ValidationError
    that ``normalize_to_adcp_error`` flattens into a suggestion-less envelope.
    """

    def test_malformed_media_buy_ids_a2a_envelope_carries_suggestion(self, integration_db):
        """A wrong-typed ``media_buy_ids`` (string instead of array) rejected
        on the A2A wire must produce the AdCP two-layer VALIDATION_ERROR
        envelope WITH a top-level ``suggestion`` (error.json
        @v3.1-04f59d2d5), matching what REST emits for the same input.

        Pins that ``_handle_get_media_buys_skill`` validates
        ``GetMediaBuysRequest`` inside the boundary; a bare
        ``model_validate(params)`` yields an envelope with code+recovery but
        NO suggestion.
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

        Pins that ``_handle_list_accounts_skill`` constructs
        ``ListAccountsRequest`` inside the boundary; a bare construction lets
        the ValidationError reach ``normalize_to_adcp_error`` and the envelope
        loses its suggestion.
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

        Pins that ``_handle_sync_accounts_skill`` constructs
        ``SyncAccountsRequest`` inside the boundary; a bare construction drops
        the suggestion.
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

        Pins that ``_handle_list_authorized_properties_skill`` constructs
        ``ListAuthorizedPropertiesRequest`` inside the boundary; a bare
        construction drops the suggestion. (The fifth bare handler, surfaced by
        the disease scan.)
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

        Pins that ``_handle_list_creative_formats_skill`` calls
        ``build_list_creative_formats_request`` inside the boundary; a bare
        call drops the suggestion.
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

        Pins that the ``/api/v1/products`` route calls the helper inside the
        boundary; without it the raw ValidationError reaches the generic
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
                    "idempotency_key": "reporting-webhook-test-0001",
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
class TestCreateMediaBuyRestUndecodableBodySuggestionParity:
    """An undecodable REST body must still reject through the typed boundary.

    ``_raw_json_body`` (api_v1.py) is a FastAPI dependency, so it resolves
    BEFORE the route's body model. If it raises on an empty or malformed body,
    the ``ValueError`` lands in the generic handler and the buyer receives a
    bare ``VALIDATION_ERROR`` carrying the json module's own message — losing
    the ``suggestion`` / ``field`` / ``details`` that ``CreateMediaBuyBody``
    produces, and leaking a stdlib-internal string onto the wire.

    Pins that the dependency swallows the decode failure so the body model —
    not the raw decoder — owns the rejection.

    Which cases actually grade the dependency (mutation-verified by reverting
    ``_raw_json_body`` to its raising form): ``empty`` and ``non-json content
    type`` redden; ``malformed`` does NOT. A syntactically-invalid JSON body is
    rejected by FastAPI's own body parsing before the dependency is resolved,
    so it never reaches ``_raw_json_body``. It is kept here because the
    buyer-facing outcome is part of the same contract, but it is not the oracle
    for this fix — do not treat its green as coverage of the dependency.
    """

    @pytest.mark.parametrize(
        ("label", "content", "content_type"),
        [
            ("empty", b"", "application/json"),
            ("malformed", b"{not json", "application/json"),
            ("non-json content type", b"plain text", "text/plain"),
        ],
    )
    def test_undecodable_body_rejects_as_invalid_request_with_suggestion(
        self, integration_db, label, content, content_type
    ):
        from tests.harness.media_buy_create import MediaBuyCreateEnv
        from tests.harness.transport import extract_wire_suggestion
        from tests.helpers import assert_envelope_shape
        from tests.helpers.envelope_assertions import assert_no_raw_validation_leak

        with MediaBuyCreateEnv() as env:
            env.setup_media_buy_data()
            client = env.get_rest_client()

            response = client.post(
                "/api/v1/media-buys",
                content=content,
                headers={"Content-Type": content_type},
            )

            assert response.status_code == 400, (
                f"An undecodable ({label}) REST body must be rejected as a 400, got "
                f"{response.status_code}: {response.text[:500]}"
            )
            envelope = response.json()
            assert_envelope_shape(envelope, "INVALID_REQUEST", recovery="correctable")
            suggestion = extract_wire_suggestion(envelope)
            assert suggestion, (
                "An undecodable body must still carry the body model's TOP-LEVEL suggestion "
                f"(error.json @v3.1-04f59d2d5), got: {envelope}"
            )
            assert_no_raw_validation_leak(envelope["adcp_error"]["message"])
            assert "Expecting value" not in envelope["adcp_error"]["message"], (
                "The json decoder's own message must not reach the buyer; the body model owns "
                f"this rejection. Got: {envelope['adcp_error']['message']!r}"
            )


@pytest.mark.requires_db
class TestCreateMediaBuyA2AWebhookSuggestionParity:
    """A2A create_media_buy object-param rejection must carry a top-level suggestion.

    The A2A twin of ``TestCreateMediaBuyRestWebhookSuggestionParity``. The A2A
    skill handler full-request-validates inside ``adcp_validation_boundary``
    BEFORE ``create_media_buy_raw`` runs, which is the only thing standing
    between a malformed ``reporting_webhook`` and the raw wrapper's
    boundary-less ``to_reporting_webhook`` call (#1417: the
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
class TestSyncCreativesA2ASuggestionParity:
    """A2A sync_creatives request-validation must carry a top-level suggestion.

    ``_handle_sync_creatives_skill`` constructs ``CreativeAsset(**c)`` from the
    raw wire dict. Bare construction leaks the pydantic ValidationError to
    ``normalize_to_adcp_error`` and the envelope loses its suggestion — the
    exact #1417 disease; this site escaped the sweep because the boundary
    guard matched only ``*Request``-suffixed names (#1417 round-8 review item 3).

    Drives the REAL A2A wire (``on_message_send`` →
    ``_handle_sync_creatives_skill``). ``CreativeSyncEnv.call_a2a`` routes to
    ``sync_creatives_raw`` (zero production callers — pre-existing harness
    debt noted at its definition), so this class overrides it to dispatch
    through the real handler.

    The sibling bare construction, ``ContextObject(**ctx_param)``, has no
    behavioral reproduction: the pinned SDK model declares zero fields with
    ``extra="allow"``, so no dict input can make it raise. It is wrapped in
    the same boundary for guard-consistency (and future SDK field additions).
    """

    def test_invalid_creative_a2a_envelope_carries_suggestion(self, integration_db):
        """A creative entry missing the required ``format_id`` rejected on the
        A2A wire must produce the AdCP two-layer VALIDATION_ERROR envelope WITH
        a top-level ``suggestion`` (error.json @v3.1-04f59d2d5) — parity with
        the nine wrapped skill handlers in the same file.
        """
        from src.core.schemas import SyncCreativesResponse
        from tests.harness.creative_sync import CreativeSyncEnv
        from tests.harness.transport import Transport

        class _RealA2AWireCreativeSyncEnv(CreativeSyncEnv):
            def call_a2a(self, **kwargs):
                return self._run_a2a_handler("sync_creatives", SyncCreativesResponse, **kwargs)

        with _RealA2AWireCreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(
                Transport.A2A,
                creatives=[{"creative_id": "cr-invalid-1", "name": "No format"}],
                idempotency_key=fresh_idempotency_key(),
            )

            assert result.is_error, (
                "A creative missing format_id must be rejected on the A2A wire, "
                f"got success payload: {result.payload!r}"
            )
            result.assert_wire_error(
                "VALIDATION_ERROR",
                recovery="correctable",
                require_suggestion=True,
                message_substr="format_id",
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

        Pins that the ``/api/v1/accounts`` route builds the request inside the
        boundary; without it the raw ValidationError reaches the generic
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

        Pins that the ``/api/v1/accounts/sync`` route builds the request inside
        the boundary; without it the raw ValidationError reaches the generic
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
                json={
                    "accounts": [{"operator": "no-brand.example"}],
                    "idempotency_key": "account-validation-parity-0001",
                },
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

        Pins that the route builds the request inside the boundary; without it
        the raw ValidationError reaches the generic ``ValueError`` handler and
        the envelope has NO suggestion.
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
