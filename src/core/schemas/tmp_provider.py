"""Transport-agnostic TMP provider registration record.

Owns the AdCP provider-registration invariants so that *every* write surface
enforces them, not just the Flask admin form.  Before this module existed, the
uid-type enum, the status enum, the "at least one match mode" rule and the
``identity_match ⇒ countries + uid_types`` rule all lived in
``src/admin/blueprints/tmp_providers.py`` while the repository write path took
``**kwargs: object`` and checked only that attribute names existed — so the
first programmatic write surface (an MCP/A2A/REST tool, a bulk import) would
have forked or silently dropped every one of them (#1197 review).

Layering:
  - **This module** owns *validity*: which values are legal for a registration.
  - **The blueprint** owns *form shape*: CSV splitting, checkbox ``"on"``,
    int parsing, and turning a rejection into a flash message.
  - **The repository** owns *persistence*, typed against
    :class:`TMPProviderFields` instead of a runtime ``hasattr`` guard.

Spec grounding (pin: ``adcp==6.6.0``, AdCP spec 3.1.1). Paths are given in the
form that RESOLVES in this tree — the installed SDK's pinned schema tree, which
``tests/helpers/pinned_schema`` reads and the tests below validate against. The
``dist/schemas/…`` prefix these citations used to carry resolves to nothing here
(``dist/`` is gitignored and absent), so it could not be checked (#1197 review):
  - ``adcp/_schemas/3.1/trusted-match/provider-registration.json`` (declared once
    as :data:`src.routes.tmp_providers.PROVIDER_REGISTRATION_SCHEMA`) — the
    ``anyOf`` requiring ``context_match`` or ``identity_match``, the
    ``identity_match ⇒ countries/uid_types non-empty`` rule (mirrored by the
    SDK's own ``_require_identity_match_dimensions`` model validator), and the
    per-field value constraints carried by the fields below.
  - ``adcp/_schemas/3.1/enums/uid-type.json`` →
    ``adcp.types.generated_poc.enums.uid_type.UidType`` (the symbol imported
    below; ``adcp.types.UidType`` does not exist in the pinned SDK).
  - The ``status`` enum → the SDK's ``provider_registration.Status``.

Both enums are derived from the pinned SDK rather than hand-maintained
frozensets, so a spec bump can only widen them by upgrading the pin.

Why this is not a subclass of the SDK's ``TmpProviderRegistration``
(CLAUDE.md Pattern #1 asks for inheritance where a library counterpart exists):
that type is a ``RootModel`` union over two closed (``extra="forbid"``)
variants describing the **wire** registration a router consumes.  It requires
``provider_id`` (assigned by us at INSERT, absent from the form), forbids the
three fields this record must carry (``name``, ``auth_type``,
``auth_credentials``) and types ``properties`` as UUIDs.  Inheriting a closed
RootModel union also yields no field inheritance.  Its https-only ``endpoint``
rule is NOT diverged from any more: this module used to relax it for local dev
hosts, and #1802 made the repo's TLS gate unconditional (the insecure hatch was
deleted so no call site can relax a scheme), while the generated test CA covers
``*.localhost`` — so local development speaks https too and the relaxation was
buying nothing.  The shared rules are instead grounded on the same
SDK enums and pinned against the library model by
``tests/unit/test_tmp_provider_registration.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, TypedDict, cast
from uuid import UUID

from adcp.types.generated_poc.enums.uid_type import UidType
from adcp.types.generated_poc.trusted_match.provider_registration import Status as ProviderStatus
from adcp.types.generated_poc.trusted_match.provider_registration import (
    TmpProviderRegistration1 as LibraryContextMatchRegistration,
)
from adcp.types.generated_poc.trusted_match.provider_registration import (
    TmpProviderRegistration2 as LibraryIdentityMatchRegistration,
)
from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    RootModel,
    StringConstraints,
    ValidationError,
    model_validator,
)

from src.core.exceptions import AdCPBlockedUrlError
from src.core.logging_config import log_safe
from src.core.schemas._base import SalesAgentBaseModel
from src.core.security.egress.policy import EgressPolicy

if TYPE_CHECKING:
    # Type-only: gives from_row() a checked contract with the ORM row without
    # creating a runtime schemas -> models edge (models.py imports nothing from
    # this package, and a future models -> schemas import would otherwise cycle).
    from src.core.database.models import TMPProvider

logger = logging.getLogger(__name__)

# ``src/core/schemas/__init__.py`` star-imports this module. Without __all__ the
# star would also re-export this module's own imports (``logger``, ``logging``,
# ``check_url_ssrf``, ``ValidationError``, …) into ``src.core.schemas``, where
# generic names like ``logger`` can shadow a sibling module's.
__all__ = [
    "VALID_AUTH_SCHEMES",
    "VALID_STATUSES",
    "VALID_UID_TYPES",
    "AuthScheme",
    "CountryCode",
    "PropertyRid",
    "TMPDiscoveryResponse",
    "TMPProviderDiscoveryEntry",
    "TMPProviderFields",
    "TMPProviderRegistration",
    "TMPProviderValidationError",
]

# Derived from the pinned SDK enums — never hand-maintained.  A spec bump that
# adds a uid type or a lifecycle status widens these automatically.
VALID_UID_TYPES: frozenset[str] = frozenset(t.value for t in UidType)
VALID_STATUSES: frozenset[str] = frozenset(s.value for s in ProviderStatus)

#: The provider auth schemes a registration may declare.
#:
#: Beside the other two registration vocabularies, because this module owns
#: registration VALIDITY. It previously lived in ``src/services/_provider_http.py``
#: — the module that consumes it — which made this package import upward into the
#: outbound-HTTP service layer (the only ``from src.services`` import in
#: ``src/core/schemas``), and the repository layer transitively pulled that module
#: in just to build a ``TMPProviderFields``. No cycle existed only because
#: ``_provider_http`` imported nothing from ``src`` (#1197 review).
#:
#: Not SDK-derived, unlike its two neighbours: the AdCP schema does not describe
#: how a seller authenticates to a provider, so the vocabulary is what
#: ``provider_auth_headers`` implements. Adding a scheme means adding a branch
#: there and an entry here, and that function imports this constant so the two
#: cannot disagree.
VALID_AUTH_SCHEMES: frozenset[str] = frozenset({"bearer"})


def _require_uuid(value: str) -> str:
    """Reject a property RID that is not a UUID, keeping the value a ``str``.

    ``provider-registration.json`` types ``properties`` items as
    ``{"type": "string", "format": "uuid"}``.  The constraint is enforced by
    parsing, but the value stays a string rather than becoming a ``UUID``
    object: the RID is persisted into a ``JSONType`` column and re-emitted on
    the discovery wire as a JSON string, so a ``UUID`` here would need
    converting back at both the repository write and the serializer — two more
    places to forget — for no additional strictness.
    """
    UUID(value)
    return value


#: ISO 3166-1 alpha-2, the ``countries`` item constraint from the pinned schema
#: (:data:`src.routes.tmp_providers.PROVIDER_REGISTRATION_SCHEMA`,
#: ``items.pattern: ^[A-Z]{2}$``).  Declared here rather than normalized by a
#: form helper, so the *second* write surface inherits it too (#1197 review).
CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]

#: A property RID — ``properties`` items are ``format: uuid`` in the pinned schema.
PropertyRid = Annotated[str, AfterValidator(_require_uuid)]


def _known_auth_scheme(value: str) -> str:
    """Reject an auth scheme no outbound call can actually make."""
    if value not in VALID_AUTH_SCHEMES:
        raise ValueError(f"Invalid auth_type '{value}'. Valid values: {', '.join(sorted(VALID_AUTH_SCHEMES))}")
    return value


#: The provider auth scheme, typed from what ``provider_auth_headers`` implements.
AuthScheme = Annotated[str, AfterValidator(_known_auth_scheme)]


class TMPProviderValidationError(ValueError):
    """A TMP provider registration was rejected by a domain invariant.

    Carries the operator-facing message as its only argument, so a write surface
    can surface it directly (``flash(str(exc))``) without reaching into pydantic
    error structures.  Subclasses ``ValueError`` so callers that already handle
    bad input generically keep working.
    """


class TMPProviderFields(TypedDict):
    """The twelve persisted TMP provider fields, as a static kwargs contract.

    Used via ``**Unpack[TMPProviderFields]`` on the repository write methods so a
    typo (``timout_ms``, ``contry``) is a type error at the call site rather than a
    ``ValueError`` raised when the write finally runs.

    **Total**, with ``auth_credentials`` the one ``NotRequired`` key. It was
    ``total=False``, which meant ``create_from_fields()`` with no fields at all
    type-checked clean — so the required half of the contract its docstring claims
    did not exist, and a missing ``name``/``endpoint`` surfaced as an
    ``IntegrityError`` at flush. One key's optionality had been paid for by
    dropping the contract on all twelve (#1197 review). ``auth_credentials`` is
    the only key ``to_update_fields`` drops, to preserve a stored credential when
    the operator leaves the field blank.

    Mirrors :class:`TMPProviderRegistration`'s field set; the two are pinned
    equal by ``test_tmp_provider_registration.py`` so they cannot drift.
    """

    name: str
    endpoint: str
    context_match: bool
    identity_match: bool
    countries: list[str] | None
    # ``list[str]``, not ``list[UidType]``: this is the PERSISTENCE contract, and
    # the column is JSONType. ``UidType`` is a ``str`` subclass so the values pass
    # through unchanged; typing the persisted shape as plain ``str`` keeps the
    # repository free of a protocol enum import.
    uid_types: list[str] | None
    properties: list[str] | None
    timeout_ms: int
    priority: int
    status: str
    auth_type: str | None
    auth_credentials: str | None


class _ContextMatchEntry(LibraryContextMatchRegistration):
    """The ``context_match: true`` branch of the pinned schema's ``anyOf``."""


class _IdentityMatchEntry(LibraryIdentityMatchRegistration):
    """The ``identity_match: true`` branch of the pinned schema's ``anyOf``."""


class TMPProviderDiscoveryEntry(RootModel[_ContextMatchEntry | _IdentityMatchEntry]):
    """One provider entry on the discovery wire — the SDK's own wire type.

        Extends the pinned codegen of ``provider-registration.json``
        (:data:`src.routes.tmp_providers.PROVIDER_REGISTRATION_SCHEMA`) instead of
        restating its closed key set as a ``TypedDict``, per CLAUDE.md Pattern #1.
        The sibling sync payload already did this — ``_build_package_payload`` builds
        through ``AvailablePackage`` so "a spec bump that renames or adds a required
        field becomes a construction error" — and this entry, the payload that
        actually crosses the service boundary, did the opposite (#1197 review).

        What the model carries that a ``TypedDict`` could not, and which therefore
        stopped being re-implemented by hand:

          - ``uid_types: list[UidType]`` — the enum, **unconditionally**. The local
            copy checked the vocabulary only under ``if identity_match:``, while the
            schema refs ``enums/uid-type.json`` on ``uid_types.items`` with no
            condition, so a context-only provider could persist a row the wire
            rejects.
          - ``countries: list[Country]`` (``^[A-Z]{2}$``), ``properties: list[UUID]``,
            ``timeout_ms`` 5..5000, ``priority`` >= 0, ``provider_id``
            ``^[A-Za-z0-9_]+$``.
          - the schema's ``if/then`` (``identity_match ⇒ countries + uid_types``) and
            its ``anyOf`` over the two match modes — expressed as the union of the two
            branch variants, which is exactly what the schema says and what a
            ``TypedDict`` cannot say at all.

        Emission is therefore construction: :meth:`from_row` raises on a row that
        cannot be represented conformantly, at the boundary, instead of serializing
        it and leaving a strict router to reject it.

        One wire-visible consequence of the SDK typing ``endpoint`` as ``AnyUrl``:
        pydantic canonicalizes it, so a stored ``http://host:3003`` publishes as
        ``http://host:3003/``. This is conformant and semantically identical — the
        schema states that two registrations "differing only in case, default port,
        or path-slash collapsing are the same provider" — and the outbound side is
        unaffected because ``provider_url()`` strips the trailing slash before
        appending ``/packages/sync``.

    The extra-field policy (Pattern #7) is carried by the two branch variants,
        which inherit ``extra="forbid"`` from the pinned codegen — pydantic rejects
        ``extra`` on a ``RootModel`` itself. ``frozen=True`` is the wrapper's own
        declared policy and a real property of the value: an entry is built from a row
        at the boundary and published, never mutated afterwards.
    """

    model_config = ConfigDict(frozen=True)

    @classmethod
    def from_row(cls, row: TMPProvider) -> TMPProviderDiscoveryEntry:
        """Build the wire entry from a ``TMPProvider`` ORM row.

        The row→entry mapping lives here rather than on the ORM model so that
        persistence does not import the protocol-schema package (that import made
        any future ``schemas → models`` import a circular-import failure instead
        of a design question).

        ``None`` conditionals are dropped rather than passed: the schema types
        ``countries``/``uid_types``/``properties`` as arrays with ``minItems: 1``,
        so an absent value must be omitted — ``null`` is a type violation, not an
        unknown key. Building a mapping and validating it (rather than picking a
        branch here) lets the union discriminate on the match-mode flags exactly
        as the schema's ``anyOf`` does.

        ``name`` is deliberately not carried: it is not in the closed schema. It
        lives on the admin view shapes, which the admin layer owns.

        *row* is typed ``TMPProvider`` through a ``TYPE_CHECKING`` import — no
        runtime edge, so ``models.py`` still imports nothing from this package and
        there is no cycle (``src/core/schemas/_base.py`` is the in-repo
        precedent). It was ``object``, which cost seven attr-defined suppressions
        and a ``getattr`` loop: the one function that decides whether a stored row
        can be published had no checked contract with the row it reads
        (#1197 review). The pragma is named rather than quoted here because the
        ratchet hook counts it by regex over ``src/``.
        """
        payload: dict[str, object] = {
            "provider_id": row.provider_id,
            "endpoint": row.endpoint,
            "context_match": row.context_match,
            "identity_match": row.identity_match,
            "timeout_ms": row.timeout_ms,
            "priority": row.priority,
            "status": row.status,
        }
        # Checked attribute access, not getattr(): omit-don't-null for the three
        # conditional arrays (the schema types each with minItems: 1).
        for key, value in (
            ("countries", row.countries),
            ("uid_types", row.uid_types),
            ("properties", row.properties),
        ):
            if value:
                payload[key] = value
        return cls.model_validate(payload)


class TMPDiscoveryResponse(SalesAgentBaseModel):
    """Body of the discovery contract (``GET`` :data:`src.routes.tmp_providers.DISCOVERY_ROUTE`).

    Used as the route's ``response_model`` so FastAPI publishes an OpenAPI
    schema for the discovery contract and validates the outgoing keys, instead
    of the route hand-building an unvalidated ``JSONResponse`` (#1197 review).
    """

    tenant_id: str
    providers: list[TMPProviderDiscoveryEntry]


class TMPProviderRegistration(SalesAgentBaseModel):
    """A validated TMP provider registration, independent of how it was submitted.

    Construct it with :meth:`parse` (which narrows the failure to a single
    operator-facing :class:`TMPProviderValidationError`) or directly with
    ``TMPProviderRegistration(...)`` when the full pydantic ``ValidationError``
    — every failing field, not just the first — is the more useful failure mode.
    """

    name: str
    endpoint: str
    context_match: bool = False
    identity_match: bool = False
    # Value constraints, not just presence: every one of these is a constraint
    # the pinned provider-registration.json puts on the SAME value the discovery
    # wire re-emits, so a row that violates one is a row the endpoint cannot
    # serialize conformantly.  Declaring them here — on the record every write
    # surface goes through — is what makes "no write surface can persist a row
    # the wire will reject" true, rather than relying on each surface to check
    # (#1197 review).  Graded against the schema itself by
    # ``tests/unit/test_tmp_provider_registration.py``.
    countries: list[CountryCode] | None = None
    # The vocabulary is enforced as a FIELD TYPE, not inside the
    # ``if self.identity_match:`` branch below. The pinned schema refs
    # ``enums/uid-type.json`` on ``uid_types.items`` unconditionally, so a
    # context-only provider with a bogus uid type was a row the discovery wire
    # rejected while this record accepted it (#1197 review). ``UidType`` is the
    # SDK enum ``VALID_UID_TYPES`` is derived from, so the two cannot disagree.
    uid_types: list[UidType] | None = None
    properties: list[PropertyRid] | None = None
    timeout_ms: int = Field(default=50, ge=5, le=5000)
    priority: int = Field(default=0, ge=0)
    status: str = "active"
    # Constrained from the vocabulary of the code that ACTS on it. An
    # unconstrained ``str`` let the admin form offer "API Key" while
    # ``provider_auth_headers`` emitted Bearer regardless, so the selected scheme
    # changed nothing (#1197 review). ``VALID_AUTH_SCHEMES`` is the set that
    # function implements, so the field and the behaviour cannot disagree.
    auth_type: AuthScheme | None = None
    auth_credentials: str | None = None

    @model_validator(mode="after")
    def _check_registration_invariants(self) -> TMPProviderRegistration:
        """Enforce every provider-registration invariant in one pass.

        Ordered so the cheapest presence checks run before the SSRF check, which
        resolves DNS.  Messages are the operator-facing strings the admin UI
        flashes verbatim.
        """
        if not self.name.strip():
            raise ValueError("Provider name is required")
        if not self.endpoint.strip():
            raise ValueError("Endpoint URL is required")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{self.status}'. Valid values: {', '.join(sorted(VALID_STATUSES))}")

        # The endpoint is STORED here and fetched later by the sync, so the
        # verdict is the seam's DNS-FREE registration one
        # (``EgressPolicy.check_registration`` — the port of the deleted
        # ``check_url_ssrf(resolve_dns=False)`` path, #1802). Not ``validate_url``:
        # that resolves, and a registration must not depend on whether the
        # provider's DNS happens to answer while an operator fills in a form.
        #
        # The URL is passed BYTE-FOR-BYTE. There is no local-dev scheme
        # relaxation any more, and deliberately so:
        #
        #   * #1802 made the TLS gate unconditional across the repo and deleted
        #     the insecure hatch, so no call site can relax the scheme — a
        #     per-feature exception here would be the one place that does.
        #   * The pinned spec already made https a MUST for this surface, which
        #     is what the SDK codegen's own rule said before this module
        #     overrode it.
        #   * Local development does not need http: the generated test CA covers
        #     ``*.localhost`` and ``agent.localhost`` (``SAN_DNS_NAMES`` in
        #     ``scripts/dev/gen_test_tls.py``), so ``https://si-agent.localhost``
        #     is registrable and reachable. The relaxation was buying nothing that
        #     https does not already give.
        #
        # An earlier port of this block asked the policy about an https-rewritten
        # copy of the URL to keep the old relaxation. That is a destination
        # rewrite, which ``test_architecture_no_destination_rewrite`` forbids for
        # exactly the right reason: what the policy judged would not have been
        # what a caller supplied.
        try:
            EgressPolicy.check_registration(self.endpoint)
        except AdCPBlockedUrlError as blocked:
            # Tagged `[TMP …]` so an operator grepping `[TMP` for this feature's
            # logs sees egress refusals from every write surface, not just the
            # admin form the check used to live behind. The policy's message is
            # already opaque about the address (AdCP 3.1.1 security point 6); the
            # real cause is in the policy's own WARNING line.
            logger.warning(
                "[TMP registration][SECURITY] Provider rejected unsafe URL %s: %s",
                log_safe(self.endpoint),
                log_safe(str(blocked)),
            )
            raise ValueError(f"Endpoint URL is not allowed: {blocked}") from blocked

        if not self.context_match and not self.identity_match:
            raise ValueError("Provider must support at least one of context_match or identity_match")

        if self.identity_match:
            if not self.countries:
                raise ValueError("Countries are required when identity_match is enabled (ISO 3166-1 alpha-2 codes)")
            if not self.uid_types:
                raise ValueError(
                    "UID types are required when identity_match is enabled (e.g. uid2, publisher_first_party)"
                )
        return self

    @classmethod
    def parse(cls, fields: TMPProviderFields) -> TMPProviderRegistration:
        """Validate *fields*, raising :class:`TMPProviderValidationError` on rejection.

        Equivalent to constructing the model directly, except the pydantic
        ``ValidationError`` is narrowed to one operator-facing message — the
        single string the admin UI flashes.  Raising (rather than returning
        ``(model | None, message | None)``) keeps the success type non-optional,
        so callers never carry an ``Optional`` past the guard.
        """
        try:
            return cls(**fields)
        except ValidationError as exc:
            raise TMPProviderValidationError(_first_error_message(exc)) from exc

    def to_fields(self) -> TMPProviderFields:
        """Return the persisted field set for ``create_from_fields(**…)``.

        Derived from the model rather than re-listing the twelve names: the field
        set was transcribed at four sites (the model, ``TMPProviderFields``, this
        method, and the test suite's ``_VALID``), and only the first two were
        pinned equal — so adding a field to both and forgetting this method still
        passed while the field silently never reached the repository
        (#1197 review).

        ``mode="json"`` is what turns the ``UidType`` members into the plain
        strings the ``JSONType`` column stores.  ``exclude_none=False`` is load
        bearing and NOT the default on this base: the persisted contract is all
        twelve keys, and a dropped ``countries: None`` would mean "leave the column
        as it is" on the update path — so clearing a provider's country list
        through the edit form would silently keep the old value.
        """
        return cast(TMPProviderFields, self.model_dump(mode="json", exclude_none=False))

    def to_update_fields(self, *, include_credentials: bool) -> TMPProviderFields:
        """Return the field set for ``update_fields(provider_id, **…)``.

        ``auth_credentials`` is omitted unless *include_credentials* is true, so
        an edit that leaves the credential field blank preserves the stored
        (encrypted) value rather than overwriting it with ``None``.  It is the one
        ``NotRequired`` key on :class:`TMPProviderFields` precisely so this is
        expressible without loosening the other eleven.

        Built by exclusion at the dump rather than by deleting a key afterwards: a
        ``NotRequired`` key still cannot be ``del``-eted from a TypedDict, and
        ``exclude`` says the intent at the point the payload is produced.
        """
        if include_credentials:
            return self.to_fields()
        return cast(
            TMPProviderFields,
            self.model_dump(mode="json", exclude_none=False, exclude={"auth_credentials"}),
        )


def _first_error_message(exc: ValidationError) -> str:
    """Extract the operator-facing message from a ``ValidationError``.

    Pydantic prefixes messages raised by a validator with ``"Value error, "``;
    strip it so the admin UI flashes the message this module wrote.

    The failing field is prefixed from ``loc``. That was unnecessary while every
    rejection was a hand-written sentence naming its own subject ("Countries are
    required when identity_match is enabled"), but the value constraints are now
    field types, and pydantic's message for one is
    ``String should match pattern '^[A-Z]{2}$'`` — which of the three
    comma-separated inputs the operator got wrong is exactly the information
    ``loc`` carries and the bare message drops (#1197 review).

    Model-level errors have an empty ``loc`` (or a synthetic one), so they keep
    their unprefixed sentence.
    """
    first = exc.errors()[0]
    message = str(first.get("msg", "Invalid TMP provider registration")).removeprefix("Value error, ")

    loc = first.get("loc") or ()
    field = next((part for part in loc if isinstance(part, str)), None)
    if not field or field not in TMPProviderRegistration.model_fields:
        # A model-level invariant: its message is a hand-written sentence that
        # already names its own subject ("Provider must support at least one of
        # context_match or identity_match").
        return message

    # For a list field, ``loc`` also carries the failing INDEX — which entry of a
    # comma-separated input was rejected.
    index = next((part for part in loc if isinstance(part, int)), None)
    label = field if index is None else f"{field}[{index}]"

    # Pydantic's built-in messages state what was expected but not what was given
    # ("Input should be 'rampid', 'uid2', …"), so a CSV field with several entries
    # left the operator without the offending value. The hand-written messages this
    # replaced did echo it; ``input`` puts it back.
    rejected = first.get("input")
    if rejected is not None and str(rejected) not in message:
        return f"{label}: {message} (got {rejected!r})"
    return f"{label}: {message}"
