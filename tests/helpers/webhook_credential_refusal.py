"""The one refusal contract for "asked for HMAC-SHA256, supplied no credentials".

Two protocol surfaces register a ``push_notification_config`` and neither goes
through the other: ``create_media_buy`` (tool path, graded in
``tests/integration/test_webhook_hmac_credentials_ingest_refusal.py``) and the
A2A-native path that carries the webhook in the PROTOCOL envelope rather than as
a tool parameter (graded by the ``@a2a_untyped_ingest`` scenarios in
``tests/bdd/features/local-egress-ssrf-refusal.feature``, which reach it through
``message/send``). They emit through different machinery — an
``AdCPValidationError`` translated by the tool boundary vs an
``InvalidParamsError`` carrying the envelope in ``data`` — but the envelope the
buyer reads is ONE contract, so it is asserted from one place here.

The A2A half used to be cited as ``tests/unit/test_a2a_push_config_credential_refusal.py``,
calling ``PushNotificationConfigUoW.upsert`` directly. No such module exists in
the tree; the pointer was dangling, and the BDD scenarios above are what
actually grades that surface today.

The second assertion in :func:`assert_credentials_refusal_envelope` is not
decoration. A refusal raised for the wrong reason would reach the buyer naming
the wrong field -- telling them to fix a URL that is fine. "It refused" is not
enough; it has to refuse about the right field.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adcp.types import ErrorCode

from tests.helpers.envelope_assertions import assert_envelope_shape

if TYPE_CHECKING:
    from src.core.exceptions import AdCPSalesAgentError

# JSONPath-lite path into the buyer's request document. Written as a literal,
# not derived from production: ``error.field`` (``core/error.json`` @3.1.1) is a
# fact about the document the BUYER sent, and
# ``push_notification_config.authentication.credentials`` is where AdCP 3.1.1
# puts the legacy shared secret (``core/push-notification-config.json`` →
# ``Authentication.credentials``).
CREDENTIALS_FIELD = "push_notification_config.authentication.credentials"

# The registration URL gate's field path and its buyer-facing suggestion wording.
# Present here only as the WRONG answer — see the module docstring.
_URL_FIELD = "push_notification_config.url"

# The tail of that path, for the surfaces that do not (yet) qualify it with the
# same prefix: the admin registration form calls the same gate with
# ``field_prefix="webhook"``. Unifying the absolute prefixes is gh-#1895 and is
# deliberately not done here, so surfaces are compared on the part that says
# WHICH INPUT the caller must fix.
CREDENTIALS_FIELD_SUFFIX = "authentication.credentials"

# One character under the pinned ``credentials`` ``minLength: 32``
# (AdCP 3.1.1, ``dist/schemas/3.1.1/core/push-notification-config.json``). A
# BOUNDARY value, spelled once: a 5-character secret would also be refused by a
# hand-written "looks too short" rule that has nothing to do with the pin, so
# only 31 proves the pinned minimum is what refused it.
SHORT_CREDENTIAL = "s" * 31


def assert_admin_flash_refuses_the_credential(
    client,
    declared: list[AdCPSalesAgentError],
    *,
    secret: str,
    field_prefix: str = "webhook",
) -> None:
    """The ADMIN surface's half of the same contract, asserted from the same place.

    ``src/admin/blueprints/principals.py`` answers an operator in flashes, not in
    AdCP envelopes, so the two shapes cannot be compared byte-for-byte — but the
    VERDICT is one contract, and the credential-specific half of it is stated
    here so the admin grader and the cross-surface equivalence pin cannot drift
    on what "refused the credential" means.

    Three halves, none decoration:

    * refused at all, because accepting a registration the seller has already
      decided it will never sign is the accept-then-never-deliver defect;
    * naming the credential, because the sibling gate one field over refuses
      URLs and an operator sent to fix a URL that is fine has been misdirected;
    * not echoing the secret, because the flash is rendered back into the page.

    WHERE EACH OBLIGATION NOW LIVES. This used to read all three off the flash
    STRING, requiring it to contain ``authentication.credentials``. Under ADR-010
    that is unsatisfiable: the operator-facing sentence is a read-only property
    resolved from ``CODE_TABLE`` by error code and by nothing else, so the flash
    renders "Error registering webhook: " plus that code's pinned sentence, and
    the field name reaches no rendered surface. The field did not disappear, it changed
    channel — the route hands the whole typed refusal to
    ``record_admin_action_failure`` — so it is read off *declared* (the refusals
    the route declared, captured by the ``declared_refusals`` spy) rather than
    reconstructed from prose. Naming the credential is now the STRONGER
    assertion it was before: equality against ``{field_prefix}.`` +
    :data:`CREDENTIALS_FIELD_SUFFIX`, not membership in a sentence.

    Delegation, not indirection: ``assert_webhook_registration_refused``
    (``tests/integration/test_admin_ingest_url_policy.py``) already grades ONE
    admin registration refusal on every channel it may speak through — count,
    code, recovery, field, the ``CODE_TABLE`` sentence for that code, and what
    the operator's screen may not carry — and it grades the URL gate's refusals
    with it. What is credential-specific is the BINDING of those three
    parameters, and that is what this function owns. A second copy of the
    grading shape here is the drift the module docstring exists to prevent.

    The code is ``INVALID_REQUEST`` and not the URL gate's ``VALIDATION_ERROR``:
    a credential under the pinned ``minLength: 32`` is a document that violates
    the pinned schema, where a deny-listed URL is a schema-VALID document
    refused by policy. The two therefore resolve to different ``CODE_TABLE``
    sentences — which is what makes asserting the sentence discriminating rather
    than vacuous, and the delegate asserts that inequality itself rather than
    assuming it.

    ``secret`` is passed as the value that must not appear in the operator's
    text: the flash is rendered back into the page, and a credential belongs in
    no operator-facing surface (AdCP 3.1.1 ``transport-errors.mdx`` § Security
    Considerations; L1 ``security.mdx`` point 6 states the same rule for the URL
    vector one field over). A helper about credential refusal that let the
    secret leak into the refusal would be the worst outcome available to it.

    Args:
        client: the Flask test client that POSTed the registration — the flash
            queue is read from its session, not from rendered HTML.
        declared: every typed refusal the route declared, in order. Exactly one
            is required: a route that stopped declaring (the
            accept-then-audit-as-success defect) captures none and fails here.
        secret: the shared secret the operator supplied, asserted absent from
            the flash.
        field_prefix: the calling route's own ``field_prefix``. ``"webhook"`` for
            the admin registration form; the protocol surfaces qualify the same
            suffix with ``push_notification_config`` (gh-#1895 unifies them).
    """
    from tests.integration.test_admin_ingest_url_policy import assert_webhook_registration_refused

    assert_webhook_registration_refused(
        client,
        declared,
        code=ErrorCode.INVALID_REQUEST,
        field=f"{field_prefix}.{CREDENTIALS_FIELD_SUFFIX}",
        withheld=(secret,),
    )


def assert_credentials_refusal_envelope(envelope: dict, *, surface: str) -> None:
    """Assert the wire envelope refuses the CREDENTIAL, correctably, by name.

    ``INVALID_REQUEST`` / ``recovery="correctable"``. The code is read off the
    PIN, not off the sibling gate. AdCP 3.1.1 ``enums/error-code.json``
    ``enumDescriptions`` defines the split exhaustively:

    * ``INVALID_REQUEST`` — "Request is malformed, missing required fields, or
      **violates schema constraints**."
    * ``VALIDATION_ERROR`` — "Request contains invalid field values or violates
      **business rules beyond schema validation**."

    A ``credentials`` that is absent or shorter than 32 characters is a document
    that violates the PINNED SCHEMA and nothing else:
    ``core/push-notification-config.json`` → ``authentication`` declares
    ``required: ["schemes", "credentials"]`` and ``credentials.minLength: 32``.
    That is "missing required fields" and "violates schema constraints"
    verbatim, so it is ``INVALID_REQUEST``. The sibling URL gate one field over
    keeps ``VALIDATION_ERROR`` because a deny-listed host is a SCHEMA-VALID
    ``format: "uri"`` string refused by seller policy — "business rules beyond
    schema validation" — which is the other half of the same sentence pair.

    This docstring previously reasoned the opposite way: that the spec was
    "silent on what a seller does with an HMAC registration carrying no secret",
    so the sibling gate was authority for the SHAPE. The silence is real but
    narrower than that inference. The spec does not mandate THAT a seller
    refuses an unservable registration (the storyboard grades no such step —
    still UNGRADED). It is not silent on WHICH CODE a refusal carries once the
    seller does refuse, because the refused document violates a schema
    constraint and ``error-code.json`` assigns that case by name. Copying the
    neighbouring gate's code answered a question the pin had already answered.

    Production has encoded this split since ``src/core/exceptions.py:1266-1281``:
    a pydantic ``ValidationError`` is "BY CONSTRUCTION a schema-constraint
    violation" and maps to ``AdCPInvalidRequestError``, while a plain
    ``ValueError`` from business logic stays ``AdCPValidationError``. Every
    surface here refuses through the pinned request model, so production emits
    ``INVALID_REQUEST`` and is right to.

    ``recovery`` does NOT move: ``enumMetadata`` carries ``"correctable"`` for
    BOTH codes, so it cannot discriminate between them and is asserted here only
    to pin that a supplied secret makes the identical request succeed. The code
    is the discriminator, which is why the two are asserted together.

    This also removes a contradiction inside this very module:
    :func:`assert_admin_flash_refuses_the_credential` already grades the ADMIN
    half of the same credential refusal as ``ErrorCode.INVALID_REQUEST``,
    against the same reasoning. The two halves of one contract now agree.
    """
    assert_envelope_shape(
        envelope,
        "INVALID_REQUEST",
        recovery="correctable",
        field=CREDENTIALS_FIELD,
    )
    _assert_not_mislabelled_as_a_url_refusal(envelope, surface=surface)


def _assert_not_mislabelled_as_a_url_refusal(envelope: dict, *, surface: str) -> None:
    """Fail if the refusal was re-enveloped as the URL/SSRF refusal.

    Checked on BOTH envelope layers for the same reason ``field`` is: the
    two-layer envelope's ``adcp_error`` and ``errors[0]`` are read by different
    storyboard steps in the wild, so a mislabel on either one reaches a buyer.

    The suggestion half used to import ``webhook_ssrf_suggestion`` from
    ``src/core/webhook_validator.py`` and assert inequality against it. That
    symbol no longer exists: under ADR-010 ``suggestion`` is a read-only
    property resolved from ``CODE_TABLE`` by error code, never a per-raise-site
    override, so the URL gate stopped passing ``suggestion=`` and the helper was
    deleted with it. The stale import was invisible while the code assertion
    above this one was failing — nothing reached line two.

    Restated against ``CODE_TABLE``, the check gets STRONGER rather than weaker.
    It now pins EQUALITY to the credential code's own sentence rather than mere
    inequality to the URL code's, and it asserts the two sentences differ rather
    than assuming it — so if a ``CODE_TABLE`` change ever collapsed
    ``INVALID_REQUEST`` and ``VALIDATION_ERROR`` onto one string, this fails
    loudly instead of passing vacuously. That collapse is the only way the
    buyer's repair instructions could stop discriminating between "add a shared
    secret" and "change your URL" now that both are derived from the code.
    """
    from src.core.errors.codes import CODE_TABLE

    # The credential gate's code, and the URL gate's — resolved, not transcribed.
    ours = CODE_TABLE["INVALID_REQUEST"].suggestion
    url_gates = CODE_TABLE["VALIDATION_ERROR"].suggestion
    assert ours != url_gates, (
        f"{surface}: CODE_TABLE resolves INVALID_REQUEST and VALIDATION_ERROR to the SAME "
        f"suggestion {ours!r} — the buyer can no longer tell a credential refusal from a URL "
        f"refusal by the repair instructions, which is the whole point of this assertion"
    )
    for layer, body in (("adcp_error", envelope["adcp_error"]), ("errors[0]", envelope["errors"][0])):
        assert body.get("field") != _URL_FIELD, (
            f"{surface}: {layer}.field is {_URL_FIELD!r} — a missing HMAC credential was "
            f"re-enveloped as a URL refusal, sending the buyer to fix a URL that is fine"
        )
        assert body.get("suggestion") == ours, (
            f"{surface}: {layer}.suggestion={body.get('suggestion')!r}, expected {ours!r}. "
            f"The URL gate's wording is {url_gates!r} — the buyer needs to add a shared "
            f"secret, not change their URL scheme"
        )
