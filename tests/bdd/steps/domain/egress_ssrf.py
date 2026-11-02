"""Domain steps for the local egress/SSRF refusal feature.

Grades the buyer-visible half of AdCP 3.1.1 L1 § "Webhook URL validation
(SSRF)" through two buyer-supplied URLs: ``property_list.agent_url`` on
``get_products`` (fetch-now — the refusal happens at send time) and
``push_notification_config.url`` on ``create_media_buy`` (store-now/dial-later
— the refusal happens at ingest, the only moment a request exists to carry
it). Both run on every wire transport, which is what makes the same scenario
runnable on a2a / mcp / rest / e2e_rest.

The refusal itself is produced by production: the harness routes @egress
scenarios to ``RealResolverProductEnv`` (real resolver, real seam) and
@egress_create scenarios to ``MediaBuyCreateEnv`` (real ``_impl``, real
ingest verdict via ``reject_unsafe_webhook_registration_url`` in
``src.core.webhook_validator``).

Note the ingest verdict is NOT the seam's: the registration gate is
deliberately DNS-free, so an unresolvable-but-public hostname is ACCEPTED at
ingest and only re-checked when the callback is dialled. The seam
(``src.core.security.outbound_http``) remains the send-time gate. Scenarios
that expect an ingest refusal must therefore pick a cause the DNS-free gate
actually rejects — a reserved-address literal — not an unresolvable name; and,
to keep the non-disclosure obligation gradable, one the gate does not report by
naming the blocked hostname or a dotted-quad range (see the Examples rationale
in the feature). What the two gates DO NOT differ on is the wire: both refuse a
buyer-supplied URL as VALIDATION_ERROR / correctable / field, because both raise
the one refusal class for that semantic. That is by construction, not
coincidence — the wire code is a function of what the buyer did wrong, never of
which gate noticed it (the pinned enum calls VALIDATION_ERROR "invalid field
values or violates business rules beyond schema validation", which is what a
well-formed URL landing in a blocked range is).

The CREDENTIAL half of a registration is graded by the second group of When
steps below. A registration has two halves and both are refusable at ingest,
but they differ in where they are REACHABLE: the URL half travels every
transport (``url`` alone is a schema-valid config), while a config naming
HMAC-SHA256 with no ``credentials`` is schema-INVALID against the pin, so every
tool surface refuses it ABOVE ``_impl`` — create and sync through the same
``to_push_notification_config`` funnel REST uses, update through the typed
``UpdateMediaBuyRequest``, MCP through FastMCP's TypeAdapter. That shared funnel
is why they all report one absolute field path, which is why the tool-surface
scenarios grade on every transport; the feature file's header for that group
carries the measurement.

Steps store in ctx (on top of what ``dispatch_request`` stores):
    ctx["supplied_agent_url"] — the URL the buyer sent, so the non-disclosure
        Then can assert its absence structurally rather than by eyeball.
"""

from __future__ import annotations

import ipaddress
import json
import re
from urllib.parse import urlparse

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import (
    _require,
    error_envelope_or_none,
    payload_or_none,
    wire_entry,
    wire_entry_errors,
)
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories.webhook import PushNotificationConfigRequestFactory
from tests.helpers.webhook_credential_refusal import SHORT_CREDENTIAL

# The list_id is irrelevant to a refusal — the seam refuses before a connection
# is opened, so the path is never built. Fixed so the request is well-formed.
_LIST_ID = "test_list"

# The one wire answer BOTH gates give a buyer-supplied URL they refuse, spelled
# once so the cause-invariance Then and this module's prose cannot drift apart.
# VALIDATION_ERROR rather than INVALID_REQUEST because the pinned enum splits
# them on malformed-vs-value-refused and a schema-valid https URI turned away by
# a deny-list is the second — the reasoning ``AdCPUrlNotAllowedError`` carries in
# full, and which the module docstring above cites the pinned enum text for.
# `correctable` is that code's pinned enumMetadata classification, and passing it
# explicitly means a pin that reclassifies the code reddens here rather than
# silently redefining what this scenario grades.
_REFUSAL_CODE = "VALIDATION_ERROR"
_REFUSAL_RECOVERY = "correctable"

# A public URL that PASSES the registration SSRF gate running immediately before
# the credential gate, so the only thing that can refuse a credential-half
# scenario is the credential half. Same host the URL gate's own "allows public"
# case uses, and the same constant the integration twin
# (tests/integration/test_webhook_hmac_credentials_ingest_refusal.py) picks.
_SAFE_WEBHOOK_URL = "https://buyer.example.com/hook"

# The pinned AuthenticationScheme spelling for legacy shared-secret signing
# (AdCP 3.1.1 enums/auth-scheme.json). Written exactly so a refusal cannot be
# explained by the scheme being unrecognized rather than the secret being absent.
_HMAC_SCHEME = "HMAC-SHA256"

# The AdCP-shaped authentication block a buyer sends on a TOOL parameter:
# ``schemes`` is an array (maxItems 1), ``credentials`` is simply absent — the
# spelling the pinned schema calls invalid and the spelling a buyer produces by
# forgetting the secret. The A2A protocol envelope uses the protobuf shape
# instead (singular ``scheme``); see the message/send step below.
_HMAC_WITHOUT_CREDENTIALS = {"schemes": [_HMAC_SCHEME]}

# TWO schemes in one array — the shape the pin FORBIDS outright
# (``authentication.schemes`` is ``minItems: 1, maxItems: 1``), not a shape whose
# precedence the seller may resolve for itself: the block's own description says
# "Precedence is a switch, not a fallback ... A seller MUST NOT sign the same
# webhook both ways". Credentials are absent as well, and that is deliberate —
# it is the document the lane's own bug report names, and it is measured (against
# the pinned model, 2026-08-18) to fail at ``authentication.schemes`` rather than
# at ``authentication.credentials``, because ``too_long`` on the array is
# reported before the missing secret. The scenario states that field exactly.
_TWO_SCHEMES = {"schemes": ["Bearer", _HMAC_SCHEME]}

# One character short of the pinned ``credentials`` ``minLength: 32``, imported
# from the module that already holds the credential-refusal contract for the
# integration graders — the boundary value is one fact and it is spelled once.
_SHORT_SECRET = SHORT_CREDENTIAL
_HMAC_WITH_SHORT_CREDENTIALS = {"schemes": [_HMAC_SCHEME], "credentials": _SHORT_SECRET}

# Anything that could spell an IP address. Deliberately over-broad: the tokens it
# yields are then handed to ``ipaddress``, which is the actual decision. A regex
# alone would either miss ``fd00:ec2::254`` or flag every ISO timestamp.
_ADDRESS_CANDIDATE_RE = re.compile(r"[0-9A-Fa-f:.]{3,}")


def _wire_error_envelope(ctx: dict) -> dict:
    """Return the wire error envelope, failing loudly if the request did not fail."""
    envelope = error_envelope_or_none(ctx)
    assert envelope is not None, (
        "Expected a wire error envelope — the request was supposed to be refused. "
        f"Got none; recorded error={ctx.get('error')!r}, response={payload_or_none(ctx)!r}"
    )
    return envelope


def _ip_addresses_in(text: str) -> list[str]:
    """Every substring of *text* that really is an IP address.

    ``ipaddress.ip_address`` is the oracle rather than a pattern: it accepts
    ``169.254.169.254`` and ``fd00:ec2::254`` and rejects ``3.1.1`` and the
    ``12:34:56`` of a timestamp, so the assertion cannot be defeated by a leak in
    a form the pattern did not anticipate, nor go red on innocent text.
    """
    found: list[str] = []
    for token in _ADDRESS_CANDIDATE_RE.findall(text):
        try:
            found.append(str(ipaddress.ip_address(token.strip(".:"))))
        except ValueError:
            continue
    return found


# ── Given steps ─────────────────────────────────────────────────────


@given("the outbound private-range egress hatch is open")
def given_egress_hatch_open(ctx: dict) -> None:
    """Run this scenario with ADCP_OUTBOUND_ALLOW_PRIVATE on (GH #1757).

    The permissive posture is the DELIBERATE choice for a refusal that must mean
    the same thing everywhere: with the reserved-range gate disarmed, a refusal
    can only come from the causes that are immune to it — a blocked
    cloud-metadata address, a host that does not resolve, a plaintext http
    scheme (``_scheme_error`` in ``src/core/security/egress/policy.py``, checked
    before ``allow_private`` is read at all), and a #974 supplement range such as
    CGNAT 100.64.0.1, which ``adcp.signing`` does not classify at all and which
    this repo's own ``_in_supplement_range`` refuses under every posture
    (GH #1802). That is also the posture the e2e stack runs in (this
    is the ONLY hatch left — the scheme hatch was deleted, the seam now requires
    https unconditionally), so the in-process transports and e2e_rest grade one
    production, not two.
    """
    ctx["env"].set_egress_hatches(private=True)


# ── When steps ──────────────────────────────────────────────────────


@when(parsers.parse('the buyer requests products with a property list agent at "{agent_url}"'))
def when_request_products_with_property_list(ctx: dict, agent_url: str) -> None:
    """Dispatch get_products carrying a buyer-supplied property_list.agent_url."""
    ctx["supplied_agent_url"] = agent_url
    dispatch_request(
        ctx,
        brief="egress refusal test",
        property_list={"agent_url": agent_url, "list_id": _LIST_ID},
    )


@when(parsers.parse('the buyer syncs a creative whose format agent is at "{agent_url}"'))
def when_sync_creative_with_agent_url(ctx: dict, agent_url: str) -> None:
    """Dispatch sync_creatives carrying a buyer-supplied creatives[].format_id.agent_url.

    The third buyer-supplied URL on the protocol surface. Unlike the two above,
    this one is a PER-CREATIVE field, so the refusal has to name which creative
    — hence the indexed ``field`` the Then step asserts.

    The creative is built through ``CreativeAssetFactory`` (overriding only
    ``format_id``) rather than as a literal dict: a payload missing a required
    field is rejected by the MCP wrapper's typed parameters BEFORE any egress
    decision, and that VALIDATION_ERROR resembles a refusal closely enough to
    pass a careless assertion for entirely the wrong reason.
    """
    from adcp.types import FormatId

    from tests.factories.creative_asset import CreativeAssetFactory

    ctx["supplied_agent_url"] = agent_url
    creative = CreativeAssetFactory(
        creative_id="c_egress_refusal",
        name="Egress Refusal Creative",
        format_id=FormatId(id="display_300x250_image", agent_url=agent_url),
    )
    dispatch_request(ctx, creatives=[creative])


@when(parsers.parse('the buyer creates a media buy with push notification url "{webhook_url}"'))
def when_create_media_buy_with_push_url(ctx: dict, webhook_url: str) -> None:
    """Dispatch create_media_buy carrying a buyer-supplied push_notification_config.url.

    The ingest twin of the get_products dispatch above: the URL is stored now
    and dialled later, so the refusal under test must happen on THIS request.
    Runs on the media-buy create harness (@egress_create routes there); the
    request body is otherwise the harness's minimal valid create.
    """
    from tests.bdd.steps.generic.given_media_buy import harness_create_request_kwargs

    ctx["supplied_agent_url"] = webhook_url
    kwargs = harness_create_request_kwargs(ctx)
    kwargs["push_notification_config"] = PushNotificationConfigRequestFactory.payload(url=webhook_url)
    dispatch_request(ctx, **kwargs)


# ── When steps: the AUTHENTICATION half of a registration ───────────
#
# Registration surfaces carrying an authentication block the seller cannot
# serve. Each dispatches through ``dispatch_request`` so the refusal is
# production's, on the wire, and none of them builds the request model in the
# test — a test-side ``ValidationError`` would prove the harness validates, not
# that the buyer is refused.
#
# The block is the only thing that varies between them, so the dispatch itself
# is written once per TOOL and the steps supply the document: a second copy
# differing only in a dict literal is the shape the DRY invariant forbids, and
# it is also how the two documents would drift into being dispatched against
# different requests.


def _dispatch_create_registering(ctx: dict, authentication: dict) -> None:
    """Dispatch create_media_buy registering *authentication* on a safe URL.

    Everything except the authentication block comes from the harness's minimal
    valid create, so a package/pricing rejection can never stand in for the
    refusal under test, and the URL is public so the SSRF gate that runs first
    cannot be the thing that refuses.
    """
    from tests.bdd.steps.generic.given_media_buy import harness_create_request_kwargs

    kwargs = harness_create_request_kwargs(ctx)
    kwargs["push_notification_config"] = PushNotificationConfigRequestFactory.payload(
        url=_SAFE_WEBHOOK_URL, authentication=authentication
    )
    dispatch_request(ctx, **kwargs)


def _dispatch_sync_registering(ctx: dict, authentication: dict) -> None:
    """Dispatch sync_creatives registering *authentication* on a safe URL.

    ``push_notification_config`` is request-level here (unlike
    ``creatives[].format_id.agent_url``), so the whole request must be refused —
    a per-item failure would tell the buyer to fix a creative when the thing that
    is wrong is the registration. The creative comes from
    ``CreativeAssetFactory`` so a missing required field cannot produce a
    VALIDATION_ERROR that resembles this refusal for the wrong reason.
    """
    from tests.factories.creative_asset import CreativeAssetFactory

    creative = CreativeAssetFactory(
        creative_id="c_creds_refusal",
        name="Credential Refusal Creative",
    )
    dispatch_request(
        ctx,
        creatives=[creative],
        push_notification_config={
            "url": _SAFE_WEBHOOK_URL,
            "authentication": authentication,
        },
    )


@when("the buyer creates a media buy registering HMAC-SHA256 with no credentials")
def when_create_media_buy_hmac_without_credentials(ctx: dict) -> None:
    """Dispatch create_media_buy with a credential-less HMAC push_notification_config.

    The surface that runs BOTH registration halves today.
    """
    _dispatch_create_registering(ctx, _HMAC_WITHOUT_CREDENTIALS)


@when("the buyer creates a media buy registering two authentication schemes")
def when_create_media_buy_two_schemes(ctx: dict) -> None:
    """Dispatch create_media_buy with a ``schemes`` array the pin forbids.

    The document lane C3 exists for: today the untyped A2A tool path forwards
    the buyer's raw dict to ``_impl``, where ``schemes[0]`` selects "Bearer" and
    the HMAC half is never inspected — so the registration is ACCEPTED, stored,
    and then never signed at delivery. The typed transports refuse it above the
    gate, but with a field path relative to the sub-model they validated, so the
    same buyer error currently reaches ``error.field`` differently depending on
    which wire it arrived on.
    """
    _dispatch_create_registering(ctx, _TWO_SCHEMES)


@when("the buyer updates the media buy registering HMAC-SHA256 with no credentials")
def when_update_media_buy_hmac_without_credentials(ctx: dict) -> None:
    """Dispatch update_media_buy with a credential-less HMAC push_notification_config.

    The request model is built here WITHOUT the config and the raw config is
    passed as a separate kwarg, which the update harness overlays onto the flat
    skill parameters after expanding the model
    (``MediaBuyDualEnv._flatten_update_request``). That is deliberate and it is
    the only honest wiring: ``UpdateMediaBuyRequest`` types
    ``push_notification_config`` against the pinned model, so constructing the
    request WITH the invalid block would raise inside this step and grade the
    test's own pydantic call instead of production's refusal.

    ``account`` and ``idempotency_key`` are supplied for that same reason, not as
    padding: AdCP 3.1.1 ``media-buy/update-media-buy-request.json`` /required is
    ``[idempotency_key, account, media_buy_id]``, so a request missing them is
    refused as a MALFORMED DOCUMENT before the registration is ever inspected —
    which would grade this step's own pydantic call instead of the egress refusal
    under test. Same constants the uc003 update dispatch uses.
    """
    from src.core.schemas import UpdateMediaBuyRequest

    media_buy = _require(ctx, "existing_media_buy")
    dispatch_request(
        ctx,
        req=UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id=media_buy.media_buy_id,
        ),
        push_notification_config={
            "url": _SAFE_WEBHOOK_URL,
            "authentication": _HMAC_WITHOUT_CREDENTIALS,
        },
    )


@when("the buyer syncs a creative registering HMAC-SHA256 with no credentials")
def when_sync_creatives_hmac_without_credentials(ctx: dict) -> None:
    """Dispatch sync_creatives with a credential-less HMAC push_notification_config."""
    _dispatch_sync_registering(ctx, _HMAC_WITHOUT_CREDENTIALS)


@when("the buyer syncs a creative registering HMAC-SHA256 with a 31-character secret")
def when_sync_creatives_short_credentials(ctx: dict) -> None:
    """Dispatch sync_creatives with a secret one character under the pinned minimum.

    The sync twin of the multi-scheme create above. The hole it grades was an untyped
    forward: a wrapper DECLARED ``PushNotificationConfig | None`` and performed no coercion,
    so the buyer's raw dict travelled to ``_sync_creatives_impl`` and a secret the pin calls
    too short was stored and later used to sign — a secret both ends must agree on, accepted
    at a strength the spec says neither end may rely on. Every transport validates into
    ``SyncCreativesRequest`` now, so the coercion happens before dispatch.
    """
    _dispatch_sync_registering(ctx, _HMAC_WITH_SHORT_CREDENTIALS)


# ── Then steps ──────────────────────────────────────────────────────


# NOTE: the request-level rejection Then for these scenarios does not live here, and no
# longer lives in ``uc_get_products_inventory.py`` either — this note used to point there,
# at ``the request is rejected with VALIDATION_ERROR naming field "{field}"``, and that
# cross-reference had gone stale: the sentence occurs in no feature, so the step it named
# bound nothing and has been deleted.
#
# The request-level refusals in local-egress-ssrf-refusal.feature were reworded into three
# narrower sentences, and those are what grade them now: ``the response arrives``, ``the
# response contains error code VALIDATION_ERROR`` and ``the response error field is
# push_notification_config.url`` (or ``property_list.agent_url``), all in
# ``generic/then_error.py``. The decomposition keeps the same obligations — ``correctable``
# included, because ``then_response_error_code`` calls ``assert_wire_error`` without a
# recovery and the helper DEFAULTS it from CODE_TABLE, so a refusal re-classified
# transient still fails. What remains here is the per-ITEM sentence below, whose failure
# lives inside a success envelope and so cannot share the request-level step.
#
# The one-global-namespace constraint that motivated the original arrangement is unchanged:
# every module in tests/bdd/conftest.py's ``pytest_plugins`` shares one step registry, so a
# second definition of a literal is an ambiguous first-wins binding, which
# ``test_guards_bdd_duplicate_step_literals`` forbids.


@then(
    "the refusal is VALIDATION_ERROR / correctable on both envelope layers, "
    "and its error object carries the code's own message"
)
def then_refusal_is_the_codes_own_message(ctx: dict) -> None:
    """Assert the refusal carries nothing that distinguishes WHICH cause fired.

    THE OBLIGATION. An unresolvable host and a blocked reserved address must be
    indistinguishable on the wire, or the refusal is a name-existence oracle —
    AdCP 3.1.1 L1 spec point 6's second half. Every Examples row of both
    scenarios that binds this step therefore expects the SAME answer.

    WHY IT IS NOT REDUNDANT with ``tests/unit/test_protocol_webhook_ssrf.py``,
    which already grades code / recovery / field and host-absence for a blocked
    URL: that test grades ONE refusal in isolation, so it structurally cannot
    see a difference BETWEEN causes. Cause-invariance is a property of the SET,
    and this Scenario Outline is where the set exists. Do not delete this step
    as duplicated coverage.

    WHY IT IS NOT COVERED by :func:`then_envelope_discloses_nothing` either.
    That Then serializes the whole envelope and forbids the supplied host and
    any IP address — so it catches ``f"{msg} (host ...)"``, but a per-cause
    sentence naming NEITHER ("URL resolves to a restricted range." against "Host
    does not resolve") sails straight through it while still telling a prober
    which branch fired. The two Thens check genuinely different things.

    WHAT REPLACED THE LITERAL, and why the structured form is stronger rather
    than weaker. This step used to pin an authored sentence — "URL resolves to a
    restricted range." — passed in from the Gherkin, on the reasoning that a
    literal never imported from production cannot agree with itself. That
    reasoning was sound; its premise stopped being true. This merge deleted
    origin/main's authored ``_default_message`` (see ``src/core/exceptions.py``
    at the ``AdCPBlockedUrlError`` alias: that message "is subsumed: the message
    here is the code's own table sentence"), so the literal named a string that
    exists nowhere in the tree and the scenario graded a sentence production
    cannot emit. Under ADR-010 the buyer-facing message is a pure function of
    the CODE through ``CODE_TABLE``, which means cause-invariance now follows
    from code-invariance — and that is what is asserted here:

    * ``assert_wire_error`` pins the code and recovery on BOTH envelope layers
      (its two-layer invariant ``adcp_error.code == errors[0].code``, plus
      canonicality against the pinned ``error-code.json`` and the pinned
      recovery classification). That is the "both envelope layers" half of the
      sentence, carried structurally rather than by comparing two strings this
      step fetched for itself.
    * The message is then pinned to CODE_TABLE's own sentence for that code.
      This is not grading the table against itself: it grades that production
      did not AUTHOR OVER the table, so a regression that appends a cause —
      ``f"{msg} (host ...)"``, or a per-branch sentence — reddens on the first
      Examples row that differs.

    The message half is asserted on ``errors[0]`` only, and the Gherkin sentence
    says so rather than claiming both layers. That is the protocol position
    ``core/error.json`` defines the per-error members on, and the harness
    deliberately publishes no ``adcp_error.message`` reader —
    ``assert_envelope_shape`` documents the envelope-level message as permitted
    to differ, since it carries the envelope's own summary. Promising both
    layers while reading one would be the same defect in the other direction.
    """
    from src.core.errors.codes import CODE_TABLE

    result = _require(ctx, "result", hint="the buyer's request was supposed to be refused")
    result.assert_wire_error(_REFUSAL_CODE, recovery=_REFUSAL_RECOVERY)

    error_object = result.wire_error_object()
    assert error_object is not None, "assert_wire_error passed but no error object is reachable on the wire"
    expected = CODE_TABLE[_REFUSAL_CODE].message
    actual = error_object.get("message")
    assert actual == expected, (
        f"errors[0].message={actual!r}, expected {_REFUSAL_CODE}'s own table sentence {expected!r}. "
        "A message that differs from the code's is authored text, and authored text is what lets a "
        "prober tell one refusal cause from another (AdCP 3.1.1 L1 point 6)."
    )


@then("the error envelope names neither the supplied host nor any IP address")
def then_envelope_discloses_nothing(ctx: dict) -> None:
    """Assert the WHOLE serialized envelope leaks neither the host nor an address.

    Over the whole envelope, not just ``errors[0].message``: ``context``,
    ``details``, ``suggestion`` and the envelope-level summary are all
    buyer-visible, and a leak in any of them turns ``property_list.agent_url``
    into an internal host-and-port scanner with a spec-compliant envelope wrapped
    around it (AdCP 3.1.1 L1 point 6).
    """
    envelope = _wire_error_envelope(ctx)
    serialized = json.dumps(envelope, default=str)

    host = urlparse(str(_require(ctx, "supplied_agent_url"))).hostname
    assert host is not None, f"malformed supplied_agent_url in ctx: {ctx.get('supplied_agent_url')!r}"
    assert host not in serialized, f"refusal echoed the supplied host {host!r} back to the buyer: {serialized}"

    leaked = _ip_addresses_in(serialized)
    assert leaked == [], f"refusal disclosed IP address(es) {leaked} to the buyer: {serialized}"


@then(parsers.parse('the creative is rejected with VALIDATION_ERROR naming field "{field}"'))
def then_creative_rejected_per_item(ctx: dict, field: str) -> None:
    """Assert the PER-ITEM failure carries the seam's own classification.

    Per-item rather than request-level because ``format_id.agent_url`` is a
    per-CREATIVE field: the pinned sync-creatives-response schema calls a
    synchronous success "best-effort processing with per-item status/failures"
    and says ``action="failed"`` items are "per-item validation/processing
    failures, not operation-level failures". The sibling
    ``push_notification_config.url`` fails the whole request because THAT field
    is request-level; the analogy does not carry to this one.

    ``field`` is the load-bearing half. The refusal message says nothing about
    the destination (L1 point 6), so it is the only channel that can tell a
    buyer WHICH of up to 100 creatives to fix.
    """
    # wire_entry / wire_entry_errors, not require_payload: the buyer's own view of
    # the response, so the assertion is graded at the level it CLAIMS to grade.
    # require_payload hands back a dict on some transports and a typed model on
    # others, which is why every read below used to re-decide the shape for itself
    # -- six dict-vs-model ladders in one step, each a private opinion about what
    # the harness happened to return.
    #
    # And the per-entry ``errors[]`` array is located by ``wire_entry_errors``, the
    # one GUARD-SANCTIONED reader for that region, rather than by indexing
    # ``entry["errors"]`` here: a partial-failure row's error list is a PROTOCOL
    # POSITION, and where the spec puts it is the harness's business. Hand-rolling
    # it made this step a second answer to that question -- the exact shape
    # ``test_architecture_bdd_wire_discipline`` forbids. Both readers resolve the
    # SAME entry (``creatives[0]``), and each asserts the index is in range, so the
    # "expected a per-creative result" guard is not lost by dropping the hand-rolled
    # emptiness check.
    entry = wire_entry(ctx, "creatives", index=0)
    # .get with a named assert, not a subscript: a missing key on the wire is a
    # real outcome (the seller omitted the field), and a bare KeyError two frames
    # up says nothing about which field the buyer did not get.
    action = entry.get("action")
    assert str(action) == "failed", f"a creative whose agent_url egress refused must not sync; action={action!r}"

    errors = wire_entry_errors(ctx, "creatives", index=0)
    assert errors, f"a failed creative must carry an error; got {entry!r}"
    error = errors[0]
    for key in ("code", "recovery", "field"):
        assert key in error, f"the per-item error omits {key!r}, which the buyer needs to act: {error!r}"
    code = error["code"]
    recovery = error["recovery"]
    got_field = error["field"]
    assert code == "VALIDATION_ERROR", f"errors[0].code={code!r} — a buyer-supplied URL is buyer-correctable"
    assert recovery == "correctable", f"errors[0].recovery={recovery!r}"
    assert got_field == field, f"errors[0].field={got_field!r}, expected {field!r}"


@then("the refusal names the missing shared secret and not the URL")
@then("the refusal names the too-short shared secret and not the URL")
def then_refusal_is_the_credential_contract(ctx: dict) -> None:
    """Assert the ONE credential-refusal contract, from its single definition.

    Bound to TWO sentences over ONE body, because a secret that is absent and a
    secret that is one character short are the same buyer mistake against the same
    pinned rule (``credentials``: ``required`` AND ``minLength: 32``) and owe the
    same answer. Two step functions would be two places for that answer to drift.

    Delegates to ``tests.helpers.webhook_credential_refusal`` — the module that
    already holds this contract for the integration graders on the create and
    A2A-native surfaces — so BDD and those graders cannot drift on what a
    credential refusal looks like. Re-asserting the code / recovery / field
    triple the preceding Then already pinned is deliberate: it comes from the
    same shared definition, so there is nothing for the two to disagree about.

    The half this step adds, and the reason it is not decoration: a credential
    refusal that reached the buyer as a URL refusal would say "fix your URL" about
    a URL that is fine. "It refused" is not enough; it has to refuse about the
    right field, with the right advice.
    """
    from tests.helpers.webhook_credential_refusal import assert_credentials_refusal_envelope

    assert_credentials_refusal_envelope(_wire_error_envelope(ctx), surface="push_notification_config registration")
