"""Format resolution with product overrides and dynamic creative agent discovery.

Provides layered format lookup:
1. Product-level overrides (from product.implementation_config.format_overrides)
2. Dynamic format discovery from creative agents (via CreativeAgentRegistry)

Note: Tenant custom formats (creative_formats table) were removed in favor of
creative agent-based format discovery per AdCP v2.4.
"""

import json
import logging
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from adcp.canonical_formats import (
    CANONICAL_CREATIVE_AGENT_URL,
    format_is_supported,
    load_default_registry,
    project_v1_format_to_declaration,
    structural_match,
    upgrade_legacy_format_id,
)
from adcp.types.domains.core.format_id import FormatReferenceStructuredObject as LibraryFormatId
from adcp.types.domains.core.package_format_snapshot import PackageFormatSnapshot as LibraryPackageFormatSnapshot
from adcp.types.domains.core.product_format_declaration import (
    ProductFormatDeclaration as LibraryFormatDeclaration,
)
from adcp.types.domains.core.requirements.daast_asset_requirements import (
    DaastAssetRequirements as LibraryDaastAssetRequirements,
)
from adcp.types.domains.core.requirements.vast_asset_requirements import (
    VastAssetRequirements as LibraryVastAssetRequirements,
)
from pydantic import TypeAdapter, ValidationError

from src.core.database.database_session import get_db_session
from src.core.errors.details import (
    CapabilityRefusalDetails,
    EntityRefDetails,
    FormatDeclarationDivergenceDetails,
    FormatOptionDivergenceSummary,
    PlatformExtensionCollisionDetails,
)
from src.core.exceptions import AdCPCapabilityNotSupportedError, AdCPFormatNotFoundError, AdCPSalesAgentError
from src.core.schemas import (
    CreativeFormatIdentity,
    Format,
    FormatKey,
    FormatOptionIdentity,
    LibraryCreativeAsset,
    canonical_agent_url,
    creative_format_identity,
    format_id_identity,
    format_key_parts,
)
from src.core.security.outbound_http import UrlProvenance
from src.core.signing.canonical import TargetUriMalformedError
from src.core.validation_helpers import run_async_in_sync_context

# What callers may hand the identity helpers: a structured reference, a bare
# dict off the wire, or a legacy string id.
FormatRef = str | LibraryFormatId | Mapping[str, Any]

#: The pinned snapshot union, built once. ``core/package-format-snapshot.json`` generates
#: the same sixteen-arm discriminated union over ``format_kind`` that
#: ``ProductFormatDeclaration`` does -- it is an ``allOf`` over that schema -- so
#: validating a declaration's document into THIS adapter is what applies the snapshot's own
#: conjuncts (the digest/contract pairing rules) rather than only the declaration's.
_PACKAGE_FORMAT_SNAPSHOT: TypeAdapter[LibraryPackageFormatSnapshot] = TypeAdapter(LibraryPackageFormatSnapshot)


logger = logging.getLogger(__name__)


# ── Format identity and kind: asked here, never re-decided at a call site ─────
#
# Two format references name the same format when they share a federation
# identity — the ``(canonical agent_url, id)`` pair ``format_id_identity``
# computes. Call sites used to compare with ``==`` on the model, which is
# Pydantic's STRUCTURAL equality — same fields AND same class. That works only
# while every producer happens to build the same concrete class, an invariant
# nothing declared and one transport quietly broke: the local ``FormatId``
# subclass (schemas/_base.py, four convenience methods, zero extra fields) never
# compares equal to the library value every other producer makes, so a lookup
# keyed on ``==`` missed on A2A and the whole agent-dial branch was skipped in
# silence.
#
# Identity is (agent_url, id) per the pinned core/format-id.json, and
# ``src.core.schemas.format_id_identity`` is the ONE implementation of it in
# this codebase — including the spec-mandated canonicalization of agent_url,
# which it delegates to the SDK. Nothing here re-implements that; these are
# thin, named delegations so there is ONE answer to "which catalogue entry does
# this name?" (:func:`same_format`) instead of one per call site.
#
# Three questions, three functions, and none answers another's:
# * IDENTITY -- the same (agent_url, id), canonicalized: :func:`same_format`.
#   This is the catalogue lookup key and the stored comparison key.
# * EQUIVALENCE -- the same canonical shape once both sides are projected through
#   the mapping registry (canonical-formats.mdx § Format matching vs product
#   satisfaction, v3.2.7): :func:`equivalent_formats`, built on
#   :func:`canonical_projection`. The deprecated filters answer this one.
# * Directional SUPPORT -- whether a product accepts a request: the SDK's, via
#   :func:`format_accepted_by`, and ``format_satisfaction``'s legacy route.


def format_ref_id(ref: FormatRef) -> str | None:
    """The bare ``id`` of a format reference, whatever shape it arrives in.

    A ``format_id`` reaches this module as a structured model, as a wire dict,
    or as a legacy bare string, depending on which producer built it — so
    reaching for ``.id`` works only until it does not. Asking here keeps that
    fact in ONE place instead of at each call site, which is the same mistake
    the ``==`` comparisons made about class identity.
    """
    if isinstance(ref, str):
        return ref
    if isinstance(ref, Mapping):
        value = ref.get("id")
        return str(value) if value is not None else None
    value = getattr(ref, "id", None)
    return str(value) if value is not None else None


def format_identity(ref: FormatRef) -> FormatKey:
    """The federation identity of a format reference, in any shape it arrives in.

    Delegates the rule itself to ``src.core.schemas.format_id_identity`` — the
    single place allowed to decide what "the same format" means — and adds only
    the shape normalization: a wire dict is validated into the library model, and
    a bare id is namespaced to the canonical creative agent (the same default the
    SDK applies when a reference omits ``agent_url``).

    That default is why this is the WRONG helper for an unscoped "search every
    agent for this id" lookup: namespacing a bare string silently narrows the
    search to the reference agent. Match on :func:`format_ref_id` there instead —
    ``get_format``'s no-``agent_url`` branch does exactly that.
    """
    return format_id_identity(format_reference(ref))


def format_reference(ref: FormatRef) -> LibraryFormatId:
    """A format reference as the validated MODEL, in any shape it arrives in.

    The shape normalization :func:`format_identity` used to do inline, lifted out because
    two questions are asked of a reference and only one of them wants the identity KEY.
    "Which catalog format does this name?" is answered by the key, which drops the
    parameters on purpose (see :func:`get_format`). "Does this conform to what the product
    sells?" is answered by the reference itself, parameters included -- a template pinned to
    300x250 is not the one pinned to 728x90.

    Returning the model rather than re-validating per caller is what keeps the second
    question from growing its own normalization: the ``agent_url`` default and the wire-dict
    validation are stated once, here.
    """
    if isinstance(ref, str):
        return LibraryFormatId.model_validate({"agent_url": CANONICAL_CREATIVE_AGENT_URL, "id": ref})
    if isinstance(ref, Mapping):
        return LibraryFormatId.model_validate({"agent_url": CANONICAL_CREATIVE_AGENT_URL, **ref})
    return ref


#: The canonical parameters a legacy reference's projection is compared on: the dimensions
#: and the fixed duration, which are what a v1 reference itself can state
#: (``core/format-id.json`` ``width`` / ``height`` / ``duration_ms``) and what
#: canonical-formats.mdx § Format matching vs product satisfaction (v3.2.7) compares
#: ("``width``, ``height``, ``duration_ms_exact``"), in the declaration's vocabulary. A
#: projection can state more (``slots``, ``pixel_ratios``, ``asset_source`` for the
#: reference agent's density variants); those are a declaration's manifest obligations,
#: graded against a manifest, and are deliberately not compared between two references here.
#:
#: KNOWN CEILING: density. The reference agent's ``display_300x250_image_2x`` projects, id
#: alone, to an image at 300x250 with no ``pixel_ratios`` (only its served declaration states
#: ``[2]``), so on the deprecated ``format_ids`` filters it compares equal to the 1x id. The
#: upgrade path is projecting with the served declaration (``spec``) at those readers and
#: adding ``pixel_ratios`` here, with a reference's ``pixel_ratio`` read as that one ratio.
_PROJECTED_SHAPE_PARAMS = ("width", "height", "duration_ms_exact")


@dataclass(frozen=True, slots=True)
class CanonicalProjection:
    """The canonical shape a deprecated ``format_id`` projects to: its kind and stated params."""

    kind: str
    params: dict[str, Any]


def canonical_projection(ref: FormatRef, *, spec: Format | None = None) -> CanonicalProjection:
    """The canonical declaration a deprecated ``format_id`` projects to: kind AND params.

    docs/creative/canonical-formats.mdx § Format matching vs product satisfaction (v3.2.7):
    a legacy ``format_id`` "resolves through the explicit ``canonical`` annotation,
    ``v1_format_ref``, or the canonical mapping registry to a canonical declaration ...
    After that projection, implementations compare the canonical shape and parameters".
    :func:`canonical_kind_for` documents the order; this is the same projection with the
    parameters kept, so a reader comparing shapes and a reader asking for the kind cannot
    project one reference two ways.

    The params come from two places and both are needed, measured on the pin: the SDK's
    size upgrade moves ``display_300x250_image``'s 300x250 onto the upgraded REFERENCE and
    projects ``image`` with no params, while an id the glob maps without an upgrade
    (``video_30s``, ``video_pre_roll_30s``) keeps its reference bare and states
    ``duration_ms_exact: 30000`` on the DECLARATION. What the reference states wins over
    what the registry derives, since the reference is what the sender said.

    THE ID PROJECTS ONLY AT THE REFERENCE AGENT (:func:`names_reference_agent`). The
    registry's ``format_id_glob`` entries and the SDK's size upgrade are written in that
    agent's id namespace, and the SDK matches the glob on the id without reading the
    ``agent_url`` half, so handed ``display_300x250`` at any other agent it answered
    ``image`` 300x250 for a format that agent never declared. Another agent's id is that
    agent's own: it projects only through the agent's served declaration (*spec*), and with
    none it has no projection (:func:`_agent_declared_projection`). That holds when the
    agent serves a declaration with no ``canonical`` too: its id is still not looked up in
    the globs, and only the declaration's slot shape (registry step 4) can project it. A URL
    the canonicalization refuses names no agent, so it has none either.

    Raises:
        ValueError: when no step resolves, as :func:`canonical_kind_for` does.
    """
    if not names_reference_agent(ref):
        if spec is None:
            raise ValueError(
                f"no canonical projection for legacy format_id {format_ref_id(ref)!r}: the mapping registry projects "
                "an id alone only at the reference creative agent"
            )
        return _agent_declared_projection(ref, spec)
    upgraded = upgraded_reference(ref)
    declaration = spec.model_copy(update={"format_id": upgraded}) if spec is not None else {"format_id": upgraded}
    projected = project_v1_format_to_declaration(declaration).declaration
    if projected is None:
        raise ValueError(
            f"no canonical projection for legacy format_id {format_ref_id(ref)!r}: the registry's resolution order "
            "fails closed for it"
        )
    derived = projected.params or {}
    params = {name: derived[name] for name in _PROJECTED_SHAPE_PARAMS if derived.get(name) is not None}
    return CanonicalProjection(kind=str(projected.format_kind), params={**params, **_stated_shape_params(upgraded)})


def _agent_declared_projection(ref: FormatRef, spec: Format) -> CanonicalProjection:
    """Another agent's legacy id, projected through that agent's served declaration alone.

    registries/v1-canonical-mapping.json (3.2.7) "Resolution order", with step 3 left out:
    (2) "if the v1 format declaration carries an explicit ``canonical`` field, use it",
    else (4) the registry's ``structural`` entries matched against the declaration's asset
    types -- "the PRIMARY fallback for v1 wire traffic -- catches custom v1 formats (a
    publisher's ``acme_homepage_300x250`` is structurally an IAB MREC)" -- else (6) fail
    closed. Step 3, the ``format_id_glob`` lookup, is the reference agent's id namespace
    (:func:`canonical_projection`), so a peer's ``video_30s`` with a ``url`` slot is a
    ``display_tag`` by its shape, not a ``video_hosted`` by its name.

    The SDK's ``project_v1_format_to_declaration`` runs step 3 between the two and reads no
    ``agent_url``, so it cannot be asked for this order. Steps 2 and 4 are therefore read
    here, with the SDK's own ``structural_match`` over its bundled registry, in the
    registry's entry order as the SDK takes them. Every constraint a structural entry
    declares is handed over -- the slot's asset types and its VAST and DAAST versions
    (:func:`_declared_versions`) -- because the registry matches that mode "against the
    format's slot shape, asset types, and version constraints", and three of its seven
    structural entries require a version. The params are only what the reference states:
    every compared param a registry entry derives (``width``, ``height``,
    ``duration_ms_exact``) comes from a glob entry, never a structural one.

    Raises:
        ValueError: when neither step resolves.
    """
    params = _stated_shape_params(canonically_addressed(ref))
    if spec.canonical is not None:
        return CanonicalProjection(kind=str(spec.canonical.kind), params=params)
    # A repeatable group carries no ``asset_type`` of its own; the SDK's step 4 skips it too.
    asset_types = list(
        dict.fromkeys(
            name for asset in spec.assets or [] if isinstance(name := getattr(asset, "asset_type", None), str)
        )
    )
    vast_versions, daast_versions = _declared_versions(spec)
    for mapping in load_default_registry().mappings:
        structural = getattr(mapping.v1_pattern, "structural", None)
        if structural is not None and structural_match(
            asset_types=asset_types,
            vast_versions=vast_versions or None,
            daast_versions=daast_versions or None,
            pattern=structural,
        ):
            return CanonicalProjection(kind=str(mapping.v2.canonical), params=params)
    raise ValueError(
        f"no canonical projection for legacy format_id {format_ref_id(ref)!r}: the agent's declaration carries no "
        "canonical and its slots match no structural entry"
    )


def _declared_versions(spec: Format) -> tuple[list[str], list[str]]:
    """The VAST and DAAST versions *spec*'s slots declare, for the structural entries that require one.

    The pinned ``Format`` carries a version only in a slot's ``requirements``
    (``vast_version`` / ``vast_versions``, ``daast_version`` / ``daast_versions``); it
    declares no top-level version member, so a slot is the only place to read one. The SDK's
    own step 4 collects the same keys, but only from a dict, so handed this model it finds
    none and its VAST and DAAST structural entries never match
    (adcontextprotocol/adcp-client-python#1750).
    """
    vast: list[str] = []
    daast: list[str] = []
    for asset in spec.assets or []:
        requirements = getattr(asset, "requirements", None)
        if isinstance(requirements, LibraryVastAssetRequirements):
            stated = [requirements.vast_version, *(requirements.vast_versions or [])]
            vast.extend(str(version) for version in stated if version is not None)
        elif isinstance(requirements, LibraryDaastAssetRequirements):
            listed = requirements.daast_versions.root if requirements.daast_versions is not None else []
            daast.extend(str(version) for version in [requirements.daast_version, *listed] if version is not None)
    return vast, daast


def _stated_shape_params(reference: LibraryFormatId) -> dict[str, Any]:
    """The comparison params *reference* states itself, in the declaration's vocabulary.

    A reference's one fixed ``duration_ms`` is a declaration's ``duration_ms_exact``
    (canonical-formats.mdx: "``duration_ms_exact`` for a fixed required duration").
    """
    stated = {"width": reference.width, "height": reference.height, "duration_ms_exact": reference.duration_ms}
    return {name: value for name, value in stated.items() if value is not None}


def registry_addressed(ref: FormatRef) -> LibraryFormatId:
    """*ref* canonically addressed, with the reference creative agent spelled as the registry spells it.

    The SDK's size upgrade (``upgrade_legacy_format_id``) recognizes the reference agent by
    comparing the whole canonical URL, path included, so the agent mounted at ``/mcp`` was
    not recognized and ``display_468x60_image`` there was not upgraded. An agent's identity
    is its origin (:func:`names_reference_agent`), so a reference naming the reference agent
    at any path is handed to the SDK at :data:`CANONICAL_CREATIVE_AGENT_URL`: recognition and
    the upgrade then follow the one origin rule. For a comparison only, as
    :func:`canonically_addressed` is.
    """
    reference = canonically_addressed(ref)
    if names_reference_agent(reference):
        return reference.model_copy(update={"agent_url": CANONICAL_CREATIVE_AGENT_URL})
    return reference


def upgraded_reference(ref: FormatRef) -> LibraryFormatId:
    """*ref* after the SDK's registry upgrade, addressed by :func:`registry_addressed` first.

    ``display_728x90_image`` at the reference agent, in any spelling of it, upgrades to
    ``display_image`` + 728x90; another agent's id is returned as that agent stated it.
    """
    return upgrade_legacy_format_id(registry_addressed(ref))


def canonical_kind_for(ref: FormatRef, *, spec: Format | None = None) -> str:
    """The canonical ``format_kind`` a deprecated ``format_id`` projects to.

    THE ONE direction that resolves. ``registries/v1-canonical-mapping.json`` is
    "authoritative for v1 -> v2 projection only", and the reverse is forbidden outright:
    a seller MUST NOT synthesize a v1 ``format_id`` by inverting a structural match,
    because the registry's id slugs carry no ``agent_url`` half and a glob entry has no
    single literal to invert to. That asymmetry is why the canonical kind -- not the
    legacy reference -- is the identity everything inside this seller shares.

    THE ORDER IS THE SDK'S, not this module's. The registry's normative "Resolution order"
    (registries/v1-canonical-mapping.json @ 3.2.7) has six steps. Step 1 -- a product
    declaration whose ``v1_format_ref`` names this id -- is a property of a PRODUCT, so the
    package route applies it before it gets here (``format_satisfaction._legacy_route``) and
    a creative write has no product to consult. Steps 2 to 6 are what
    ``adcp.canonical_formats.project_v1_format_to_declaration`` implements, in order, and
    its module docstring says so:

    2. the v1 format declaration's own ``canonical`` annotation;
    3. the registry's ``format_id_glob`` entries;
    4. the registry's ``structural`` entries, matched against the declaration's asset types
       -- a family-level identification the SDK flags ``FORMAT_DECLARATION_V1_AMBIGUOUS``
       because it cannot be inverted (step 5), which is no obstacle in this direction;
    6. fail closed.

    This module used to carry its own copy of steps 2 and 3 and stopped there, so a served
    format with no annotation and no glob hit but a registry-matchable shape was refused
    where the normative order projects it (salesagent-ol033.20.24).

    The declaration is the fetched *spec* when the caller has one, else the reference alone,
    so steps 2 and 4 have nothing to read and only the glob can answer -- which it does only
    for a reference naming the reference agent (:func:`canonical_projection`). At any other
    agent step 3 is not run even with a declaration: steps 2 and 4 read the agent's own
    declaration and nothing else (:func:`_agent_declared_projection`), so what follows is
    the reference agent's order. THE SDK IS HANDED THE UPGRADED ID, once, before the order runs. ``display_300x250_image`` is not
    spelled like any glob, but it is a member of the ``display_image`` mapping: the SDK's
    ``upgrade_legacy_format_id`` rewrites it to that template (salesagent-iz7dm). Upgrading
    only after a failed projection let step 4 answer first whenever a declaration was
    supplied -- a served v1 image format with no ``canonical`` has the standard ``image`` +
    ``url`` shape, so the structural ``{asset_types: [url]}`` entry projected it to
    ``display_tag`` before the glob that names it ``image`` was consulted, and the kind
    differed with and without the declaration. Normalizing first is what keeps step 3 ahead
    of step 4, and keeps the id-only answer and the declared answer the same wherever the
    declaration adds nothing a higher step reads.

    THE REFERENCE IS CANONICALLY ADDRESSED BEFORE IT IS UPGRADED, for the reason
    :func:`format_accepted_by` gives: the SDK upgrades a size-encoded id only for the
    reference agent, and recognizes that agent by a comparison applying four of the eight
    steps of docs/reference/url-canonicalization. Handed the raw reference, a respelling of
    the reference agent's URL with a trailing root-label dot or a removable dot segment was
    not upgraded, so ``display_468x60_image`` there failed closed while the same id at the
    canonical spelling projected to ``image`` (salesagent-ol033.20.23). The reference agent
    at another path is the same agent too, so :func:`registry_addressed` spells it as the
    registry does before the upgrade.

    :func:`canonical_projection` is the projection itself; this is its kind.

    Args:
        ref: the deprecated reference, in any shape :func:`format_identity` accepts.
        spec: the fetched format declaration, when the caller already has it. Passing it
            is what makes steps 2 and 4 reachable.

    Returns:
        A ``format_kind`` from the pinned vocabulary.

    Raises:
        ValueError: when no step resolves. The caller decides how to refuse -- this
            module does not mint an error code for a projection it could not make.
    """
    return canonical_projection(ref, spec=spec).kind


def creative_identity_of(creative: LibraryCreativeAsset, *, spec: Format | None) -> CreativeFormatIdentity:
    """A creative's format identity, with a legacy reference PROJECTED to its canonical kind.

    THE ONE edge where the two identities meet. ``creative_format_identity`` reads whichever
    arm the creative carries; this wraps it with the only direction that resolves, so every
    layer past here keys on ``identity.kind`` instead of branching on which arm arrived.

    A projection that does not resolve is a REFUSAL, and this is the one place it is raised.
    ``format_kind`` is the canonical identifier every layer past here keys on, so a creative
    whose reference has no canonical kind has no identity to store -- and the column is
    ``NOT NULL`` precisely so that it cannot be stored anyway. Gating here rather than at
    each write site is the root-cause shape: this function is the ONE edge the two identities
    meet at, its three callers are two write paths and a factory, and a guard per caller
    would leave the next caller to rediscover the rule.

    THIS PARAGRAPH USED TO SAY THE OPPOSITE -- that a ``None`` kind is "NOT a refusal",
    because ``core/creative-asset.json`` keeps ``format_id`` a supported branch through 4.x
    and refusing it "would refuse spec-legal traffic". That argument confuses the branch being
    legal with every id on it being SERVABLE. The pin permitting a buyer to send a legacy
    reference says nothing about this seller being able to resolve one it has no canonical
    mapping for: ``registries/v1-canonical-mapping.json`` is authoritative for v1 -> v2 and an
    id absent from it, whose fetched declaration also carries no ``canonical``, HAS no
    canonical kind anywhere. A seller that cannot serve a named format refuses it, which is
    exactly what the pinned enum classifies ``UNSUPPORTED_FEATURE`` as -- ``correctable``,
    "remove unsupported fields" -- leaving the buyer the lever to resend under a ref the
    seller does serve. Storing the row with ``kind=NULL`` instead was the defect
    (salesagent-jzhea): it admitted a creative no later layer could key on, and the write
    succeeded silently.

    Args:
        creative: the pinned sync input, or this repo's DTO subclass of it. Typed rather than
            duck-read: both declare the two arms, so ``format_id`` is a plain attribute and
            mypy holds every caller to a real model instead of to whatever answers.
        spec: the fetched format declaration, when the caller already has it. Passing it is
            what makes the agent's own explicit projection (registry step 2) and the
            structural match (step 4) reachable; without it only the glob (step 3) is
            consulted, and only for a reference at the reference agent.
    """
    format_id = creative.format_id
    if format_id is None:
        # The canonical arm already carries the kind; there is nothing to project and no
        # optional local to carry a ``None`` through. ``creative_format_identity`` re-reads
        # the arm and refuses an out-of-vocabulary value on construction.
        return creative_format_identity(creative, kind=str(creative.format_kind))
    try:
        kind = canonical_kind_for(format_id, spec=spec)
    except ValueError as exc:
        # ``from exc``: the boundary attaches the traceback when an error has a __cause__, so
        # the unresolvable id's own ValueError is on the record without any raise site
        # authoring a sentence about it.
        raise AdCPCapabilityNotSupportedError(
            details=CapabilityRefusalDetails(
                format_id=str(getattr(format_id, "id", format_id)),
                creative_id=creative.creative_id,
                capability="format_kind projection",
            )
        ) from exc
    return creative_format_identity(creative, kind=kind)


def same_format(left: FormatRef, right: FormatRef) -> bool:
    """Whether two format references name the same format.

    Deliberately NOT ``==``: that compares Python classes as well as values.
    Deliberately not a hand-rolled string compare either — the pinned schema
    makes canonicalizing ``agent_url`` before treating two references as one a
    MUST, and :func:`format_identity` is where that happens.
    """
    return format_identity(left) == format_identity(right)


def format_identity_or_none(entry: Any) -> FormatKey | None:
    """The federation identity of a format reference, or None if it is not one.

    Two things separate this from :func:`format_identity`. It absorbs the ONE
    shape variance a persisted reference has — ``Product.format_ids`` is
    ``list[FormatId]`` in the schema but a JSON ``list[dict]`` in the column,
    and rows predating the rename key the id as ``format_id`` rather
    than ``id`` — and it answers ``None`` instead of raising when the value is
    not a usable reference at all. A ``None`` field is read as ABSENT, so a
    reference carrying ``agent_url: None`` takes the same canonical-agent
    default a reference omitting the key does.

    ``None`` is the right answer here because both callers act on it correctly
    without a second code path: a product entry that yields None declares no
    restriction, and a creative that yields None matches no product entry,
    which is exactly "this format is not one the product accepts". Raising
    would turn one corrupt row into a 500 on a buyer's request.

    Asking here is what lets a call site pose the identity question instead of
    re-deciding what "the same agent_url" means. The three sites that check a
    creative against its product each grew a private ``normalize_url`` doing a
    bare ``rstrip("/")`` and, in two of them, an unmandated
    ``removesuffix("/mcp")`` that collapsed one host's MCP and A2A endpoints
    into a single agent. The pinned ``core/format-id.json`` requires the AdCP
    canonical form before two references may be treated as the same, and
    :func:`format_identity` is the one place that applies it.
    """
    # The absent-field and legacy-key handling applies to the persisted dict
    # shape only. A model already carries a validated ``agent_url`` and ``id`` (both
    # required on the library type), so it is handed on as the model it is and never
    # dumped here — business logic does not serialize a model to inspect it.
    reference = format_reference_or_none(entry)
    return None if reference is None else format_id_identity(reference)


def format_reference_or_none(entry: Any) -> LibraryFormatId | None:
    """A STORED format reference as the validated model, or None if it is not one.

    The stored-shape absorption and the fail-soft that :func:`format_identity_or_none`
    documents, stopping one step earlier so a caller can have the reference rather than its
    key. Everything that docstring says about ``None`` and about absent fields applies here
    and is not restated.

    An entry whose ``agent_url`` the canonicalizer refuses is dropped here too, the way
    :func:`format_identity_or_none` already drops it when it computes the key: kept, it
    would reach every comparison against the product as a reference no agent can match.
    """
    try:
        ref: Any = entry
        if isinstance(ref, Mapping):
            ref = {k: v for k, v in ref.items() if v is not None}
            if "id" not in ref and "format_id" in ref:
                ref["id"] = ref["format_id"]
            ref.pop("format_id", None)
            if "id" not in ref:
                return None
        reference = format_reference(ref)
        canonical_agent_url(reference.agent_url)  # TargetUriMalformedError is a ValueError
        return reference
    except (ValidationError, ValueError, TypeError, AttributeError):
        logger.warning("Unusable stored format reference, ignored: %r", entry)
        return None


def product_format_identities(format_ids: Iterable[Any] | None) -> set[FormatKey]:
    """The federation identities a product's declared ``format_ids`` accept.

    Empty means the product imposes no format restriction — the same reading
    an empty column has, and the reading every caller already applied.

    These are TEMPLATE keys and are the wrong value to decide ACCEPTANCE with: two products
    restricting to different sizes of one template produce the identical set, so the
    restriction is not merely unread but unrepresentable. :func:`product_format_references`
    is the acceptance side. This one remains correct for the filter sites, which ask which
    catalog format a reference names.
    """
    identities = (format_identity_or_none(entry) for entry in format_ids or [])
    return {identity for identity in identities if identity is not None}


def product_format_references(format_ids: Iterable[Any] | None) -> tuple[LibraryFormatId, ...]:
    """The format references a product's declared ``format_ids`` accept, parameters included.

    The ACCEPTANCE half of what :func:`product_format_identities` asks, and separate from it
    because the pin makes them different questions.
    ``core/product-format-declaration.json``, under "Format matching vs satisfaction
    (normative)": "do not exact-match raw ``(agent_url, id)`` pairs once a ``format_id`` has
    been projected", and "when this declaration specifies fixed constraints such as
    ``width``, ``height`` ... a buyer request or creative manifest MUST declare and satisfy
    those constraints".

    A TUPLE and not a set: a reference is a pydantic model whose equality folds in the
    parameters, so a set would be well-defined but would also silently collapse nothing and
    buy nothing. Order is the stored order, which is what a refusal lists back to the buyer.
    """
    references = (format_reference_or_none(entry) for entry in format_ids or [])
    return tuple(reference for reference in references if reference is not None)


def product_format_kinds(format_options: Iterable[Any] | None) -> set[str]:
    """The canonical format kinds a product's declared ``format_options`` accept.

    The CANONICAL half of the question :func:`product_format_identities` asks of the
    legacy half, and placed beside it so the two halves of one filter question stay
    together rather than one living in whichever tool first needed it.

    Empty means the product declares no canonical kind. That reading differs from the
    legacy half's deliberately: ``product_format_identities`` treats empty as "imposes
    no format restriction", because a product with no ``format_ids`` used to mean a
    product that had not narrowed itself. A product with no ``format_options`` has not
    declared a KIND, and ``core/product-filters.json`` asks for "products accepting any
    of these canonical format kinds" -- a product declaring none accepts none of them,
    so it is not selected. Reading empty as "matches everything" here is precisely the
    bug ``standard_formats_only`` had: an absent arm silently satisfied every filter.

    ``str()`` on the kind because it arrives as a ``StrEnum`` member on the model and as
    a plain string in a filter. The two compare equal today because the generated type
    is a ``StrEnum``; normalizing both sides means that stays true if it ever is not.
    """
    return {str(declaration.format_kind) for declaration in format_options or []}


def platform_extension_collisions(
    extensions: Iterable[Any] | None,
    definitions: Mapping[str, Any] | None,
) -> list[PlatformExtensionCollisionDetails]:
    """Per-field overlaps between a declaration's ``platform_extensions``, and who wins each.

    ``formats/canonical/_base.json``'s ``platform_extensions`` (3.2.3) states the rule: when
    two or more entries "extend the same target ... with overlapping field names, **array
    order is authoritative -- later entries override earlier ones on a per-field basis**
    (last-in-array-wins)".

    THE PRECEDENCE IS COMPUTED, NOT APPLIED. Nothing here merges the extensions, and that is
    the pin's instruction rather than a shortcut: the same paragraph ends "the deterministic
    precedence is for last-resort consistency across SDK implementations, **not a sanctioned
    merging strategy**". A seller that merged colliding extensions would be implementing the
    thing the pin declines to sanction. What a producer owes is to SAY the overlap happened --
    "``error.details: { collision_kind, target, overlapping_fields, winning_extension_uri }``"
    -- and the buyer resolves it with the stated order.

    *definitions* is a parameter and not a lookup because the collision is NOT DECIDABLE FROM
    THE DECLARATION. ``core/platform-extension-ref.json`` requires ``uri`` + ``digest`` and
    declares nothing else, while the target (``extends``) and the field names
    (``fields.properties``) are properties of the definition -- carried in
    ``get-products-response.json``'s bundled ``extensions`` map, keyed ``<uri>@<digest>``. A
    reference whose definition is absent is therefore left alone rather than guessed at: the
    advisory's every key would be invented. That is the state this seller is in for EVERY
    reference today, because nothing populates that map (salesagent-hff54's deferred half).

    An identical ``uri@digest`` twice contributes nothing, and the digest is the proof: equal
    digests mean the same document, so last-in-array-wins selects a value identical to the one
    it displaced. A buyer can observe no divergence, and an advisory reporting one would be
    reporting a conflict that does not exist. The SAME uri at two DIFFERENT digests does
    collide -- one extension, two versions.

    Returns one record per (target, winner) rather than per target: precedence is per-field, so
    partially-overlapping extensions can each win different fields of the same target, and the
    pin's own shape pairs plural ``overlapping_fields`` with a singular
    ``winning_extension_uri``.
    """
    # Keyed by uri@digest, so the same document referenced twice resolves to one entry and
    # drops out of the overlap by construction.
    resolved: dict[str, tuple[str, frozenset[str]]] = {}
    order: list[str] = []
    for reference in extensions or ():
        uri, digest = _reference_member(reference, "uri"), _reference_member(reference, "digest")
        if uri is None or digest is None:
            continue
        key = f"{uri}@{digest}"
        definition = (definitions or {}).get(key)
        target, fields = _definition_target_and_fields(definition)
        if target is None:
            continue
        if key not in resolved:
            resolved[key] = (target, fields)
            order.append(key)

    winners: dict[tuple[str, str], str] = {}
    for key in order:
        target, fields = resolved[key]
        for field in fields:
            winners[(target, field)] = key

    # A field is only overlapping if two or more DISTINCT documents declare it.
    overlapping: dict[tuple[str, str], str] = {
        (target, field): winner
        for (target, field), winner in winners.items()
        if sum(1 for key in order if resolved[key][0] == target and field in resolved[key][1]) > 1
    }

    grouped: dict[tuple[str, str], list[str]] = {}
    for (target, field), winner in overlapping.items():
        grouped.setdefault((target, winner), []).append(field)

    return [
        PlatformExtensionCollisionDetails(
            target=target,
            overlapping_fields=sorted(fields),
            winning_extension_uri=resolved_uri_of(winner),
        )
        for (target, winner), fields in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1]))
    ]


def bundled_extension_definitions() -> Mapping[str, Any]:
    """The platform-extension definitions this seller holds, keyed ``<uri>@<digest>``.

    THE ONE SEAM, and it is empty. ``get-products-response.json``'s ``extensions`` map is the
    pin's named resolution source -- "lets buyers resolve ``platform_extensions`` references on
    product format declarations without a separate fetch" -- and this seller stores no extension
    definitions, so it has nothing to put there. The map is a MAY ("subsequent get_products
    responses MAY omit definitions the buyer already has cached"), so serving none is conforming;
    what it costs is that :func:`platform_extension_collisions` can decide nothing, since the
    target and field names it compares are the definition's.

    Filling this is the deferred half of ``salesagent-hff54`` and it is a design choice, not a
    gap to patch here: either a definition store carrying the pin's full fetch contract (the SSRF
    allowlist, response-size cap and ``$ref`` sandbox documented on
    ``product-format-declaration.json#format_schema``), or resolution per request through
    ``src/core/security/outbound_http.py``. The SDK's own
    ``CanonicalReferenceResolver.resolve_platform_extension`` does the single-reference half
    correctly but fetches with its own ``httpx`` client and its own address checks, which is the
    one thing critical pattern #9 reserves to this repo's gateway.

    A function rather than a constant so the seam has a name, one caller, and a docstring saying
    what feeding it turns on.
    """
    return {}


def declaration_platform_extensions(declaration: Any) -> list[Any]:
    """One declaration's ``platform_extensions`` references.

    They live on ``params`` rather than on the declaration itself -- the field is declared by
    ``formats/canonical/_base.json``, which types each arm's ``params`` -- so 15 of the 16 arms
    carry it and ``custom`` does not. Read as a document or through ``getattr``, because a
    declaration arrives as a validated arm from the wire and as a stored document from the
    database.
    """
    params = declaration.get("params") if isinstance(declaration, Mapping) else getattr(declaration, "params", None)
    if params is None:
        return []
    extensions = (
        params.get("platform_extensions")
        if isinstance(params, Mapping)
        else getattr(params, "platform_extensions", None)
    )
    return list(extensions or ())


def resolved_uri_of(key: str) -> str:
    """The ``uri`` half of a ``<uri>@<digest>`` extensions-map key.

    Split from the RIGHT: the digest is ``@``-prefixed and a URI may itself contain ``@``
    (userinfo), so ``partition`` would cut the wrong one.
    """
    return key.rpartition("@")[0]


def _reference_member(reference: Any, name: str) -> str | None:
    """One member of a platform-extension reference, as an object or as a document."""
    value = _member(reference, name)
    return str(value) if value is not None else None


def _definition_target_and_fields(definition: Any) -> tuple[str | None, frozenset[str]]:
    """An extension definition's ``extends`` target and the field names it contributes.

    ``get-products-response.json``'s ``extensions`` values require both ``extends`` and
    ``fields``, the latter being "a JSON Schema fragment declaring the additional fields this
    extension contributes" -- so the names are the fragment's ``properties`` keys. A
    definition missing either is treated as no definition at all, for the same reason an
    absent one is: a target this seller cannot read is a target it cannot say was shared.
    """
    if definition is None:
        return None, frozenset()
    extends = definition.get("extends") if isinstance(definition, Mapping) else getattr(definition, "extends", None)
    fields = definition.get("fields") if isinstance(definition, Mapping) else getattr(definition, "fields", None)
    properties = fields.get("properties") if isinstance(fields, Mapping) else None
    if not extends or not isinstance(properties, Mapping):
        return None, frozenset()
    return str(extends), frozenset(str(name) for name in properties)


def product_format_option_identities(format_options: Iterable[Any] | None) -> set[FormatOptionIdentity]:
    """The format-option identities a product's declarations name.

    The third reader beside :func:`product_format_identities` (legacy arm) and
    :func:`product_format_kinds` (canonical kind), and the finest-grained of them:
    ``core/product-filters.json`` asks ``format_option_refs`` for products accepting "any of
    these EXACT publisher- or product-scoped canonical format options", so the comparison is
    whole identities and never their parts.

    :class:`FormatOptionIdentity` already owns the scope distinction, and owns it the way the
    pin defines it: the two arms of ``core/format-option-ref.json`` make ``publisher_domain``
    required on one and forbidden on the other, so the domain's PRESENCE is the discriminator
    and a publisher-catalog option and a product-local option sharing one
    ``format_option_id`` are DIFFERENT options. Constructed directly here rather than through
    ``of_reference`` because a declaration carries no ``scope`` member to read -- the
    declaration schema's own allOf says ``publisher_domain`` "is addressable only alongside
    ``format_option_id``" -- so presence is the only signal there is, which is what that
    identity derives its scope from anyway.

    A declaration naming NO option is skipped rather than given an empty identity. A bare
    canonical declaration (``format_kind`` + ``params``, no option id) narrows a kind without
    naming a catalogued option, so it accepts none of the refs this filter can name.
    """
    identities: set[FormatOptionIdentity] = set()
    for declaration in format_options or []:
        option_id = declaration.format_option_id
        if not option_id:
            continue
        identities.add(
            FormatOptionIdentity(
                publisher_domain=declaration.publisher_domain,
                format_option_id=option_id,
            )
        )
    return identities


def format_display(identity: FormatKey) -> str:
    """``<canonical agent_url>/<id>`` — how a format identity is spelled to a buyer.

    The identity, not the raw reference: an error that lists "supported
    formats" spelled differently from the values actually compared reads as a
    contradiction to whoever has to act on it.

    The single-slash join is DISPLAY ONLY and never a comparison. A canonical
    agent_url whose path is empty ends in ``/`` (the algorithm's step 5), so a
    naive ``f"{url}/{id}"`` renders ``https://x.org//display_300x250`` for the
    common case. Collapsing that pair of slashes makes two identities that
    differ ONLY by a trailing slash on a non-empty path (``/a`` vs ``/a/``,
    which the spec keeps distinct) render alike; that is accepted here because
    this string is read by a human deciding what to resubmit, and nothing
    parses it back.

    THE ONE reader of a key's halves (via ``format_key_parts``), and it is a renderer:
    splitting a key anywhere else is how ``id``-alone matching gets reintroduced.
    """
    agent_url, format_id = format_key_parts(identity)
    separator = "" if agent_url.endswith("/") else "/"
    return f"{agent_url}{separator}{format_id}"


@dataclass(frozen=True, slots=True)
class CanonicalFormats:
    """A product whose format contract is its canonical declarations.

    ``core/product.json`` on ``format_ids``: "New products MUST author canonical
    ``format_options[]``; sellers MAY additionally project those declarations to
    ``format_ids`` for legacy buyers." And canonical-formats.mdx § "Deprecated named-format
    compatibility projection (normative)", Producer rules: "Compatibility ``format_ids``
    MUST be derived from them rather than maintained as a second authored catalog."

    So this type has no authored legacy column to read. ``format_ids`` is computed from the
    declarations alone: the ``v1_format_ref`` entries they assert, deduplicated on the
    canonical identity, in declared order. Nothing is synthesized from a kind or a
    structural match, because ``registries/v1-canonical-mapping.json`` makes the registry
    "authoritative for v1 → v2 projection only" and the v2 → v1 direction "MUST rely on
    ``v1_format_ref``". A product whose declarations assert none has no legacy projection,
    which is the pin's MAY declined for that product.

    An asserted reference the declaration does not NARROW is left out ("A producer that
    cannot guarantee agreement MUST omit the legacy compatibility projection"), and
    :func:`format_declaration_divergence` reports it as ``FORMAT_DECLARATION_DIVERGENT``. A
    stored ``format_ids`` column beside the declarations (a row authored before the canonical
    arm existed, then given one) is not consulted at all.
    """

    format_options: tuple[Mapping[str, Any], ...]

    @property
    def format_ids(self) -> tuple[LibraryFormatId, ...]:
        """The legacy projection the declarations assert, empty when none asserts one."""
        return tuple(ref for _declaration, ref in _asserted_refs(self.format_options))


@dataclass(frozen=True, slots=True)
class NamedFormats:
    """A product that carries only the deprecated arm, as it was authored.

    The "Named-format product" branch of ``core/product.json``'s root ``anyOf``. It is
    ``deprecated: true`` and new products must not be authored this way, but the 3.x
    window still admits it ("Products MUST carry format_ids, format_options, or both"), so
    a pre-existing legacy-only row is served as it is. Here ``format_ids`` is the product's
    only catalog, not a compatibility projection beside a canonical one.

    An empty ``format_ids`` means the product declares neither arm; the converter refuses
    that state, and the satisfaction gates read it as "no restriction".
    """

    format_ids: tuple[Any, ...]
    format_options: None = None


#: A product's format contract: canonical declarations (with a derived legacy projection),
#: or the deprecated arm alone. Both expose ``format_options`` and ``format_ids`` with the
#: meaning the wire Product's fields have.
FormatContract = CanonicalFormats | NamedFormats


def format_contract(*, format_options: Iterable[Any] | None, format_ids: Iterable[Any] | None) -> FormatContract:
    """The format contract a product carries, given the arms it resolved.

    The canonical arm wins whenever it is present: the legacy arm is then derived from it
    and *format_ids* is never read. Only a product with no canonical declarations serves
    its authored legacy arm.
    """
    declarations = tuple(format_options or ())
    if declarations:
        return CanonicalFormats(format_options=declarations)
    return NamedFormats(format_ids=tuple(format_ids or ()))


def linked_format_contract(
    *, profile: Any | None, format_options: Iterable[Any] | None, format_ids: Iterable[Any] | None
) -> FormatContract:
    """The contract a product carries given its own arms and the inventory profile it links.

    A linked *profile* supplies the contract, as it supplies the publisher properties; the
    product's own arms answer only when it links none. The one exception is a profile with
    no canonical arm beside a product that has one: the product's declarations are then
    the canonical source, because the pin makes ``format_options`` the source of truth and
    a legacy-only profile has none to offer.

    ``Product.format_contract`` asks this of a stored row; the admin authoring surface asks
    it of a product it has not written yet, so the two cannot disagree about which arm a
    profile-linked product is served.
    """
    return format_contract(
        format_options=(profile.format_options if profile else None) or format_options,
        format_ids=profile.format_ids if profile else format_ids,
    )


def package_format_snapshot(
    declaration: Mapping[str, Any] | LibraryFormatDeclaration,
    *,
    product_id: str,
) -> LibraryPackageFormatSnapshot:
    """The immutable package-time record of ONE selected product format declaration.

    ``core/package-format-snapshot.json`` is "an immutable package-time snapshot of one
    selected Product format declaration. Product-format fields remain flattened at the
    root for 3.x compatibility" — structurally it is ``allOf`` over
    ``core/product-format-declaration.json`` plus a handful of package-only bindings, and
    the pinned type is the same sixteen-arm discriminated union over ``format_kind``. So
    the snapshot is BUILT BY VALIDATING the declaration's own document into that union
    with ``product_id`` added, not by copying field by field: a per-field copy is a second
    list of what a declaration carries, and it goes stale the next time the pin adds a
    parameter.

    ``product_id`` is the one binding this seller can make. The pin requires it "whenever
    ``tracker_execution_contract`` is present" and pairs it with
    ``product_snapshot_digest``; neither of the other bindings is authored here.
    ``placement_refs`` is deliberately absent, and the pin says what that means — "omission
    binds the product-wide common effective contract; it never means an inferred list of all
    current placements" — which is exactly true of this seller, whose packages bind no
    placement subset. ``product_snapshot_digest`` is likewise absent rather than computed:
    it is "required for every contract-bearing snapshot", and a snapshot carrying no
    ``tracker_execution_contract`` is not one; the pin's own conjunct forbids the sibling
    digest without the contract.

    WHY THE SHAPE IS A CONTRACT rather than an implementation detail: an adapter reads the
    selected declaration's ``params`` off this snapshot (``salesagent-ol033.8``), and the
    whole point of a snapshot is that "the live Product is never substituted for the
    package snapshot" (``core/package.json`` on ``formats_to_provide``).

    THE SECOND UNION IS NOT STRICTER THAN THE DTO'S, and that is MEASURED rather than
    argued — the lesson of salesagent-1d5z4, where the product read path, by
    validating a stored declaration into a differently-generated type
    (``canonical_creative.Format``) answered INTERNAL_ERROR for every product of a tenant
    because that type refuses a ``custom`` declaration the DTO accepts (salesagent-1d5z4).
    Validating into a second generated union here is the same move, so it is measured on
    the wheel the application runs:

    * DERIVED over all sixteen arm pairs, not spot-checked: for every ``format_kind``,
      ``PackageFormatSnapshot``'s required-field set is a SUBSET of the matching
      ``ProductFormatDeclaration`` arm's. No kind exists in one union and not the other.
    * The exact document that caused that incident -- ``{format_kind: "custom", params:
      {}}``, no ``format_shape`` -- is accepted by BOTH unions and refused only by
      ``canonical_creative.Format``.
    * The snapshot schema's one EXTRA written conjunct (a ``tracker_execution_contract``
      requires ``format_option_id``, ``product_id``, ``tracker_execution_contract_digest``
      and ``product_snapshot_digest``) is NOT implemented by the generated union: a
      declaration carrying a valid contract validates with ``product_id`` alone. So the
      conjunct cannot refuse what this function supplies.

    If a pin bump ever makes the snapshot union refuse a declaration the DTO accepted, the
    answer is the one that function reached and NOT a try/except here: stop validating into
    the second type and read the fields off the stored document. Absorbing the error would
    serve a package with no snapshot at all, which ``core/package.json`` forbids ("MUST
    retain and return this checklist") and which stage 7's adapters read params off.

    Raises:
        pydantic.ValidationError: only for a document the DTO's own union would also
            refuse — i.e. a corrupt row, which ``format_satisfaction.product_declarations``
            has already dropped before a declaration can be SELECTED and reach here.
    """
    document = declaration if isinstance(declaration, Mapping) else dict(declaration)
    return _PACKAGE_FORMAT_SNAPSHOT.validate_python({**document, "product_id": product_id})


def snapshot_legacy_arm(snapshots: Iterable[LibraryPackageFormatSnapshot]) -> tuple[LibraryFormatId, ...]:
    """The deprecated references *snapshots* project to — the SELLER-ASSERTED ones only.

    ``core/package.json`` on ``format_ids_to_provide``: "**DEPRECATED in 3.2.** Legacy
    named-format projection of ``formats_to_provide`` retained for older 3.x peers. New
    sellers emit canonical ``formats_to_provide`` declarations." So the legacy array is a
    PROJECTION OF THE SNAPSHOT, which is what makes it derived here rather than echoed
    from the buyer's request — a buyer that named only a canonical route used to receive
    nothing at all on this field, which is a readiness checklist and whose absence the pin
    gives a defined meaning to (``format_ids_pending``: "Absence means legacy readiness was
    not reported and MUST NOT be interpreted as full coverage").

    A READ AND NEVER A COMPUTATION. ``registries/v1-canonical-mapping.json`` is normative
    that "this registry is authoritative for v1 → v2 projection only. v2 → v1 projection
    MUST rely on ``v1_format_ref`` on the v2 ``ProductFormatDeclaration`` — sellers assert
    the v1 pairing explicitly. SDKs MUST NOT synthesize a v1 ``format_id`` from the
    registry by inverting structural matches." So the only source is the declaration's own
    ``v1_format_ref``, and a snapshot asserting none projects to nothing — the array is
    then ABSENT. The product read path derives ``Product.format_ids`` by the same rule
    (:class:`CanonicalFormats`), so a package's legacy checklist and the product's legacy
    arm cannot name different formats for one declaration.

    Duplicate-free and order-preserving on the canonical identity, because the array is a
    checklist: two snapshots asserting one reference ask for one creative, not two.

    FOR THE WIRE, AND NOT FOR AN ADAPTER. The references are returned AS AUTHORED, which
    means without dimensions -- measured: ``v1_format_ref: [{id: display_300x250, width:
    None, height: None}]``. That is right for the wire field, whose job is to name formats,
    and wrong for an adapter carrier, whose job is to be sufficient to place a line item.
    :func:`snapshot_adapter_format_ids` is the carrier's projection for that reason.
    """
    return tuple(ref for _snapshot, ref in _asserted_refs(snapshots))


def _asserted_refs(holders: Iterable[Any]) -> tuple[tuple[Any, LibraryFormatId], ...]:
    """Each seller-asserted ``v1_format_ref``, paired with the declaration that asserts it.

    The ref-reading rule lives HERE once -- read ``v1_format_ref`` and nothing else, and
    deduplicate on the canonical ``(agent_url, id)`` identity -- because three consumers need
    it: a product's derived legacy arm (:class:`CanonicalFormats`), a package's wire field
    (:func:`snapshot_legacy_arm`), and an ADAPTER carrier that wants each ref carrying the
    selected declaration's dimensions (:func:`snapshot_adapter_format_ids`). Pairing rather
    than returning bare refs is what lets the last one reach the right snapshot's ``params``
    when a package records several.

    *holders* are declarations as stored documents (a product's column) or as validated
    models (a package's snapshots), so each member is read either way. A stored reference
    goes through :func:`format_reference_or_none`, which absorbs the persisted shape and
    drops an unusable entry rather than raising -- one malformed ref must not take a
    tenant's catalogue down.

    ``canonical_formats_only`` short-circuits because the pin makes the two mutually
    exclusive ("a declaration can EITHER assert no v1 projection OR link to v1 named
    formats, never both"), so a document carrying both is malformed and its opt-out is the
    half that is safe to honour: treating it as asserting nothing cannot invent a link.

    A reference its declaration does not NARROW is not projected (:func:`_referenced`), so
    none of the three consumers can name a format the declaration is not.
    """
    return _unique((holder, ref) for holder, ref, narrows in _referenced(holders) if narrows)


def _referenced(holders: Iterable[Any]) -> Iterator[tuple[Any, LibraryFormatId, bool]]:
    """Each usable ``v1_format_ref`` entry, its declaration, and whether the declaration narrows it.

    ``core/product-format-declaration.json`` on ``v1_format_ref`` (3.2.7): "The v2
    declaration's ``params`` MUST narrow (be compatible with) each referenced v1 format's
    ``requirements``". A declaration of a 300x250 image asserting ``display_728x90_image``
    breaks that MUST, and serving the reference would be the dual emission
    canonical-formats.mdx calls "a contract violation": "A producer that cannot guarantee
    agreement MUST omit the legacy compatibility projection." The verdict is
    ``format_satisfaction.declaration_narrows``, the rule the legacy selector route applies.
    """
    from src.core.format_satisfaction import declaration_narrows

    for holder in holders:
        refs = _member(holder, "v1_format_ref")
        if (
            _member(holder, "canonical_formats_only")
            or not isinstance(refs, Iterable)
            or isinstance(refs, str | bytes | Mapping)
        ):
            continue
        for entry in refs:
            ref = format_reference_or_none(entry)
            if ref is not None:
                yield holder, ref, declaration_narrows(_member(holder, "format_kind"), _member(holder, "params"), ref)


def _member(holder: Any, name: str) -> Any:
    """One member of a declaration held as a stored document or as a validated model."""
    return holder.get(name) if isinstance(holder, Mapping) else getattr(holder, name, None)


def _unique(pairs: Iterable[tuple[Any, LibraryFormatId]]) -> tuple[tuple[Any, LibraryFormatId], ...]:
    """*pairs* deduplicated on the reference's canonical ``(agent_url, id)`` identity, in order."""
    seen: set[FormatKey] = set()
    unique: list[tuple[Any, LibraryFormatId]] = []
    for holder, ref in pairs:
        identity = format_id_identity(ref)
        if identity not in seen:
            seen.add(identity)
            unique.append((holder, ref))
    return tuple(unique)


def format_declaration_divergence(
    product_id: str, format_options: Iterable[Any] | None
) -> FormatDeclarationDivergenceDetails | None:
    """The ``FORMAT_DECLARATION_DIVERGENT`` details for a product, or ``None`` when it has none.

    The references its declarations assert and do not narrow -- the ones
    :class:`CanonicalFormats` left out of the legacy arm. ``enums/error-code.json`` (3.2.7):
    "a SELLER may self-detect on emit (own producer bug; rare)", and the declaration still
    carries the reference on the wire, so a buyer's SDK comparing the two shapes finds the
    same disagreement; saying so on ``errors[]`` is what keeps it observable rather than
    "silently picked-one-and-dropped-other".

    *format_options* are the declarations as served, models or stored documents alike.
    """
    declarations = tuple(format_options or ())
    referenced = tuple(_referenced(declarations))
    divergent = _unique((holder, ref) for holder, ref, narrows in referenced if not narrows)
    if not divergent:
        return None
    return FormatDeclarationDivergenceDetails(
        product_id=product_id,
        format_ids=[format_display(format_id_identity(ref)) for _holder, ref in divergent],
        format_options_summary=[
            FormatOptionDivergenceSummary(
                format_kind=str(_member(declaration, "format_kind")),
                v1_format_ref=[
                    format_display(format_id_identity(ref)) for holder, ref, _ in referenced if holder is declaration
                ],
            )
            for declaration in declarations
        ],
    )


def resolved_dimensions(
    ref: FormatRef,
    *,
    fallback: tuple[int | None, int | None, float | None] = (None, None, None),
) -> tuple[int | None, int | None, float | None]:
    """The width, height and duration *ref* delivers at, falling back to *fallback*.

    THE ONE RULE for "which dimensions apply to this format reference", because the two
    places that need it are one logical operation with one parameter substituted -- the
    FALLBACK SOURCE -- and not two policies:

    * ``_create_media_buy_impl``'s request path passes the PRODUCT's catalogue entry for the
      same federation identity, because a buyer may name a format without sizing it;
    * the approval path passes the recorded SNAPSHOT's ``params``, via
      :func:`snapshot_adapter_format_ids`.

    THE REFERENCE'S OWN DIMENSIONS WIN, and the fallback answers only when it does not state
    both. That reads as "always overwrite" on the approval path and as "the buyer's sizing is
    respected" on the create path, but it is the same sentence: an asserted ``v1_format_ref``
    carries no width or height at all (measured: ``{id: display_300x250, width: None, height:
    None}``), so the fallback is simply the only source there.

    Duration is resolved independently of the pair, because a video reference can carry a
    duration without a size and a fallback can supply one the reference lacks.

    WHAT THIS REPLACED, since the shape is the point: the create path had THREE branches
    constructing a ``FormatId``, and two of them -- "the request stated its dimensions" and
    "neither the request nor the product stated them" -- were character-for-character
    identical, both passing the request's own values. So the three-way branch expressed a
    two-way decision and the duplicate ratchet would have refused a fourth copy of it on the
    approval path (CLAUDE.md § DRY).
    """
    own = (getattr(ref, "width", None), getattr(ref, "height", None), getattr(ref, "duration_ms", None))
    if own[0] is not None and own[1] is not None:
        return own

    width, height, duration = fallback
    if width is not None or height is not None:
        return width, height, duration if duration is not None else own[2]
    return own


def snapshot_adapter_format_ids(
    snapshots: Iterable[LibraryPackageFormatSnapshot],
) -> tuple[LibraryFormatId, ...]:
    """The snapshots as the deprecated references an ADAPTER carrier holds, with dimensions.

    RESTORED AFTER BEING DELETED PREMATURELY, and the reason is worth keeping because the
    mistake is easy to repeat. Stage 7 gave ``MediaPackage`` a ``formats_to_provide`` field
    and taught the GAM order manager to read ``params`` off the snapshot, then removed this
    projection on the reasoning that the canonical path now dominated it. It dominates it
    FOR GAM. The carrier goes to EVERY adapter, and GAM is the only one that reads the
    snapshot, so for the mock, Kevel, Triton and Broadstreet the legacy arm is still the only
    channel a dimension has. Approving a parked buy then handed the ad server
    ``[(None, None)]`` and the scenario that grades it went red on all four transports.

    So the ceiling below is NARROWER than before stage 7 rather than retired, and that
    correction matters: ``salesagent-ol033.8``'s commit claimed the ceiling went away with the
    deletion, which was true only of the one adapter that gained the better channel.

    The asserted refs themselves carry no width or height -- measured:
    ``v1_format_ref: [{id: display_300x250, width: None, height: None}]`` -- so without this
    merge an adapter would be handed a dimensionless format and would have to ask the live
    product, which is the substitution the snapshot exists to prevent.

    SELLER-ASSERTED ONLY, like its sibling: ``registries/v1-canonical-mapping.json`` makes
    ``v1_format_ref`` the one legal v2 -> v1 source and forbids synthesizing an id from the
    registry. A snapshot asserting no reference therefore projects to NOTHING here, and the
    caller decides what to do with that -- this function does not invent a name for a
    declaration the seller never paired with one.

    CEILING, still real and now narrower: a canonical-only declaration with no
    ``v1_format_ref`` cannot be expressed in this carrier at all. It no longer binds GAM,
    which reads ``formats_to_provide``. The upgrade path for the rest is the same one -- each
    adapter reads the snapshot -- and this projection can be deleted when the LAST of them
    does, not when the first does.
    """
    return tuple(
        _with_dimensions(
            ref,
            resolved_dimensions(
                ref,
                fallback=(
                    _plain_param(snapshot, "width"),
                    _plain_param(snapshot, "height"),
                    _plain_param(snapshot, "duration_ms"),
                ),
            ),
        )
        for snapshot, ref in _asserted_refs(snapshots)
    )


def _with_dimensions(ref: LibraryFormatId, dimensions: tuple[int | None, int | None, float | None]) -> LibraryFormatId:
    """*ref* carrying *dimensions*, as the same class it arrived as.

    ``model_copy`` rather than a constructor so the reference keeps its own type: the
    approval path is handed the pinned ``FormatReferenceStructuredObject`` off a snapshot and
    the local ``FormatId`` subclass refuses its own library parent on validation, which is
    the adoption trap recorded on ``package_format_snapshot``.
    """
    width, height, duration = dimensions
    return ref.model_copy(update={"width": width, "height": height, "duration_ms": duration})


def _plain_param(snapshot: LibraryPackageFormatSnapshot, name: str) -> Any:
    """One canonical param off a snapshot, or None when the declaration does not state it."""
    return getattr(snapshot.params, name, None)


def format_accepted_by(requested: FormatRef, supported: FormatRef) -> bool:
    """Whether *supported* satisfies a request for *requested*.

    Distinct from :func:`same_format`: support is directional (a parameterized
    supported format can satisfy a narrower request), which is why the SDK gives
    it its own predicate rather than reusing equivalence — and why this one
    delegates to ``adcp.canonical_formats.format_is_supported`` rather than to
    the identity pair, which cannot express direction.

    BOTH SIDES ARE CANONICALLY ADDRESSED FIRST, because the SDK's own agent_url
    comparison is not the pin's. ``core/format-id.json`` makes canonicalization
    per docs/reference/url-canonicalization a MUST before two references are
    treated as the same format, and that page says "Implementations MUST apply
    every step". The SDK's ``canonicalize_agent_url`` applies four of them
    (scheme and host case, userinfo, default port): measured on the pin, a
    trailing root-label dot (step 2) and a removable dot segment (step 5) each
    made one agent two. That also decided whether a legacy id was upgraded at
    all, because the SDK upgrades ``display_300x250_image`` only for the
    reference agent and recognizes the agent by that same partial comparison.
    ``canonical_agent_url`` applies all eight, and the SDK's four are idempotent
    over its output, so handing the SDK canonical spellings makes its comparison
    the spec's without re-implementing it.
    """
    return format_is_supported(canonically_addressed(requested), canonically_addressed(supported))


def canonically_addressed(ref: FormatRef) -> LibraryFormatId:
    """*ref* as a model whose ``agent_url`` is the canonical spelling of the same agent.

    For a COMPARISON only: a reference that is stored or echoed keeps the buyer's
    bytes (see :data:`src.core.schemas.FormatKey`). ``model_copy`` rather than a
    constructor, so the reference keeps its own type and every parameter it carries.

    An ``agent_url`` the algorithm refuses to canonicalize (``TargetUriMalformedError``,
    for example a host with two trailing dots, which ``AnyUrl`` accepts) is returned as
    sent. It cannot name the same agent as any well-formed URL, so the comparison fails
    closed and the caller answers its own correctable refusal, the policy #1291 set for a
    malformed agent_url, instead of the exception escaping as INTERNAL_ERROR.
    """
    reference = format_reference(ref)
    try:
        canonical = canonical_agent_url(reference.agent_url)
    except TargetUriMalformedError:
        return reference
    return reference.model_copy(update={"agent_url": canonical})


def equivalent_formats(left: FormatRef, right: FormatRef) -> bool:
    """Whether *left* and *right* name the same canonical format once both are normalized.

    The EQUIVALENCE relation of docs/creative/canonical-formats.mdx § Format matching vs
    product satisfaction (v3.2.7): "whether two declarations identify the same underlying
    creative shape after normalization", so ``display_300x250`` and
    ``display_300x250_image`` are one format although their ids differ. The satisfaction
    relation, which is directional, is :func:`format_accepted_by`.

    The spec's rule is to resolve each side "through ... the canonical mapping registry to a
    canonical declaration" and then "compare the canonical shape and parameters, not the raw
    ``(agent_url, id)`` pair". So each side is reduced to :func:`_comparison_shape`, and two
    shapes are equivalent when they name the same format and no parameter BOTH state
    differs: ``video_30s`` and ``video_pre_roll_30s`` both project to ``video_hosted`` at
    ``duration_ms_exact`` 30000, and ``display_static`` (``image``, no size) is the same
    shape as ``display_300x250_image`` with the size left open. The SDK's
    ``formats_are_equivalent`` is not this relation: it upgrades only the size-encoded
    display ids and then compares ids, so it answered "different" for all three pairs.

    Equivalence is symmetric and is not satisfaction: whether a product ACCEPTS a reference
    is :func:`format_accepted_by` and ``format_satisfaction``'s legacy route.
    """
    left_shape, right_shape = _comparison_shape(left), _comparison_shape(right)
    if left_shape is None or right_shape is None or left_shape[0] != right_shape[0]:
        return False
    left_params, right_params = left_shape[1], right_shape[1]
    return all(left_params[name] == right_params[name] for name in left_params.keys() & right_params.keys())


def _comparison_shape(ref: FormatRef) -> tuple[tuple[str, object], dict[str, Any]] | None:
    """What *ref* is compared on for equivalence: the format it names, and the params it states.

    At the reference agent, in any spelling of it, the format is the canonical KIND the
    registry projects (:func:`canonical_projection`): the owner's rule is that a legacy v1
    id maps to a kind only at the reference agent's origin, whose namespace the registry is
    written in. Anywhere else, or for an id the registry cannot project, the format is the
    reference's own federation identity (``core/format-id.json``: agent_url and id,
    canonicalized) and the params are the ones the reference states.

    ``None`` for an ``agent_url`` the canonicalization refuses: it names no agent, so it is
    equivalent to nothing.
    """
    if (projection := _reference_agent_projection(ref)) is not None:
        return ("kind", projection.kind), projection.params
    try:
        identity = format_identity(ref)
    except TargetUriMalformedError:
        return None
    return ("format", identity), _stated_shape_params(format_reference(ref))


def names_reference_agent(ref: FormatRef) -> bool:
    """Whether *ref* names the AdCP reference creative agent, compared by origin.

    The canonical mapping registry's ``format_id_glob`` entries and the SDK's size upgrade
    are written in that agent's id namespace, so it is the one agent whose legacy ids the
    registry can project without the agent's own declaration. An agent's identity is its
    origin (``creative_agent_registry.agent_origin``), so the path the reference agent is
    mounted at does not make it another agent. A URL the canonicalization refuses names no
    agent at all.
    """
    from src.core.creative_agent_registry import agent_origin

    try:
        return agent_origin(str(format_reference(ref).agent_url)) == agent_origin(CANONICAL_CREATIVE_AGENT_URL)
    except TargetUriMalformedError:
        return False


def registry_projects(ref: FormatRef) -> bool:
    """Whether the canonical mapping registry alone projects *ref* to a canonical kind.

    docs/creative/template-format-ids.mdx § Compatibility (v3.2.7): "Normalize them to
    canonical declarations immediately ... Do not ... treat a deprecated
    ``list_creative_formats`` response as a source of truth". So whether a legacy reference
    to the reference agent names a real format is answered by the registry
    (registries/v1-canonical-mapping.json, "authoritative for v1 -> v2 projection"), not by
    whether the agent's deprecated catalogue happens to list the id. ``display_468x60_image``
    is the case: the registry upgrades it to ``display_image`` 468x60, and the reference
    agent's catalogue does not carry it.

    Only for the reference agent (:func:`names_reference_agent`): the registry's ids are in
    its namespace, and another agent's id is that agent's own business.
    """
    return _reference_agent_projection(ref) is not None


def _reference_agent_projection(ref: FormatRef) -> CanonicalProjection | None:
    """The registry's projection of *ref*, or None.

    The id alone, so None for any agent but the reference agent (:func:`canonical_projection`),
    and None too when the registry fails closed for the id.
    """
    try:
        return canonical_projection(ref)
    except ValueError:
        return None


def find_format(formats: Iterable[Format], format_id: FormatRef) -> Format | None:
    """The format in *formats* that *format_id* names, or None.

    ONE definition of "is this the format the buyer asked for", so no caller can
    reintroduce a class-sensitive ``==`` against a registry listing. ``FormatId``
    exists twice -- the library type the AdCP schemas declare and our subclass --
    and pydantic v2 equality is class-sensitive, so comparing the models matched
    NOTHING whenever the two sides were built by different code paths
    (the A2A boundary built one, MCP and REST the other; #2093).

    Identity is ``format_id_identity``'s ``(canonical agent_url, id)``, the pair the
    graded contract matches on (``core/format-id.json`` requires ``[agent_url, id]``;
    the list_formats storyboard step uses ``match_keys: [agent_url, id]``) and the
    same pair ``list_creative_formats`` filters with. Notably that means a
    PARAMETERIZED reference resolves to the template it parameterizes -- which is
    the point of a template format, and what lets a 300x250 request find the
    ``display`` format's spec.

    PURE: no HTTP and no DB, so it is callable from inside a savepoint where
    ``get_format`` is not — the creative-sync paths pre-fetch their catalog
    outside the transaction for exactly that reason and then need to select from
    it without dialling anything.
    """
    wanted = format_identity(format_id)
    return next((fmt for fmt in formats if format_identity(fmt.format_id) == wanted), None)


def find_operator_format_of_kind(formats: Iterable[Format], kind: str) -> Format | None:
    """The ONE format the operator's creative agent declares as canonical *kind*, or None.

    The canonical counterpart of :func:`find_format`. A creative identified by
    ``format_kind`` names no format and so no agent; the agent that previews it is the one
    this deployment stands behind (``is_operator_agent``: CREATIVE_AGENT_ORIGIN, dialled at
    CREATIVE_AGENT_URL). Its catalogue's own ``canonical`` declarations are its claims about
    which kinds it renders -- docs/creative/specification.mdx (v3.2.7): creative agents "own
    claims about what they can build, validate, or preview".

    EXACTLY ONE, or None. creative/preview-creative-request.json (v3.2.7) lets a request omit
    ``target_capability_id`` and says the agent "MAY resolve the renderer only if exactly one
    advertised preview capability satisfies the manifest's canonical declaration; zero
    matches or multiple matches MUST be rejected with FORMAT_NOT_SUPPORTED". This seller does
    not read the agent's ``get_adcp_capabilities``, so it cannot name a capability; a kind
    the operator serves through several formats is therefore not one this seller can route
    a preview for, and it is treated like a format no agent is dialled for (BR-RULE-035
    INV-2: processed with no external validation and no preview fetched). DELIBERATE
    CEILING: selecting ``target_capability_id`` from the agent's advertised
    ``creative.supported_formats[]`` is the upgrade that would preview those kinds too.

    PURE, like :func:`find_format`: it reads the pre-fetched catalogue and nothing else.
    """
    from src.core.creative_agent_registry import is_operator_agent

    declaring = [
        fmt
        for fmt in formats
        if fmt.canonical is not None
        and str(fmt.canonical.kind) == kind
        and fmt.agent_url is not None
        and is_operator_agent(fmt.agent_url)
    ]
    return declaring[0] if len(declaring) == 1 else None


def is_agent_backed(fmt: Format) -> bool:
    """Whether this format is served by a creative agent.

    Asks the format, rather than probing it: ``fmt.agent_url`` is a declared
    property on the local Format (schemas/_base.py), not an attribute that may
    or may not be there. Compose with — do not duplicate —
    :func:`is_dialled_agent_url`: an adapter pseudo-URL is agent-backed but
    never dialled.
    """
    return fmt.agent_url is not None


def is_generative(fmt: Format) -> bool:
    """Whether this format is built by the agent rather than supplied whole.

    ``output_format_ids`` IS a declared field on ``adcp.types.Format``, so the
    ``getattr(fmt, "output_format_ids", None)`` guard call sites used protected
    against nothing while the sibling read of ``agent_url`` — the one that could
    actually surprise — went undefended.
    """
    return bool(fmt.output_format_ids)


def is_dialled_agent_url(agent_url: str) -> bool:
    """Whether *agent_url* names an endpoint we will actually dial over HTTP.

    False for an adapter-provided pseudo-URL like ``broadstreet://<tenant_id>``
    (advertised by ``creative_formats.py`` and the Broadstreet adapter): those
    formats are served by the adapter in-process, so there is no request to
    make, no address to judge, and an egress gate applied to one would refuse
    a format the SELLER published to the buyer.

    Shared by every format-fetch call site (``creatives/_validation.py``'s
    ingest gate, ``media_buy_create.py``'s pre-adapter validation and asset
    build) so the adapter-format exemption is decided once, here, rather than
    re-derived per call site.
    """
    return agent_url.startswith(("http://", "https://"))


def is_dialled_agent_format(fmt: Format) -> bool:
    """Whether a creative in this format is served by an agent we will DIAL.

    THE COMPOSITION :func:`is_agent_backed`'s docstring prescribes, in one place rather
    than as the same two-clause condition written at each call site. An adapter-served
    format is agent-backed -- it declares an ``agent_url`` -- and is never dialled,
    because that url is a pseudo-URL (``broadstreet://<tenant_id>``) with no host: the
    adapter serves the format in process.

    GATING ON ``is_agent_backed`` ALONE SENT THE SELLER AT ITS OWN PSEUDO-URL.
    ``sync_creatives``' agent-backed branch fetches a preview (or a generative build)
    from ``format_obj.agent_url``, so a resolved ``broadstreet_*`` format reached
    ``preview_creative("broadstreet://...")``, the egress seam refused the scheme, and
    the creative came back ``failed`` -- measured on box run innet_171026_2343, on
    e2e_rest only, because the in-process harness answers ``preview_creative`` with an
    ``AsyncMock`` and never dials (salesagent-zufx8). The branch was unreachable for such
    a format until an adapter declaration made one resolvable (salesagent-zthsk), which
    is why the hole outlived the docstring that named it.

    A format that fails this is still a format the seller SERVES. It is processed with no
    external validation and no preview fetched, which is what BR-RULE-035 INV-2 states.
    """
    return is_agent_backed(fmt) and is_dialled_agent_url(fmt.agent_url or "")


def fetch_format_spec(agent_url: str, format_id: str, *, provenance: UrlProvenance | None = None) -> Format | None:
    """Fetch one format spec from the creative-agent registry (sync bridge).

    THE single fetch path for format specs — create_media_buy,
    sync_creatives validation, and get_format all route through here so typed
    transient errors behave identically on every tool:

    - Typed ``AdCPSalesAgentError`` from the registry (429 -> AdCPRateLimitError,
      5xx/timeout/connect -> AdCPServiceUnavailableError) PROPAGATES: it carries
      its own recovery semantics, and swallowing it into ``None`` degrades a
      transient agent failure to a terminal "unknown format" rejection.
    - ``None`` means the agent genuinely doesn't expose the format (unknown-
      format semantics — the caller decides how to reject or fall back).
    - Untyped exceptions are logged and become ``None``: the registry types all
      its network errors, so an untyped one here is a programming surprise, not
      a transport signal.

    ``provenance`` passes straight through to the registry — a
    :class:`CounterpartyUrl` when a buyer's request document supplied
    ``agent_url``, optionally naming the request path; ``None`` for an
    operator-registered agent.
    """
    from src.core.creative_agent_registry import get_creative_agent_registry

    registry = get_creative_agent_registry()
    try:
        return run_async_in_sync_context(registry.get_format(agent_url, format_id, provenance=provenance))
    except AdCPSalesAgentError:
        raise
    except Exception as e:
        logger.warning(f"Could not fetch format {format_id} from {agent_url}: {e}")
        return None


def seller_format_catalogue(tenant_id: str) -> list[Format]:
    """Every format a reference this seller receives is resolved against.

    The dialled creative agents PLUS the formats the tenant's own ad-server adapter serves.
    The registry half is agents only -- an adapter-served format's ``agent_url`` is a
    pseudo-URL with no host to dial -- so a catalogue built from the registry alone made
    ``find_format`` answer None for every ``broadstreet_*`` reference, which left the
    projection with no ``canonical`` declaration to read and refused the reference
    (salesagent-zthsk). ``list_creative_formats`` reads its definitions from the same two
    parts and lists only those a product declares, so a format that compatibility list
    names is a format a buyer may NAME -- on a creative and on a package alike.

    Fetched once per request and outside any transaction by its callers: the registry's
    async HTTP must not run inside a database savepoint.
    """
    from src.core.creative_agent_registry import get_creative_agent_registry
    from src.core.helpers.adapter_helpers import adapter_served_creative_formats

    registry = get_creative_agent_registry()
    return [
        *run_async_in_sync_context(registry.list_all_formats(tenant_id=tenant_id)),
        *adapter_served_creative_formats(tenant_id),
    ]


def get_format(
    format_id: str,
    agent_url: str | None = None,
    tenant_id: str | None = None,
    product_id: str | None = None,
    *,
    provenance: UrlProvenance | None = None,
) -> Format:
    """Resolve format with priority: product override → creative agent discovery.

    Args:
        format_id: Format identifier (e.g., "display_300x250_image")
        agent_url: Optional creative agent URL (defaults to AdCP standard agent)
        tenant_id: Optional tenant ID for agent lookup
        product_id: Optional product ID for product-level overrides
        provenance: Whose URL ``agent_url`` is, forwarded to
            :func:`fetch_format_spec` unchanged. A caller re-dialling a
            buyer-supplied URL it already fetched once (e.g. a fallback path)
            MUST pass the same ``CounterpartyUrl`` it used the first time —
            omitting it silently reclassifies the same URL as operator
            configuration and routes it off the egress seam entirely.

    Returns:
        Format object with all configuration

    Raises:
        AdCPFormatNotFoundError: If format_id not found in any source
    """
    # Check product override first
    if product_id and tenant_id:
        override = _get_product_format_override(tenant_id, product_id, format_id, agent_url=agent_url)
        if override:
            return override

    # Get from creative agent registry
    from src.core.creative_agent_registry import get_creative_agent_registry

    registry = get_creative_agent_registry()

    # If agent_url provided, get format directly from that agent
    # Coerce to str: FormatId.agent_url is Pydantic AnyUrl (not a str subclass)
    if agent_url:
        fmt = fetch_format_spec(str(agent_url), format_id, provenance=provenance)
        if fmt:
            return fmt
    else:
        # Search all agents for this format. The caller supplied NO agent scope,
        # so the match is on the id alone — matching on full (agent_url, id)
        # identity here would silently narrow "any agent" to "the reference
        # agent", because upgrading a bare string defaults agent_url to the
        # canonical creative agent.
        #
        # This branch was dead before: it compared ``fmt.format_id`` (a FormatId
        # MODEL) against ``format_id`` (a str), which is never equal — so the
        # one canonical resolver's fallback always fell through to the raise.
        all_formats = run_async_in_sync_context(registry.list_all_formats(tenant_id=tenant_id))
        # The id component alone, read through ``format_ref_id``: the only
        # comparison available when no agent_url scopes the lookup (#2093). With
        # one, ``CreativeAgentRegistry.get_format`` matches the pair instead. An id
        # with no agent_url to namespace it is inherently ambiguous across agents;
        # first in listing order wins, which is what "search all agents" has
        # always meant here.
        for fmt in all_formats:
            if format_ref_id(fmt.format_id) == format_id:
                return fmt

    # Not found anywhere. The identifiers travel as structured detail rather than
    # interpolated prose: the buyer needs to know WHICH format_id was rejected, and
    # ``details`` is where a machine can read it.
    raise AdCPFormatNotFoundError(
        field="format_id",
        # The conditional spreads this replaces kept absent identifiers out of the
        # block; ``to_wire()`` drops unset fields, so a plain None says the same
        # thing without three dict literals.
        details=EntityRefDetails(format_id=format_id, agent_url=agent_url, tenant_id=tenant_id),
    )


def _get_product_format_override(
    tenant_id: str, product_id: str, format_id: str, agent_url: str | None = None
) -> Format | None:
    """Get product-level format override from product.implementation_config.

    Product can override any format's platform_config. Example:
    {
        "format_overrides": {
            "display_300x250": {
                "platform_config": {
                    "gam": {
                        "creative_placeholder": {
                            "width": 1,
                            "height": 1,
                            "creative_template_id": 12345678
                        }
                    }
                }
            }
        }
    }

    Args:
        tenant_id: Tenant identifier
        product_id: Product identifier
        format_id: Format to look up
        agent_url: Optional creative agent URL (needed to fetch base format)

    Returns:
        Format with overridden config, or None if no override exists
    """
    from sqlalchemy import text

    with get_db_session() as session:
        result = session.execute(
            text(
                "SELECT implementation_config FROM products WHERE tenant_id = :tenant_id AND product_id = :product_id"
            ),
            {"tenant_id": tenant_id, "product_id": product_id},
        )
        row = result.fetchone()
        if not row or not row[0]:
            return None

        # Parse implementation_config JSON
        impl_config = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        format_overrides = impl_config.get("format_overrides", {})

        if format_id not in format_overrides:
            return None

        # Get base format from creative agent registry (WITHOUT product_id to avoid recursion)
        from src.core.creative_agent_registry import get_creative_agent_registry

        registry = get_creative_agent_registry()

        try:
            # format_id is a string key in format_overrides dict
            # Pass agent_url to find the base format from the correct creative agent
            base_format = get_format(format_id, agent_url=agent_url, tenant_id=tenant_id, product_id=None)
        except AdCPFormatNotFoundError:
            # The base format genuinely does not exist, so there is nothing to
            # override. The only condition this branch was ever meant to handle.
            return None
        except AdCPSalesAgentError:
            # Anything else typed -- a rate limit, an unreachable agent -- is NOT
            # "no such format". Swallowing it here reported a transient outage as
            # an absent override, which is a lie the buyer cannot act on.
            raise

        # Apply override to base format
        override_config = format_overrides[format_id]

        # Merge platform_config override
        if "platform_config" in override_config:
            # Access platform_config directly from the model, not via model_dump(),
            # because platform_config has exclude=True and model_dump() drops it.
            base_platform_config = base_format.platform_config or {}
            override_platform_config = override_config["platform_config"]

            # Deep merge platform configs (override takes precedence)
            merged_platform_config = {**base_platform_config}
            for platform, config in override_platform_config.items():
                if platform in merged_platform_config:
                    # Merge platform-specific configs
                    merged_platform_config[platform] = {
                        **merged_platform_config[platform],
                        **config,
                    }
                else:
                    merged_platform_config[platform] = config

            return base_format.model_copy(update={"platform_config": merged_platform_config})

        return base_format


def list_available_formats(
    tenant_id: str | None = None,
    max_width: int | None = None,
    max_height: int | None = None,
    min_width: int | None = None,
    min_height: int | None = None,
    is_responsive: bool | None = None,
    asset_types: list[str] | None = None,
    name_search: str | None = None,
) -> list[Format]:
    """List all formats available to a tenant from all registered creative agents.

    Args:
        tenant_id: Optional tenant ID to include tenant-specific agents
        max_width: Maximum width in pixels (inclusive)
        max_height: Maximum height in pixels (inclusive)
        min_width: Minimum width in pixels (inclusive)
        min_height: Minimum height in pixels (inclusive)
        is_responsive: Filter for responsive formats
        asset_types: Filter by asset types
        name_search: Search by name

    Returns:
        List of all available Format objects from all registered agents
    """
    import logging

    logger = logging.getLogger(__name__)

    from src.core.creative_agent_registry import get_creative_agent_registry

    logger.info(f"[list_available_formats] Starting format fetch for tenant_id={tenant_id}")

    try:
        registry = get_creative_agent_registry()
    except Exception as e:
        logger.error(f"[list_available_formats] Failed to get creative agent registry: {e}", exc_info=True)
        return []

    # Get formats from all agents (default + tenant-specific)
    try:
        formats = run_async_in_sync_context(
            registry.list_all_formats(
                tenant_id=tenant_id,
                max_width=max_width,
                max_height=max_height,
                min_width=min_width,
                min_height=min_height,
                is_responsive=is_responsive,
                asset_types=asset_types,
                name_search=name_search,
            )
        )
    except Exception as e:
        # Deliberately still degrades to [] for EVERY failure, typed or not. The
        # only caller is the admin UI (src/admin/blueprints/products.py:156), which
        # catches the SDK's adcp.exceptions.ADCPError -- a different class tree from
        # src.core.exceptions.AdCPSalesAgentError -- so propagating a typed error here does not
        # reach a buyer, it 500s an admin page that used to degrade. salesagent-w4x1's
        # defect ("buyer sees empty catalog SUCCESS on an agent 429") is real but is
        # NOT at this site; wiring this path to a buyer is what would create it.
        logger.error(f"[list_available_formats] Error fetching formats: {e}", exc_info=True)
        return []

    logger.info(f"[list_available_formats] Successfully fetched {len(formats)} formats")
    return formats
