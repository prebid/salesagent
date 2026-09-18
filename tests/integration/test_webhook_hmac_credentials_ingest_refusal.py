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
3. ``enums/error-code.json`` ``enumDescriptions`` settles the CODE, and the
   two members are defined against each other:

     INVALID_REQUEST  — "Request is malformed, missing required fields, or
                         violates schema constraints."
     VALIDATION_ERROR — "Request contains invalid field values or violates
                         business rules beyond schema validation."

   Point 1 already established that this document violates the pinned schema:
   ``credentials`` is REQUIRED and carries ``minLength: 32``. "Missing required
   fields" and "violates schema constraints" are the literal words, so the code
   is ``INVALID_REQUEST``. ``enumMetadata`` gives BOTH members
   ``recovery: "correctable"``, so recovery cannot discriminate them; the buyer
   is still the only party who can supply the secret, and supplying it makes the
   identical request succeed, so ``correctable`` is right for the reason it was
   always right.

The spec does not say a seller MUST refuse such a registration — that half is
genuinely silent, and the conformance storyboard grades no such step (see
below). But it is NOT silent on which code a refusal carries, because the
refused document violates a schema constraint and ``error-code.json`` assigns
that case by name. This module previously reasoned from the silence to the
sibling gate one field over — ``reject_unsafe_webhook_registration_url``
(``src/core/webhook_validator.py``) → ``VALIDATION_ERROR`` — and so copied a
code for a question the pin had already answered. The sibling keeps
``VALIDATION_ERROR`` correctly: a deny-listed host is a SCHEMA-VALID
``format: "uri"`` string refused by seller policy, which is "business rules
beyond schema validation". Same envelope shape, different halves of one
sentence pair.

Production already encodes exactly this split, and emits ``INVALID_REQUEST``
here because it is right to: ``src/core/exceptions.py:1266-1281`` maps a pydantic
``ValidationError`` ("BY CONSTRUCTION a schema-constraint violation") to
``AdCPInvalidRequestError``, and leaves a plain ``ValueError`` from business
logic on ``AdCPValidationError``. Every surface graded here refuses through the
pinned request model.

Asserted from ``tests/helpers/webhook_credential_refusal.py``, which owns this
contract for the protocol AND admin surfaces so they cannot drift from it.

Conformance storyboard: UNGRADED — nothing in ``dist/compliance/3.1.1/`` grades
a seller refusing an unservable webhook registration (same finding recorded for
the sibling URL refusal, ``test_webhook_url_ingest_refusal.py``).

Transport coverage is per-surface, and the surfaces no longer differ in
MECHANISM the way they did when this module was written. Every transport now
enters through ``src/core/tools/_boundary.py`` — MCP (``src/core/main.py``), A2A
(``src/a2a_server/adcp_a2a_server.py``) and REST (``src/routes/api_v1.py``) each
call ``serve()``, which runs ``validated_request()`` →
``CreateMediaBuyRequest.model_validate(raw)``, and that DTO types the parameter
as ``PushNotificationConfig | None``. So the invalid document is refused at
schema conformance on all three, and there is no longer a transport that hands a
raw dict to ``_impl`` untouched (the per-transport wrappers that did —
``create_media_buy_raw`` and its siblings — are gone; tools are rows in
``src/core/tools/registry.py``).

That symmetry is the reason every surface is STILL graded separately rather than
collapsed into one case. What each class below pins is that its surface reaches
the same buyer verdict; the equivalence is asserted directly in
:class:`TestShortCredentialReachesOneVerdictOnEverySurface`. Relax the DTO field
to ``dict`` for "forward compatibility" and one of these reddens instead of a
hole opening silently.

Buyer-visible change (named here as well as in the PR, not smuggled): a
create_media_buy that TODAY succeeds with ``schemes: ["HMAC-SHA256"]`` and no
credentials starts failing with a correctable INVALID_REQUEST. That is the
intent.
"""

from __future__ import annotations

import pytest

from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.transport import Transport
from tests.helpers.adcp_factories import create_test_media_buy_request_dict
from tests.helpers.envelope_assertions import assert_envelope_shape
from tests.helpers.signing import inbound_verifier_disabled
from tests.helpers.webhook_credential_refusal import SHORT_CREDENTIAL, assert_credentials_refusal_envelope

# The persistence assertion for this exact table, already written for the
# sibling URL refusal. Imported rather than re-implemented: "the repository
# upsert is the single write funnel, so an empty active list IS 'the refusal
# preceded the store'" is one fact about one table, and two copies of it would
# drift the moment the funnel moves. Its home is the suite-local helper module,
# not the sibling suite that used to own it, because a module whose job is to BE
# a test must not also be a helper library
# (``tests/unit/test_architecture_no_cross_test_module_imports.py``).
from tests.integration._egress_ingest_helpers import _assert_no_push_config_persisted

# ``declared_refusals`` — the spy that captures the typed refusals the admin
# route declares. Under ADR-010 the flash carries CODE_TABLE's sentence for the
# code and nothing else, so WHICH FIELD was refused travels on the channel the
# route hands the whole error to (``record_admin_action_failure``); this is that
# channel. At module level rather than inside the test because pytest resolves
# fixtures from the module namespace at collection time.
#
# Still imported from the sibling SUITE because that is where the fixture lives
# in this tree — ``tests/helpers/webhook_credential_refusal.py`` reaches the same
# module for ``assert_webhook_registration_refused``. Moving both into
# ``_egress_ingest_helpers`` is the guard-clean end state and is not this file's
# to do.
from tests.integration.test_admin_ingest_url_policy import declared_refusals  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# A URL that passes the registration SSRF gate (the gate that runs immediately
# before this one), so the ONLY possible refusal is about the credential. Same
# host the gate's own "allows public" case uses.
_SAFE_URL = "https://buyer.example.com/hook"

# The A2A leg, graded on its own because it is the one that historically let the
# document through.
#
# This list is NAMED for what it once was: the transports that carried the
# buyer's document to ``_impl`` unvalidated, where the refusal came from the
# ingest GATE rather than from schema conformance. REST left it first (its
# request model validated ``push_notification_config`` against the spec model),
# and A2A left it when the per-transport wrappers were replaced by
# ``serve()``/``validated_request()``, which validates every payload against
# ``CreateMediaBuyRequest`` regardless of wire. So A2A now refuses by the same
# MECHANISM as MCP and REST.
#
# It stays a separate case rather than folding into
# :class:`TestSchemaTypedTransportsRefuseTheSameDocument` because the OUTCOME
# this grades is the gate's contract — INVALID_REQUEST / correctable / a field
# naming the credential, nothing persisted — and that contract must hold on A2A
# whichever layer happens to enforce it. A future change that reintroduces an
# untyped A2A payload path reddens here, which is the point.
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
    # MOVED OUT of this list by Epic D lane C3 (salesagent-fo99.3): the A2A TOOL
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


@pytest.fixture(autouse=True)
def _not_a_verifying_agent(monkeypatch):
    """This module's agent does not verify inbound signatures.

    Every scenario here grades the INGEST gate's verdict on a webhook credential's
    SHAPE, and every one of them registers ``push_notification_config.authentication``.
    On an agent that CAN verify, that block forces a signature "regardless of
    ``required_for`` membership" (security.mdx @ v3.1.1 :1462-1465,
    ``src.core.signing.webhook_credentials.registers_webhook_credentials``, read at the
    boundary by ``invoke_tool``), so the REST leg is refused with a bodyless 401
    — no body, so no AdCP envelope, so the assertions fail with "no error envelope
    captured" instead of on the obligation they exist to grade.

    ``SigningSettings.verifier_enabled`` is the lever rather than a per-tenant
    ``request_signing {supported: false}`` because the pin defines that field as an
    AGENT-level fact and ``verify_inbound_signature`` runs inside ``_resolve_identity``
    for every request this process serves; see
    :func:`tests.helpers.signing.inbound_verifier_disabled`. It is sound HERE because
    every transport this module dispatches on (MCP, REST, A2A) runs in THIS process.

    The sibling BDD ``@egress`` scenarios use the declared-posture lever instead, and
    must: they also run over ``e2e_rest``, whose server is a separate process this
    patch cannot reach.

    Autouse rather than a per-test seeding call — a per-test call is the line the next
    case added here will forget, and forgetting it fails as "no error envelope
    captured" rather than as a missing declaration.
    """
    with inbound_verifier_disabled(monkeypatch):
        yield


class TestCreateMediaBuyRefusesHmacRegistrationWithoutCredentials:
    """The untyped transports must refuse the document ``_impl`` actually receives."""

    @pytest.mark.parametrize("transport", _UNTYPED_TRANSPORTS, ids=lambda t: t.value)
    @pytest.mark.parametrize("authentication", _HMAC_WITHOUT_CREDENTIALS)
    def test_refused_at_ingest_naming_the_credentials_field(self, integration_db, transport, authentication):
        """INVALID_REQUEST / correctable / field=...authentication.credentials, nothing persisted."""
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
                # ``hmac-sha256`` is outside the pinned ``enums/auth-scheme.json``
                # membership, so this document violates a schema constraint —
                # INVALID_REQUEST by ``enums/error-code.json``, exactly as the
                # credential cases above. See the module docstring.
                "INVALID_REQUEST",
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
    """REGRESSION PIN (salesagent-pldmk.8) — not the lane's red.

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

    # A 31-character secret violates the pinned ``credentials.minLength: 32``, and
    # ``enums/error-code.json`` assigns a schema-constraint violation to
    # INVALID_REQUEST. ``correctable`` is the recovery BOTH candidate codes carry
    # in ``enumMetadata``, so it discriminates nothing on its own — the pair is
    # asserted together so a code/recovery mismatch cannot slip through.
    _EXPECTED = ("INVALID_REQUEST", "correctable")

    @staticmethod
    def _verdict(result) -> tuple[str, str, str]:
        """The (code, recovery, field) triple the buyer reads, from the wire envelope."""
        envelope = result.error_envelope()
        body = envelope["errors"][0]
        return (body["code"], body["recovery"], body.get("field") or "")

    def test_every_protocol_transport_returns_the_same_triple(self, integration_db):
        """MCP, REST and A2A refuse the same short secret with the same triple.

        A2A was the leg that differed before salesagent-pldmk.8: its document is
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

    def test_the_admin_form_reaches_the_same_verdict(
        self,
        integration_db,
        authenticated_admin_client,
        monkeypatch,
        declared_refusals,  # noqa: F811 - the imported fixture, requested by name
    ):
        """The fourth surface, and the one the carve-out's own justification never covered.

        ``accept_push_notification_primitives`` is called from
        ``src/admin/blueprints/principals.py`` with ``field_prefix="webhook"`` —
        an Admin HTML form, no A2A anywhere — so the "the A2A ``configuration``
        envelope is a transport-layer parameter" premise never applied to it.
        RED before salesagent-pldmk.8 for the same reason as the A2A leg above.

        The admin surface answers in flashes, not envelopes, so the shapes cannot
        be compared byte-for-byte. What must match is the VERDICT: refused,
        nothing stored, and the operator pointed at the credential.

        The operator is pointed at the credential on the channel that carries it
        under ADR-010 — the typed refusal the route declares, captured by
        ``declared_refusals`` — because the flash is now CODE_TABLE's sentence
        for the code and names no field at all. The helper owns which code and
        which field a credential refusal must be; this test owns driving the
        real form and reading the table back.
        """
        from tests.helpers.webhook_credential_refusal import assert_admin_flash_refuses_the_credential
        from tests.integration._egress_ingest_helpers import post_register_hmac_webhook

        # ``set_flags`` is the INJECTED hatch (``inject_limits`` onto
        # ``limits.adcp_outbound_allow_private``), not the retired
        # ``ADCP_OUTBOUND_ALLOW_PRIVATE`` environ write: the seam reads
        # ``get_settings().limits`` off a settings object built once and cached, so a
        # ``setenv`` reaches it only if nothing has resolved settings yet — a hatch that
        # silently stays shut and decides this case by accident.
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
            assert_admin_flash_refuses_the_credential(
                authenticated_admin_client, declared_refusals, secret=SHORT_CREDENTIAL
            )
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
            # Every parametrization above is a PINNED-SCHEMA violation — a missing
            # required ``credentials``, one under ``minLength: 32``, or a scheme
            # outside the ``auth-scheme.json`` enum. ``enums/error-code.json``
            # assigns that case to INVALID_REQUEST ("violates schema
            # constraints"), not to VALIDATION_ERROR ("beyond schema
            # validation"). REST reaches it through the typed request model,
            # which is the mechanism the code names.
            assert_envelope_shape(envelope, "INVALID_REQUEST", recovery="correctable")
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
