"""create_media_buy refuses an HMAC-SHA256 webhook registration that carries no secret.

A buyer who registers ``push_notification_config.authentication.schemes =
["HMAC-SHA256"]`` and supplies no ``credentials`` has asked for a webhook we
cannot produce. Today that registration is ACCEPTED: ``_create_media_buy_impl``
maps ``schemes[0]`` → ``authentication_type`` and ``credentials`` →
``authentication_token`` and persists the row with a null token
(``src/core/tools/media_buy_create.py`` push-config persistence block), the
create returns success, and the defect only surfaces later — inside a
background sender, where no request is left to refuse into and the buyer's only
possible signal is a log line nobody reads.

Spec grounding — AdCP 3.1.1, the version this repo PINS (``adcp==6.6.0``,
``docs/adcp-spec-version.md``):

1. ``dist/schemas/3.1.1/core/push-notification-config.json`` →
   ``Authentication``: ``required: ["schemes", "credentials"]``, and
   ``credentials`` carries ``minLength: 32`` — "For HMAC-SHA256: shared secret
   used to generate signature." Read off the pinned SDK's generated model
   (``adcp.types.PushNotificationConfig.model_json_schema()``). A registration
   naming the scheme with no credentials is not an under-specified request; it
   is an INVALID document by the pinned schema.
2. Same file, ``authentication`` description: "**Precedence is a switch, not a
   fallback:** presence of this block selects the legacy scheme; absence
   selects 9421. A seller MUST NOT sign the same webhook both ways". So there
   is no legal salvage: we cannot sign (no secret), and we cannot quietly fall
   back to the RFC 9421 profile, because the block's PRESENCE already selected
   legacy. The registration is unservable and the only honest answer is to
   refuse it while the buyer is still on the phone.
3. ``docs/building/by-layer/L3/error-handling.mdx`` § "Request
   Validation" (``VALIDATION_ERROR | correctable``): the buyer is the only
   party who can supply the secret, and supplying it makes the identical
   request succeed — so ``correctable``, not ``terminal``.

The spec does not say what a seller MUST do with such a registration, so the
refusal SHAPE is an internal decision (source hierarchy: schema silent →
production authoritative). It is settled by the sibling gate one field over:
``reject_unsafe_webhook_registration_url`` (``src/core/webhook_validator.py``)
raises ``AdCPValidationError`` → ``VALIDATION_ERROR`` / ``correctable`` /
``field``. Asserted from ``tests/helpers/webhook_credential_refusal.py`` so the
A2A-native surface in ``tests/unit/test_a2a_push_config_credential_refusal.py``
cannot drift from it.

Conformance storyboard: UNGRADED — nothing in ``dist/compliance/3.1.1/`` grades
a seller refusing an unservable webhook registration (same finding recorded for
the sibling URL refusal, ``test_webhook_url_ingest_refusal.py``).

Transport coverage is deliberately asymmetric, because the surfaces differ:

* REST (``CreateMediaBuyBody.push_notification_config: dict[str, Any]``) and
  A2A (the create skill forwards the raw dict — ``create_media_buy_raw``
  ``model_dump``s only when it is already a ``PushNotificationConfig``) accept
  the invalid document and hand it to ``_impl`` untouched. These are the
  surfaces the gate exists for.
* MCP types the parameter as ``adcp.PushNotificationConfig``, so FastMCP's
  TypeAdapter rejects the document one layer earlier, on
  ``authentication.credentials`` being required. Same buyer outcome, different
  mechanism — graded separately below so nobody "simplifies" that annotation to
  ``dict`` and silently opens a third hole.

Buyer-visible change (named here as well as in the PR, not smuggled): a
create_media_buy that TODAY succeeds with ``schemes: ["HMAC-SHA256"]`` and no
credentials starts failing with a correctable VALIDATION_ERROR. That is the
intent.
"""

from __future__ import annotations

import pytest

from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.transport import Transport
from tests.helpers.adcp_factories import create_test_media_buy_request_dict
from tests.helpers.envelope_assertions import assert_envelope_shape
from tests.helpers.webhook_credential_refusal import SHORT_CREDENTIAL, assert_credentials_refusal_envelope

# The persistence assertion for this exact table, already written for the
# sibling URL refusal. Imported rather than re-implemented: "the repository
# upsert is the single write funnel, so an empty active list IS 'the refusal
# preceded the store'" is one fact about one table, and two copies of it would
# drift the moment the funnel moves.
from tests.integration.test_webhook_url_ingest_refusal import _assert_no_push_config_persisted

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# A URL that passes the registration SSRF gate (the gate that runs immediately
# before this one), so the ONLY possible refusal is about the credential. Same
# host the gate's own "allows public" case uses.
_SAFE_URL = "https://buyer.example.com/hook"

# Transports that carry the buyer's document through to ``_impl`` unvalidated.
# A2A alone hands the raw ``push_notification_config`` dict to ``_impl``, so it
# is the only transport whose refusal comes from the ingest GATE.
#
# REST was originally listed here too, and that was wrong about production: the
# REST request model validates ``push_notification_config`` against the AdCP
# spec model, whose ``Authentication`` requires ``credentials``, so REST refuses
# at schema conformance BEFORE the gate runs — the same mechanism as MCP. It is
# graded in :class:`TestSchemaTypedTransportsRefuseTheSameDocument` below, where
# the assertion matches the layer that actually refuses. Moved after observing
# the real envelope, not to make a red test pass: REST already produced
# VALIDATION_ERROR / correctable / a field naming credentials.
_UNTYPED_TRANSPORTS = [Transport.A2A]

# Every spelling of "asked for HMAC-SHA256, supplied no secret" that can reach
# the persistence block. All four produce the same row —
# ``authentication_type="HMAC-SHA256"``, ``authentication_token`` falsy — which
# is the state no sender can serve.
_HMAC_WITHOUT_CREDENTIALS = [
    pytest.param({"schemes": ["HMAC-SHA256"]}, id="credentials-key-absent"),
    pytest.param({"schemes": ["HMAC-SHA256"], "credentials": None}, id="credentials-null"),
    pytest.param({"schemes": ["HMAC-SHA256"], "credentials": ""}, id="credentials-empty-string"),
    # Lowercase is not hypothetical: the A2A push-config endpoint stores
    # ``params.authentication.scheme`` verbatim from a free-form protobuf
    # string, so lowercase rows are reachable from a real buyer. A gate that
    # compares exactly against the enum spelling would wave this one through
    # into precisely the unservable state the exact-spelling rows are refused
    # for.
    #
    # MOVED OUT of this list by Epic D lane C3 (GH #1802): the A2A TOOL
    # path now coerces through the pinned model like MCP and REST already did, so
    # ``hmac-sha256`` is refused for the SCHEME (it is not a member of the pinned
    # AuthenticationScheme enum) BEFORE the credential requirement is reached. It
    # is still refused, still correctable, and now names the field that is
    # actually wrong — a strictly better answer than "you are missing a credential
    # for a scheme that does not exist". Graded in its own case below.
]

# The lowercase spelling, now refused for the SCHEME on every transport.
_LOWERCASE_SCHEME = {"schemes": ["hmac-sha256"]}


def _create_kwargs(product, authentication: dict | None) -> dict:
    """A fully valid create request whose ONLY defect is the auth block.

    Everything else — brand, packages, pricing option, dates, idempotency key —
    comes from the shared builder, so a package/pricing rejection can never
    stand in for the refusal under test.
    """
    return create_test_media_buy_request_dict(
        product_ids=[product.product_id],
        pricing_option_id="cpm_usd_fixed",
        total_budget=5000.0,
        po_number="HMAC-CREDS-INGEST",
        push_notification_config={"url": _SAFE_URL, "authentication": authentication},
    )


class TestCreateMediaBuyRefusesHmacRegistrationWithoutCredentials:
    """The untyped transports must refuse the document ``_impl`` actually receives."""

    @pytest.mark.parametrize("transport", _UNTYPED_TRANSPORTS, ids=lambda t: t.value)
    @pytest.mark.parametrize("authentication", _HMAC_WITHOUT_CREDENTIALS)
    def test_refused_at_ingest_naming_the_credentials_field(self, integration_db, transport, authentication):
        """VALIDATION_ERROR / correctable / field=...authentication.credentials, nothing persisted."""
        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()

            result = env.call_via(transport, **_create_kwargs(product, authentication))

            assert result.is_error, (
                "A push_notification_config asking for HMAC-SHA256 with no credentials must fail "
                "create_media_buy at ingest — the buyer is the only party who can supply the secret, "
                "and after this response there is no request left to refuse into. Got: "
                f"{getattr(result, 'wire_response', None) or result.payload!r}"
            )
            assert_credentials_refusal_envelope(
                result.error_envelope(),
                surface="create_media_buy",
            )
            _assert_no_push_config_persisted(env._tenant_id, env._principal_id)

    @pytest.mark.parametrize("transport", _UNTYPED_TRANSPORTS, ids=lambda t: t.value)
    def test_lowercase_scheme_is_refused_naming_the_scheme(self, integration_db, transport):
        """``hmac-sha256`` is refused for the SCHEME, and nothing is persisted.

        Epic D lane C3 moved this case here from the credentials list. Before C3
        the untyped A2A path had no enum between the buyer and ``_impl``, so the
        registration gate caught the lowercase spelling as "HMAC asked for, no
        secret" — right refusal, wrong field. Now that the A2A tool wrapper
        coerces through the pinned model, the document is refused for the member
        that is actually invalid, matching what MCP and REST have always done.

        Still refused, still correctable, still nothing stored: the buyer cannot
        end up with an unservable row either way. What changed is only that
        error.field now names the thing the buyer must fix.
        """
        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()

            result = env.call_via(transport, **_create_kwargs(product, _LOWERCASE_SCHEME))

            assert result.is_error, (
                "a scheme outside the pinned AuthenticationScheme enum must fail create_media_buy "
                f"at ingest. Got: {getattr(result, 'wire_response', None) or result.payload!r}"
            )
            envelope = result.error_envelope()
            assert_envelope_shape(
                envelope,
                "VALIDATION_ERROR",
                recovery="correctable",
                field="push_notification_config.authentication.schemes[0]",
            )
            _assert_no_push_config_persisted(env._tenant_id, env._principal_id)

    def test_hmac_registration_with_credentials_is_accepted(self, integration_db):
        """The control: the same request WITH a secret is not refused.

        Without this, a gate that rejected every HMAC registration — or every
        ``push_notification_config`` — would pass every case above. This is the
        assertion that makes the refusal specific rather than merely present.
        """
        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()

            result = env.call_via(
                Transport.REST,
                **_create_kwargs(product, {"schemes": ["HMAC-SHA256"], "credentials": "s" * 32}),
            )

            assert not result.is_error, (
                "A complete HMAC-SHA256 registration is servable and must be accepted; "
                f"got {result.error_envelope_or_none()!r}"
            )


# The URL the ADMIN form uses for its leg below. It is not ``_SAFE_URL``: the
# admin route runs the same registration gate but reaches it through a form whose
# positive control already uses this loopback URL, and loopback keeps that leg off
# the network exactly as the protocol legs' public host does. Each surface's URL
# only has to survive its OWN SSRF gate; what is being compared is the CREDENTIAL
# verdict.
_ADMIN_SAFE_URL = "https://127.0.0.1:9999/agent"


class TestShortCredentialReachesOneVerdictOnEverySurface:
    """REGRESSION PIN (GH #1802) — not the lane's red.

    The red for this lane lives where the behaviour actually changes: the A2A
    protocol envelope (``tests/bdd/features/local-egress-ssrf-refusal.feature``,
    ``@T-EGRESS-CREDS-short-a2a-message-send``) and the admin form
    (``tests/integration/test_admin_ingest_url_policy.py``). MCP and REST reach
    the credential rule through the TYPED request model, which refuses 31
    characters today and is untouched by the carve-out deletion, so a grader
    written there alone would be green before the change and its green would
    camouflage the two surfaces that matter.

    What this class adds instead is the property that only exists once those two
    are fixed: the same 31-character secret gets the SAME answer wherever a buyer
    or operator can register one. Four surfaces, four different mechanisms —
    FastMCP's TypeAdapter, the REST request model, the A2A ingest gate, the admin
    form — and a divergence between them is exactly how the carve-out came to
    exist in the first place.

    The absolute ``error.field`` prefix is deliberately NOT unified here: the
    admin gate is called with ``field_prefix="webhook"`` and the protocol
    surfaces with ``push_notification_config``. Unifying those is gh-#1895, which
    stays open; a suffix assertion keeps this pin honest about what it grades.
    """

    _EXPECTED = ("VALIDATION_ERROR", "correctable")

    @staticmethod
    def _verdict(result) -> tuple[str, str, str]:
        """The (code, recovery, field) triple the buyer reads, from the wire envelope."""
        envelope = result.error_envelope()
        body = envelope["errors"][0]
        return (body["code"], body["recovery"], body.get("field") or "")

    def test_every_protocol_transport_returns_the_same_triple(self, integration_db):
        """MCP, REST and A2A refuse the same short secret with the same triple.

        A2A was the leg that differed before GH #1802: its document is
        destructured to primitives and rebuilt in
        ``src/core/webhooks/registration.py``, which used to re-validate with a
        padded secret and restore the buyer's short one — so the create succeeded
        and the webhook was refused later, inside the sender, as
        ``credentials_too_short``. All three refuse at ingest now; they stay in
        the parametrization so the ASSERTION IS EQUIVALENCE rather than three
        independent claims.
        """
        verdicts: dict[str, tuple[str, str, str]] = {}
        for transport in (Transport.MCP, Transport.REST, Transport.A2A):
            with MediaBuyCreateEnv() as env:
                _tenant, _principal, product, _pricing = env.setup_media_buy_data()

                result = env.call_via(
                    transport,
                    **_create_kwargs(product, {"schemes": ["HMAC-SHA256"], "credentials": SHORT_CREDENTIAL}),
                )

                assert result.is_error, (
                    f"{transport.value}: a shared secret shorter than the pinned minimum must be refused "
                    "at ingest — accepting it stores a registration this seller has already decided it "
                    f"will never sign. Got: {getattr(result, 'wire_response', None) or result.payload!r}"
                )
                verdicts[transport.value] = self._verdict(result)
                _assert_no_push_config_persisted(env._tenant_id, env._principal_id)

        distinct = set(verdicts.values())
        assert len(distinct) == 1, (
            "the same buyer mistake must not depend on which wire it arrived on; per-transport "
            f"(code, recovery, field) were {verdicts}"
        )
        code, recovery, field = distinct.pop()
        assert (code, recovery) == self._EXPECTED, f"(code, recovery)=({code!r}, {recovery!r})"
        assert field.endswith("authentication.credentials"), (
            f"error.field is {field!r}; the buyer must be pointed at the secret, not at a URL that is fine"
        )

    def test_the_admin_form_reaches_the_same_verdict(self, integration_db, authenticated_admin_client, monkeypatch):
        """The fourth surface, and the one the carve-out's own justification never covered.

        ``accept_push_notification_primitives`` is called from
        ``src/admin/blueprints/principals.py`` with ``field_prefix="webhook"`` —
        an Admin HTML form, no A2A anywhere — so the "the A2A ``configuration``
        envelope is a transport-layer parameter" premise never applied to it.
        RED before GH #1802 for the same reason as the A2A leg above.

        The admin surface answers in flashes, not envelopes, so the shapes cannot
        be compared byte-for-byte. What must match is the VERDICT: refused,
        nothing stored, and the operator pointed at the credential.
        """
        from tests.helpers.webhook_credential_refusal import assert_admin_flash_refuses_the_credential
        from tests.integration.test_admin_ingest_url_policy import flashes, post_register_hmac_webhook
        from tests.integration.test_outbound_http import set_flags

        set_flags(monkeypatch, private=True)

        with MediaBuyCreateEnv() as env:
            env.setup_media_buy_data()

            response = post_register_hmac_webhook(
                authenticated_admin_client,
                _ADMIN_SAFE_URL,
                SHORT_CREDENTIAL,
                tenant_id=env._tenant_id,
                principal_id=env._principal_id,
            )

            assert response.status_code == 302
            assert_admin_flash_refuses_the_credential(flashes(authenticated_admin_client), secret=SHORT_CREDENTIAL)
            _assert_no_push_config_persisted(env._tenant_id, env._principal_id)


class TestSchemaTypedTransportsRefuseTheSameDocument:
    """REST refuses at AdCP schema conformance, one layer above the gate.

    The REST request model validates ``push_notification_config`` against the
    spec model, whose ``Authentication`` requires ``credentials``, so the
    document is rejected before ``_impl`` (and therefore before the gate) ever
    sees it. Same OUTCOME as the gate, earlier MECHANISM.

    What is asserted here is everything the buyer contract needs — refusal, wire
    code, recovery semantics, that the named field is the CREDENTIAL and not the
    URL, and that nothing persisted.

    The ``push_notification_config.`` prefix USED to diverge here: this layer
    derived ``field`` from Pydantic's ``loc``, which is relative to the sub-model
    being validated, so it emitted ``authentication.credentials`` while the
    registration gate emitted the absolute path. Epic D lane C3 closed that fork
    for THIS field — ``to_push_notification_config`` now qualifies the derived
    location, so every transport reports
    ``push_notification_config.authentication.credentials``, which is what FastMCP
    already emitted and what the gate raises.

    The ``endswith`` assertions below are RETAINED anyway, deliberately: the
    broader inconsistency across every OTHER field this validator reports is
    gh-#1895 and is still open, so a suffix assertion here keeps this case honest
    about what it is pinning — the credential naming — rather than quietly
    becoming a prefix regression test for a change it does not own.
    """

    @pytest.mark.parametrize(
        ("authentication", "expected_field_suffix"),
        [
            pytest.param({"schemes": ["HMAC-SHA256"]}, "authentication.credentials", id="credentials-key-absent"),
            pytest.param(
                {"schemes": ["HMAC-SHA256"], "credentials": None}, "authentication.credentials", id="credentials-null"
            ),
            pytest.param(
                {"schemes": ["HMAC-SHA256"], "credentials": ""},
                "authentication.credentials",
                id="credentials-empty-string",
            ),
            # On a schema-typed transport the lowercase spelling is refused for
            # the SCHEME, not the credential: ``hmac-sha256`` is not a member of
            # the pinned AuthenticationScheme enum, so the document is invalid
            # before the credential requirement is even reached. That is the
            # correct outcome and a stricter one — the untyped A2A path, where no
            # enum guards the value, is where the gate has to catch this spelling.
            pytest.param(
                {"schemes": ["hmac-sha256"]}, "authentication.schemes[0]", id="credentials-absent-lowercase-scheme"
            ),
        ],
    )
    def test_rest_refuses_at_schema_conformance(self, integration_db, authentication, expected_field_suffix):
        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()

            result = env.call_via(Transport.REST, **_create_kwargs(product, authentication))

            assert result.is_error, (
                "REST must refuse a push_notification_config asking for HMAC-SHA256 with no "
                "credentials. Accepting it means the request model stopped validating the config "
                f"against the AdCP spec. Got: {getattr(result, 'wire_response', None) or result.payload!r}"
            )
            envelope = result.error_envelope()
            assert_envelope_shape(envelope, "VALIDATION_ERROR", recovery="correctable")
            for layer, body in (("adcp_error", envelope["adcp_error"]), ("errors[0]", envelope["errors"][0])):
                field = body.get("field") or ""
                assert field.endswith(expected_field_suffix), (
                    f"REST: {layer}.field is {field!r}, expected it to name {expected_field_suffix!r} — "
                    f"the buyer must be pointed at the part of their document that is actually wrong"
                )
            _assert_no_push_config_persisted(env._tenant_id, env._principal_id)


class TestMcpTypedParameterRefusesTheSameDocument:
    """MCP's typed wrapper refuses one layer earlier — pin that it still does.

    ``create_media_buy``'s MCP signature annotates the parameter as
    ``adcp.PushNotificationConfig``, whose ``Authentication`` model requires
    ``credentials``. So this document never reaches ``_impl`` on MCP at all and
    the buyer is refused by FastMCP's TypeAdapter. That is the right OUTCOME by
    a different MECHANISM, which is exactly why it is pinned separately: relax
    the annotation to ``dict`` for "forward compatibility" and this transport
    silently joins the untyped ones.

    Deliberately NOT asserting the credentials field path here — the refusal
    comes from schema validation, not from the gate, and demanding the gate's
    envelope would be demanding that the typed wrapper stop typing.
    """

    def test_mcp_refuses_and_persists_nothing(self, integration_db):
        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()

            result = env.call_via(Transport.MCP, **_create_kwargs(product, {"schemes": ["HMAC-SHA256"]}))

            assert result.is_error, (
                "MCP's typed push_notification_config parameter must reject an authentication "
                "block with no credentials; accepting it means the annotation stopped validating. "
                f"Got: {getattr(result, 'wire_response', None) or result.payload!r}"
            )
            _assert_no_push_config_persisted(env._tenant_id, env._principal_id)
