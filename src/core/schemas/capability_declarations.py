"""Per-tenant AdCP capability declarations (#1592 T1a).

The store behind ``tenants.capability_declarations``: the blocks an operator may
declare and have echoed on ``get_adcp_capabilities``.

STRICT policy (KonstantinMirin's decision, 2026-07-27). A capability posture may be
declared -- and therefore emitted -- ONLY when the implementation backs it. So this
model carries fields for *business facts* the response merely echoes, and has NO
field at all for a *behavioral posture* production does not implement. Naming an
unbacked block is a deployment fault, not a buyer error: it raises
``AdCPConfigurationError`` -> ``CONFIGURATION_ERROR`` (recovery ``terminal``),
naming the block and the issue that will implement it.

Why "no field" rather than "a field we validate": a field that exists but is always
rejected still tempts the next implementer to relax the check. The absence is the
enforcement.

Shape follows ``IdempotencyPosture`` (src/core/idempotency_policy.py): a typed
model whose ``validate_backing()`` raises ``AdCPConfigurationError`` rather than
silently clamping or emitting a non-conformant response.
"""

from collections.abc import Collection, Iterable
from enum import Enum
from typing import Any, NamedTuple

from adcp.types.generated_poc.enums.specialism import AdcpSpecialism
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
    ExperimentalFeature,
    ProtocolMethodsRequiredForItem,
    ReportingDeliveryMethod,
    SupportedProtocol,
)

# `Measurement` is aliased `LibraryMeasurementDeclaration`, NOT the conventional
# `LibraryMeasurement`: the SDK has TWO distinct `Measurement` types, and
# `src/core/schemas/_base.py:95` already binds `LibraryMeasurement` to the
# product-level one (`adcp.types.Measurement`), whose local subclass is `Measurement`
# (_base.py:1364). The schema-inheritance guard keys its Library*-alias map on the
# un-prefixed name across ALL schema modules, so reusing the alias made it demand that
# the product-level `Measurement` inherit the capabilities-response type. Aliasing to
# match THIS module's subclass keeps the guard checking the right pair. Do not
# "simplify" it back to `LibraryMeasurement`.
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import (
    Measurement as LibraryMeasurementDeclaration,
)
from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import TrustedMatch as LibraryTrustedMatch
from pydantic import BaseModel, ConfigDict, ValidationError

from src.core.errors.details import ConfigurationDetails
from src.core.signing.posture import (
    IdentityDeclaration,
    RequestSigningPosture,
    bucket_names,
    request_signing_buckets_declared,
    requires_trust_root,
)

#: The two AdCP-namespace bucket names and the three protocol-method ones, as they are
#: spelled in a stored declaration. Read off the model rather than typed out, so a bucket the
#: pinned schema adds is covered by the split the day the field appears.
_ADCP_BUCKETS = ("required_for", "warn_for", "supported_for")
_PROTOCOL_BUCKETS = ("protocol_methods_required_for", "protocol_methods_warn_for", "protocol_methods_supported_for")


def _is_protocol_method(name: str) -> bool:
    """Whether *name* is a JSON-RPC protocol method name, per the PINNED SCHEMA.

    Asked by validating the candidate against the generated ``protocol_methods_*`` item
    model, whose ``^[a-z][a-z0-9_]*/[a-z][a-z0-9_]*$`` pattern is what the schema means by
    that namespace. The rule is READ from the pin rather than transcribed as a ``/`` test
    that would drift the day the pattern changed.
    """
    try:
        ProtocolMethodsRequiredForItem(root=name)
    except ValidationError:
        return False
    return True


def _reject_mixed_namespaces(declared: Any) -> None:
    """The namespace split, refused at CONFIGURATION time. security.mdx @ v3.1.1 :1053.

        AdCP tool names (no ``/``) MUST NOT appear in any ``protocol_methods_*`` array, and
        JSON-RPC method names (containing ``/``) MUST NOT appear in ``supported_for`` /
        ``warn_for`` / ``required_for``. Verifiers MUST reject capability blocks that violate
        the namespace split with a configuration-time error rather than silently coercing
        strings between the two.

    Runs on the RAW declaration, BEFORE ``model_validate``, and that placement is the point.
    The generated ``protocol_methods_*`` item model would refuse a slash-free AdCP tool name
    on its own -- with pydantic's "String should match pattern '^[a-z]...'", which names a
    regex rather than the rule an operator broke. The spec asks for a configuration-time
    error about the NAMESPACE SPLIT, so it is raised here, where the strings are still
    strings and both directions can be reported together.

    NEITHER DIRECTION TESTS FOR A ``/``. That test is a second definition of a question two
    other modules already answer, and it answers it wrongly in both directions:
    ``get_signals`` has no slash and is not a tool this seller implements, while
    ``tasks/cancel`` has one and is not an AdCP operation either. So each half asks the
    authority that owns it -- the REGISTRY for "is this an AdCP operation of this seller"
    (``src.core.tools.registry.is_adcp_operation``, which IS what makes a name one), and the
    PINNED SCHEMA for "is this a protocol method" (:func:`_is_protocol_method`).

    Anything that is not a mapping of lists is left alone: this is a namespace check, not a
    type check, and the shape is pydantic's to refuse a moment later.
    """
    from src.core.exceptions import AdCPConfigurationError
    from src.core.tools.registry import is_adcp_operation

    posture = declared.get("request_signing") if isinstance(declared, dict) else None
    if not isinstance(posture, dict):
        return

    def _names(buckets: tuple[str, ...]) -> list[str]:
        return [name for bucket in buckets for name in (posture.get(bucket) or []) if isinstance(name, str)]

    misplaced = sorted(
        {name for name in _names(_ADCP_BUCKETS) if _is_protocol_method(name)}
        | {name for name in _names(_PROTOCOL_BUCKETS) if is_adcp_operation(name)}
    )
    if misplaced:
        raise AdCPConfigurationError(
            details=ConfigurationDetails(
                capability="capability_declarations.request_signing",
                rejected_value=misplaced,
                tracked_by=(
                    "The two buckets are matched against disjoint envelope fields "
                    "(security.mdx @ v3.1.1 :1053): AdCP operation names belong in "
                    "required_for/warn_for/supported_for, JSON-RPC method names in the "
                    "matching protocol_methods_* bucket."
                ),
            ),
        )


# Two refusal tables, for two genuinely different reasons. Both make
# ``is_block_declarable`` False, and both name the block explicitly so the operator
# gets an actionable error instead of pydantic's generic "extra fields not permitted".
#
# _UNBACKED_BLOCKS -- the AdCP schema defines it, this deployment does not IMPLEMENT
# it, and the entry names the GitHub issue that will. Every entry is a promise we
# would otherwise make to buyers and could not keep.
#
# _DERIVED_BLOCKS -- we DO implement it, but its value is platform state rather than
# operator configuration, so there is nothing for a tenant to declare. Same
# philosophy the ``_DERIVATION_ONLY_BUILDERS`` guard encodes for ``account.*`` and
# ``adcp.*``; a different message because the fix for the operator is different.
#
# The signing family (#1291 D1) is now backed: request_signing and identity are
# declarable fields below, reporting_delivery_methods is member-gated in
# ``validate_backing``, and webhook_signing is derived. What stays refused here is
# refused for reasons that OUTLIVE #1291 -- content_standards and
# wholesale_feed_webhooks have no implementation of ANY kind, so signing landing does
# not make either declarable and an entry citing #1291 would point at a closed issue
# the moment it merges. Re-homing them is the same fix commit 3b92577b0 applied to
# offline_delivery_protocols; each reason therefore names the MISSING SURFACE, tracked
# on its own issue: content_standards on #1855, wholesale_feed_webhooks on #1867.
#
# wholesale_feed_webhooks has no field on the model either -- it is listed here so the
# operator learns why rather than reading pydantic's generic extra-field error, and
# because it is the third ``must_equal_when`` trigger.
_UNBACKED_BLOCKS: dict[str, str] = {
    "content_standards": (
        "#1855 (no content-standards surface exists in this deployment: nothing implements local "
        "evaluation, artifacts, verdicts or artifact_webhook delivery)"
    ),
    "wholesale_feed_webhooks": (
        "#1867 (no wholesale feed surface exists in this deployment, so no feed webhooks are ever emitted)"
    ),
    "offline_delivery_protocols": "#1729 (no offline report delivery is implemented; see reporting_bucket)",
}

# Blocks whose value this deployment DERIVES and therefore refuses to take from
# configuration. ``webhook_signing`` comes from key material plus trust-root
# publishability (``webhook_signing_posture``), and C1's outbound sender reads that
# same object -- so a declared value could only ever contradict what we actually do.
_DERIVED_BLOCKS: dict[str, str] = {
    "webhook_signing": (
        "it is DERIVED from this tenant's signing keys and the origin its trust root is served "
        "from, and the outbound webhook sender reads the same value, so a declaration could only "
        "contradict what this agent actually signs (#1291)"
    ),
}


# The protocols a tenant may CLAIM. Derivation rule, applied uniformly: a protocol
# is backed iff every tool in its conformance bundle
# (`dist/compliance/3.1.1/protocols/<p>/index.yaml#required_tools`) is implemented
# in src/. Verified at v3.1.1:
#   media_buy   -> [get_products, create_media_buy]  : both implemented.
#   measurement -> NO protocol bundle exists (only brand, creative, governance,
#                  media-buy, signals, sponsored-intelligence), and
#                  #/properties/supported_protocols scopes 3.1 measurement to
#                  "get_adcp_capabilities catalog discovery" -- so the claim commits
#                  us to nothing beyond the catalog this batch implements.
# Deliberately ABSENT, same rule, opposite answer -- this is what keeps STRICT honest:
#   brand    -> [get_brand_identity] : ZERO hits in src/  -> #1724.
#   creative -> generative creative unimplemented         -> #1724.
#   signals     -> BACKED, but NOT by a get_signals tool. That justification was wrong:
#                  src/core/tools/signals.py was unreachable from every transport -- never
#                  registered on MCP, no REST route, no A2A skill -- because #826
#                  ("remove signals tools", 2025-12-09) removed the registration and left
#                  the implementation behind. The file is deleted.
#                  What actually backs the protocol is the signals-AGENT integration:
#                  src/services/dynamic_products.py queries signals agents to generate
#                  product variants (reachable through get_products), and
#                  src/admin/blueprints/signals_agents.py manages them. Still graded by the
#                  `signal-owned` accept scenario, which needs it as the parent protocol.
#                  NOTE the failure mode this row demonstrates: backing was checked by
#                  "does an implementation file exist" rather than "can a buyer reach it".
_BACKED_PROTOCOLS: frozenset[SupportedProtocol] = frozenset(
    {SupportedProtocol.media_buy, SupportedProtocol.measurement, SupportedProtocol.signals}
)

# What every response advertises before any declaration is applied. Lives HERE rather
# than in the tools layer because ``validate_backing`` must reason about the EMITTED
# protocol set (defaults unioned with the declaration) to check specialism roll-up --
# and a schema importing from src/core/tools would invert the layering.
# ``capabilities.py`` imports these as its single source for both the no-tenant and
# tenant-resolved constructions.
DEFAULT_SUPPORTED_PROTOCOLS: list[SupportedProtocol] = [SupportedProtocol.media_buy]
DEFAULT_SPECIALISMS: list[AdcpSpecialism] = [AdcpSpecialism.sales_non_guaranteed]

# The specialisms a tenant may CLAIM, hand-maintained with a per-entry justification.
#
# Deliberately NOT derived from `specialisms/<id>/index.yaml#required_tools`: that
# derivation would REJECT `sales-non-guaranteed`, which production already emits
# unconditionally (`_DEFAULT_SPECIALISMS`, capabilities.py) -- its bundle requires
# `sync_governance`, which has zero implementations here, plus 15 scenarios. Deriving
# would therefore regress the wire for every tenant. That pre-existing inconsistency is
# recorded, not "fixed" here; changing the default is a separate, wire-visible decision.
#
# Each entry states the bundle requirement and why this deployment meets it:
_BACKED_SPECIALISMS: dict[AdcpSpecialism, SupportedProtocol] = {
    # required_tools [sync_governance, get_products, create_media_buy], 15 scenarios.
    # PRE-EXISTING and unconditional -- see the note above. Kept so a tenant redeclaring
    # the default is not rejected for stating what we already advertise.
    AdcpSpecialism.sales_non_guaranteed: SupportedProtocol.media_buy,
    # required_tools [get_signals] ONLY, and requires_scenarios: 0. `get_signals` is
    # implemented (src/core/tools/signals.py). This is the one specialism a tenant can
    # declare that genuinely CHANGES the wire, which is what makes its accept scenario
    # non-vacuous rather than an echo of the default.
    AdcpSpecialism.signal_owned: SupportedProtocol.signals,
}


# The type parameter is VALUE-restricted (not bounded), which is what makes mixing the two
# branches a type error: passing protocols against the specialism backing map now fails with
# `Value of type variable "_Declared" cannot be "StrEnum"`, which `Iterable[Any]` silently
# accepted. Restricting here also means mypy.ini needs no new disallow_any_explicit entry
# for this module (#1721 review F7).
def _reject_unbacked[Declared: (SupportedProtocol, AdcpSpecialism)](
    claimed: Iterable[Declared],
    backed: Collection[Declared],
    *,
    field: str,
    noun: str,
    tracked_by: str | None = None,
) -> None:
    """Raise ``AdCPConfigurationError`` if ``claimed`` contains a value ``backed``
    does not cover.

    Shared shape for the ``supported_protocols`` and ``specialisms`` platform-backing
    checks (#1721 M1 / D1 -- was duplicated verbatim). ``backed`` may be
    a frozenset (protocols) or a dict keyed by the claimed enum (specialisms) --
    ``in`` and iteration both work identically for either.

    GENERIC IN ``Declared`` (#1879), which is the whole point: with ``Iterable[Any]`` on
    both sides the signature stated neither the element type nor the RELATION between
    them, so a protocol list checked against a specialisms dict typechecked cleanly — the
    two call sites could be crossed and nothing would say so. Tying both parameters to one
    type parameter makes that a type error.
    """
    from src.core.exceptions import AdCPConfigurationError

    unbacked = sorted({v.value for v in claimed if v not in backed})
    if not unbacked:
        return
    raise AdCPConfigurationError(
        # The axis name was IN THE KEY (f"unbacked_{field}"), so no single read
        # found it. It is a value now, like every other capability refusal.
        details=ConfigurationDetails(
            capability=field,
            rejected_value=unbacked,
            accepted_values=sorted(b.value for b in backed),
            tracked_by=tracked_by,
        ),
    )


# Declaring a block whose surface is x-status:experimental obliges the agent to list
# the feature id: "Sellers that implement any experimental surface MUST list its
# feature id here" (#/properties/experimental_features). The ids are therefore
# DERIVED from the declared blocks, not echoed from operator config -- a bare echo
# would let a tenant declare the block while omitting the id the spec requires.
_EXPERIMENTAL_FEATURE_BY_BLOCK: dict[str, str] = {
    "measurement": "measurement.core",
    "trusted_match": "trusted_match.core",
}


def is_block_declarable(block: str) -> bool:
    """Whether a tenant may declare *block* — i.e. whether a stored value can exist.

    Public read of the STRICT policy above, for consumers that must know whether a
    stored declaration can exist at all. The inbound signature verifier (#1291 B1) uses
    it to skip resolving a tenant whose posture cannot differ from the default: an
    undeclarable block means no tenant can have declared one, so the read could not
    change any decision. Removing an entry from either table therefore switches those
    consumers on by itself, with no second flag to remember.

    BOTH tables answer False. The reason differs (unimplemented vs derived) and the
    operator-facing message differs with it, but the consumers only ever ask "can a
    stored value exist", so they keep reading ONE predicate.
    """
    return block not in _UNBACKED_BLOCKS and block not in _DERIVED_BLOCKS


class MeasurementDeclaration(LibraryMeasurementDeclaration):
    """The tenant's measurement vendor/metric catalog.

    ``extra="forbid"`` is RESTATED because the library type is ``extra="ignore"``:
    inherited as-is, an operator's typo'd key would be silently dropped and their
    declaration would never reach the wire with no indication why. Metric field
    constraints (id pattern, 1..64 length, ``minItems``, ``Accreditation`` shape)
    ride the SDK unchanged.

    Declarable under STRICT: the catalog is a tenant business fact the response
    echoes. At 3.1 the measurement protocol is scoped to catalog discovery via
    ``get_adcp_capabilities``, so echoing it promises nothing further.
    """

    model_config = ConfigDict(extra="forbid")


class TrustedMatchDeclaration(LibraryTrustedMatch):
    """The tenant's deployed TMP surfaces.

    Extends the library type (Pattern #1) so the closed surface enum, uniqueItems
    and minItems come from the SDK rather than being restated here. Presence of the
    object is itself the signal that TMP infrastructure is deployed, which is a
    tenant-side operational fact -- not a protocol behavior this codebase has to
    implement -- so it is declarable under STRICT.
    """


def _union_sorted[EnumMember: Enum](defaults: list[EnumMember], declared: list[EnumMember] | None) -> list[EnumMember]:
    """Defaults unioned with a declaration, sorted by enum value.

    One body for the specialisms and supported_protocols emissions, which were
    the same set-union-then-sort expressed twice with a different lambda -- two
    copies of a rule ("declared ADDS to defaults, never replaces") that must not
    be able to diverge, because a divergence emits a self-inconsistent wire.
    """
    return sorted(set(defaults) | set(declared or []), key=lambda m: m.value)


class SigningPlatformBacking(NamedTuple):
    """The platform facts the signing relation rules are checked AGAINST.

    Resolved by the caller that owns a session (``src/core/tools/capabilities.py``,
    inside its unit of work) and passed in, so this module keeps its zero DB access and
    the OTHER reader of the same store -- ``posture_for_tenant``, which
    ``_resolve_identity`` calls on every AdCP request -- pays for no key or tenant read
    it does not need.
    """

    #: ``webhook_signing.supported`` as DERIVED from key material plus publishability.
    webhook_signing_supported: bool
    #: ``identity.brand_json_url`` as DERIVED from ``src/core/agent_identity.py``.
    brand_json_url: str


class CapabilityDeclarations(BaseModel):
    """Implementation-backed capability blocks for one tenant.

    ``extra="forbid"`` is unconditional -- deliberately NOT
    ``get_pydantic_extra_mode()``. That helper relaxes to ``ignore`` in production
    for forward compatibility at the BUYER boundary, which is right for inbound
    requests and wrong here: this is operator configuration, and silently dropping
    a block an operator wrote means their declaration never reaches the wire with
    no indication why.
    """

    model_config = ConfigDict(extra="forbid")

    trusted_match: TrustedMatchDeclaration | None = None
    measurement: MeasurementDeclaration | None = None
    supported_protocols: list[SupportedProtocol] | None = None
    specialisms: list[AdcpSpecialism] | None = None
    # The signing family (#1291 D1). ``request_signing`` is typed as the EXISTING
    # posture class rather than a parallel declaration model, which is what makes the
    # block a tenant advertises and the capability the inbound verifier enforces ONE
    # object -- and gets the namespace split, the protocol-method pattern and the
    # covers_content_digest enum from the pinned schema for free.
    #
    # There is deliberately NO ``webhook_signing`` field: it is derived platform state
    # (see ``_DERIVED_BLOCKS``). ``reporting_delivery_methods`` is MEMBER-gated in
    # ``validate_backing`` rather than block-gated, because ``[webhook]`` has real
    # backing while ``[offline]`` does not.
    request_signing: RequestSigningPosture | None = None
    identity: IdentityDeclaration | None = None
    reporting_delivery_methods: list[ReportingDeliveryMethod] | None = None

    @classmethod
    def from_tenant(cls, declared: object) -> "CapabilityDeclarations":
        """Parse a tenant's stored declarations; an EMPTY instance when nothing is declared.

        Returns an empty declaration rather than ``None`` so callers can read it
        unconditionally. Every emission site had to write
        ``declarations.x if declarations else <default>``, which put the
        undeclared-tenant default in five places instead of one -- and each
        ternary was a chance to pick a different default than the emitted-union
        rules on this class already define. An empty instance answers every one
        of them correctly: the unions fall back to the defaults, and the optional
        blocks are None.

        The emitted wire for an undeclared tenant is unchanged (pre-#1592
        behavior), which is what every tenant that never declared anything must
        keep seeing.

        ``declared: object``, NOT ``Any`` (#1879). This is the entry point
        ``posture_for_tenant`` calls on EVERY AdCP request to decide whether that request
        is refused, so it is the parse boundary of the request path. Under ``Any`` the
        ``isinstance`` guard below was invisible to mypy — every attribute access past it
        typechecked whatever the shape — which is the opposite of what a parse boundary is
        for. Under ``object`` the narrowing is the only way through, and mypy enforces it.

        Runs the SPEC-coherence and no-DB backing rules only. The two rules that need
        platform state -- ``webhook_signing``'s ``must_equal_when`` and the
        declared-vs-derived ``brand_json_url`` cross-check -- are
        :meth:`validate_signing_platform_backing`, called by the one caller that owns a
        session.

        READ SIDE ONLY. Nothing here writes ``capability_declarations`` — the
        column is populated out of band (fixtures, operator SQL), which is why
        the undeclared-tenant path is the one every scenario actually exercises
        and why "operator declares X, then a buyer sees X" cannot be graded end
        to end today. The write seam is #1856; when it lands, the round trip
        becomes gradeable and the defaults above stop being the only covered branch.
        """
        from src.core.exceptions import AdCPConfigurationError

        if not declared:
            return cls()
        if not isinstance(declared, dict):
            raise AdCPConfigurationError(
                details=ConfigurationDetails(received_type=type(declared).__name__),
            )

        # Name undeclarable blocks explicitly, before pydantic's generic extra-field
        # error, so the operator learns WHICH block they cannot declare and why.
        # Unbacked first: "we do not implement this" is the more fundamental answer
        # than "this one is ours to derive".
        for block in sorted(_UNBACKED_BLOCKS):
            if block in declared:
                raise AdCPConfigurationError(
                    field=f"capability_declarations.{block}",
                    details=ConfigurationDetails(block=block, tracked_by=_UNBACKED_BLOCKS[block]),
                )
        # Same shape, same axes, a different reason string: ``tracked_by`` carries WHY the
        # block cannot be declared, which for a derived block is "this agent emits the
        # derived value" rather than an issue number. A second key (``derived_because``)
        # would be a second spelling of one axis, which ``ConfigurationDetails`` exists to
        # prevent -- the two tables are told apart by the sentence, not by the key name.
        for block in sorted(_DERIVED_BLOCKS):
            if block in declared:
                raise AdCPConfigurationError(
                    field=f"capability_declarations.{block}",
                    details=ConfigurationDetails(
                        block=block,
                        tracked_by=(
                            f"Remove the block -- this agent emits the derived value, because {_DERIVED_BLOCKS[block]}."
                        ),
                    ),
                )

        # The namespace split, before pydantic sees the strings -- see the function for why
        # the placement is the point rather than an ordering convenience.
        _reject_mixed_namespaces(declared)

        # ValidationError only -- never a broad `except Exception`, which would
        # flatten any typed AdCPSalesAgentError raised from a nested validator into a
        # generic CONFIGURATION_ERROR and lose its code
        # (guard: test_architecture_no_error_flattening).
        try:
            parsed = cls.model_validate(declared)
        except ValidationError as exc:
            raise AdCPConfigurationError(
                internal_detail=exc,
                details=ConfigurationDetails(capability="capability_declarations", rejected_value=sorted(declared)),
            ) from exc

        parsed.validate_backing()
        return parsed

    def validate_backing(self) -> None:
        """Cross-field and platform-backing rules the JSON Schema cannot express.

        Rule ORDER is load-bearing: spec cross-field coherence runs BEFORE platform
        backing, so an identity rejection still names ``brand_json_url`` rather than
        being pre-empted by a backing error. No rule lands here without a scenario that
        executes it.
        """
        self._validate_signing_relations()

        # Platform backing: ``[webhook]`` report delivery is real -- the delivery
        # webhook scheduler sends daily reports through ``protocol_webhook_service``,
        # which #1291 C1 routes and signs. ``[offline]`` is bucket delivery nothing
        # implements. MEMBER-level rather than block-level, because a block-level
        # refusal cannot express "half of this is backed" -- and the member split is
        # what lets the webhook-only row be graded on its own terms.
        self._validate_reporting_delivery_methods()

        from src.core.exceptions import AdCPConfigurationError

        # Platform backing: a tenant may only claim protocols/specialisms this
        # deployment actually serves. Advertising an unserved one is the exact
        # over-promise STRICT exists to prevent -- the buyer would route traffic
        # for a domain we cannot answer. `creative-generative` is the specialisms
        # scenario's case -- nothing implements generative creative, and the AAO
        # runner grades the claim.
        _reject_unbacked(
            self.supported_protocols or [],
            _BACKED_PROTOCOLS,
            field="supported_protocols",
            noun="required tool surface",
        )
        _reject_unbacked(
            self.specialisms or [],
            _BACKED_SPECIALISMS,
            field="specialisms",
            noun="tools the specialism's conformance bundle requires",
            tracked_by="Generative creative is tracked by #1724.",
        )

        # Roll-up coherence: "the runner rejects a specialism claim whose parent
        # protocol is missing" (#/properties/specialisms). Checked against the
        # EMITTED protocol set, not the declared one, because the defaults are
        # unioned in -- a tenant declaring only `signal-owned` still gets media_buy.
        emitted_protocols = set(self.emitted_supported_protocols(DEFAULT_SUPPORTED_PROTOCOLS))
        orphaned = sorted(
            f"{s.value} (needs {_BACKED_SPECIALISMS[s].value})"
            for s in (self.specialisms or [])
            if s in _BACKED_SPECIALISMS and _BACKED_SPECIALISMS[s] not in emitted_protocols
        )
        if orphaned:
            raise AdCPConfigurationError(
                details=ConfigurationDetails(
                    capability="specialisms",
                    rejected_value=orphaned,
                    accepted_values=sorted(p.value for p in emitted_protocols),
                ),
            )

    # -- the signing family's relation rules (#1291 D1) ------------------------
    #
    # Ordered as the pin orders them, because the order decides WHICH field a
    # rejection names and the graded rows depend on the name:
    #   (a) namespace split          -- on RequestSigningPosture itself (inherited)
    #   (b) required_for  subset of supported_for  (both namespaces)
    #   (c) warn_for      subset of supported_for, disjoint from required_for
    #   (d) must_equal_when          -- needs platform state, see
    #                                   validate_signing_platform_backing
    #   (e) required_when            -- identity.brand_json_url obliged
    #   (f) brand_json_url ^https:// (pattern here, equality in the platform pass)
    #   (g) key_origins purpose_anchoring
    # (e) MUST pre-empt (g), or a declaration missing brand_json_url is rejected
    # naming key_origins instead of the field the operator has to add.

    def _reject(
        self,
        field: str,
        rule: str,
        *,
        rejected_value: str | list[str] | None = None,
        accepted_values: list[str] | None = None,
    ) -> None:
        """Raise the one error shape every rule above uses, naming *field*.

        THREE structured positions and no authored sentence, because
        ``AdCPSalesAgentError`` has no ``message=``: buyer-facing text is a read-only
        property over ``CODE_TABLE`` keyed by the code, identical for every refusal here.
        So the rule the operator broke travels in ``ConfigurationDetails.tracked_by``, the
        offending value in ``rejected_value`` and the permitted set in
        ``accepted_values`` — the pin's canonical rejection-set keys (v3.1.1
        ``core/error.json``), which is what lets a buyer's error classifier read this
        without per-seller pattern matching.

        ``field`` is passed to the EXCEPTION rather than copied into ``details``: it is a
        protocol top-level position on both envelope layers, and a duplicate key in
        ``details`` that merely repeats the field name is the exact defect
        ``ValueRejectionDetails`` was extracted to remove. *rule* therefore never
        interpolates the value either — that would be a second channel for
        ``rejected_value``.
        """
        from src.core.exceptions import AdCPConfigurationError

        qualified_field = f"capability_declarations.{field}"
        raise AdCPConfigurationError(
            field=qualified_field,
            details=ConfigurationDetails(
                rejected_value=rejected_value,
                accepted_values=accepted_values,
                tracked_by=rule,
            ),
        )

    def _validate_signing_relations(self) -> None:
        """Rules (b), (c), (e), (f-pattern) and (g) — everything with no DB read."""
        posture = self.request_signing
        if posture is not None:
            self._validate_bucket_monotonicity(posture)
        self._validate_identity_relations(posture)

    def _validate_bucket_monotonicity(self, posture: RequestSigningPosture) -> None:
        """``required_for``/``warn_for`` may only name what a DECLARED ``supported_for`` does.

        ``x-adcp-validation.subset_of`` on both namespaces: "an operation can't be
        required without being supported". The rule bites only where the operator actually
        WROTE the narrowing bucket, which is why the loop keys on ``model_fields_set``
        rather than on the value:

        * an ABSENT ``supported_for`` means "wherever a signature appears" — the same
          reading ``_bucket_for`` gives a null ``supported_for``, and the reading the pin's
          own graded corpus requires: ``negative/028-unsigned-protocol-method-required``
          declares ``protocol_methods_required_for: ["tasks/cancel"]`` and NO
          ``protocol_methods_supported_for``, and is graded as a LEGAL declaration whose
          unsigned request must be rejected. Keying on the value would have refused that
          declaration, because the SDK defaults that bucket to ``[]`` (not ``None``) and an
          empty list read as a narrowing forbids every required method.
        * an EXPLICIT ``supported_for: []`` alongside a non-empty ``required_for`` IS the
          contradiction the rule exists for, and is rejected.

        ``warn_for`` disjoint from ``required_for``: an operation cannot be both graded
        in shadow mode and rejected outright, and silently letting one win would enforce
        a rule the buyer was never told.
        """
        for subset_field, superset_field in (
            ("required_for", "supported_for"),
            ("protocol_methods_required_for", "protocol_methods_supported_for"),
            ("warn_for", "supported_for"),
        ):
            narrowed = getattr(posture, superset_field)
            if superset_field not in posture.model_fields_set or narrowed is None:
                continue
            extra = sorted(bucket_names(getattr(posture, subset_field)) - bucket_names(narrowed))
            if extra:
                self._reject(
                    f"request_signing.{subset_field}",
                    f"An operation cannot be required or warned on without being supported: every "
                    f"name here must also appear in "
                    f"capability_declarations.request_signing.{superset_field} "
                    f"(get-adcp-capabilities-response.json x-adcp-validation.subset_of).",
                    rejected_value=extra,
                    accepted_values=sorted(bucket_names(narrowed)),
                )

        for warn_field, required_field in (
            ("warn_for", "required_for"),
            ("protocol_methods_warn_for", "protocol_methods_required_for"),
        ):
            both = sorted(bucket_names(getattr(posture, warn_field)) & bucket_names(getattr(posture, required_field)))
            if both:
                self._reject(
                    f"request_signing.{warn_field}",
                    f"An operation is graded in shadow mode or rejected outright, never both: "
                    f"capability_declarations.request_signing.{required_field} names it too.",
                    rejected_value=both,
                )

    def _validate_identity_relations(self, posture: RequestSigningPosture | None) -> None:
        """Rules (e), (f-pattern) and (g) — the trust-root pointer's obligations.

        ``webhook_signing.supported`` is one of the six ``required_when`` triggers but is
        DERIVED, so it cannot be evaluated here; the declaration-time half covers the
        four ``request_signing`` bucket triggers, and the derived trigger is checked in
        :meth:`validate_signing_platform_backing`. That split is why a keyed tenant that
        declares nothing at all is still obliged to emit ``brand_json_url`` -- the
        obligation is on the EMITTED document, and emission derives it.
        """
        declared_url = str(self.identity.brand_json_url) if self.identity and self.identity.brand_json_url else None

        # (e) required_when. ``identity: {}`` alongside a posture is rejected as missing
        # brand_json_url, not accepted as "an identity block was supplied" -- that is
        # prose in the identity object's own description rather than a schema keyword,
        # so it is ours to implement.
        if posture is not None and requires_trust_root(posture, webhook_signing_supported=False) and not declared_url:
            self._reject(
                "identity.brand_json_url",
                "Required when request_signing names any operation or protocol method: a "
                "counterparty resolves this agent's signing keys through the brand.json served "
                "there, so a posture with no trust-root pointer cannot be verified "
                "(get-adcp-capabilities-response.json x-adcp-validation.required_when).",
                # The names that FIRED the trigger, so the operator can either add the
                # pointer or drop them. Exactly the four buckets
                # ``request_signing_buckets_declared`` reads -- ``warn_for`` is not a
                # trigger in either namespace, and listing it here would name operations
                # that did not oblige anything.
                rejected_value=sorted(
                    bucket_names(posture.supported_for)
                    | bucket_names(posture.required_for)
                    | bucket_names(posture.protocol_methods_supported_for)
                    | bucket_names(posture.protocol_methods_required_for)
                ),
            )

        # (f) pattern. The SDK types brand_json_url as a bare AnyUrl -- the schema's
        # ``pattern: "^https://"`` was dropped in generation, so enforcing it is ours.
        # security.mdx restates it normatively: a non-HTTPS value is rejected with
        # request_signature_brand_json_url_missing. A host that cannot serve https
        # therefore cannot carry a declared signing posture at all, which is the honest
        # answer -- a trust root nothing can fetch is not a trust root.
        if declared_url is not None and not declared_url.startswith("https://"):
            self._reject(
                "identity.brand_json_url",
                "Must be an https:// URL. The pinned schema fixes the pattern to ^https:// and a "
                "verifier rejects anything else with request_signature_brand_json_url_missing, so "
                "a trust root served over plain HTTP anchors nothing.",
                rejected_value=declared_url,
            )

        # (g) purpose_anchoring: "every entry listed MUST have a corresponding signing
        # posture declared elsewhere". Checked AFTER required_when so a declaration
        # missing the pointer is told to add the pointer.
        self._validate_key_origin_anchoring(posture)

    def _validate_key_origin_anchoring(self, posture: RequestSigningPosture | None) -> None:
        """Rule (g) — a declared ``key_origins`` entry needs the posture that anchors it.

        ``#/properties/identity/properties/key_origins/x-adcp-validation
        .verifier_constraints.purpose_anchoring``, restated normatively in security.mdx:
        without the posture "the consistency check at signature-verification time has
        nothing to anchor against".

        ``webhook_signing`` is NOT checked here: its anchor
        (``webhook_signing.supported === true``) is derived, so it belongs to the
        platform pass. ``governance_signing`` needs governance in
        ``supported_protocols``; ``tmp_signing`` needs a non-empty
        ``trusted_match.surfaces``.
        """
        origins = self.identity.key_origins if self.identity else None
        if origins is None:
            return

        anchors: list[tuple[Any, str, bool, str]] = [
            (
                origins.request_signing,
                "request_signing",
                posture is not None and request_signing_buckets_declared(posture),
                "request_signing must name at least one operation or protocol method",
            ),
            (
                origins.governance_signing,
                "governance_signing",
                SupportedProtocol.governance in (self.supported_protocols or []),
                "supported_protocols must include governance",
            ),
            (
                origins.tmp_signing,
                "tmp_signing",
                bool(self.trusted_match and self.trusted_match.surfaces),
                "trusted_match.surfaces must be non-empty",
            ),
        ]
        for value, purpose, anchored, requirement in anchors:
            if value is not None and not anchored:
                self._reject(
                    f"identity.key_origins.{purpose}",
                    f"Declares an origin with no posture to anchor it: {requirement}. Without the "
                    f"posture the verifier's origin-separation check has nothing to compare against "
                    f"(x-adcp-validation.verifier_constraints.purpose_anchoring).",
                    rejected_value=str(value),
                )

    def _validate_reporting_delivery_methods(self) -> None:
        """``[webhook]`` is backed; any list containing ``offline`` is not (#1729)."""
        declared = self.reporting_delivery_methods or []
        if ReportingDeliveryMethod.offline in declared:
            self._reject(
                "reporting_delivery_methods",
                "No bucket report delivery is implemented, so a buyer polling an offline "
                "destination would find nothing there. Tracked by #1729. 'webhook' delivery is "
                "backed and may be declared.",
                rejected_value=[ReportingDeliveryMethod.offline.value],
                accepted_values=[ReportingDeliveryMethod.webhook.value],
            )

    def validate_signing_platform_backing(self, platform: SigningPlatformBacking) -> None:
        """Rules (d) and (f-equality) — the two that need resolved platform state.

        Split from :meth:`validate_backing` rather than folded into it because the OTHER
        caller of the store is ``posture_for_tenant``, which ``_resolve_identity`` runs on
        every AdCP request and must not pay for a signing-key read plus a tenant read to
        answer a question that cannot change its decision. The capabilities read path owns
        a unit of work already and calls this inside it.
        """
        # (d) must_equal_when: declaring any webhook-emitting surface forces
        # ``webhook_signing.supported == true``. Checked against the DERIVED value, so a
        # keyless tenant declaring webhook report delivery is REFUSED rather than
        # resolved by lying in either direction -- neither silently promoting the
        # derivation nor silently dropping the declaration.
        #
        # The pin lists three triggers; only ``reporting_delivery_methods`` is declarable
        # here. ``content_standards.supports_webhook_delivery`` and
        # ``wholesale_feed_webhooks`` are in ``_UNBACKED_BLOCKS``, so they are
        # structurally absent rather than unchecked -- the loop is written over all three
        # so that un-gating either one lands the rule with it.
        triggers = {
            "reporting_delivery_methods": ReportingDeliveryMethod.webhook in (self.reporting_delivery_methods or []),
            "content_standards.supports_webhook_delivery": False,
            "wholesale_feed_webhooks.supported": False,
        }
        fired = sorted(name for name, present in triggers.items() if present)
        if fired and not platform.webhook_signing_supported:
            self._reject(
                "webhook_signing.supported",
                "Must be true because the blocks named in rejected_value declare webhook delivery, but "
                "this tenant has no ACTIVE signing key it can open on a trust root it can publish, so "
                "this agent derives false (x-adcp-validation.must_equal_when). Provision a signing "
                "key, or drop the webhook delivery declaration.",
                rejected_value=fired,
            )

        # (f) equality. The verifier byte-matches the origin our keys resolved at against
        # the one we published, so a declared pointer that differs from the derived one is
        # a request_signature_key_origin_mismatch by construction -- and the emitted value
        # is always the derived one, which would make the declaration silently inert.
        declared_url = str(self.identity.brand_json_url) if self.identity and self.identity.brand_json_url else None
        if declared_url is not None and declared_url.rstrip("/") != platform.brand_json_url.rstrip("/"):
            self._reject(
                "identity.brand_json_url",
                "Must equal the URL this agent actually serves its brand.json at, which is the one "
                "entry in accepted_values. A counterparty byte-matches the origin it resolved a key "
                "at against the one we published, so a second value is a "
                "request_signature_key_origin_mismatch waiting to happen.",
                rejected_value=declared_url,
                accepted_values=[platform.brand_json_url],
            )

    def emitted_specialisms(self, defaults: list[AdcpSpecialism]) -> list[AdcpSpecialism]:
        """Defaults UNIONED with the declaration -- same rule as protocols."""
        return _union_sorted(defaults, self.specialisms)

    def emitted_supported_protocols(self, defaults: list[SupportedProtocol]) -> list[SupportedProtocol]:
        """Defaults UNIONED with the declaration -- never replaced.

        Replacing would drop ``media_buy`` and leave the unconditionally-emitted
        ``sales-non-guaranteed`` specialism without its parent protocol, which the
        spec forbids: #/properties/specialisms -- "the runner rejects a specialism
        claim whose parent protocol is missing"
        (``specialisms/sales-non-guaranteed/index.yaml#protocol`` -> ``media-buy``).
        A replacing semantics would therefore emit a self-inconsistent wire.
        """
        return _union_sorted(defaults, self.supported_protocols)

    def emitted_experimental_features(self) -> list[ExperimentalFeature] | None:
        """Feature ids DERIVED from the declared experimental blocks.

        Derivation, not echo. ``#/properties/experimental_features`` obliges an
        agent implementing an experimental surface to list its id, so the ids
        follow from which blocks were declared -- a tenant cannot declare
        ``measurement`` and omit ``measurement.core``, and cannot invent an id for
        a block it did not declare.

        There is deliberately NO declarable ``experimental_features`` field. The
        one scenario that would grade an operator-supplied list
        (``T-UC-010-v31-experimental-features``) demands ``brand.rights_lifecycle``
        -- an unbacked surface re-homed to #1724 -- so a declarable half would ship
        with no grader, which this codebase does not allow.
        """
        ids = sorted(
            feature_id
            for block, feature_id in _EXPERIMENTAL_FEATURE_BY_BLOCK.items()
            if getattr(self, block) is not None
        )
        return [ExperimentalFeature(root=i) for i in ids] or None


# Every key in the table must name a real declarable block. With the previous
# `getattr(self, block, None)` a key that no longer matched a field simply read as
# "not declared", so renaming a block would silently stop emitting its experimental
# feature id -- a wire regression with nothing to fail. Checked at import so the
# mismatch is a startup error, not a quietly shorter list on a buyer's response.
_UNKNOWN_BLOCKS = sorted(set(_EXPERIMENTAL_FEATURE_BY_BLOCK) - set(CapabilityDeclarations.model_fields))
if _UNKNOWN_BLOCKS:
    raise RuntimeError(
        f"_EXPERIMENTAL_FEATURE_BY_BLOCK names blocks that are not fields of "
        f"CapabilityDeclarations: {_UNKNOWN_BLOCKS}. Their experimental feature ids would "
        f"never be emitted."
    )
