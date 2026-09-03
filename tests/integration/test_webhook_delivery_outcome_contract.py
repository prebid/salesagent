"""The seam returns ONE outcome record, decided by ONE match on the pinned scheme enum.

Epic D lane C4 (GH #1802). The lane deletes the hand-rolled union — its
resolver and five variants, both aliases, and all fifteen ``isinstance`` sites —
and replaces the decision with ONE construction of the pinned library
``Authentication`` (imported as ``LibraryAuthentication``, an import alias rather
than a subclass) inside
``deliver_webhook``/``adeliver_webhook``, which hand every caller back a
:class:`WebhookDeliveryOutcome` instead of a decision object the caller could get
wrong. This file is that seam's contract, graded against a real origin and the
real transport.

THE RULE this file grades (owner rulings #1-#3, #5, #6), and where each half of
it comes from. INHERITED from the pinned library type (AdCP 3.1.1
``core/push-notification-config.json``): ``credentials`` required with
``minLength: 32``, ``schemes`` with ``minItems``/``maxItems`` 1, and the
``AuthenticationScheme`` enum ``["Bearer", "HMAC-SHA256"]``. ADDED by the
subclass, and nothing else: ``Literal["Basic"]`` widening the enum, and a
before-validator that folds casing.

    no scheme AND no credential               -> deliver PLAIN (the spec's own selector:
                                                 there was no authentication block)
    no scheme BUT a stored credential         -> REFUSE, reason ``no_scheme``
    Bearer,       creds >= 32              -> Authorization: Bearer
    HMAC-SHA256,  creds >= 32              -> sign those exact bytes
    anything the pinned type refuses       -> REFUSE, nothing dialled

There is ONE vocabulary: the pinned ``AuthenticationScheme``, case-sensitive,
exactly ``["Bearer", "HMAC-SHA256"]``. No ``Basic``, no case folding, no
seller-local superset.

That is a reversal, recorded here because the reversed reasoning is the more
tempting one and will be re-proposed. Earlier lanes widened the type: the A2A
``setTaskPushNotificationConfig`` handler stores
``params.authentication.scheme`` verbatim off a free-form protobuf string, so
``basic`` and ``hmac-sha256`` rows really do exist in production, and refusing
them stops deliveries whose owners are not on the phone to be asked. Every step
of that is still true. The owner ruled against it anyway: those rows are to be
MIGRATED, not tolerated — re-registered with a scheme and credentials the spec
defines — and until they are, they do not deliver.

The argument for tolerance is self-perpetuating, which is the point. It licenses
one member and one fold, and each is individually cheap; what it produced was
three spellings of one fact across three senders, disagreeing about which the
column held. A widening justified by "rows exist" can never expire, because rows
always exist. Refusing also fails visibly, in the operator's face, rather than
assembling an ``Authorization`` header from a scheme no registration validly
named and leaving the receiver unable to tell we guessed.

So a non-conforming stored row is graded below as ``refused_auth`` /
``scheme_not_in_spec``, and the log line naming the row is the migration surface.

Why zero hits is the discriminating assertion for a refusal, and not merely a
small number: the origin below is programmed to answer 200 and really is
listening. A destination that would have ACCEPTED the delivery is what makes "no
request arrived" mean *the seam refused* rather than *the request went out and
failed on arrival*. The same rationale is written at
``tests/integration/test_webhook_sender_auth_contract.py`` and
``test_order_approval_webhook.py::TestHmacSigning``.

Why integration and not BDD: the seam is not reached inside a request/response
cycle — every webhook sender runs after the buyer's call has already returned
(delivery scheduler, delivery poller, workflow-step status change), so there is
no wire envelope for a ``Then`` step to assert on. The identical rationale is
already recorded at ``tests/bdd/features/local-egress-ssrf-refusal.feature:45-51``.

This module was authored RED, before ``deliver_webhook`` existed: the seam then
exposed only a lower-level helper that took a ``secret`` the caller had resolved
for itself, and no outcome type at all. That lane has since landed, and the
helper it names was later folded into ``deliver_webhook`` itself. The two
seats that consume the outcome are graded
next door in ``tests/integration/test_webhook_refusal_reaches_both_seats.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

from tests.helpers import SIGNATURE_HEADER, assert_signature_verifies_over_wire_body
from tests.helpers.egress_hatches import egress_hatch_env

# Both entry points get every case. The async twin is a separate code path and it
# is the one this lane finally gives a caller (protocol_webhook_service), so an
# ungraded half would ship the lane's own defect in the half that is new.
SEAM_CALLS = ["deliver_webhook", "adeliver_webhook"]

# The pinned ``AuthenticationScheme`` spellings, as constants: a regression that
# changed the spelling production compares against must fail these cases rather
# than be quietly re-typed into them.
HMAC_SCHEME = "HMAC-SHA256"
BEARER_SCHEME = "Bearer"

# The one member the salesagent subclass ADDS to the pinned enum. Named as the
# legacy scheme rather than spelled inline per case, because the owner's stated
# exit is "drop it by deleting one line" — when that day comes, the cases that
# have to change are exactly the ones naming this constant.
# Not an AdCP scheme. Kept as a NAMED constant even though it is now refused:
# stored rows carry it, so it is the concrete thing the refusal cases grade.
LEGACY_SCHEME = "Basic"

# ``credentials`` ``minLength: 32`` @ AdCP 3.1.1 core/push-notification-config.json.
# Both sides of the boundary are spelled, because the boundary is the rule: 32
# delivers, 31 refuses, and a test that only carries one of them grades an
# inequality direction it cannot see.
CONFORMING_SECRET = "s" * 32
ONE_SHORT_OF_CONFORMING = "s" * 31

# A cloud-metadata address: refused by the egress seam before any connection,
# escape hatches or not (graded in tests/integration/test_outbound_http.py).
METADATA_URL = "https://169.254.169.254/webhook"

# The seam's own logger — the refusal's only operator surface.
SEAM_LOGGER = "src.core.security.webhook_egress"

# The retry backoff base, driven from outside the seam exactly as the seam's own
# suite drives it, so an exhaustion case does not wait out a real 1s/2s schedule.
BACKOFF_BASE_ENV = "ADCP_OUTBOUND_BACKOFF_BASE_SECONDS"

# Non-ASCII on purpose. An ASCII payload would also be accepted by a sender that
# re-serialized with ``json=`` (compact separators but ``ensure_ascii=False``),
# so only a non-ASCII body grades "the bytes signed are the bytes transmitted"
# rather than coincidental agreement — the same reason
# ``test_protocol_webhook_egress.py::TestSignedBodyIntegrity`` uses one.
PAYLOAD: dict[str, Any] = {"event": "delivery", "advertiser": "Åkerlund & Rausing", "spend": 1234.5}


def _seam():
    """Import the seam lazily.

    A module-level import would turn "the seam does not exist yet" into a
    collection error for the whole file, which would also stop the origin fixture
    from ever running. Per-call keeps each case an independently diagnosable
    failure — which is what a RED grader is for.
    """
    from src.core.security import webhook_egress

    return webhook_egress


def deliver(seam_call: str, url: str, payload: dict[str, Any], **kwargs: Any):
    """Dispatch to ``deliver_webhook`` or ``adeliver_webhook`` — the one place they differ."""
    seam = _seam()
    if seam_call == "deliver_webhook":
        return seam.deliver_webhook(url, payload, **kwargs)
    return asyncio.run(seam.adeliver_webhook(url, payload, **kwargs))


def open_private_hatch(monkeypatch) -> None:
    """Let the seam dial the loopback origin, explicitly.

    Always written — including the ``"false"`` half of the mapping — so an
    ambient value exported into the shell cannot decide what these cases dial.
    The name and literal come from ``tests.helpers.egress_hatches``, the one
    place in the test tree that spells them.
    """
    for name, value in egress_hatch_env(private=True).items():
        monkeypatch.setenv(name, value)


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """The compact-separator form pinned by adcontextprotocol/adcp#2478."""
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# 1. The refusal half of THE RULE: an outcome record, and nothing dialled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
@pytest.mark.parametrize(
    ("reason", "scheme", "credentials", "reported_scheme"),
    [
        # The buyer asked for a signature this sender cannot produce. Refusing is
        # not the over-correction it looks like: an unsigned POST to a receiver
        # that verifies is strictly worse than no POST, because it is an
        # unauthenticated request no receiver can attribute to us.
        ("no_credentials", HMAC_SCHEME, None, HMAC_SCHEME),
        ("no_credentials", BEARER_SCHEME, None, BEARER_SCHEME),
        # One character under the pinned minimum. A sub-32 secret cannot be
        # lengthened without changing what the RECEIVER verifies against, so
        # there is nothing to migrate and nothing to tolerate (ruling #2/#3).
        ("credentials_too_short", HMAC_SCHEME, ONE_SHORT_OF_CONFORMING, HMAC_SCHEME),
        ("credentials_too_short", BEARER_SCHEME, ONE_SHORT_OF_CONFORMING, BEARER_SCHEME),
        # Not a member of the pinned enum. ``Digest`` and ``Basic`` land the same
        # way now that there is one vocabulary — the scheme is refused BEFORE the
        # credential rule is reached, so a short credential under a bad scheme is
        # still reported as the scheme problem, which is the one to fix first.
        ("scheme_not_in_spec", "Digest", CONFORMING_SECRET, "Digest"),
        ("scheme_not_in_spec", LEGACY_SCHEME, ONE_SHORT_OF_CONFORMING, LEGACY_SCHEME),
        # A stored credential with no scheme naming it: the authentication block
        # is PRESENT (that is what the credential proves) but selects nothing, so
        # the spec's own selector cannot fire. Distinct from the plain case
        # below, where the absence of both IS the selector for RFC 9421.
        # ``reported_scheme`` is None for BOTH: a blank string is a stored row with no
        # scheme, not a scheme spelled with spaces (§5: "absent/blank scheme"), so the
        # refusal names nothing rather than naming whitespace.
        ("no_scheme", None, CONFORMING_SECRET, None),
        ("no_scheme", "   ", CONFORMING_SECRET, None),
    ],
)
def test_a_refused_scheme_returns_a_refusal_and_dials_nothing(
    seam_call, reason, scheme, credentials, reported_scheme, monkeypatch, local_origin_tls
):
    """Every refusal is the SAME record shape, discriminated by ``reason``.

    ``attempts == 0`` and ``http_status is None`` together say what "refused"
    means: the decision was taken before a socket was opened, so there is no
    attempt to have counted and no response to have read a status off. The hit
    count is the independent witness — a refusal that dials is a failed refusal,
    and the outcome record alone cannot tell the difference.
    """
    open_private_hatch(monkeypatch)
    local_origin_tls.respond_with(200)

    outcome = deliver(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        PAYLOAD,
        scheme=scheme,
        credentials=credentials,
    )

    assert (outcome.kind, outcome.attempts, outcome.http_status) == ("refused_auth", 0, None), (
        f"{scheme!r}/{reason}: expected a pre-flight refusal, got "
        f"kind={outcome.kind!r} attempts={outcome.attempts} http_status={outcome.http_status}"
    )
    assert outcome.reason == reason, (
        f"{scheme!r}: the refusal is discriminated as {outcome.reason!r}, not {reason!r} — the "
        f"reason is a closed discriminator the ingest refusal ENVELOPE is graded against, so a "
        f"row that lands on the wrong member re-labels a buyer-visible message"
    )
    assert outcome.scheme == reported_scheme, (
        f"the refusal must carry the scheme it refused ({reported_scheme!r}), got {outcome.scheme!r} — "
        f"an operator reading the log cannot find the row otherwise"
    )
    assert local_origin_tls.hits == 0, (
        f"{scheme!r}/{reason}: the seam refused and then dialled anyway "
        f"({local_origin_tls.hits} request(s) reached an origin that answers 200) — "
        f"a refusal that puts bytes on the wire is not a refusal"
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_the_refusal_log_names_the_scheme_and_the_reason(seam_call, monkeypatch, local_origin_tls, caplog):
    """With no migration and no durable record, the log is the whole operator surface.

    Ruling #3 stops non-conforming rows delivering until they are re-registered,
    and deliberately ships no backfill and no report. That makes this line the
    ONLY way an operator learns which row stopped and why, so its content is part
    of the contract rather than incidental output.
    """
    open_private_hatch(monkeypatch)
    local_origin_tls.respond_with(200)

    with caplog.at_level(logging.ERROR, logger=SEAM_LOGGER):
        deliver(
            seam_call,
            f"{local_origin_tls.base_url}/webhook",
            PAYLOAD,
            scheme="Digest",
            credentials=CONFORMING_SECRET,
        )

    refusals = [record for record in caplog.records if record.name == SEAM_LOGGER and record.levelno >= logging.ERROR]
    assert len(refusals) == 1, (
        f"a refused delivery produced {len(refusals)} ERROR lines from {SEAM_LOGGER} — "
        f"a row that silently stops delivering is the failure ruling #3 accepted on the "
        f"condition that it is loud"
    )
    message = refusals[0].getMessage()
    assert "Digest" in message, f"the refusal log does not name the scheme it refused: {message!r}"
    assert "scheme_not_in_spec" in message, f"the refusal log does not name the reason: {message!r}"
    assert CONFORMING_SECRET not in message, f"the refusal log leaked the buyer's credential: {message!r}"


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_the_refusal_detail_carries_no_credential_and_no_destination(seam_call, monkeypatch, local_origin_tls):
    """``detail`` is PRE-SANITIZED at the seam, because its consumers re-emit it.

    Four senders read ``outcome.detail`` into their own log lines and (for two of
    them) into a stored failure record. Sanitizing at each consumer is the shape
    that already leaked once; sanitizing here is what makes "never a URL or a
    credential" a property of the value rather than a rule four call sites must
    each remember.
    """
    open_private_hatch(monkeypatch)
    local_origin_tls.respond_with(200)

    outcome = deliver(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        PAYLOAD,
        scheme=BEARER_SCHEME,
        credentials=ONE_SHORT_OF_CONFORMING,
    )

    detail = outcome.detail or ""
    assert ONE_SHORT_OF_CONFORMING not in detail, f"outcome.detail carries the buyer's credential: {detail!r}"
    assert local_origin_tls.base_url not in detail, f"outcome.detail carries the destination URL: {detail!r}"
    assert str(local_origin_tls.port) not in detail, f"outcome.detail carries the destination port: {detail!r}"


# ---------------------------------------------------------------------------
# 2. The delivering half of THE RULE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
@pytest.mark.parametrize("scheme", [HMAC_SCHEME])
def test_an_hmac_row_delivers_signed_over_the_exact_bytes(seam_call, scheme, monkeypatch, local_origin_tls):
    """200 + HMAC: ``delivered``, signed over the bytes that crossed the socket.

    ``payload_size_bytes`` is asserted against the RECEIVED body, not against a
    re-serialization of the payload here: it exists so a sender can log the size
    without reaching past the seam for ``prepare_signed_request``'s ``body_bytes``,
    and a number that does not equal what arrived would make that reach-through
    correct after all.

    The lowercase spelling is parametrized rather than split off: it is the same
    obligation, and it is exactly what a "just compare against the enum" repair
    would break.
    """
    open_private_hatch(monkeypatch)
    local_origin_tls.respond_with(200)

    outcome = deliver(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        PAYLOAD,
        scheme=scheme,
        credentials=CONFORMING_SECRET,
    )

    assert (outcome.kind, outcome.attempts, outcome.http_status) == ("delivered", 1, 200), (
        f"kind={outcome.kind!r} attempts={outcome.attempts} http_status={outcome.http_status}"
    )
    assert local_origin_tls.hits == 1
    delivered = local_origin_tls.last_request
    assert delivered.body == canonical_bytes(PAYLOAD), (
        f"the body on the wire is not the pinned compact-separator form: {delivered.body!r}"
    )
    assert_signature_verifies_over_wire_body(delivered, CONFORMING_SECRET)
    assert outcome.payload_size_bytes == len(delivered.body), (
        f"payload_size_bytes={outcome.payload_size_bytes} but {len(delivered.body)} bytes arrived — "
        f"the size a sender records must be the size it sent"
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
@pytest.mark.parametrize("scheme", [BEARER_SCHEME])
def test_a_bearer_row_delivers_the_header_and_an_unsigned_body(seam_call, scheme, monkeypatch, local_origin_tls):
    """200 + Bearer: ``Authorization`` present, body UNSIGNED.

    Signing is gated by the SCHEME, never by "a credential is lying around" —
    this row holds the very credential the HMAC arm signs with. A seam that asks
    the second question attaches HMAC headers to a receiver expecting a plain
    bearer POST.

    The PINNED spelling only. ``AuthenticationScheme`` is case-sensitive and
    there is one vocabulary, so ``bearer``/``BEARER`` rows do not deliver in a
    different case — they refuse until re-registered, graded next door in
    ``test_a_non_canonical_spelling_refuses_until_re_registered``.
    """
    open_private_hatch(monkeypatch)
    local_origin_tls.respond_with(200)

    outcome = deliver(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        PAYLOAD,
        scheme=scheme,
        credentials=CONFORMING_SECRET,
    )

    assert (outcome.kind, outcome.attempts, outcome.http_status) == ("delivered", 1, 200)
    delivered = local_origin_tls.last_request
    assert delivered.headers["Authorization"] == f"Bearer {CONFORMING_SECRET}", (
        f"Authorization={delivered.headers['Authorization']!r}"
    )
    assert SIGNATURE_HEADER not in delivered.headers, (
        f"a Bearer delivery carries {SIGNATURE_HEADER} — signing is gated by the scheme"
    )
    assert delivered.body == canonical_bytes(PAYLOAD)
    assert outcome.payload_size_bytes == len(delivered.body)


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
@pytest.mark.parametrize(
    "scheme",
    ["Basic", "basic", "bearer", "BEARER", "hmac-sha256", "HMAC_SHA256"],
    ids=["Basic", "basic", "bearer-lower", "BEARER-upper", "hmac-lower", "hmac-underscore"],
)
def test_a_non_canonical_spelling_refuses_until_re_registered(seam_call, scheme, monkeypatch, local_origin_tls):
    """A stored row whose scheme is not a pinned enum member stops delivering.

    This case used to assert the opposite. It graded ``Basic`` and the lower/upper
    spellings as first-class DELIVERING rows, on the reasoning that the untyped
    A2A push-config endpoint stores a free-form protobuf string, so such rows exist
    and refusing them would stop deliveries their owners cannot be asked to fix.

    The owner's ruling replaced that with one vocabulary: the pinned
    ``AuthenticationScheme``, case-sensitive, and nothing else. Non-conforming rows
    are MIGRATED, not tolerated — the operator re-registers the webhook with a
    scheme and credentials the spec defines, and until they do it does not deliver.
    Tolerance is what produced three spellings of one fact in the first place, and
    a widening kept alive by its own compatibility argument never expires.

    Refusing is not merely the tidy answer here. A stored ``basic`` row that we
    "helpfully" fold and deliver is an ``Authorization`` header we assembled from a
    scheme the buyer's registration never validly named; the receiver has no way
    to know we guessed. Refusing leaves the row visible and fixable.

    The zero-hit assertion is the independent witness: the origin answers 200 and
    really is listening, so "no request arrived" means the SEAM refused rather
    than the request going out and failing on arrival.
    """
    open_private_hatch(monkeypatch)
    local_origin_tls.respond_with(200)

    outcome = deliver(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        PAYLOAD,
        scheme=scheme,
        credentials=CONFORMING_SECRET,
    )

    assert (outcome.kind, outcome.reason, outcome.attempts, outcome.http_status) == (
        "refused_auth",
        "scheme_not_in_spec",
        0,
        None,
    ), (
        f"{scheme!r}: expected a pre-flight refusal discriminated as 'scheme_not_in_spec', got "
        f"kind={outcome.kind!r} reason={getattr(outcome, 'reason', None)!r} "
        f"attempts={outcome.attempts} http_status={outcome.http_status}"
    )
    assert outcome.scheme == scheme, (
        f"the refusal must carry the scheme it refused ({scheme!r}), got {outcome.scheme!r} — "
        f"an operator reading the log cannot find the row to re-register otherwise"
    )
    assert local_origin_tls.hits == 0, (
        f"{scheme!r}: the seam refused and then dialled anyway "
        f"({local_origin_tls.hits} request(s) reached an origin that answers 200)"
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_no_scheme_and_no_credential_delivers_plain(seam_call, monkeypatch, local_origin_tls):
    """The spec's own selector: absence of the block selects unauthenticated delivery.

    This is the row THE RULE deliberately does NOT refuse, and it is why the
    refusal cases above pair "no scheme" with a stored credential: without this
    case, "refuse anything that is not one of the two schemes" would read as
    covering an absent block too, and every unauthenticated buyer would stop
    being delivered to.
    """
    open_private_hatch(monkeypatch)
    local_origin_tls.respond_with(200)

    outcome = deliver(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        PAYLOAD,
        scheme=None,
        credentials=None,
    )

    assert (outcome.kind, outcome.attempts, outcome.http_status) == ("delivered", 1, 200)
    delivered = local_origin_tls.last_request
    assert "Authorization" not in delivered.headers, (
        f"an unauthenticated delivery carries Authorization={delivered.headers['Authorization']!r}"
    )
    assert SIGNATURE_HEADER not in delivered.headers
    assert delivered.body == canonical_bytes(PAYLOAD)


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_the_conforming_boundary_delivers(seam_call, monkeypatch, local_origin_tls):
    """Exactly 32 characters delivers — the other side of ``credentials_too_short``.

    Held separately from the signing case so the boundary is graded as a
    boundary: a seam that read ``> 32`` would keep every other delivering case
    green and stop exactly this row.
    """
    open_private_hatch(monkeypatch)
    local_origin_tls.respond_with(200)

    outcome = deliver(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        PAYLOAD,
        scheme=HMAC_SCHEME,
        credentials=CONFORMING_SECRET,
    )

    assert len(CONFORMING_SECRET) == 32, "this case grades the pinned minimum; the constant drifted"
    assert outcome.kind == "delivered", (
        f"a credential of exactly the pinned minLength was refused as {outcome.kind!r}/{outcome.reason!r}"
    )


# ---------------------------------------------------------------------------
# 3. Everything the destination decides, mapped onto the same record
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_a_blocked_destination_is_its_own_kind(seam_call, monkeypatch):
    """``OutboundRequestBlocked`` -> ``refused_destination``, never ``refused_auth``.

    Two different pre-flight refusals with two different remedies: the buyer
    re-registers a credential for one and a URL for the other. Collapsing them
    into one kind would send every operator to the wrong half of the row.
    """
    open_private_hatch(monkeypatch)

    outcome = deliver(seam_call, METADATA_URL, PAYLOAD, scheme=HMAC_SCHEME, credentials=CONFORMING_SECRET)

    assert (outcome.kind, outcome.attempts, outcome.http_status) == ("refused_destination", 0, None), (
        f"kind={outcome.kind!r} attempts={outcome.attempts} http_status={outcome.http_status}"
    )
    assert METADATA_URL not in (outcome.detail or ""), (
        f"outcome.detail echoes the refused destination back: {outcome.detail!r}"
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_a_terminal_client_error_reports_one_attempt_and_the_status(seam_call, monkeypatch, local_origin_tls):
    """404 -> ``client_error``, one attempt, the status the origin actually sent.

    The status is what an operator uses to tell a wrong URL from an outage, and
    the attempt count is what says the seam did not retry something that will
    never succeed. Both come off the same record now, so a sender cannot report
    one without the other.
    """
    open_private_hatch(monkeypatch)
    local_origin_tls.respond_with(404, body=b'{"error": "no such hook"}')

    outcome = deliver(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        PAYLOAD,
        scheme=HMAC_SCHEME,
        credentials=CONFORMING_SECRET,
        max_attempts=3,
    )

    assert (outcome.kind, outcome.attempts, outcome.http_status) == ("client_error", 1, 404), (
        f"kind={outcome.kind!r} attempts={outcome.attempts} http_status={outcome.http_status}"
    )
    assert local_origin_tls.hits == 1, (
        f"a terminal 404 was retried: {local_origin_tls.hits} attempts reached the origin"
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_an_exhausted_delivery_counts_every_attempt(seam_call, monkeypatch, local_origin_tls):
    """503 to exhaustion -> ``exhausted``, with the attempt count and the last status."""
    open_private_hatch(monkeypatch)
    monkeypatch.setenv(BACKOFF_BASE_ENV, "0.001")
    local_origin_tls.respond_with(503, body=b'{"error": "unavailable"}')

    outcome = deliver(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        PAYLOAD,
        scheme=HMAC_SCHEME,
        credentials=CONFORMING_SECRET,
        max_attempts=3,
    )

    assert (outcome.kind, outcome.attempts, outcome.http_status) == ("exhausted", 3, 503), (
        f"kind={outcome.kind!r} attempts={outcome.attempts} http_status={outcome.http_status}"
    )
    assert local_origin_tls.hits == 3, f"the origin saw {local_origin_tls.hits} attempts, the outcome claims 3"
