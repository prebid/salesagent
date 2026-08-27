"""The per-request ``request_signing`` posture — ONE object for advertise and enforce.

#1291 B1 (salesagent-z6nr.12), plan step 1.

``request_signing`` is a *behavioral* declaration: ``covers_content_digest`` and the
six operation buckets are tenant-declared but they change what the VERIFIER does. So
the block the agent advertises on ``get_adcp_capabilities`` and the capability the
verifier enforces must come from one object; two sources is a correctness bug that
advertises a posture we do not enforce (or vice versa).

:class:`RequestSigningPosture` is that object. It extends the AdCP library type — all
eight schema properties, no re-declaration (CLAUDE.md Pattern #1) — and adds the two
derivations the verifier needs:

* :meth:`RequestSigningPosture.bucket_for` — the schema's precedence rule
  ``required_for > warn_for > supported_for``, which is OURS to implement because
  ``adcp.signing.verifier.VerifierCapability`` carries only 4 of the 8 properties and
  only 2 of the 6 buckets: ``warn_for`` and all three ``protocol_methods_*`` are
  SILENTLY DROPPED if handed to the SDK.
* :meth:`RequestSigningPosture.to_verifier_capability` — the lossy projection onto the
  4 fields the SDK does carry, kept in one place so the loss is explicit.

:func:`posture_for_tenant` is the single READER of the tenant declaration. #1291 D1
populates it from ``CapabilityDeclarations`` and serializes the SAME object to the
``get_adcp_capabilities`` wire, so advertise and enforce are two views of one read.

``webhook_signing`` sits beside it and is DERIVED, never declared: ``supported`` from
key material plus trust-root publishability, ``algorithms`` from the ACTIVE key row,
``profile`` from the SDK tag the signer emits. :func:`webhook_signing_posture` is its
one producer and C1's outbound sender consumes that same object, so the wire and the
socket cannot disagree about whether we sign or with what.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, TypedDict, cast

from adcp.signing.verifier import CoversDigestPolicy, VerifierCapability
from adcp.signing.webhook_signer import WEBHOOK_TAG
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import Identity as LibraryIdentity
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import KeyOrigins
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import RequestSigning as LibraryRequestSigning
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import WebhookSigning as LibraryWebhookSigning
from pydantic import AnyUrl, ConfigDict, RootModel, model_validator

from src.core.enum_helpers import enum_value

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps this module free of DB imports
    from src.core.database.models import SigningKey
    from src.core.database.repositories.signing_key import SigningKeyRepository
    from src.core.schemas.capability_declarations import CapabilityDeclarations

logger = logging.getLogger(__name__)

#: The four outcomes the middleware branches on. ``none`` means "signatures are
#: ignored (requests are bearer-authenticated only)" — the schema's own words for
#: ``supported: false``.
PostureBucket = Literal["required", "warn", "supported", "none"]


def _name(item: Any) -> str:
    """The wire string a single declared bucket entry compares against.

    The three ``protocol_methods_*`` buckets are typed as generated
    ``RootModel[str]`` wrappers (``ProtocolMethodsRequiredForItem`` and siblings,
    each carrying the ``^[a-z][a-z0-9_]*/[a-z][a-z0-9_]*$`` pattern), while
    ``required_for`` / ``warn_for`` / ``supported_for`` are plain strings. The
    wrappers are NOT enums, so ``enum_value`` falls through to ``str(v)`` and
    yields ``"root='tasks/cancel'"`` — a frozenset that matches no wire method,
    i.e. silently zero enforcement for any tenant declaring a protocol-method
    bucket. B1 could not see it because ``UnresolvedOperationResolver`` never
    supplied a protocol method; B2 is what makes the branch reachable.

    The unwrap is typed on :class:`pydantic.RootModel` rather than a defensive
    attribute probe, which is both wrong (it would swallow a genuine shape change)
    and forbidden by ``test_architecture_no_defensive_rootmodel``.
    """
    if isinstance(item, RootModel):
        return str(item.root)
    return enum_value(item)


#: One declared bucket as it arrives off the wire. Named rather than left as ``Any``
#: (#1879) so the call sites state what they accept — and the members are NOT uniform:
#: ``required_for`` / ``warn_for`` / ``supported_for`` are plain strings, while the three
#: ``protocol_methods_*`` buckets are generated ``RootModel[str]`` wrappers. That
#: non-uniformity is exactly what :func:`_name` exists to flatten, and stating it here is
#: what stopped a fourth member shape being added silently.
type DeclaredBucket = Iterable[str | Enum | RootModel[str]] | None


def bucket_names(items: DeclaredBucket) -> frozenset[str]:
    """Wire-format names from a declared bucket, ``None`` -> empty."""
    if not items:
        return frozenset()
    return frozenset(_name(item) for item in items)


def _bucket_for(name: str, required: Any, warn: Any, supported: Any) -> PostureBucket:
    """Apply the schema's ``required_for > warn_for > supported_for`` precedence.

    ``supported_for`` defaulting to ``None`` (rather than ``[]``) is load-bearing and
    is NOT the same as an empty list: an agent that declares ``supported: true`` and
    nothing else verifies signatures wherever they appear — the signed-requests
    storyboard gates all 28 negative vectors on ``request_signing.supported: true``
    alone, so a null ``supported_for`` cannot mean "verify nothing". An explicit list
    narrows that to the operations named.
    """
    if name in bucket_names(required):
        return "required"
    if name in bucket_names(warn):
        return "warn"
    if supported is None:
        return "supported"
    return "supported" if name in bucket_names(supported) else "none"


class RequestSigningPosture(LibraryRequestSigning):
    """A tenant's declared ``request_signing`` block, plus the verifier's two views of it.

    Frozen: one posture is resolved per request and then read by the pre-check, the
    verify call and the outcome branch. A posture that could change between those
    reads would let a request be admitted under one rule and graded under another.
    """

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="after")
    def _namespaces_must_not_be_mixed(self) -> RequestSigningPosture:
        """Reject a declaration that puts a JSON-RPC method in an AdCP bucket.

        security.mdx :1045-1059 — the schema's own words on ``required_for``: "Not MCP
        tool names, A2A skill names, or any transport-specific rename … JSON-RPC
        protocol method names like ``tasks/cancel`` belong in
        ``protocol_methods_required_for``, not here", and :1053 requires a
        CONFIGURATION-time rejection rather than coercion.

        Only this direction needs enforcing here: the ``protocol_methods_*`` items are
        generated ``RootModel``s carrying the ``^[a-z][a-z0-9_]*/…`` pattern, so
        pydantic already refuses a slash-free AdCP name in them. Nothing enforced the
        reverse, and nothing could observe it either, until B2 started naming requests
        in both namespaces.

        Only the namespace split lives here, on the type: it is true of any posture
        however it was built. The relation rules that need the whole declaration
        document — the ``x-adcp-validation`` subset/disjoint checks, ``required_when``
        and ``key_origins`` anchoring — are enforced by
        ``CapabilityDeclarations.validate_backing`` (#1291 D1), in the order the
        graded rows depend on.
        """
        mixed = sorted(
            name
            for bucket in (self.required_for, self.warn_for, self.supported_for)
            for name in bucket_names(bucket)
            if "/" in name
        )
        if mixed:
            raise ValueError(
                f"request_signing operation buckets name JSON-RPC protocol methods: {mixed}. "
                "required_for / warn_for / supported_for carry AdCP operation names only; "
                "a name containing '/' belongs in the matching protocol_methods_* bucket "
                "(get-adcp-capabilities-response.json, security.mdx :1045-1059)."
            )
        return self

    def bucket_for(self, operation: str, protocol_method: str | None = None) -> PostureBucket:
        """Which enforcement bucket *operation* (or *protocol_method*) falls in.

        ``protocol_method`` is a JSON-RPC wire method (``tasks/cancel``) and is graded
        against the ``protocol_methods_*`` trio, which the schema keeps as a SEPARATE
        namespace from the AdCP tool names precisely so the two cannot collide as bare
        strings. B2 (``salesagent-z6nr.13``) is what starts supplying it.
        """
        if not self.supported:
            return "none"
        if protocol_method is not None:
            return _bucket_for(
                protocol_method,
                self.protocol_methods_required_for,
                self.protocol_methods_warn_for,
                self.protocol_methods_supported_for,
            )
        return _bucket_for(operation, self.required_for, self.warn_for, self.supported_for)

    def to_verifier_capability(self) -> VerifierCapability:
        """Project onto the 4 fields ``VerifierCapability`` carries.

        Everything else — ``warn_for`` and the three ``protocol_methods_*`` buckets —
        stays HERE, in :meth:`bucket_for`. The SDK reads only ``required_for`` (for its
        absent-header pre-check) and ``covers_content_digest``; handing it the other
        buckets would look like configuration and do nothing.
        """
        return VerifierCapability(
            supported=self.supported,
            covers_content_digest=cast(CoversDigestPolicy, enum_value(self.covers_content_digest) or "either"),
            required_for=bucket_names(self.required_for),
            supported_for=bucket_names(self.supported_for),
        )


#: The posture of an agent that does not verify at all: ``supported: false`` puts
#: every operation in the ``none`` bucket. Reached two ways — the verifier kill
#: switch is off (:func:`agent_level_posture`), or a tenant's declaration cannot be
#: read on the middleware path (:func:`posture_for_tenant`).
UNSUPPORTED_POSTURE = RequestSigningPosture(supported=False)


def request_signing_is_declarable() -> bool:
    """Whether any tenant CAN declare a ``request_signing`` posture.

    True since #1291 D1 backed the block. Callers use it to skip work whose result
    could not change a decision, and because it reads the SAME table ``from_tenant``
    rejects against, adding the block back to ``_UNBACKED_BLOCKS`` would switch them
    off again with no second flag to keep in sync.
    """
    from src.core.schemas.capability_declarations import is_block_declarable

    return is_block_declarable("request_signing")


def agent_level_posture() -> RequestSigningPosture:
    """The posture a tenant that declared nothing honestly holds.

    ``supported`` is ``SigningConfig.verifier_enabled`` and every bucket is EMPTY.
    Both halves are the pin's, not a convenience:

    * v3.1.1 ``get-adcp-capabilities-response.json`` defines
      ``request_signing.supported`` as "Whether this agent VERIFIES RFC 9421
      signatures on incoming requests" — an AGENT-level fact.
      ``RequestSignatureMiddleware`` is mounted process-wide (``src/app.py``), so a
      literal ``false`` UNDER-declares something true, which is the same dishonesty
      as an over-declaration pointed the other way.
    * ``required_for`` is "empty in 3.0 by default; sellers populate selectively
      during per-counterparty pilots". A non-empty default would reject every
      existing buyer at once, and per the pinned ``required_when`` trigger list an
      all-empty posture fires nothing — so it stays emittable for a tenant with no
      trust root at all.
    """
    from src.core.config import get_config

    return RequestSigningPosture(supported=get_config().signing.verifier_enabled)


def posture_from_declarations(declarations: CapabilityDeclarations | None) -> RequestSigningPosture:
    """The posture *declarations* resolves to — the ONE declaration -> posture mapping.

    Callers that already parsed the store (the capabilities read path, which must let
    a malformed declaration surface as ``CONFIGURATION_ERROR``) use this; callers
    holding only the tenant dict use :func:`posture_for_tenant`, which is this plus
    the parse.

    An undeclared posture falls back to :func:`agent_level_posture`. A DECLARED one
    still degrades to ``supported=False`` when agent-level backing is absent: the
    verifier really is not running, and because this is ONE object the middleware then
    also declines to enforce. Rolling the verifier back is therefore a flag flip that
    makes the wire honest, not one that turns discovery into an error for every tenant
    that changed nothing.
    """
    from src.core.config import get_config

    declared = declarations.request_signing if declarations else None
    if declared is None:
        return agent_level_posture()
    if not get_config().signing.verifier_enabled:
        logger.warning(
            "Tenant declared a request_signing posture but SigningConfig.verifier_enabled is "
            "false, so the inbound verifier is not mounted; advertising and enforcing "
            "supported=false instead of a posture nothing backs"
        )
        return UNSUPPORTED_POSTURE
    return declared


class PostureTenant(TypedDict, total=False):
    """The two keys :func:`posture_for_tenant` reads off a detected tenant.

    ``total=False`` because this is what ``_detect_tenant`` HAPPENS to carry, not a
    contract that function declares — every member is read with ``.get``.

    WHY A TypedDict AND NOT ``Tenant`` (#1879). The prescription for this item asked for
    ``posture_for_tenant(tenant: Tenant | None)``. That signature is UNREACHABLE from
    here: the caller does not hold an ORM row. ``_detect_tenant_for_posture`` delegates to
    ``_detect_tenant`` (``src/core/resolved_identity.py`` :71), which returns a DICT and is
    shared with the identity/auth path at :161 — so reaching the ORM type would mean
    either changing that function's return type, which is IDENTITY RESOLUTION and belongs
    to #1870 ("out of scope: identity/ACL collapse"), or reading the tenant a SECOND time
    on the request path, inside the very function whose docstring exists to avoid a second
    read. The first is out of scope by the charter's own words; the second is a behaviour
    change smuggled in as a type fix.

    So this states the shape that actually flows, keeps the ORM boundary where #1870 will
    find it, and costs nothing at runtime. DO NOT "finish the job" by editing
    ``resolved_identity.py``.
    """

    tenant_id: str
    capability_declarations: dict[str, Any] | None


def posture_for_tenant(tenant: PostureTenant | None) -> RequestSigningPosture:
    """The resolved tenant's declared posture — the ONE reader of that declaration.

    Parses ``tenant["capability_declarations"]`` through the store and projects it with
    :func:`posture_from_declarations`, so the block serialized onto the
    ``get_adcp_capabilities`` wire and the :class:`VerifierCapability` the middleware
    enforces are two views of one object.

    ``AdCPError`` is caught HERE and downgraded to :data:`UNSUPPORTED_POSTURE`, with a
    WARNING naming the tenant, because one of this function's callers is the ASGI
    verifier middleware (``src/core/signing/request_verifier_middleware.py``): it runs
    outside any AdCP envelope translation, so an uncaught raise would answer EVERY AdCP
    request with a bare 500 rather than only the one surface that can report the
    problem. The same misconfiguration IS a terminal ``CONFIGURATION_ERROR`` on
    ``get_adcp_capabilities``, which parses the store itself — loud where it belongs,
    and the fallback does not INVENT enforcement the tenant never declared. The
    rejected alternative, promoting an unreadable declaration to ``required``, turns a
    config typo into a 401 on every AdCP surface. ``_fail_closed_bucket`` is unchanged:
    it covers the different case of a READABLE posture plus an unnameable request.

    Never a bare ``except Exception`` — a broad catch here would hide a genuine bug as
    a silently unenforced posture.
    """
    from src.core.exceptions import AdCPError
    from src.core.schemas.capability_declarations import CapabilityDeclarations

    if not tenant:
        return agent_level_posture()
    try:
        declarations = CapabilityDeclarations.from_tenant(tenant.get("capability_declarations"))
    except AdCPError as exc:
        logger.warning(
            "Tenant %r has an unreadable capability declaration, so its request_signing posture "
            "cannot be resolved and nothing is enforced for it: %s",
            tenant.get("tenant_id"),
            exc,
        )
        return UNSUPPORTED_POSTURE
    return posture_from_declarations(declarations)


def request_signing_buckets_declared(posture: RequestSigningPosture) -> bool:
    """Whether *posture* names any operation or protocol method in any bucket.

    The pin's ``key_origins`` ``purpose_anchoring`` constraint: a declared
    ``key_origins.request_signing`` origin "requires non-empty
    ``request_signing.supported_for``/``required_for``/``protocol_methods_supported_for``
    /``protocol_methods_required_for``". ``warn_for`` is deliberately NOT in that list
    — the pin's, not an omission here.
    """
    return any(
        bucket_names(bucket)
        for bucket in (
            posture.supported_for,
            posture.required_for,
            posture.protocol_methods_supported_for,
            posture.protocol_methods_required_for,
        )
    )


def requires_trust_root(posture: RequestSigningPosture, *, webhook_signing_supported: bool) -> bool:
    """Whether the pinned ``identity.brand_json_url`` ``required_when`` fires.

    ``#/properties/identity/properties/brand_json_url/x-adcp-validation.required_when``
    lists SIX triggers: the four ``request_signing`` buckets of
    :func:`request_signing_buckets_declared`, ``webhook_signing.supported === true``,
    and any ``identity.key_origins`` subfield. The third is handled at its own layer —
    we only ever emit ``key_origins`` where anchoring already holds, so it cannot
    self-trigger.

    What is NOT a trigger, and this is what makes the conservative default shippable:
    ``request_signing.supported == true`` on its own, and ``warn_for`` in either
    namespace. A keyless tenant with no trust root can therefore declare
    ``supported: true`` with empty buckets and stay conformant.
    """
    return webhook_signing_supported or request_signing_buckets_declared(posture)


class IdentityDeclaration(LibraryIdentity):
    """The trust-root pointer: DECLARED for validation, DERIVED for emission (#1291 D1).

    ``extra="forbid"`` is RESTATED because the library type is ``extra="allow"``:
    inherited as-is, an operator's typo'd key would be silently ACCEPTED and their
    intent would never reach the wire with no indication why. Same reason
    ``MeasurementDeclaration`` restates it.

    Two layers, and neither substitutes for the other:

    * DECLARED, for VALIDATION (``src/core/schemas/capability_declarations.py``). The
      store carries the block so a declaration naming a signing posture WITHOUT
      ``brand_json_url`` is rejectable at all — which is what BR-UC-010's
      ``posture_declared_identity_absent`` / ``..._identity_empty`` rows grade. A value
      that were always derived could never be missing.
    * DERIVED, for EMISSION (:func:`emitted_identity`). What goes on the wire comes from
      ``src/core/agent_identity.py``, so a response carrying a posture always carries a
      conformant pointer. A declared value that DIFFERS from the derived one is a
      ``CONFIGURATION_ERROR`` naming the field, because a second literal for a key origin
      is a ``request_signature_key_origin_mismatch`` waiting to happen.

    It lives HERE, beside :class:`RequestSigningPosture` and
    :class:`WebhookSigningPosture`, because this module is the one place the library
    signing types may be constructed — the AST-detectable form of the two deleted
    ``capabilities.py`` literals, guarded by
    ``tests/unit/test_architecture_signing_block_construction.py``.
    """

    model_config = ConfigDict(extra="forbid")


def emitted_identity(
    *,
    posture: RequestSigningPosture,
    webhook_signing_supported: bool,
    brand_json_url: str | None,
    jwks_origin: str | None,
) -> IdentityDeclaration | None:
    """The ``identity`` block to put on the wire, or ``None`` when none is owed.

    Emitted exactly when :func:`requires_trust_root` fires, and never otherwise: the
    block's whole purpose is to anchor a declared signing posture, so a tenant with no
    posture and no key publishes nothing to anchor. Omission there is not silence about
    a fact — there is no fact.

    ``key_origins.request_signing`` is emitted ONLY where
    :func:`request_signing_buckets_declared` holds, even though A3 publishes a JWKS at
    that origin for every keyed tenant. Two reasons, both the pin's: the
    ``purpose_anchoring`` constraint requires a declared origin to have its posture, and
    an unconditional emission would SELF-TRIGGER ``required_when`` — turning the
    conservative default into a rule about itself.

    ``brand_json_url`` absent means the origin cannot serve https (see
    :func:`origin_is_publishable`), so there is nothing conformant to emit.
    """
    if brand_json_url is None or not requires_trust_root(posture, webhook_signing_supported=webhook_signing_supported):
        return None
    anchored = jwks_origin is not None and request_signing_buckets_declared(posture)
    origins = KeyOrigins(request_signing=AnyUrl(jwks_origin)) if anchored and jwks_origin else None
    return IdentityDeclaration(
        # AnyUrl(...), not cast(Any, ...) (#1879). Both fields are declared
        # ``AnyUrl | None``; a cast is a RUNTIME NO-OP, so a malformed URL reached the
        # served capabilities document unvalidated. Constructing the type validates it
        # here, where the declaration is, instead of asserting it is already right.
        brand_json_url=AnyUrl(brand_json_url),
        key_origins=origins,
    )


class WebhookSigningPosture(LibraryWebhookSigning):
    """A tenant's ``webhook_signing`` block — what we advertise AND what we emit.

    #1291 C1 (``salesagent-z6nr.18``), design step 5. The sibling of
    :class:`RequestSigningPosture`, and frozen for the same reason: one posture is
    resolved per delivery and then read twice — once to decide whether the RFC 9421
    arm is reachable at all, once to check the algorithm we are about to sign with
    is one we would declare. A posture that could change between those reads would
    let a webhook be signed under one rule and advertised under another.

    Two invariants are structural rather than remembered:

    * ``profile`` defaults to the SDK's :data:`WEBHOOK_TAG` — the SAME constant
      ``adcp.signing.webhook_signer.sign_webhook`` pins into the emitted
      ``Signature-Input`` ``tag=`` parameter. The schema types the field as a closed
      ``Literal``, so if the SDK constant ever drifts from the schema enum this
      becomes a loud pydantic failure at construction instead of a silent
      advertise/emit mismatch (``get-adcp-capabilities-response.json``
      ``#/properties/webhook_signing/profile``: the value "MUST match the ``tag=``
      parameter … so receivers can statically validate the declared profile against
      the on-wire tag").
    * ``algorithms`` is the set we WILL SIGN WITH (security.mdx @ v3.1.1 :1419), so
      :func:`webhook_signing_posture` derives it from the tenant's ACTIVE key rows —
      never from ``SIGNING_ALG_VALUES``, which is the ALLOWED set. A tenant holding
      one ed25519 key that declared both algorithms would promise one it never emits.

    #1291 D1 puts this object on the ``get_adcp_capabilities`` wire. It is never
    parsed FROM a tenant declaration: the block is derived platform state, listed in
    ``_DERIVED_BLOCKS`` so an operator declaring it is told why rather than told it is
    unimplemented — see :func:`webhook_signing_is_declarable`.
    """

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _profile_defaults_to_the_emitted_tag(cls, data: Any) -> Any:
        """Fill ``profile`` from the SDK constant the signer actually emits — but
        ONLY when we sign.

        Done as a validator rather than by re-declaring the field so the schema's
        own ``Literal`` annotation (and its description) stay the single definition
        — the constant is CHECKED against the enum here instead of being copied
        beside it.

        The ``supported`` guard is a spec obligation, not a nicety. In AdCP 3.1.1
        (``dist/schemas/3.1.1/bundled/protocol/get-adcp-capabilities-response.json``)
        ``webhook_signing.required`` is ``["supported"]`` alone, ``profile`` is
        optional, and its description fixes its meaning: *"Identifier of the
        webhook-signing profile version the agent emits. Value MUST match the
        ``tag=`` parameter emitted in the RFC 9421 ``Signature-Input`` header … so
        receivers can statically validate the declared profile against the on-wire
        tag."* ``supported: false`` means *"receivers MUST NOT expect a Signature
        header"* — there is no ``Signature-Input`` and no ``tag=`` to match, so a
        declared profile would be a claim about a header we never send and its one
        documented use would be unsatisfiable. The graded scenario
        ``@T-UC-010-v31-webhook-signing`` asserts the profile is ABSENT when
        unsupported, and it is right to (#1291 D1).
        """
        if isinstance(data, dict) and data.get("profile") is None and data.get("supported"):
            return {**data, "profile": WEBHOOK_TAG}
        return data


def webhook_signing_is_declarable() -> bool:
    """Whether any tenant CAN declare a ``webhook_signing`` posture.

    False, and permanently so — but for a DIFFERENT reason from an unbacked block.
    ``webhook_signing`` is DERIVED platform state (key material plus trust-root
    publishability), so it is not the operator's to declare at all; the store lists it
    in ``_DERIVED_BLOCKS`` rather than ``_UNBACKED_BLOCKS``.
    :func:`is_block_declarable` reads both tables, so this predicate and the
    middleware gates keep consulting ONE function.
    """
    from src.core.schemas.capability_declarations import is_block_declarable

    return is_block_declarable("webhook_signing")


def origin_is_publishable(origin: str | None) -> bool:
    """Whether a conformant ``identity.brand_json_url`` can exist for *origin*.

    The pin fixes ``brand_json_url`` to ``pattern: "^https://"`` and security.mdx
    rejects anything else with ``request_signature_brand_json_url_missing``, while
    ``_get_protocol_for_domain`` (``src/core/domain_config.py``) deliberately derives
    ``http`` for localhost and single-label hosts — neither can present a
    publicly-trusted certificate, and advertising https there publishes a trust root
    nothing can reach.

    The two constraints are irreconcilable on such a host, so the honest answer is to
    claim nothing: every signature we emitted there would be one no conformant receiver
    can resolve a key for. That makes this a gate on the SHARED posture object rather
    than on the advertise side alone — see :func:`webhook_signing_posture`.
    """
    return origin is not None and origin.startswith("https://")


def unsupported_webhook_signing_posture() -> WebhookSigningPosture:
    """The block for an agent that does NOT sign its outbound webhooks.

    ``algorithms`` is absent because there is no active key to name one from, which is
    the whole difference from the supported case. ``profile`` is absent for the same
    kind of reason: it names the ``tag=`` of a ``Signature-Input`` header this posture
    promises NOT to send, so declaring it would be a claim about a header that never
    goes out (see :meth:`WebhookSigningPosture._profile_defaults_to_the_emitted_tag`
    for the schema citation). This CHANGES the pre-#1291-D1 wire, which filled the
    profile unconditionally. ``legacy_hmac_fallback`` is derived either way; it
    describes the OTHER arm, which stays reachable whether or not we sign.

    The ONE construction of the unsupported block, so the no-tenant capabilities
    response, a keyless tenant's response and the sender's own refusal path cannot
    drift into three answers.
    """
    from src.core.signing.webhook_sender_factory import legacy_hmac_fallback_supported

    return WebhookSigningPosture(
        supported=False,
        profile=None,
        legacy_hmac_fallback=legacy_hmac_fallback_supported(),
    )


def webhook_signing_posture(
    repo: SigningKeyRepository,
    *,
    now: datetime,
    origin: str | None,
    key_backing: KeyBacking | None = None,
) -> WebhookSigningPosture:
    """The posture *repo*'s tenant can HONESTLY hold at *now* on *origin*.

    Derived from platform state, never from a constant or a declaration:

    * ``supported`` is :func:`signing_key_backed`'s ``signs`` (the ONE key-presence
      derivation) AND :func:`origin_is_publishable` — a key we can open, published
      under a trust root a receiver can actually fetch;
    * ``algorithms`` is the algorithm of the key that would actually be used;
    * ``profile`` is the SDK tag the signer emits (see the class docstring);
    * ``legacy_hmac_fallback`` is the legacy arm's own reachability, owned beside that
      arm in ``webhook_sender_factory``.

    A keyless tenant — or a keyed one on an unpublishable origin — gets
    :func:`unsupported_webhook_signing_posture`: the schema's own wording for "webhooks
    are delivered with legacy Bearer or HMAC-SHA256 auth only and receivers MUST NOT
    expect a Signature header". C1's ``_rfc9421_sender`` reads THIS object, so the wire
    and the socket cannot disagree in either direction.

    ``webhook_sender_factory`` imports this module at module level, so the
    ``legacy_hmac_fallback`` predicate is imported function-locally there and here — the
    same shape as the function-local ``get_config`` in :func:`signing_key_backed`.

    *key_backing*: pass an already-derived :class:`KeyBacking` when the caller also
    needs ``.publishes`` (e.g. capabilities' identity/key_origins gate) so
    :func:`signing_key_backed` runs ONCE per request instead of being re-derived here
    -- a second derivation is how the advertised posture and the enforced one drift
    apart (see :class:`KeyBacking`'s own docstring). Defaults to ``None`` so the two
    ``webhook_sender_factory`` call sites (the actual signing decision, out of scope
    for this ticket) are unaffected and keep deriving it themselves.
    """
    from src.core.signing.webhook_sender_factory import legacy_hmac_fallback_supported

    backing = key_backing if key_backing is not None else signing_key_backed(repo, now=now)
    if not (backing.signs and origin_is_publishable(origin)):
        return unsupported_webhook_signing_posture()

    active = repo.active_at(now=now)
    # ``signs`` is True, so ``active_at`` returned a row: the two read the same
    # selector. Narrowed for mypy rather than re-derived (ADR-009).
    assert active is not None
    return WebhookSigningPosture(
        supported=True,
        algorithms=[active.alg],
        legacy_hmac_fallback=legacy_hmac_fallback_supported(),
    )


class KeyBacking(NamedTuple):
    """What this tenant's own signing keys let it honestly declare.

    THE single key-presence derivation. Both C1's outbound signer
    (``salesagent-z6nr.18``) and D1's declaration builder (``salesagent-z6nr.20``)
    consume this one — a second derivation of "does this tenant have a key" is
    how the advertised posture and the enforced one drift apart, which is the
    whole reason this module exists.

    Two fields rather than one, because the facts they back sit on opposite sides
    of a distinction the repository calls load-bearing
    (``SigningKeyRepository.active_at`` vs ``publishable_at``):

    ======================================  ===============================  ================
    field                                   means                            selector
    ======================================  ===============================  ================
    ``signs`` -> ``webhook_signing``        this agent SIGNS                 ``active_at``
    ``publishes`` -> ``identity``           this agent PUBLISHES             ``publishable_at``
    ======================================  ===============================  ================

    A key inside its revocation grace window, or one with a future
    ``not_before``, is publishable but not active. Collapsing the two would push
    the choice down a layer into whichever consumer guessed first.

    ``signs`` additionally requires that this deployment can OPEN the row's private
    half; ``publishes`` does not, because a JWKS needs only the public one. See
    :func:`_private_half_is_resolvable`.
    """

    signs: bool
    publishes: bool


def _private_half_is_resolvable(repo: SigningKeyRepository, row: SigningKey, *, now: datetime) -> bool:
    """Whether this deployment can ACTUALLY DECRYPT *row*'s private key.

    A ``db:`` row stores the private PEM ENCRYPTED under the deployment-wide key
    encryption key named by ``SigningConfig.key_passphrase_env``. Minting refuses
    without it (``src/core/signing/keys.py``) and ``resolve_signing_material`` needs it
    again at every use — so a row minted under a KEK that was later unset, or under a
    DIFFERENT KEK than this process holds (salesagent-dn4i: a rotated KEK, a
    per-environment mismatch, a stale secret), is a key we cannot open, and reporting
    it as backing means the wire says ``supported: true`` while every delivery raises.

    This ATTEMPTS the real resolution (via :func:`~src.core.signing.provider.resolve_signing_material`)
    rather than only checking that SOME passphrase is configured — presence is not
    correctness. The three WARNINGs are deliberately distinct so an operator can tell
    "no key" from "a passphrase is configured but it's the wrong one": the first two are
    provisioning gaps, the third is a deployment misconfiguration with the key material
    intact. Reuses the SAME per-(tenant, kid) resolution cache signing itself uses
    (``_provider_cache``) — cache success, never errors, so a later-fixed KEK is retried
    on the next call rather than pinned as permanently unresolvable.

    ``env:`` / ``file:`` refs are resolved by the process's own environment or mount at
    use time and carry no KEK of ours, so their resolvability is not knowable here —
    they are taken at face value, exactly as before.

    Matched on the scheme PREFIX rather than through ``parse_key_ref``, which RAISES on a
    malformed reference. This function answers "what may this tenant honestly declare",
    and it is called on the capabilities read path and on the admin setup checklist:
    turning a malformed row into an exception there would fail discovery for a fault that
    belongs to resolution, which reports it loudly at the point of use.
    """
    from src.core.config import get_config
    from src.core.exceptions import AdCPConfigurationError
    from src.core.signing.provider import DB_SCHEME, resolve_signing_material

    if not (row.private_key_ref or "").startswith(f"{DB_SCHEME}:"):
        return True
    if get_config().signing.key_passphrase is None:
        logger.warning(
            "Tenant %s holds ACTIVE signing key %s whose private half is encrypted under the "
            "deployment key encryption key, but SigningConfig.key_passphrase_env resolves to "
            "nothing — the key cannot be opened, so this agent declares and performs NO signing "
            "with it (#1291)",
            row.tenant_id,
            row.kid,
        )
        return False
    try:
        resolve_signing_material(repo, tenant_id=row.tenant_id, purpose=row.purpose, now=now, kid=row.kid)
    except AdCPConfigurationError as exc:
        logger.warning(
            "Tenant %s holds ACTIVE signing key %s and a passphrase IS configured, but this "
            "deployment could not decrypt the key's private half (%s) — the configured KEK is "
            "wrong for this row, so this agent declares and performs NO signing with it (#1291, "
            "salesagent-dn4i)",
            row.tenant_id,
            row.kid,
            exc,
        )
        return False
    return True


def signing_key_backed(repo: SigningKeyRepository, *, now: datetime) -> KeyBacking:
    """Whether *repo*'s tenant has key material backing each declared fact.

    Takes a repository and opens NO session: the transport (the admin blueprint,
    the setup checklist, D1's capabilities builder) already owns one, and a
    per-call unit of work inside this module would add a second session to a
    capabilities request that already holds one.

    It does NOT back ``request_signing``. That block declares whether this agent
    VERIFIES signatures on INCOMING requests, which uses the COUNTERPARTY's keys
    — it is backed by ``SigningConfig.verifier_enabled`` plus the mounted
    ``RequestSignatureMiddleware``, and is completely independent of whether this
    tenant owns a key of its own. The asymmetry is the spec's: v3.1.1
    ``get-adcp-capabilities-response.json`` defines ``request_signing.supported``
    as "Whether this agent VERIFIES RFC 9421 signatures on incoming requests" and
    ``webhook_signing.supported`` as "Whether this agent SIGNS outbound webhooks".
    """
    from src.core.config import get_config

    active = repo.active_at(now=now)
    return KeyBacking(
        signs=active is not None and _private_half_is_resolvable(repo, active, now=now),
        publishes=bool(repo.publishable_at(now=now, grace_seconds=get_config().signing.grace_seconds)),
    )
