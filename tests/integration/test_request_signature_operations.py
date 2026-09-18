"""The operation a request is graded AS, and the rule that outranks its bucket.

WHAT #1721 DID TO THIS FILE'S SUBJECT
-------------------------------------
Pre-merge, the verifier was an ASGI middleware ABOVE authentication and above transport
dispatch, so it had to NAME the operation itself: sniff an anonymous body, reconcile four
transport-local name registries, and promote whatever it could not name.
``src/core/signing/operations.py`` was that resolver, and most of this module graded it.

It does not exist. On #1721 the verifier runs inside ``_resolve_identity``, which
``invoke_tool`` reaches only AFTER ``TOOLS[tool_name]`` resolved, and the name it grades is
the registry key the transport dispatched on
(``SignatureSubject(operation=tool_name, ...)``, ``src/core/tools/_boundary.py``). The
operation IS the registry key, on every transport, by construction. So:

* "the same logical call carries the same name on every surface" is no longer a
  reconciliation to grade — MCP registration, the A2A agent card and the ``/api/v1`` router
  are all GENERATED from ``TOOLS`` — and it is observed end to end, on all three legs, by
  ``tests/integration/test_harness_signed_dispatch.py``;
* the namespace split (``protocol_methods_*`` against the AdCP buckets) moved, whole, to
  ``tests/unit/test_request_signing_namespace_split.py``, which grades it at config time AND
  through ``verify_inbound_signature`` itself;
* "an anonymous body cannot mint a Prometheus series with ``params.name``" is
  unrepresentable: a name no registry serves never reaches the boundary at all. The bound is
  graded structurally by
  ``tests/unit/test_architecture_signing_operations.py::TestTheOperationLabelIsBoundedByADerivedSet``;
* "an unsigned body survives the buffer" is graded on the capture itself by
  ``tests/unit/test_signed_exchange_capture.py`` — lossless replay, over-cap flagged and
  still replayed, non-AdCP paths untouched. Nothing rewrites a request body between the
  signer and the capture any more (``src/routes/rest_compat_middleware.py`` is deleted);
* "a session frame is not an unresolvable request" has no subject left. There is no
  promote-what-you-cannot-name rule to bypass, because a frame that names no registry row
  never reaches a verifier: a bodiless ``GET /mcp`` and an unparseable POST are answered by
  the transport, above the boundary.

WHAT SURVIVES, AND WHY IT IS HERE AND NOT IN A UNIT TEST
--------------------------------------------------------
Three obligations whose subject is the WIRE, and which no unit call on
``verify_inbound_signature`` can state, because each turns on something only a real request
carries — the route the verb and path resolved to, the VALIDATED request the escalation is
read off, or the byte count the capture measured:

1. **The verb is part of the operation's identity.** ``POST /api/v1/media-buys`` is
   ``create_media_buy`` and ``PUT /api/v1/media-buys/{id}`` is ``update_media_buy``. A
   declaration naming one must not grade the other.
2. **Webhook credentials outrank the bucket** (security.mdx @ v3.1.1 :1462-1465, restated at
   :1375 as firing "regardless of ``required_for`` membership"), and BOTH triggers do. Only
   ``push_notification_config.authentication`` has a compliance vector, so a reader handling
   it alone passes all 40 and is still wrong; the ``accounts[].notification_configs[]``
   trigger is the one graded here.
3. **A signed body too large to hash** is refused where the seller verifies and served where
   it does not.

Every rejection has a control that must NOT be rejected under a declaration differing in ONE
variable, and every acceptance is paired with a POSITIVE observable
(:func:`_assert_verifier_looked`) — an absence of rejection is byte-identical to a verifier
that never ran.

THE ORACLE IS THE CHALLENGE, NEVER THE STATUS. ``rejection_code`` reads
``WWW-Authenticate: Signature error="<code>"`` off the response (``AuthChallengeResponder``,
``src/core/auth_middleware.py``, writes it from the code on the finished body). A bare 401 is
equally produced by the bearer refusal one step earlier, and a "not 200" oracle is satisfied
by the application refusing the same request for an entirely different reason.

TWO THINGS THIS FILE DELIBERATELY DOES NOT DRIVE, stated rather than silently dropped:

* an ANONYMOUS unsigned call to a PROTECTED row. ``_resolve_identity`` step 2 answers
  ``AUTH_MISSING`` before the verifier is reached, which is correct — nothing was presented —
  so the anonymous half of the composition rule is graded on the PUBLIC rows below and, for
  the rest, by ``tests/unit/test_request_signature_composition_rule.py``;
* the FIRST escalation trigger unsigned. It is already driven through a validated request by
  ``tests/integration/test_credential_block_is_logged.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from adcp.signing import (
    REQUEST_SIGNATURE_DIGEST_MISMATCH,
    REQUEST_SIGNATURE_HEADER_MALFORMED,
    REQUEST_SIGNATURE_REQUIRED,
)

from src.core.tools.registry import TOOLS

# The conformant request baselines (CLAUDE.md pattern #8 — factories, never inline dicts).
# Imported from the module that DEFINES them rather than from the package: two of the five
# are not re-exported by ``tests/factories/__init__.py``, and a payload hand-written because
# an export was missing is exactly the inline dict the pattern forbids.
from tests.factories.request import (
    CreateMediaBuyRequestFactory,
    GetAdcpCapabilitiesRequestFactory,
    ListCreativeFormatsRequestFactory,
    SyncAccountsRequestFactory,
    UpdateMediaBuyRequestFactory,
)
from tests.harness._base import BareIntegrationEnv

# The seams, the shared tenant/surface constants and the shared request builders all come
# from their single home (salesagent-z6nr.14 step 2). What stays below is only what this
# suite alone uses — the per-surface paths and payload overrides.
from tests.helpers.signing import (
    CAPABILITIES_ADCP_PATH,
    COUNTERPARTY_KID,
    FAILED_METRIC,
    SIGNING_PRINCIPAL_ID,
    SIGNING_TENANT_ID,
    bucketed_declaration,
    counter_total,
    counterparty_key,
    keypair_for,
    narrowed_none,
    request_headers,
    samples_with,
    seed_principal,
    signed_headers,
    signing_config,
    tampered_signing_body,
    unsupported,
)
from tests.helpers.signing import (
    declared_posture as _declared_posture,
)
from tests.helpers.signing import (
    rejection_code as _rejection_code,
)

#: Module-wide, and load-bearing rather than defensive. ``SignedExchangeCapture`` drains the
#: ASGI receive channel to read the body and replays it downstream, and the failure mode of a
#: replay that yields nothing is a DEADLOCK, not a short body: the handler awaits a body that
#: will never arrive while Starlette's test transport awaits a response that will never be
#: sent. ``method="thread"`` is what makes it terminate — the default signal timeout fires and
#: then the portal's own teardown deadlocks on the same coroutine, so the run hangs anyway. A
#: correct implementation never reaches this.
pytestmark = pytest.mark.timeout(60, method="thread")

#: The operations this module addresses, spelled as constants because a declaration and the
#: path it is supposed to bucket have to be linked by something. On the pre-merge branch that
#: link could only be measured off a metric label, since four registries could disagree about
#: it; here ``TOOLS`` is the authority and
#: :meth:`TestTheOperationIsTheRegistryKey.test_the_route_table_binds_each_verb_and_path_to_its_own_operation`
#: reads it directly.
_CREATE_MEDIA_BUY_OPERATION = "create_media_buy"
_UPDATE_MEDIA_BUY_OPERATION = "update_media_buy"
_SYNC_ACCOUNTS_OPERATION = "sync_accounts"
_CAPABILITIES_OPERATION = "get_adcp_capabilities"
_UNREQUIRED_PUBLIC_OPERATION = "list_creative_formats"

_CREATE_MEDIA_BUY_PATH = "/api/v1/media-buys"
_UPDATE_MEDIA_BUY_TEMPLATE = "/api/v1/media-buys/{media_buy_id}"
_SYNC_ACCOUNTS_PATH = "/api/v1/accounts/sync"
_UNREQUIRED_PUBLIC_PATH = "/api/v1/creative-formats"

#: The buy an update addresses. It need not exist: the account and the buy are loaded AFTER
#: the signature is read (``_resolve_identity`` step 4b precedes the account lookup), so a
#: refusal under test happens first and the control cases are blind to what happens later.
_MEDIA_BUY_ID = "mb_signature_probe"
_UPDATE_MEDIA_BUY_PATH = _UPDATE_MEDIA_BUY_TEMPLATE.format(media_buy_id=_MEDIA_BUY_ID)

#: A webhook registration carrying credentials — the thing security.mdx :1465 says a seller
#: supporting request signing MUST NOT accept unsigned. ``schemes``/``credentials`` is the
#: PINNED shape (``core/push-notification-config.json`` @ 3.1.1) and the secret clears the
#: ``minLength: 32`` floor: a shorter one is refused as a validation error before the request
#: reaches the boundary at all, and the test would then pass for the wrong reason.
_WEBHOOK_AUTHENTICATION = {"schemes": ["HMAC-SHA256"], "credentials": "webhook-shared-secret-0123456789abcdef"}

#: Small enough to make the over-cap path a millisecond test rather than a ten-megabyte one,
#: large enough that the padding below exceeds it.
_TEST_BODY_CAP = 4096

#: Padding long enough to push any body it appears in past :data:`_TEST_BODY_CAP`.
_PADDING = "P" * (_TEST_BODY_CAP * 2)

#: Signature headers whose CONTENT is never parsed on either arm of the over-cap case: their
#: PRESENCE alone is what ``HttpExchange.presents_signature`` reads, and both arms return
#: before any parse — ``supported: false`` at the posture gate, and the over-cap branch which
#: sits ahead of ``_strict_header_precheck``.
_PRESENT_SIGNATURE_HEADERS = {
    "Signature-Input": 'sig1=("@method");created=1',
    "Signature": "sig1=:AAAA:",
}


# --------------------------------------------------------------------------
# Declarations and payloads
# --------------------------------------------------------------------------


def _supported_only() -> dict[str, Any]:
    """``supported: true`` with no operation narrowing — vector 027's shape.

    ``required_for`` is EXPLICITLY empty and ``supported_for`` is left ABSENT, which
    ``posture.py`` reads as "verify wherever signatures appear". So no rejection this posture
    produces can have come from bucket membership — only from the payload escalation.
    """
    return {"supported": True, "required_for": []}


def _account_registration(*, with_authentication: bool) -> dict[str, Any]:
    """A ``sync_accounts`` body registering one account's notification config.

    The SECOND escalation trigger's surface. ``with_authentication`` is the ONE variable
    between the refusal and its control: the two bodies are otherwise identical, so a
    rejection can only be attributed to the credentials in the payload.

    Built from ``SyncAccountsRequestFactory`` (CLAUDE.md pattern #8) and overridden into the
    SETTINGS-UPDATE branch of the pin's ``oneOf``, which is the branch that takes an existing
    ``account`` — the provisioning branch the factory defaults to requires
    ``brand``+``operator``+``billing`` and carries no account to attach a config to.
    """
    config: dict[str, Any] = {
        "subscriber_id": "sub-signature-probe",
        "url": "https://buyer.example.com/account-webhook",
        "event_types": ["scheduled"],
    }
    if with_authentication:
        config["authentication"] = dict(_WEBHOOK_AUTHENTICATION)
    return SyncAccountsRequestFactory.payload(
        accounts=[{"account": {"account_id": "acct-signature-probe"}, "notification_configs": [config]}]
    )


# --------------------------------------------------------------------------
# Observation helpers
# --------------------------------------------------------------------------


def _json_headers(token: str | None) -> dict[str, str]:
    return request_headers(token, {"Content-Type": "application/json"})


def _as_json_bytes(payload: dict[str, Any]) -> bytes:
    """The exact bytes a signed request sends, so the digest covers what arrives.

    Serialized ONCE here and handed to both the signer and the client as ``content=``.
    Letting httpx re-serialize (``json=``) after the dict was signed would put different
    bytes on the wire than the ``content-digest`` covers, which surfaces as
    ``request_signature_digest_mismatch`` on every case — including the ones meant to pass —
    i.e. a fixture bug wearing a verifier bug's clothes.
    """
    return json.dumps(payload).encode()


def _assert_rejected(response: Any, why: str, *, code: str = REQUEST_SIGNATURE_REQUIRED) -> None:
    """The verifier answered the spec's 401 with the challenge *code*.

    *code* is DEFAULTED rather than required so the unsigned escalation callers keep their
    exact oracle — ``request_signature_required``, the code an unsigned refusal carries —
    while the signed cases name the code their own failure earns. Widening the helper to "any
    401" would weaken every caller at once: a bare 401 is equally produced by the bearer
    refusal one step earlier in ``_resolve_identity``, and by a 404 wearing a 401.
    """
    assert _rejection_code(response) == code, (
        f"{why}\nExpected 401 + WWW-Authenticate: Signature "
        f'error="{code}"; got status {response.status_code} with '
        f"WWW-Authenticate={response.headers.get('WWW-Authenticate')!r}"
    )


def _assert_not_rejected(response: Any, why: str) -> None:
    """The verifier did not reject — whatever the transport answered afterwards.

    Deliberately blind to the downstream status: these are the CONTROL cases, and what they
    grade is that the signature verifier let the request past. Whether the application then
    refused it for a domain reason is a different obligation with different graders.

    Pair this with :func:`_assert_verifier_looked` around the request itself. On its own it
    only rules out a rejection; it cannot distinguish "the verifier considered this and
    allowed it" from "the verifier never ran".
    """
    assert _rejection_code(response) is None, (
        f"{why}\nThe verifier rejected it: status {response.status_code}, "
        f"WWW-Authenticate={response.headers.get('WWW-Authenticate')!r}"
    )


@contextmanager
def _assert_verifier_looked() -> Iterator[None]:
    """The verifier ACTUALLY RAN and reached a verdict for an unsigned request.

    Absence-of-rejection alone is worthless as an acceptance signal — it is byte-identical to
    a verifier that never looked, which is true whenever the kill switch is off, the operation
    is in the ``none`` bucket, or the posture is ``warn``. So the control cases assert a
    POSITIVE observable instead: ``verify_inbound_signature`` increments
    ``adcp_request_unsigned_total`` through ``record_request_unsigned`` on exactly the path
    that decides an unsigned request may proceed. A boundary that returned early emits nothing
    and the delta is zero.

    Reads the counter through ``counter_total`` (tests/helpers/signing.py) rather than
    hand-rolling a before/after pair — that idiom was open-coded at eight sites across three
    modules, and a ninth would make it worse.
    """
    before = counter_total("adcp_request_unsigned_total")
    yield
    after = counter_total("adcp_request_unsigned_total")
    assert after > before, (
        "the verifier never recorded a verdict for this request: "
        f"adcp_request_unsigned_total did not move ({before} -> {after}). A non-rejection is "
        "equally true of a boundary that returned early — kill switch off, 'none' bucket, or "
        "'warn' posture — so this control proves nothing without the counter."
    )


@contextmanager
def _body_cap(max_bytes: int) -> Iterator[None]:
    """Shrink ``max_signed_body_bytes`` for the duration of one request.

    The production default is 10 MiB, and shrinking it is what makes the over-cap path
    testable in milliseconds instead of by posting ten megabytes. ``signing_config`` replaces
    the whole ``SigningSettings`` on the process-global settings object, which is the object
    BOTH readers of this value hold — ``SignedExchangeCapture`` when it decides how much to
    buffer, and ``verify_inbound_signature`` when it decides what to do about it — so one
    substitution reaches both.
    """
    with signing_config(max_signed_body_bytes=max_bytes):
        yield


# --------------------------------------------------------------------------
# The name is the registry key, and the verb is part of it
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestTheOperationIsTheRegistryKey:
    """A declaration naming one operation grades that operation and no neighbour.

    The cross-TRANSPORT half of "same logical call ⇒ same operation" is graded on all three
    legs by ``tests/integration/test_harness_signed_dispatch.py``, and it is structural here:
    every transport dispatches on ``TOOLS`` before the boundary runs. What is left to grade is
    the half no generator makes true by itself — that two rows sharing a path SHAPE are two
    operations, and that a declaration naming one of them leaves the other alone.
    """

    def test_the_route_table_binds_each_verb_and_path_to_its_own_operation(self, integration_db):
        """The paths this module posts to really are the operations it buckets.

        Without this, every declaration below could name an operation its path does not
        resolve to, which would leave the surface under test in the ``none`` bucket — waved
        through unverified, refused (or not) for some other reason, and green either way.

        Read off ``TOOLS`` because on #1721 the registry is the authority:
        ``src/routes/api_v1.py`` adds one route per ``rest`` binding under
        ``name=<registry key>``, so a row's ``RestBinding`` IS its REST surface, verb
        included. The media-buys pair is the one that makes the verb load-bearing rather than
        decorative: one path shape, two rows, two operations.
        """
        bound = {
            (spec.rest.verb, f"/api/v1{spec.rest.path}"): name for name, spec in TOOLS.items() if spec.rest is not None
        }
        assert bound[("POST", _CREATE_MEDIA_BUY_PATH)] == _CREATE_MEDIA_BUY_OPERATION
        assert bound[("PUT", _UPDATE_MEDIA_BUY_TEMPLATE)] == _UPDATE_MEDIA_BUY_OPERATION, (
            "the verb is part of the operation's identity: one path shape, two rows"
        )
        assert bound[("POST", _SYNC_ACCOUNTS_PATH)] == _SYNC_ACCOUNTS_OPERATION
        assert bound[("POST", CAPABILITIES_ADCP_PATH)] == _CAPABILITIES_OPERATION
        assert bound[("POST", _UNREQUIRED_PUBLIC_PATH)] == _UNREQUIRED_PUBLIC_OPERATION

    def test_an_unsigned_call_to_a_required_operation_is_refused(self, integration_db):
        """The composition rule's first bullet, on the wire (security.mdx :1268, vector 001).

        A PUBLIC row, deliberately: ``_resolve_identity`` refuses an anonymous call to a
        protected row with ``AUTH_MISSING`` before the verifier is reached, which is correct —
        nothing was presented — so the only surface that can state "refused for the MISSING
        SIGNATURE" is one that would otherwise have been served anonymously.
        """
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            seed_principal(env)
            client = env.get_rest_client()

            with _declared_posture(**bucketed_declaration("required", _CAPABILITIES_OPERATION)):
                response = client.post(
                    CAPABILITIES_ADCP_PATH,
                    json=GetAdcpCapabilitiesRequestFactory.payload(),
                    headers=_json_headers(None),
                )

            _assert_rejected(
                response,
                f"POST {CAPABILITIES_ADCP_PATH} is {_CAPABILITIES_OPERATION}, which this posture puts in "
                "required_for; the caller presented no credential this agent accepts",
            )

    def test_a_different_operation_under_the_same_declaration_is_not_refused(self, integration_db):
        """The control that makes the refusal above non-vacuous.

        A posture that blanket-promoted everything — or a boundary that graded "an AdCP
        request happened" rather than WHICH one — answers 401 here.
        """
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            seed_principal(env)
            client = env.get_rest_client()

            with (
                _declared_posture(**bucketed_declaration("required", _CAPABILITIES_OPERATION)),
                _assert_verifier_looked(),
            ):
                response = client.post(
                    _UNREQUIRED_PUBLIC_PATH,
                    json=ListCreativeFormatsRequestFactory.payload(),
                    headers=_json_headers(None),
                )

            _assert_not_rejected(
                response,
                f"{_UNREQUIRED_PUBLIC_OPERATION} is not in required_for; naming the operation must "
                "discriminate between rows, not merely detect that an AdCP request happened",
            )

    def test_the_same_path_with_a_different_method_is_a_different_operation(self, integration_db):
        """``PUT /media-buys/{id}`` and ``POST /media-buys`` are graded as different operations.

        THE VERB IS THE VARIABLE, and the probe has to be SIGNED for the difference to reach
        the wire. Both rows require a credential, so both requests carry the same valid
        bearer; an UNSIGNED pair would then be SERVED on both sides — :1269 forbids refusing a
        bearer-authed caller for a missing signature — and would discriminate nothing.

        Under ``supported_for: [update_media_buy]`` the two buckets differ, and so do the two
        answers: ``update_media_buy`` is in the narrowed set, so its checklist runs and the
        tampered digest is refused; ``create_media_buy`` falls in ``none``, where a well-formed
        signature clears the pre-check and passes through unverified. A router keyed on the
        path alone answers the same thing twice.

        Both bodies come from their request factories, and that is not decoration: ``serve``
        validates BEFORE ``invoke_tool``, so a body that does not conform is refused as
        ``INVALID_REQUEST`` and the verifier never runs — under which this test's non-rejection
        arm would pass having graded nothing.
        """
        private_key, jwks = keypair_for(COUNTERPARTY_KID)

        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            update_body = _as_json_bytes(UpdateMediaBuyRequestFactory.payload(media_buy_id=_MEDIA_BUY_ID))
            create_body = _as_json_bytes(CreateMediaBuyRequestFactory.payload())

            # Signed over DIFFERENT bytes than the ones sent, so the headers are well-formed
            # and the signature cryptographically real: the request clears the step-1
            # pre-check on its merits and reaches the digest mismatch INSIDE the checklist,
            # which is the arm the bucket governs. A MALFORMED signature is refused in every
            # bucket and would grade nothing about the name.
            update_headers = signed_headers(
                private_key,
                token,
                method="PUT",
                path=_UPDATE_MEDIA_BUY_PATH,
                body=tampered_signing_body(update_body),
                extra={"Content-Type": "application/json"},
            )
            create_headers = signed_headers(
                private_key,
                token,
                method="POST",
                path=_CREATE_MEDIA_BUY_PATH,
                body=tampered_signing_body(create_body),
                extra={"Content-Type": "application/json"},
            )

            with (
                _declared_posture(**bucketed_declaration("supported", _UPDATE_MEDIA_BUY_OPERATION)),
                counterparty_key(jwks),
            ):
                updated = client.put(_UPDATE_MEDIA_BUY_PATH, content=update_body, headers=update_headers)
                created = client.post(_CREATE_MEDIA_BUY_PATH, content=create_body, headers=create_headers)

            _assert_rejected(
                updated,
                f"PUT {_UPDATE_MEDIA_BUY_PATH} is {_UPDATE_MEDIA_BUY_OPERATION}, the operation this "
                "posture supports, so its signature is verified and the tampered digest is refused",
                code=REQUEST_SIGNATURE_DIGEST_MISMATCH,
            )
            _assert_not_rejected(
                created,
                f"POST {_CREATE_MEDIA_BUY_PATH} is {_CREATE_MEDIA_BUY_OPERATION}, which this posture "
                "does NOT support; only the verb distinguishes it from the request above",
            )


# --------------------------------------------------------------------------
# The webhook-credential escalation (security.mdx :1462-1465, vector 027)
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookCredentialsOutrankTheBucket:
    """Credentials in the payload force a signature REGARDLESS of the bucket and of the bearer.

    ":1465 — Sellers that support request signing MUST require the inbound request to be
    9421-signed … when ``authentication`` is present on
    ``push_notification_config.authentication`` or any
    ``accounts[].notification_configs[].authentication``", restated at :1375 as a trigger
    firing "regardless of ``required_for`` membership".

    On #1721 the rule is two typed reads off the VALIDATED request
    (``src/core/signing/webhook_credentials.py``) applied by ``_bucket_for`` BEFORE the
    signed/unsigned split. Every test below drives the SECOND read — the trigger with no
    compliance vector, and the one a reader handling ``push_notification_config`` alone
    misses while passing all 40 vectors. A unit call passing ``registers_credentials=True`` by
    hand asserts the consequence and skips the read entirely, which is why these are here.

    The first trigger, unsigned, is driven through a validated request by
    ``tests/integration/test_credential_block_is_logged.py``; the refusal it produces is
    graded at the unit level by ``tests/unit/test_request_signature_composition_rule.py``.
    """

    def test_an_account_notification_config_authentication_forces_a_signature(self, integration_db):
        """The second trigger named at :1465, for an AUTHENTICATED caller.

        The bearer is a REAL principal of this tenant and the posture declares
        ``required_for: []``, so neither exemption the composition rule would otherwise grant
        is available: a rejection here can only have come from the payload. Exempting
        authenticated callers would defeat the rule entirely — it exists BECAUSE the
        registering caller is normally bearer-authed and an on-path mutator can inject or
        strip the ``authentication`` block.
        """
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            with _declared_posture(**_supported_only()):
                response = client.post(
                    _SYNC_ACCOUNTS_PATH,
                    json=_account_registration(with_authentication=True),
                    headers=_json_headers(token),
                )

            _assert_rejected(
                response,
                "accounts[].notification_configs[].authentication is the second escalation trigger "
                "at security.mdx :1465 and carries the same MUST, on an operation this posture "
                "places in no bucket and for a caller this seller authenticated",
            )

    def test_the_same_registration_without_credentials_is_not_escalated(self, integration_db):
        """The control: identical apart from the ``authentication`` block.

        Without it there are no credentials to steal, the escalation must not fire, and an
        unsigned bearer-authed account sync is ordinary traffic. A predicate answering "yes"
        for an absent block would 401 every webhook registration this seller serves.
        """
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            with _declared_posture(**_supported_only()), _assert_verifier_looked():
                response = client.post(
                    _SYNC_ACCOUNTS_PATH,
                    json=_account_registration(with_authentication=False),
                    headers=_json_headers(token),
                )

            _assert_not_rejected(
                response,
                "a notification config with no authentication block carries no credentials; "
                "escalating it would 401 ordinary webhook registrations",
            )

    def test_the_escalation_fires_for_a_tenant_that_declared_nothing(self, integration_db):
        """The only case here with no declaration at all.

        Every other test in this class establishes an explicit posture through
        ``_declared_posture``. A tenant that declares NOTHING resolves to the AGENT-LEVEL
        posture (``posture_for_tenant`` -> ``agent_level_posture``, ``supported`` =
        ``SigningSettings.verifier_enabled``, every bucket empty), which puts every operation
        in the ``supported`` bucket rather than ``none`` — so :1465 binds this deployment for
        ordinary traffic, not only for tenants that opted into a posture.

        No ``_declared_posture`` context BY DESIGN: substituting one would recreate the gap
        this test exists to close.
        """
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            response = client.post(
                _SYNC_ACCOUNTS_PATH,
                json=_account_registration(with_authentication=True),
                headers=_json_headers(token),
            )

            _assert_rejected(
                response,
                "a tenant that declared no posture at all still SUPPORTS request signing (the "
                "agent-level default is supported=verifier_enabled), so "
                "accounts[].notification_configs[].authentication must force a signature here "
                "exactly as it does under an explicitly declared posture",
            )

    def test_a_signed_but_invalid_registration_under_warn_is_still_refused(self, integration_db):
        """The SIGNED half — the half every unsigned test above is blind to.

        ":1375 regardless of ``required_for`` membership" promotes the REQUEST, not one branch
        of it, and ``_bucket_for`` applies it AHEAD of the signed/unsigned split for a reason
        that is an attack: a request whose bucket stayed ``none`` is waved through unverified
        on the SIGNED path, so an escalation enforced only on the unsigned branch would refuse
        the honest unsigned registration and admit the same registration carrying a junk
        ``Signature`` header.

        THE BUCKET IS THE VARIABLE, and it has to be ``warn``. Under ``supported`` a
        signed-but-invalid request is refused on its own merits, promoted or not, so the
        promotion would be a no-op and this case would pass with it deleted — the very defect
        it exists to catch. Under ``warn`` the two arms differ: promoted to ``required``, the
        checklist failure is a refusal; un-promoted, ``warn`` SUPPRESSES that same failure and
        the registration completes with its credentials handed over unverified. Refusal
        becomes completion, on one variable.

        THE ORACLE IS THE CHALLENGE, never the status: un-promoted, this request is waved past
        the verifier and then refused by the APPLICATION, so any "not 200" oracle passes for
        the wrong reason.
        """
        private_key, jwks = keypair_for(COUNTERPARTY_KID)
        sent_body = _as_json_bytes(_account_registration(with_authentication=True))

        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            headers = signed_headers(
                private_key,
                token,
                method="POST",
                path=_SYNC_ACCOUNTS_PATH,
                body=tampered_signing_body(sent_body),
                extra={"Content-Type": "application/json"},
            )

            def digest_failures() -> float:
                return sum(
                    samples_with(
                        FAILED_METRIC,
                        operation=_SYNC_ACCOUNTS_OPERATION,
                        code=REQUEST_SIGNATURE_DIGEST_MISMATCH,
                    ).values()
                )

            before = digest_failures()

            with (
                _declared_posture(**bucketed_declaration("warn", _SYNC_ACCOUNTS_OPERATION)),
                counterparty_key(jwks),
            ):
                response = client.post(_SYNC_ACCOUNTS_PATH, content=sent_body, headers=headers)

            _assert_rejected(
                response,
                "the payload registers webhook credentials, so :1465 promotes this request to "
                "required 'regardless of required_for membership' (:1375) — and a promoted request "
                "is REFUSED on the checklist failure the warn bucket would otherwise suppress",
                code=REQUEST_SIGNATURE_DIGEST_MISMATCH,
            )

            # PRODUCTION NAMED THE SURFACE, which is what keeps the declaration honest. The
            # route-table guard above reads the registry; this reads the label the VERIFIER
            # wrote, so the two agree about which operation this path actually reached. Zero
            # would mean the declaration bucketed an operation the path does not resolve to,
            # leaving the surface in ``none`` — waved through unverified and refused for a
            # different reason, red under the same mutation while grading something else.
            after = digest_failures()
            assert after == before + 1, (
                f"the verifier recorded {after - before} {REQUEST_SIGNATURE_DIGEST_MISMATCH!r} failure(s) "
                f"labelled operation={_SYNC_ACCOUNTS_OPERATION!r}, expected exactly 1"
            )

    def test_the_escalation_does_not_fire_under_an_unsupported_posture(self, integration_db):
        """:1465 binds sellers that SUPPORT request signing.

        Under ``supported: false`` signatures are ignored entirely, and an agent that does not
        verify signatures cannot be made to demand one — :1465 sends it to log-and-alarm
        instead, which ``tests/integration/test_credential_block_is_logged.py`` grades. This
        is the wire control that makes the refusals above attributable to the posture rather
        than to the payload alone.
        """
        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            with _declared_posture(**unsupported()), _assert_verifier_looked():
                response = client.post(
                    _SYNC_ACCOUNTS_PATH,
                    json=_account_registration(with_authentication=True),
                    headers=_json_headers(token),
                )

            _assert_not_rejected(
                response,
                "security.mdx :1465 binds sellers that SUPPORT request signing; under "
                "supported: false the boundary ignores signatures entirely",
            )


# --------------------------------------------------------------------------
# A signed body too large to hash, and which half of `none` it reaches
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestASignedOverCapBodyIsGradedByTheDeclaration:
    """``max_signed_body_bytes`` bounds what the verifier will BUFFER TO HASH, and the refusal
    it answers with is reached only by a request the verifier actually inspects.

    Which requests those are is exactly what the two halves of ``none`` disagree about, and
    the disagreement is on the WIRE:

    * ``supported: true`` narrowed to other operations — the seller verifies, so a signed body
      it cannot digest is refused rather than silently admitted unverified;
    * ``supported: false`` — the seller advertises that it verifies nothing, so it must not
      start refusing signed traffic it has never inspected. Its 200 is the CONTROL, and it is
      what pins the intra-method ORDER: an over-cap check hoisted ahead of the ``supported``
      gate would refuse this row too, taking a conformant pass-through away from every seller
      that never opted in.

    THE REFUSAL IS A 401 WITH A CODE, not the pre-merge branch's bodyless 413. The verifier no
    longer sends a response of its own: it raises ``AdCPRequestSignatureError`` inside
    ``_resolve_identity`` and ``AuthChallengeResponder`` renders the code, so the graded byte
    is ``request_signature_header_malformed`` at step 1 — the pre-check, refused in every
    bucket.

    The third point of the same rule stays where the bytes are:
    ``tests/unit/test_signed_exchange_capture.py::test_an_over_cap_body_is_flagged_and_still_replayed``
    — an UNSIGNED over-cap request is served and its handler receives every byte, because
    nothing about it is ever hashed.
    """

    @pytest.mark.parametrize(
        ("declaration", "expected_code", "expected_status"),
        [
            # The two halves of ``none``, both from their single home
            # (``tests/helpers/signing.py``): a local second copy of
            # ``bucketed_declaration("supported", ...)`` is the duplication the DRY invariant
            # exists to stop, because a narrowing fixed in one copy and not the other silently
            # buckets one suite's surface differently from the other's.
            pytest.param(narrowed_none(), REQUEST_SIGNATURE_HEADER_MALFORMED, 401, id="narrowed-none"),
            pytest.param(unsupported(), None, 200, id="unsupported-none"),
        ],
    )
    def test_a_signed_over_cap_body_is_refused_only_where_the_seller_verifies(
        self, integration_db, declaration, expected_code, expected_status
    ):
        """One over-cap signed request, two declarations, two wire answers.

        The status is asserted TOO, and only because the challenge alone cannot say it: on the
        pass-through row ``rejection_code`` is ``None`` for a 500 exactly as it is for a served
        request, so the control would hold while the seller answered an error.
        """
        body = GetAdcpCapabilitiesRequestFactory.payload(context={"request_id": _PADDING})

        with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
            token = seed_principal(env)
            client = env.get_rest_client()

            with _declared_posture(**declaration), _body_cap(_TEST_BODY_CAP):
                response = client.post(
                    CAPABILITIES_ADCP_PATH,
                    json=body,
                    headers=request_headers(token, {"Content-Type": "application/json", **_PRESENT_SIGNATURE_HEADERS}),
                )

            assert _rejection_code(response) == expected_code, (
                f"a SIGNED body over max_signed_body_bytes under {declaration!r} must answer "
                f"{expected_code!r}; got status {response.status_code} with "
                f"WWW-Authenticate={response.headers.get('WWW-Authenticate')!r}: {response.text[:300]}"
            )
            assert response.status_code == expected_status, (
                f"under {declaration!r} the wire answer must be {expected_status}; got "
                f"{response.status_code}: {response.text[:300]}"
            )
