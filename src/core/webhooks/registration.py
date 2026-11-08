"""A webhook registration that is valid *by having been constructed*.

Core Invariant (Epic D, lane C1): there is no callable path that runs the URL
half of a push-notification registration without also running the credential
half, and a registration that cannot be delivered is refused rather than
silently downgraded to a plain send.

Before this module, "this registration is valid" was a *remembered call* over
primitives: ``reject_unsafe_webhook_registration_url`` (URL half) and
``reject_invalid_webhook_registration`` (both halves) sat side by side, so of
the five registration surfaces two remembered both halves and three remembered
only the URL half. A buyer registering ``HMAC-SHA256`` with no credentials was
accepted on those three, and then never delivered to -- the fail-closed sender
branches refuse to send unsigned what was registered signed. Accept-then-never-
deliver was reachable because the wrong thing required importing nothing and
the right thing required a type that did not exist.

The type is that missing thing. :attr:`ValidatedWebhookRegistration.auth` is
validated against :class:`~adcp.types.PushNotificationConfig` ``Authentication`` -- the pinned
``Authentication`` widened by exactly the legacy schemes this seller still
honours -- so what "deliverable" means is stated once, by a type derived from the
spec, instead of by a hand-rolled union each caller had to destructure.

Spec grounding: pinned AdCP,
``dist/schemas/<pin>/core/push-notification-config.json`` -- ``url`` is
required at top level; ``authentication`` is optional but, when present,
requires ``schemes`` and ``credentials``; ``AuthenticationScheme`` is
``["Bearer", "HMAC-SHA256"]``. The schema is SILENT on refusing a
credential-less HMAC registration at ingest -- the same standing as the SSRF
gate -- so this refusal is production-authoritative behavior, ungraded by any
conformance storyboard. Its shape (``VALIDATION_ERROR`` /
``recovery="correctable"`` / ``field``) is settled by the sibling URL gate, and
the recovery value derives from the SDK's bundled pinned
``adcp/_schemas/3.1/enums/error-code.json`` ``enumMetadata`` -- never from
``adcp.server.helpers.STANDARD_ERROR_CODES``, which contradicts the pin.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from pydantic import BaseModel, ValidationError

from src.core.errors.details import ValidationDetails
from src.core.exceptions import AdCPInvalidRequestError, AdCPValidationError
from src.core.schema_helpers import require_push_notification_config, to_push_notification_config

# The push-config spelling of the block, as a narrowing subtype of the one Authentication
# concept (src/core/schemas/notification.py): this module builds and validates PUSH
# registrations, whose pin requires credentials, so it names the subtype.
from src.core.schemas.notification import PushAuthentication, PushNotificationConfig
from src.core.webhook_validator import reject_unsafe_webhook_registration_url, webhook_url_for_log

logger = logging.getLogger(__name__)


class WebhookConfigColumns(TypedDict):
    """The persistable projection's exact shape.

    A TypedDict rather than ``dict[str, Any]`` on purpose: this projection exists
    so the persistence boundary does not destructure the value, and that boundary's
    whole thesis is "the type is the receipt". Handing it a bag of ``Any`` would
    trade three type-checked attribute reads for three string-keyed lookups, making
    a key typo a runtime ``KeyError`` instead of a mypy error.
    """

    url: str
    authentication_type: str | None
    authentication_token: str | None
    operation_id: str | None
    token: str | None


def _construct_stored_config(document: dict[str, Any]) -> PushNotificationConfig:
    """Build the library model from a STORED document without validating it.

    ``model_construct`` skips validation — which is the point on this path, since a
    stored row was accepted under an older gate and must keep delivering — but it
    also does not build NESTED models, so a bare call would leave
    ``config.authentication`` as a raw dict. Every consumer would then need an
    is-it-a-dict branch, which is the shape this package deletes rather than
    spreads.

    So the nested block is constructed too. The value therefore ALWAYS holds
    properly-typed library models; what differs between ingest and rehydration is
    only whether the contents were VALIDATED, never whether they are typed.
    """
    fields = dict(document)
    auth_block = fields.get("authentication")
    if isinstance(auth_block, dict):
        fields["authentication"] = PushAuthentication.model_construct(**auth_block)
    return PushNotificationConfig.model_construct(**fields)


@dataclass(frozen=True, slots=True, repr=False)
class ValidatedWebhookRegistration:
    """A push-notification registration that passed BOTH ingest preconditions.

    Holding one of these is the receipt: the URL cleared the registration SSRF
    gate and the credential half resolved to something a sender can actually
    apply. :attr:`auth` is what the senders consume -- resolved once, here, by
    the same pinned type the egress seam validates against, so ingest and delivery cannot answer "is this signed?"
    differently.

    It HOLDS the library ``PushNotificationConfig`` rather than re-declaring a
    subset of it, and that is not a stylistic preference. The first version of
    this type was a hand-rolled dataclass carrying ``url`` plus two flattened
    auth primitives -- 2 of the 4 fields the pinned schema defines
    (``dist/schemas/<pin>/core/push-notification-config.json``: ``url``,
    ``operation_id``, ``token``, ``authentication``). It therefore silently DROPPED
    ``operation_id`` and ``token``, both of which carry echo obligations the seller
    MUST honour -- ``operation_id`` is graded by the conformance universal
    ``dist/compliance/<pin>/universal/webhook-emission.yaml`` requirement 1, and
    the schema requires ``token`` echoed "verbatim in every webhook payload".
    A value that claims to receipt a document must not be a lossy projection of it;
    that is the same defect this package exists to remove, one level up.

    Composition rather than inheritance because this is a RECEIPT wrapping a
    validated document, not a new kind of config: the library model stays exactly
    the library model, and no field of it can be lost without deleting it from
    the SDK.

    It holds NO resolved-auth field. An earlier version carried one, typed to a
    hand-rolled union, so that an unservable variant was unholdable. The union is
    gone: what a deliverable registration is, is now stated by
    :class:`~adcp.types.PushNotificationConfig` ``Authentication`` — the pinned type widened by
    exactly the legacy schemes this seller still honours — and the delivery
    decision is made once, at the egress seam, from the stored primitives.
    The invariant therefore holds by TWO routes, both required:
    :func:`accept_push_notification_config` validates against the pinned model,
    and :meth:`from_stash` validates the stored authentication block explicitly.
    """

    config: PushNotificationConfig

    def __repr__(self) -> str:
        """Name the registration without rendering what authenticates it.

        The generated dataclass repr walked straight into
        ``config.authentication.credentials`` and printed the buyer's secret
        verbatim -- into any f-string, any ``str()``, and any log line that
        carried the object. ``repr=False`` plus this body is why that is no
        longer reachable through the carrier.

        Not ``field(repr=False)`` on ``config``: ``config`` is the only field, so
        that spelling yields ``ValidatedWebhookRegistration()`` -- an empty repr
        an operator cannot diagnose anything from. The URL goes through
        :func:`webhook_url_for_log`, which drops userinfo and the query string
        (a token can ride in either), and the authentication SCHEME is named
        because which scheme is configured is the diagnostic question; what the
        credential IS never is.

        KNOWN AND ACCEPTED LIMITATION: this protects the CARRIER only.
        ``repr(reg.config)`` still renders the credential, because
        ``PushNotificationConfig`` is a generated SDK model -- annotating it
        ``SecretStr`` would not survive a pin bump, would need Pattern #4 nested
        re-annotation, and ``SecretStr.model_dump()`` writes ``**********`` into
        a persistence path that is read back FOR SIGNING. The escalation is
        deferred deliberately, not overlooked.
        """
        return (
            f"ValidatedWebhookRegistration(url={webhook_url_for_log(self.url)!r}, "
            f"authentication={self.authentication_type!r})"
        )

    @property
    def url(self) -> str:
        """The registration URL as a PLAIN str.

        Coerced here rather than stored: the library field is a pydantic
        ``AnyUrl``, and a pydantic object reaching a SQLAlchemy ``String`` column
        raises ``StatementError`` at flush (gh-#1377). The wire type stops being a
        wire type at THIS boundary, once, instead of at every transport wrapper.
        """
        return str(self.config.url) if self.config.url is not None else ""

    @property
    def authentication_type(self) -> str | None:
        """The single requested scheme as a PLAIN str (never an enum member)."""
        auth_block = self.config.authentication
        if auth_block is None or not auth_block.schemes:
            return None
        return str(auth_block.schemes[0])

    @property
    def authentication_token(self) -> str | None:
        """The buyer's credential as a PLAIN str."""
        auth_block = self.config.authentication
        if auth_block is None or auth_block.credentials is None:
            return None
        return str(auth_block.credentials)

    @property
    def operation_id(self) -> str | None:
        """The buyer's correlation id, which the seller MUST echo in every payload.

        Preserved rather than projected away: ``webhook-emission.yaml`` req. 1 is a
        graded conformance requirement, and it explicitly forbids recovering this by
        parsing the receiver URL -- so if the registration does not carry it, it is
        unrecoverable.
        """
        return str(self.config.operation_id) if self.config.operation_id is not None else None

    @property
    def token(self) -> str | None:
        """The buyer's validation token, which the seller MUST echo verbatim."""
        return str(self.config.token) if self.config.token is not None else None

    @classmethod
    def from_stash(
        cls,
        # `object`, not `Any`. The value is read off a JSONType column, so it
        # genuinely can be anything -- but `Any` says "I decline to type this"
        # and lets a caller subscript it unchecked, while `object` says "anything,
        # and you must narrow it first". The isinstance below is that narrowing,
        # and it is load-bearing: its refusal is what a buyer sees when a stored
        # registration is unreadable. It stays.
        stashed: object,
        *,
        field_prefix: str = "push_notification_config",
    ) -> ValidatedWebhookRegistration:
        """Rehydrate a STORED registration — deliberately NOT a fresh ingest.

        This does NOT run the stash through the schema, and that is the whole
        point. Ingest validates against the pinned model because a buyer is there
        to correct what it rejects. A stored row has no buyer left: it was accepted
        under whatever gate existed when it was written, it DELIVERS today, and the
        delivery path fails closed (``context_manager`` catches
        ``AdCPValidationError`` and skips the webhook). So validating here does not
        protect anyone — it silently converts "delivered" into "never delivered at
        all", which is the exact failure this package exists to remove, arriving
        from the far end.

        That is not theoretical. Rows written through the untyped A2A path carry any
        scheme spelling and any credential length: measured, routing rehydration
        through the model stopped FIVE shapes delivering that deliver today — a
        sub-32-character credential, a non-canonical spelling like ``hmac-sha256``,
        an unrecognised scheme such as ``Basic``, a short ``token`` or a malformed
        ``operation_id`` (fields the old gate simply ignored), and an empty
        ``schemes`` list.

        What IS still enforced is the one thing that was never deliverable:
        ``HMAC-SHA256`` with no usable secret resolves to
        an authentication block the pinned type refuses — such a row
        refused before this package existed and must keep refusing, by name.

        The stored document is carried in the library type via ``model_construct``
        (no validation) rather than a hand-rolled shape, so nothing is dropped and
        the value still holds exactly one representation of a registration.
        """
        document = stashed
        if not isinstance(document, dict):
            # Buyer-facing text is a function of the code (ADR-010): the one
            # structured fact — what the stash actually held -- goes to the typed
            # details. `field` still names the buyer-correctable target, which is
            # what the refusal is graded on.
            raise AdCPValidationError(
                field=field_prefix,
                details=ValidationDetails(received_type=type(stashed).__name__),
            )

        url = str(document.get("url") or "").strip()
        if not url:
            raise AdCPValidationError(field=f"{field_prefix}.url")

        # The authentication block IS validated here, through the SAME type the
        # seam constructs, even though the rest of the stored document is not.
        # Deleting the resolved-auth field removed the type-level guarantee that a
        # held value is deliverable, so this gate is what replaces it — the two
        # changes are one requirement and neither is safe without the other.
        # Using PushAuthentication rather than a hand-written re-check keeps
        # ingest, the seam and rehydration from holding three definitions of what
        # a valid block is.
        auth_block = document.get("authentication")
        if auth_block is not None:
            try:
                # The VALIDATED block replaces the stored one below, so the
                # canonical spelling survives into the value. Validating and then
                # building from the raw document would discard the case-folding and
                # leave `hmac-sha256` in a value whose whole job is to be the one
                # answer to "what was registered".
                validated = PushAuthentication.model_validate(auth_block)
            except ValidationError as exc:
                # Name the SPECIFIC sub-field pydantic objected to, not just the
                # block: "…authentication.credentials" tells the owner what to fix,
                # and it is the same path the ingest refusal envelope is graded on,
                # so the two surfaces cannot drift into naming different things for
                # the same defect.
                first = exc.errors()[0].get("loc") or ()
                sub = ".".join(str(part) for part in first if isinstance(part, str | int))
                field = f"{field_prefix}.authentication" + (f".{sub}" if sub else "")
                # NAME THE SCHEME. With no outcome record and no migration for the
                # rows this affects, an operator still has to be able to tell WHICH
                # registrations stopped delivering — "2 problems" is not enumerable.
                # The name no longer rides the buyer-facing message, which is now the
                # code's own table sentence: the scheme is a value the buyer supplied
                # and had refused, so it goes to the pin's canonical rejection key
                # (`details.rejected_value`), and pydantic's own error is the cause,
                # logged with its traceback and never serialized.
                stored_schemes = auth_block.get("schemes") if isinstance(auth_block, dict) else None
                named_schemes = [str(entry) for entry in stored_schemes] if stored_schemes else []
                raise AdCPValidationError(
                    field=field,
                    details=ValidationDetails(rejected_value=named_schemes or None),
                    internal_detail=exc,
                ) from exc
            document = {**document, "authentication": validated.model_dump(mode="json")}

        return cls(config=_construct_stored_config(document))

    def to_columns(self) -> WebhookConfigColumns:
        """The persistable projection: exactly the columns a config row stores.

        The value knows which columns it becomes, so the repository never has to
        destructure it. That is not only cohesion — reading
        ``registration.authentication_type`` / ``.authentication_token`` at a call
        site is precisely the shape
        ``tests/unit/test_architecture_no_inline_webhook_auth_resolution.py``
        forbids, because it is how three senders each grew their own answer to
        "is this signed?". Projecting here (off ``self``) keeps the columns in one
        place and leaves no credential read at the persistence call site.
        """
        return WebhookConfigColumns(
            url=self.url,
            authentication_type=self.authentication_type,
            authentication_token=self.authentication_token,
            operation_id=self.operation_id,
            token=self.token,
        )

    def to_stash(self) -> dict[str, Any]:
        """Serialize for ``workflow_steps.request_data["push_notification_config"]``.

        Emits the WIRE shape — the same one
        ``PushNotificationConfig.model_dump(mode="json")`` writes — because this
        key already has other producers this lane does not convert
        (``media_buy_update`` stashes the whole request model,
        ``creatives/_workflow`` stashes the config object) and rows written
        before a deploy are wire-shaped too. One shape means the generic reader
        in :mod:`src.core.context_manager` can rehydrate ALL of them through
        :func:`from_stash`, instead of each producer needing its own parser.

        The ``authentication`` block is OMITTED when there is no scheme rather
        than emitted as ``{"schemes": [None], "credentials": None}``: both
        rehydrate identically, but only the omitting form is byte-identical to
        the library dump, and byte-identity is the whole point of "one shape".

        NOTHING is projected away: this dumps the held library model, so
        ``operation_id`` and ``token`` survive into the stash along with ``url``
        and ``authentication``. An earlier version rebuilt the dict from three
        flattened fields and silently dropped the other two — including
        ``operation_id``, whose echo is graded by
        ``dist/compliance/<pin>/universal/webhook-emission.yaml`` req. 1 and which
        that requirement forbids recovering from the URL.

        Dumping the model also makes the byte-identity claim STRUCTURAL rather
        than hand-maintained: this IS ``model_dump(mode="json")`` of the same
        library type the other producers stash, so the shapes cannot drift apart
        by someone editing a literal here.
        """
        return self.config.model_dump(mode="json", exclude_none=True)


def _accept(
    *,
    config: PushNotificationConfig,
    field_prefix: str,
) -> ValidatedWebhookRegistration:
    """Run both preconditions, then build the value. The ONE gate body.

    Absorbed verbatim from ``webhook_validator.reject_invalid_webhook_registration``
    so that removing that public reject-shaped gate changes no wording a buyer
    sees -- ``tests/integration/test_webhook_hmac_credentials_ingest_refusal.py``
    and ``tests/helpers/webhook_credential_refusal.py`` grade exactly this text
    and that a credential refusal is not mislabelled as a URL refusal.

    The URL and credential halves live here because they hold of EVERY registration
    this seller accepts, from every surface. The correlation-id refusal does NOT,
    and so is not here: see :func:`accept_push_notification_config`.
    """
    url = str(config.url) if config.url is not None else None
    reject_unsafe_webhook_registration_url(url, field=f"{field_prefix}.url")

    # No credential check here any more, and its absence is the DELETION of dead
    # code rather than of a safeguard. The config reaching this function has
    # already been validated against the pinned model by
    # to_push_notification_config, which requires `credentials` whenever an
    # `authentication` block is present and enforces minLength 32 — so a
    # credential-less HMAC registration is refused BEFORE it arrives, with the
    # identical wire answer this branch used to produce (measured:
    # field="push_notification_config.authentication.credentials",
    # recovery="correctable", i.e. exactly what
    # tests/helpers/webhook_credential_refusal.py grades). Keeping a second copy
    # of the rule here would be a place for the two to drift apart.

    return ValidatedWebhookRegistration(config=config)


def accept_push_notification_primitives(
    url: str | None,
    scheme: str | None,
    credentials: str | None,
    *,
    token: str | None = None,
    field_prefix: str = "push_notification_config",
) -> ValidatedWebhookRegistration:
    """Accept a registration already destructured into primitives.

    THE ONLY ``src/`` CALLER IS THE ADMIN FORM
    (:mod:`src.admin.blueprints.principals`, the per-principal webhook route),
    which holds ``request.form`` strings rather than a config-shaped document.
    This docstring said "for the A2A protobuf shape" and that is no longer true of
    any caller — a comment that lies about what a function is for is how a defect
    survives review, and this one hid a real gap for a while: the admin surface
    registers a STANDING push config, while ``push_notification_config`` is a
    PER-REQUEST registration whose ``operation_id`` the buyer generates per task
    invocation (``L3/webhooks.mdx`` :136). So this path cannot supply one, and the
    rows it writes are refused by the one envelope builder at delivery time.
    That gap is tracked upstream of this function, not papered over inside it.

    The SHAPE the signature takes is still the flattened one the A2A protobuf used:
    a SINGULAR free-form ``scheme`` string rather than the tool path's ``schemes``
    list, plus ``token`` — passed through so the value keeps it, since the pinned
    schema requires the seller to echo it verbatim in every payload. That shape is
    why the function exists; the A2A route that once fed it does not.

    It must NOT grow an ``operation_id`` parameter. The value is the buyer's, and
    an operator typing one would be fabricating a correlation id — which :143
    forbids as squarely as deriving one from the receiver URL.

    The URL gate runs BEFORE the model is built, deliberately: a blocked-but-
    well-formed URL (a metadata address, say) is a valid ``uri`` to pydantic, and
    refusing it here keeps the buyer's answer ``AdCPBlockedUrlError`` naming
    ``{prefix}.url`` rather than a generic parse failure.

    The scheme must be the canonical enum spelling. A non-canonical one is
    REJECTED here rather than normalized -- probed: ``"bearer"`` raises
    ``AdCPValidationError`` at this gate. The A2A push-config endpoint stores a
    free-form protobuf string, so lowercase rows can reach the database by other
    routes; this path refuses to create more of them, and the seam refuses to
    deliver the ones that exist.
    """
    reject_unsafe_webhook_registration_url(url, field=f"{field_prefix}.url")

    authentication: dict[str, Any] | None = None
    if scheme is not None or credentials is not None:
        # No folding here, and none downstream either: the pinned type has no
        # canonicalising validator, so a folder in this module would be inventing
        # a tolerance the spec does not grant
        # answering the same question — the divergence this package exists to delete.
        authentication = {"schemes": [scheme], "credentials": credentials}

    return _accept(
        config=_coerce_primitives_to_config(
            {"url": url, "token": token, "authentication": authentication},
            field_prefix=field_prefix,
        ),
        field_prefix=field_prefix,
    )


def _coerce_primitives_to_config(
    document: dict[str, Any],
    *,
    field_prefix: str,
) -> PushNotificationConfig:
    """Build the library model from the A2A protobuf primitives.

    Uses the same funnel the transport wrappers use, so this path cannot drift
    into its own validation dialect, and a refusal names the same field path.

    EVERY rule applies, including ``credentials`` ``minLength: 32``. An branch here
    used to exempt exactly that one, on the premise that an A2A
    ``params.configuration`` value is a transport-layer parameter outside
    request-body validation. The pinned schema draws no such distinction --
    ``core/push-notification-config.json`` states the constraint unconditionally,
    and the spec's own prose says the A2A envelope differs while "the object's
    contents are identical". The exemption also reached an Admin HTML form,
    a surface its own justification never covered.
    """
    payload = {key: value for key, value in document.items() if value is not None}
    return require_push_notification_config(payload, field_prefix=field_prefix)


def stored_registration_fields(document: Mapping[str, Any]) -> dict[str, Any]:
    """Project a STORED webhook document onto the fields the pinned config declares.

    For a sender that has to rebuild a registration out of a document it read
    back -- a media buy's ``raw_request["reporting_webhook"]``, a workflow step's
    ``request_data["push_notification_config"]`` -- rather than receiving one
    through the ingest gate.

    The field set is DERIVED from :class:`PushNotificationConfig`, never listed
    here. Both callers used to list it, and both listed the same two of the four
    fields the pinned schema defines::

        registration_config = {"url": url}
        if document.get("authentication"):
            registration_config["authentication"] = document["authentication"]

    so ``operation_id`` and ``token`` were dropped on every read. Both carry echo
    obligations the seller MUST honour (``webhooks.mdx`` :142), and
    ``operation_id`` is REQUIRED on ``core/mcp-webhook-payload.json``, so dropping
    it did not degrade the payload -- it made the envelope unbuildable, and
    ``build_webhook_envelope`` then refused every delivery these two paths
    attempted. This is the same lossy-projection defect
    :class:`ValidatedWebhookRegistration` was introduced to delete one level up,
    reintroduced by the two callers that build the document instead of receiving
    it.

    Only TRUTHY values are carried, which preserves the ``if
    document.get("authentication")`` semantics both call sites had exactly: a
    stored ``authentication: {}`` keeps meaning "no authentication" and keeps
    delivering unsigned. The gate refuses an empty block, so forwarding one would
    turn a delivered webhook into a refusal -- a change that needs an owner
    sign-off (#1802 made one for a legacy scheme) and is not a projection's to
    make. An empty ``operation_id`` or ``token`` is likewise indistinguishable
    from an absent one for an echo obligation.
    """
    return {field: value for field in PushNotificationConfig.model_fields if (value := document.get(field))}


#: The field a reporting registration's correlation value is carried on, named once
#: because two surfaces answer for it: the scheduler suppresses the stream and
#: ``get_media_buy_delivery`` flags it, and a field path spelled twice is a field path
#: that drifts.
REPORTING_STREAM_ID_FIELD = "reporting_webhook.operation_id"


def reporting_stream_id_missing(document: Mapping[str, Any]) -> bool:
    """Is this stored ``reporting_webhook`` registration unable to carry a correlation id?

    THE REPORTING-PATH CONDITION, and it is deliberately NOT the registration-path
    one. ``L3/webhooks.mdx`` :652-673 scopes the fail-closed ``MUST NOT``
    to 3.3 and makes 3.2 opt-in guidance; this seller has adopted the interim
    agreement, so :666 is the rule it follows: "A registration without a
    buyer-supplied ID cannot enable this interim delivery path. Flag the missing
    value for the buyer and suppress scheduled sends; do not fabricate an ID or
    extract one from the URL."

    So the reporting route does not reject the REGISTRATION the way :141 lets the
    push-config route reject one. ``core/reporting-webhook.json`` does not define
    ``operation_id`` at the 3.2 pin — the field rides that schema's
    ``additionalProperties: true`` — so refusing a ``create_media_buy`` over its
    absence would refuse a document the published schema accepts. The two sanctioned
    shapes differ by route and this is the reporting one: accept, then suppress and
    flag.

    Read off the REGISTRATION DOCUMENT, never off a ``push_notification_config``
    row that happens to share the URL: :669 says the reporting registration's value
    "is independent of any task-status ``push_notification_config.operation_id``",
    so borrowing one would be fabricating a correlation id from the wrong stream.
    """
    return not document.get("operation_id")


#: Where a media buy's reporting registration is stashed. Named HERE and nowhere else:
#: ``media_buys.raw_request`` is a persistence document, and a reader that spells its key
#: inline is a second place that has to be edited when the stash shape moves.
_STASHED_REPORTING_WEBHOOK = "reporting_webhook"


def suppressed_reporting_stream(raw_request: Mapping[str, Any] | None) -> bool:
    """Does this media buy's stored request register a reporting stream with no id?

    The same question :func:`reporting_stream_id_missing` answers, reached from a media
    buy's ``raw_request`` rather than from the registration document — for a reader that
    holds the buy and not the registration.

    It exists so that reader does not have to know where a registration is stashed. That
    is cohesion and it is also what
    ``tests/unit/test_architecture_webhook_ingest_validation.py`` enforces: a module
    under ``src/core/tools/`` that NAMES ``reporting_webhook`` must be built against the
    ingest gate, because naming the field is how the five hand-rolled
    pull-the-url-out-and-store-it copies looked. A delivery READ is not an ingest and
    has no registration to gate, so it asks this package instead of spelling the key and
    then needing an allowlist row for a gate it has no use for.
    """
    registration = (raw_request or {}).get(_STASHED_REPORTING_WEBHOOK)
    if not registration:
        # No reporting stream registered at all, so none is suppressed. An early
        # return rather than an ``and``: the stash is a JSON document, so the read
        # is typed ``Any | None``, and narrowing it by returning is what makes the
        # delegation below type-check without a cast.
        return False
    return reporting_stream_id_missing(registration)


def accept_push_notification_config(
    # The two shapes the tool surfaces actually hold, named: create had a
    # dict, update a typed model, sync either. The signature said `Any`,
    # which is why each surface grew its own destructuring branch before
    # this funnel existed -- a caller cannot normalize what the type will
    # not describe.
    config: dict[str, Any] | PushNotificationConfig | None,
    *,
    field_prefix: str = "push_notification_config",
    # The PINNED SCHEMA the buyer's document came from, which decides whether the
    # correlation-id refusal below applies at all. Defaulted to the push config
    # because that is what every protocol surface sends; the account-level gate is
    # the one caller that holds a different pinned shape and says so.
    declared_by: type[BaseModel] = PushNotificationConfig,
) -> ValidatedWebhookRegistration:
    """Accept a config-shaped registration, normalizing model-or-dict ONCE.

    The tool surfaces disagreed about the shape they held -- create had a dict,
    update a typed model, sync either -- and each grew its own little
    destructuring branch. Normalizing here is what lets them all call one
    constructor and stop having a shape opinion.

    Normalization goes through :func:`to_push_notification_config`, the SAME funnel
    the transport wrappers use, so the pinned schema does the structural refusing:
    ``schemes`` ``maxItems: 1`` rejects a multi-scheme registration, ``credentials``
    ``minLength: 32`` rejects a too-short secret, and the ``AuthenticationScheme``
    enum rejects an unknown spelling — each naming its own field path. Those rules
    are therefore ENFORCED BY THE SPEC ARTEFACT rather than restated here in
    hand-written checks that could drift from it.

    What remains genuinely ours is what the schema does NOT say: the registration
    SSRF gate on the URL, and the refusal of an ``HMAC-SHA256`` registration with no
    usable secret (the schema is silent on both — see the module docstring).

    THE CORRELATION-ID REFUSAL lives here, and this is the one funnel it belongs in.
    ``operation_id`` is optional on ``core/push-notification-config.json`` and
    REQUIRED on ``core/mcp-webhook-payload.json``, and ``L3/webhooks.mdx``
    :143 forbids the one bridge between them ("Sellers MUST NOT derive
    ``operation_id`` by parsing ``push_notification_config.url``"). So a registration
    without it names a webhook whose conformant body can never be built, and :141
    grants the seller the way out it takes here: "Sellers MAY reject a webhook
    registration that omits ``operation_id`` with ``INVALID_REQUEST``."

    The alternative is why. Without this, the registration is accepted, every later
    delivery is refused by the one envelope builder
    (:func:`src.core.webhooks.delivery.build_webhook_envelope`), and both senders log
    that refusal and continue -- so the buyer registers a webhook, receives a success,
    and is never told that nothing will ever arrive on it. PERMANENT SILENCE, with the
    seller's own reason in a log line the buyer cannot read.

    WHY NOT IN ``_accept``, the shared gate body. Two other registration shapes reach
    that body and the rule is wrong for both, which was MEASURED rather than reasoned:
    written there, it failed ``test_notification_config_gate_accepts_authentication``.

    * An ACCOUNT-LEVEL subscription (``core/notification-config.json``) is routed
      through this very function by ``accounts._check_notification_configs``, because
      its ``url`` and ``authentication`` carry the same contract in the pin's own
      words. But that schema declares NO ``operation_id`` -- so there is nothing for
      its buyer to supply, and refusing one would reject every conformant
      subscription. ``declared_by.model_fields`` is the pin's own answer to "does this
      shape carry a correlation id", so that caller passes its own type and the rule
      skips itself. A hand-written "push only" condition would be a second opinion
      about a pinned field set, which is the 2-of-4 field list this module exists to
      delete, one level up.
    * The ADMIN registration form reaches ``_accept`` through
      :func:`accept_push_notification_primitives`, which has no ``operation_id``
      parameter -- and must not grow one. The value is the BUYER's, generated per task
      invocation (:141: "Buyers SHOULD supply ``operation_id`` ... and generate a
      unique value per task invocation"), and an operator typing one would be
      fabricating a correlation id, which :143 forbids as squarely as reading one off
      the URL. So an admin-registered push config cannot carry one, and refusing it
      for the omission would break the form while fixing nothing. What that surface
      produces instead is a stored row the sender refuses, and the refusal now has an
      enumerable record (``context_manager._log_webhook_refusal``) -- which is the
      honest answer when there is no buyer on the call. That the admin surface can
      register a task-notification webhook no conformant envelope can be built for is
      a REAL GAP, reported rather than papered over: closing it means that surface
      registering something other than a per-task push config.
    """
    coerced = to_push_notification_config(config, field_prefix=field_prefix)
    if coerced is None:
        raise AdCPValidationError(
            field=field_prefix,
            details=ValidationDetails(received_type=type(config).__name__),
        )
    # THE URL GATE RUNS FIRST, and the order is load-bearing rather than incidental.
    # ``_accept`` opens with the registration SSRF gate, so letting the correlation
    # check jump ahead of it means an UNSAFE DESTINATION gets refused for a missing
    # ``operation_id`` instead of for being unsafe — the buyer is told to add a
    # correlation id to a webhook pointed at a blocked address, and the security
    # verdict never reaches them. Measured, not foreseen: writing the check above this
    # line reddened
    # ``test_protocol_webhook_ssrf.py::test_sync_creatives_rejects_unsafe_push_config_url``,
    # which builds a raw dict with an unsafe URL and no ``operation_id`` and grades
    # ``.url``. The egress scenarios did not catch it because
    # ``PushNotificationConfigRequestFactory`` defaults ``operation_id``, so their
    # documents are only wrong in one way.
    #
    # An unsafe destination is refused unconditionally; an absent correlation id is a
    # lesser, correctable omission. So the value is BUILT first — which is what runs
    # the URL half — and the correlation rule applies to a registration that already
    # cleared it.
    registration = _accept(config=coerced, field_prefix=field_prefix)
    if "operation_id" in declared_by.model_fields and not coerced.operation_id:
        raise AdCPInvalidRequestError(field=f"{field_prefix}.operation_id")
    return registration
