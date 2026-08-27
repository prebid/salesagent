"""Steps for the inbound request-signature enforcement scenarios (salesagent-n78j0.1.3).

Every step here is TRANSPORT-BLIND, and that is the whole reason the file exists.
The property under test — a signed request accepted, an unsigned one refused,
identically on every transport — is a CROSS-transport property, so a step that
learned which transport it was on could not grade it: it would be four claims that
happen to be spelled alike. Signing, the posture write, the credential location and
the verification oracle are all realized by the ENV
(``tests.harness.signing_capability``, ``BaseTestEnv.declare_request_signing`` /
``signature_verifications``), which is the one place transport knowledge is allowed.

Spec grounding, pinned AdCP 3.1.1 (``adcp==6.6.0``), all in
``v3.1.1:docs/building/by-layer/L1/security.mdx``:

* :1268-1269 the composition rule — an unsigned request to a ``required_for``
  operation is refused only when the caller presents no credential the agent accepts;
* :1375 / :1462-1465 the payload escalation — a seller that SUPPORTS request signing
  MUST require a signature when the request carries webhook ``authentication``,
  "regardless of ``required_for`` membership";
* graded by ``dist/compliance/3.1.1/test-vectors/request-signing/negative/
  027-webhook-registration-authentication-unsigned.json``, whose
  ``verifier_capability`` is ``{supported: true, required_for: []}``;
* :1226 the pre-check — a verifier MUST NOT fall back to bearer-only auth when a
  MALFORMED signature is present, "even for operations not in ``required_for``", which
  is a quantifier over BUCKETS and is graded as one only by declaring each in turn;
* :1273 the contrast — ``warn_for`` is scoped to signed-but-INVALID requests, so it
  suppresses a checklist failure and not a pre-check one. Ungraded upstream: ``warn_for``
  is this repo's extension, absent from the SDK's ``VerifierCapability`` and appearing
  zero times in the 40 conformance vectors.
"""

from __future__ import annotations

from pytest_bdd import given, parsers, then

from tests.bdd.steps.generic._dispatch import CREDENTIAL_REGISTRATIONS, GRADE_EVERY_CREDENTIAL_LOCATION

#: How a scenario that registered nothing names its single dispatch. The three
#: scenarios do not all register credentials, and the two that do not have exactly one
#: request to grade — a location they never chose is not one a failure may name.
_THE_REQUEST = "the request"

#: A credential long enough for production's own boundary. ``Authentication.credentials``
#: carries ``MinLen=32`` on the pinned model, so a shorter placeholder would be rejected
#: as a validation error — a scenario that exists to prove the request is refused for the
#: MISSING SIGNATURE would then pass for the wrong reason on any transport that validated
#: before the verifier ran.
_WEBHOOK_CREDENTIAL = "harness-webhook-shared-secret-0123456789"

#: The buyer's webhook endpoint. Under an RFC 2606/6761 reserved TLD so no seller-side
#: reachability check can turn into a real network dial (``.example``); the request never
#: gets that far in these scenarios, and it must not be able to.
_WEBHOOK_URL = "https://buyer.example/webhooks/adcp/creative"


@given("the Buyer Agent has published a signing key the seller can resolve")
def given_buyer_publishes_signing_key(ctx: dict) -> None:
    """Mint this buyer's key and publish it through its own trust root.

    "The seller can resolve it" is the substance: in process the counterparty's whole
    ``AgentResolution`` is seeded into the middleware's cache; over e2e the key is
    PUBLISHED on a counterparty origin and the server reaches it by its own
    ``agent_url -> capabilities -> brand.json -> jwks_uri`` walk. The step says neither.

    Required by ALL THREE scenarios, including the two that dispatch UNSIGNED: once an
    env can sign, both its signed and its unsigned dispatches travel the same real HTTP
    path carrying the same bearer and tenant hint, so the two differ by exactly one
    variable — the signature. Without it an unsigned dispatch is a different experiment.
    """
    ctx["env"].enable_request_signing()


@given(parsers.parse('the seller requires a request signature for "{operation}"'))
def given_seller_requires_signature_for(ctx: dict, operation: str) -> None:
    """Declare ``required_for: [operation]`` as the seller's REAL posture.

    Stored on the tenant and read back by production (``CapabilityDeclarations.from_tenant``
    -> ``posture_for_tenant`` -> ``bucket_for``), never patched in. Naming the operation
    explicitly is what leaves every OTHER operation in the ``none`` bucket, so the
    refusal below is attributable to this declaration.
    """
    ctx["env"].declare_request_signing(required_for=[operation])


@given("the seller supports request signatures but requires them for no operation")
def given_seller_supports_signatures_only(ctx: dict) -> None:
    """Declare the pinned vector's own ``{supported: true, required_for: []}``.

    Load-bearing that ``required_for`` stays EMPTY: with the operation in it, the
    refusal that follows could come from the composition rule, and the scenario would
    stop grading the payload escalation it exists for
    (vector 027, ``verifier_capability``).
    """
    ctx["env"].declare_request_signing()


@given("the Buyer Agent signs the request")
def given_buyer_signs_the_request(ctx: dict) -> None:
    """Ask for a REAL RFC 9421 signature on the request this scenario is about to send.

    What "signed" means per transport — which bytes, which headers, which path — is the
    dispatcher's business (``BaseTestEnv.wire_request``). A transport that cannot sign
    REFUSES rather than sending an unsigned request, so this can never silently degrade
    into the control it is being compared against.
    """
    ctx["signed"] = True


@given(parsers.parse('the seller places "{operation}" in the "{bucket}" request-signature bucket'))
def given_seller_buckets_operation(ctx: dict, operation: str, bucket: str) -> None:
    """Declare the seller's REAL posture with *operation* in exactly one bucket.

    The generalization of the two Givens above, and the one the bucket-quantified
    scenarios need: ``required_for > warn_for > supported_for`` is a PRECEDENCE, so a
    claim quantified over buckets can only be graded by declaring each of them in turn
    through the same seam. The env writes the declaration onto the tenant and production
    reads it back (``CapabilityDeclarations.from_tenant`` -> ``posture_for_tenant`` ->
    ``bucket_for``); nothing here patches a posture in.

    Naming the operation is what leaves every OTHER operation in the ``none`` bucket, so
    the outcome is attributable to this declaration
    (:func:`tests.helpers.signing.bucketed_declaration`).
    """
    ctx["env"].declare_request_signing(bucket=bucket, operations=[operation])


@given("the Buyer Agent sends a signature the seller cannot parse")
def given_buyer_sends_malformed_signature(ctx: dict) -> None:
    """Ask for the MALFORMED realization: both headers present, neither parseable.

    The realization travels VERBATIM (never ``bool(...)``): ``bool("malformed")`` is
    True, so a collapse here would put a WELL-FORMED signature on the wire and the
    scenario would grade an acceptance while claiming to grade a refusal
    (:func:`tests.helpers.signing.realization` refuses anything else for the same
    reason).

    What the malformation IS — which headers, which bytes, on which surface — belongs to
    the env (:data:`tests.helpers.signing.MALFORMED_SIGNATURE_HEADERS`, placed by
    ``BaseTestEnv.wire_request``). The scenario states only that the seller cannot parse
    what it was sent. This is the seller's checklist STEP 1, which is what makes it a
    PRE-CHECK failure rather than a verdict about the signature's validity.
    """
    ctx["signed"] = "malformed"


@given("the Buyer Agent signs a different rendering of the request")
def given_buyer_signs_different_bytes(ctx: dict) -> None:
    """Ask for the TAMPERED realization: a real signature over different bytes.

    The contrast to the Given above, and the whole reason the two are separate
    realizations: the signature is cryptographically REAL and the headers are
    well-formed, so the verifier gets past the pre-check on its merits and reaches
    ``request_signature_digest_mismatch`` inside the checklist — the arm ``warn_for``
    governs (security.mdx @ v3.1.1 :1273).

    Verbatim for the same reason as above. WHICH bytes differ is the env's business
    (:func:`tests.helpers.signing.tampered_signing_body`); the scenario says only that
    the buyer signed a different rendering of what it sent.
    """
    ctx["signed"] = "tampered"


@given("the request registers a webhook whose authentication carries credentials")
def given_request_registers_authenticated_webhook(ctx: dict) -> None:
    """Attach a ``push_notification_config`` carrying an ``authentication`` block.

    This is the escalation's trigger, in the buyer's own words: "register this webhook,
    here are the credentials for it". WHERE those credentials sit on the wire is the
    transport's business and is genuinely NOT the same place on each one — the AdCP
    request body on REST, the ``tools/call`` arguments on MCP, and the A2A protocol
    envelope (``params.configuration.task_push_notification_config``) on A2A, which is
    where ``src/a2a_server/adcp_a2a_server.py:657-659`` READS it. The env owns that
    placement; the scenario states only the intent.

    AND ON A2A THAT IS NOT THE ONLY PLACE (salesagent-jj90f). The same transport also
    serves ``tasks/pushNotificationConfig/set``, where a buyer registers a webhook and
    its credentials on a JSON-RPC method of its own with no skill invocation at all —
    answered by our own ``AdCPRequestHandler.on_create_task_push_notification_config``,
    which persists the credentials and returns a config id. So this Given asks for a
    claim about CREDENTIALS, not about one request, and opts the scenario into being
    graded at every location the transport offers
    (``BaseTestEnv.credential_registrations``).

    SAY IT PLAINLY: that makes this scenario's single When put TWO dispatches on the
    wire where the transport has two locations, which departs from this suite's
    "one request, one outcome" shape. It is worth the departure at two locations,
    where naming the accepted one in the failure is enough to say what broke. It stops
    being worth it at THREE: if a third location ever appears, switch to B1 —
    parametrise the location at the ENV level so each location is its own leg with its
    own outcome — rather than stacking a third dispatch inside one When.

    What must NOT happen either way is MOVING the placement from one location to the
    other. That yields the same number of failures while silently un-grading the
    location it moved off, which looks like progress and is a coverage trade.

    Spec: security.mdx @ v3.1.1 :1462-1465, ``push_notification_config.authentication``
    named verbatim as a trigger; the pinned vector 027 registers exactly this shape.
    """
    ctx["push_notification_config"] = {
        "url": _WEBHOOK_URL,
        "authentication": {"scheme": "HMAC-SHA256", "credentials": _WEBHOOK_CREDENTIAL},
    }
    ctx[GRADE_EVERY_CREDENTIAL_LOCATION] = True


@then(parsers.parse('the seller answers with the request-signature challenge "{code}"'))
def then_signature_challenge(ctx: dict, code: str) -> None:
    """Grade the ``WWW-Authenticate: Signature error="<code>"`` challenge, byte-exactly.

    Through the harness helper, which is the single way a request-signature refusal may
    be graded: it refuses a code outside the request-family vocabulary production reads
    (``REQUEST_TO_WEBHOOK_CODE``, which is wider than a prefix scan of
    ``adcp.signing.errors`` by ``request_target_uri_malformed``), and FAILS (rather than
    passing for want of evidence) on a result that
    carries no raw HTTP response. ``status_code == 401`` is deliberately not the
    assertion — see ``TransportResult.assert_signature_challenge``.

    ONE CHALLENGE PER CREDENTIAL LOCATION when the scenario is about credentials
    (see the Given above for why one When then makes more than one dispatch, and for
    when that shape must be abandoned). EVERY location is graded before anything is
    raised — not the first failure — because the reason for exercising two is to be
    able to say WHICH of them the seller accepted, and a first-failure abort would
    hide the second behind the first for exactly as long as the first stays broken.
    That is also the property S2 is graded on: a fix that closes one location and not
    the other must still read as a failure naming the open one, never as a smaller
    number.
    """
    accepted: list[tuple[str, AssertionError]] = []
    for location, result in ctx.get(CREDENTIAL_REGISTRATIONS) or ((_THE_REQUEST, ctx["result"]),):
        try:
            result.assert_signature_challenge(code)
        except AssertionError as exc:
            accepted.append((location, exc))
    if not accepted:
        return
    raise AssertionError(
        f"The seller did not answer {code!r} at {len(accepted)} of the credential "
        f"location(s) this transport carries.\n\n" + "\n\n".join(f"AT {location}:\n{exc}" for location, exc in accepted)
    )


@then(parsers.parse('the seller recorded exactly {count:d} suppressed "{code}" signature failure'))
def then_seller_recorded_suppressed_failure(ctx: dict, count: int, code: str) -> None:
    """Pin how many checklist failures carrying *code* THIS seller recorded.

    The negative oracle, and half of the pair that grades a warn completion — the other
    half is the completion itself, asserted by its own Then, because a step asserts one
    claim. Neither half grades the arm alone: a completion is equally true of a
    middleware that never looked at the request, and a recorded failure is equally true
    of the refusal the ``supported`` control asserts. Together they say the verifier ran
    the checklist, recorded exactly one failure, and served the request anyway.

    ``== count`` rather than "at least one": zero means the checklist never ran (the
    posture collapsed, or the leg put no bytes on a wire) and more than one means frames
    that are not the graded operation were verified as if they were.

    CODE-SCOPED, because the code is the attribution: a key-resolution flake surfacing at
    checklist step 7 is also a warn-suppressed failure and also leaves a completion
    behind, so a code-blind count would pass on a run where the tamper did nothing.

    Counted by the ENV (``BaseTestEnv.signature_failures``), never off the metrics
    registry here: an in-process registry read is a correct number on three transports
    and a silent 0 on the fourth, where production increments the counter in another
    container. A step that reads it names no transport in its SPELLING while depending
    on one in its SEMANTICS, which is the failure this module exists to avoid.
    """
    recorded = ctx["env"].signature_failures(code)
    assert recorded == count, (
        f"the seller recorded {recorded} {code!r} signature failure(s) since it declared its posture, "
        f"expected {count}. 0 means the verifier never reached the checklist — the signature was waved "
        "through unverified, or it was refused earlier at the pre-check, which is a different arm of the "
        "rule; more than expected means requests other than the graded operation failed the same way."
    )


@then(parsers.parse("the seller verified exactly {count:d} request under the Buyer Agent's published key"))
def then_seller_verified_requests(ctx: dict, count: int) -> None:
    """Pin how many requests the seller's verifier ACCEPTED under THIS buyer's key.

    ``== count`` rather than "at least one": zero means the request never reached the
    verifier (the posture collapsed to ``none``, or the leg never put bytes on a wire),
    and more than one means frames that are not graded operations — an MCP session's
    ``initialize`` / ``notifications/initialized`` — were verified as if they were.
    """
    verified = ctx["env"].signature_verifications()
    assert verified == count, (
        f"the seller's verifier accepted {verified} request(s) under this buyer's key, expected {count}. "
        "0 means the signature was never verified — the request was waved through unverified (bucket "
        "'none'), or the leg dispatched without putting bytes on a wire; more than expected means "
        "transport session frames were graded as operations."
    )
