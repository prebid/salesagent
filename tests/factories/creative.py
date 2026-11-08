"""Factory-boy factories for Creative and CreativeAssignment ORM models."""

from __future__ import annotations

from typing import Any

import factory
from factory.alchemy import SQLAlchemyModelFactory

from src.core.creative_agent_registry import PUBLIC_DEFAULT_AGENT_URL
from src.core.database.models import Creative, CreativeAssignment
from src.core.database.repositories.creative import _identity_columns
from src.core.format_resolver import creative_identity_of, find_format
from src.core.schemas import CreativeFormatIdentity, Format, FormatId
from src.core.schemas.creative import CreativeAssetRequest

from .core import TenantFactory
from .creative_asset import build_assets, image_spec
from .media_buy import MediaBuyFactory
from .principal import PrincipalFactory

#: The creative agent a factory-built creative asserts a format from, unless a test names
#: another one.
#:
#: DERIVED from production's own constant, not re-typed, because the two must be the same
#: string for a fixture's reference to behave like a buyer's. It is a federated IDENTITY and
#: not a destination: ``is_operator_agent`` counts this url as an agent the deployment
#: stands behind, so a reference naming it takes ``ReferenceFormatsRegistry``'s catalog
#: answer under ``ADCP_TESTING`` and ``_connection_agent_url``'s reroute to the pinned
#: container otherwise. Any OTHER url -- including the dialable ``CREATIVE_AGENT_URL`` the
#: e2e stack configures -- is a counterparty, judged by the egress seam. So a fixture names
#: THIS url and lets production decide the transport; it never names the one it can reach.
DEFAULT_CREATIVE_AGENT_URL = PUBLIC_DEFAULT_AGENT_URL

#: One ``format_option_ref`` document per arm of core/format-option-ref.json, written as the
#: buyer sends it and keyed by the ``scope`` discriminator. The product arm declares
#: ``publisher_domain: false``, so it carries no such member, not even null; the publisher
#: arm's domain satisfies that arm's ``pattern``. The two share a ``format_option_id`` on
#: purpose: they are still DIFFERENT options, which is what makes the scope worth keeping.
_FORMAT_OPTION_REFS_BY_SCOPE: dict[str, dict[str, str]] = {
    "product": {"scope": "product", "format_option_id": "opt_hero_image"},
    "publisher": {
        "scope": "publisher",
        "publisher_domain": "example.com",
        "format_option_id": "opt_hero_image",
    },
}


def format_option_ref_for_scope(scope: str) -> dict[str, str]:
    """A fresh ``format_option_ref`` on the named arm, checked against the pinned file first.

    Both checks keep a scenario that names a scope non-vacuous. An unknown scope would
    otherwise seed nothing and grade the absent case under a sentence promising a
    reference; and a fixture that were already invalid against core/format-option-ref.json
    would make a readback comparison compare one malformed document to another.
    """
    from tests.helpers.pinned_schema import validator_for

    assert scope in _FORMAT_OPTION_REFS_BY_SCOPE, (
        f"scope {scope!r} names no arm of core/format-option-ref.json; "
        f"the pinned arms are {sorted(_FORMAT_OPTION_REFS_BY_SCOPE)}"
    )
    reference = dict(_FORMAT_OPTION_REFS_BY_SCOPE[scope])
    validator_for("core/format-option-ref.json").validate(reference)
    return reference


def served_format_spec(agent_url: str, format_id: str) -> Format | None:
    """The catalog declaration for ``(agent_url, format_id)``, or None if nothing serves it.

    THE SAME CATALOG THE SERVER ANSWERS WITH. Under ``ADCP_TESTING`` the unpatched
    registry is ``ReferenceFormatsRegistry``, which serves the checked-in capture
    (``tests/fixtures/creative_formats/reference_formats.json``) and never dials -- so
    this reads the identical formats a synced creative is resolved against, through
    production's own ``find_format`` rather than a second matching rule.

    IT EXISTS BECAUSE A SEEDED CREATIVE AND A SYNCED ONE MUST AGREE. The sync path
    resolves ``identity_spec = find_format(all_formats, creative.format_id)``
    (``src/core/tools/creatives/_processing.py``), which is what makes steps 2 and 4 of the
    registry's normative projection order -- the format's own ``canonical`` declaration and
    the structural match against its asset types -- reachable.
    Measured on the pinned capture: 69 of the 76 formats declare ``canonical.kind``, while
    only 23 are matched by a registry glob. So a factory that consulted no catalog could
    seed 23 of the 76 and would refuse the other 53, including every video, audio, html5
    and native format the agent serves.

    Safe to apply by default, measured rather than assumed: of the 76 captured formats, 23
    resolve by BOTH routes and all 23 agree (``canonical_kind_for(id)`` ==
    ``canonical_kind_for(id, spec=fmt)``), 46 resolve only with the spec through its
    ``canonical``, and the 7 card formats that declare none resolve only with the spec,
    through the registry's structural ``url`` entry (``display_tag``).

    That agreement is a property of the ORDER, not of the capture happening to annotate
    every image format. ``canonical_kind_for`` upgrades the reference before the SDK walks
    steps 2 to 4, so a size-encoded image id reaches the registry's ``display_image`` glob
    (step 3) ahead of the structural ``url`` entry (step 4) its ``image`` + ``url`` shape also
    matches; stripping ``canonical`` off the 7 ``display_*_image`` formats still yields
    ``image``. Projecting first and upgrading only after a failure answered ``display_tag``
    for them with the spec and ``image`` without it. So supplying the spec adds only what a
    higher step reads off the declaration -- it can turn a refusal into the kind the
    normative order gives, and cannot overturn a glob hit with a structural guess.

    None for an id no catalog carries, which keeps the gate real: ``gen_banner`` and
    ``broadstreet_display`` are still refused unless a caller passes the declaration whose
    seller actually serves them.
    """
    from src.core.format_cache import load_reference_formats

    return find_format(load_reference_formats(), FormatId(agent_url=agent_url, id=format_id))


def declared_format_spec(agent_url: str, format_id: str, kind: str = "image") -> Format:
    """The declaration an agent OTHER than the reference one serves for *format_id*, naming *kind*.

    Pass it as ``format_spec`` when a test seeds a creative at another agent on purpose.
    The mapping registry projects an id alone only at the reference agent's origin; another
    agent's id is that agent's own and has a canonical kind only through the declaration it
    serves (``format_resolver.canonical_projection``). So a creative seeded at
    ``https://other.example`` with ``display_300x250`` and no declaration is refused in the
    factory, which is the production gate working. This states the agent's ``canonical``
    annotation, step 1 of the registry's resolution order, and nothing else.
    """
    from adcp.types import CanonicalProjectionReference

    from .format import FormatFactory

    return FormatFactory(
        format_id=FormatId(agent_url=agent_url, id=format_id),
        canonical=CanonicalProjectionReference(kind=kind),
    )


def seeded_format_identity(
    *,
    agent_url: str = DEFAULT_CREATIVE_AGENT_URL,
    format_id: str = "display_300x250",
    format_params: dict[str, Any] | None = None,
    canonical_kind: str | None = None,
    format_option_ref: dict[str, Any] | None = None,
    format_spec: Format | None = None,
) -> CreativeFormatIdentity:
    """The format identity a seeded creative carries, through the PRODUCTION accessor.

    PUBLIC because a test that seeds through ``CreativeRepository.create`` directly -- rather
    than through ``CreativeFactory`` -- still has to supply its ``identity=`` argument, and
    the one thing it must not do is hand-build one. ``_identity_columns`` reads four members
    off a ``CreativeFormatIdentity``; the parallel ``FormatIdentity`` comparison key carries
    none of them and is ``slots=True``, so passing one raised ``AttributeError`` inside the
    repository on every seed (#2351).

    A test says which arm the creative carries -- a legacy reference via ``agent_url`` +
    ``format_id`` (optionally parameterized by ``format_params``), or a canonical kind via
    ``canonical_kind`` -- and the stored columns are derived from that by the same code the
    sync path runs, including the v1 -> v2 projection. So a seeded row and a synced row
    cannot differ in shape, which is what seeding the columns by hand could not promise:
    the identity used to be three partial columns and the factory set two of them.

    ``format_params`` is spread onto the reference rather than kept beside it, because that
    is what a parameterized ``format_id`` IS -- ``core/format-id.json`` declares width,
    height, duration_ms and pixel_ratio on the reference object itself. A scenario that needs
    a parameterized format therefore seeds one reference, not a reference plus a blob.

    The DTO is built FOR REAL, and BOTH arms go through the accessor. A ``SimpleNamespace``
    stood here -- a duck that happened to answer three attribute reads -- and it was the only
    caller keeping those reads defensive in production. The canonical branch also used to
    return a hand-built identity, skipping the accessor and with it the seller's vocabulary
    check, so a scenario could seed a kind production would refuse.

    ``format_option_ref`` narrows the creative to one of the enclosing product's concrete
    options, exactly as ``core/format-option-ref.json`` declares it -- a discriminated
    reference, so a scenario passes the document verbatim and the DTO picks its branch. It
    rides with EITHER arm: ``core/creative-asset.json`` declares it beside the root oneOf on
    the identifier, and neither arm forbids it.

    ``format_spec`` is the catalog declaration the projection resolves against, and
    defaults to whatever :func:`served_format_spec` finds in the capture the server
    serves. A caller supplies one only for a format NO shared catalog carries -- an
    adapter-served id like ``broadstreet_display``, whose declaration belongs to the
    adapter rather than to a creative agent (salesagent-zthsk).
    """
    identity_arm: dict[str, Any] = {
        **(
            {"format_kind": canonical_kind}
            if canonical_kind is not None
            else {"format_id": FormatId(agent_url=agent_url, id=format_id, **(format_params or {}))}
        ),
        "format_option_ref": format_option_ref,
    }
    return creative_identity_of(
        CreativeAssetRequest(
            creative_id="seeded-identity",
            name="seeded identity",
            assets={},
            **identity_arm,
        ),
        # THE CATALOG THE SERVER ANSWERS WITH, so a seeded row and a synced row resolve
        # the same way. The canonical arm needs no spec at all -- it already carries the
        # kind -- and ``served_format_spec`` answers None for it harmlessly.
        #
        # THIS PASSED ``spec=None`` AND CALLED THAT A DECLARATION. The reasoning was that
        # "a seeded identity has no fetched catalog to resolve against", and it concluded
        # that ids like ``display_300x250_image`` "cannot be seeded through this helper. A
        # scenario needing one of those seeds through the sync path, which has a catalog."
        # The premise was wrong: the catalog is a checked-in file this process can read,
        # and it is the SAME file the sync path resolves against under ``ADCP_TESTING``. So
        # the declaration made the factory stricter than production and refused 49 of the
        # 71 formats the agent serves -- every video, audio, html5 and native one -- which
        # is what regressed 16 nodeids naming ``video_1280x720``, ``video_1920x1080`` and
        # ``native_content``, all three of them CATALOG ids the scenarios picked on purpose.
        #
        # CONSEQUENCE A SCENARIO SHOULD STILL KNOW: a legacy ``format_id`` that NO catalog
        # carries and no registry glob matches RAISES ``AdCPCapabilityNotSupportedError``
        # here rather than seeding ``format_kind=NULL``. That is the production gate
        # working, not a factory fault.
        spec=format_spec if format_spec is not None else served_format_spec(agent_url, format_id),
    )


def _seeded_identity(obj) -> CreativeFormatIdentity:
    """``factory_boy`` adapter: the factory stub's knobs, named for :func:`seeded_format_identity`."""
    return seeded_format_identity(
        agent_url=obj.agent_url,
        format_id=obj.format,
        format_params=obj.format_params,
        canonical_kind=obj.canonical_kind,
        format_option_ref=obj.format_option_ref_document,
        format_spec=obj.format_spec,
    )


def stamp_format_identity(
    row,
    *,
    format_id: str = "display_300x250",
    agent_url: str = DEFAULT_CREATIVE_AGENT_URL,
    canonical_kind: str | None = None,
    format_params: dict[str, Any] | None = None,
) -> CreativeFormatIdentity:
    """Stamp the four stored identity columns onto a MOCK creative row, and return them.

    A ``MagicMock`` answers a fresh ``Mock`` for any attribute nobody stubbed, and a ``Mock``
    is not a state any of these columns can hold: ``CreativeFormatIdentity.from_row``
    validates ``legacy_format_id`` as the JSON document the column holds, so an unstubbed row
    fails inside the reader rather than in the code under test. The columns are derived here
    through the same production mapping ``CreativeRepository.create`` uses, so a mock row and
    a real row agree -- and a column added to the identity is stamped in ONE place instead of
    at every mock.

    For a row that goes through the real session, use ``CreativeFactory`` instead.
    """
    identity = seeded_format_identity(
        agent_url=agent_url,
        format_id=format_id,
        format_params=format_params,
        canonical_kind=canonical_kind,
    )
    for column, value in _identity_columns(identity).items():
        setattr(row, column, value)
    return identity


class CreativeFactory(SQLAlchemyModelFactory):
    class Meta:
        model = Creative
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"
        # The identity knobs are inputs, not columns: `_seeded_identity` turns them into the
        # four columns that exist.
        exclude = (
            "principal",
            "agent_url",
            "format",
            "format_params",
            "canonical_kind",
            "format_option_ref_document",
            "format_spec",
            "identity",
        )

    # Composite PK fields
    creative_id = factory.Sequence(lambda n: f"creative_{n:04d}")
    tenant = factory.SubFactory(TenantFactory)
    tenant_id = factory.LazyAttribute(lambda o: o.tenant.tenant_id)
    principal = factory.SubFactory(PrincipalFactory, tenant=factory.SelfAttribute("..tenant"))
    principal_id = factory.LazyAttribute(lambda o: o.principal.principal_id)

    # Required fields
    name = factory.LazyAttribute(lambda o: f"Test Creative {o.creative_id}")

    # --- Format identity: knobs in, four columns out ---
    # `agent_url` + `format` name the legacy reference a creative asserts (the default, and
    # what most scenarios exercise), `format_params` parameterizes it (width, height,
    # duration_ms, pixel_ratio -- the reference's own members); `canonical_kind` instead
    # builds a creative identified by its canonical kind, which asserts no reference at all,
    # and `format_option_ref_document` narrows that kind to one concrete product option.
    # That knob is named for the DOCUMENT it takes because `format_option_ref` is already a
    # COLUMN on this model below -- one factory field cannot be both the input and the output.
    #
    # `format_spec` overrides the catalog lookup `seeded_format_identity` does by default,
    # and is for ONE case: a format whose declaration belongs to an ADAPTER rather than to
    # a creative agent, so no shared catalog carries it (salesagent-zthsk). Everything the
    # reference agent serves is found without it.
    agent_url = DEFAULT_CREATIVE_AGENT_URL
    format = "display_300x250"
    format_params = None
    canonical_kind = None
    format_option_ref_document = None
    format_spec = None
    identity = factory.LazyAttribute(_seeded_identity)
    # Each column read off the ONE production mapping by its own name, so a column added to
    # the identity is a line here and nothing else -- never a second copy of the mapping.
    format_kind = factory.LazyAttribute(lambda o: _identity_columns(o.identity)["format_kind"])
    format_option_ref = factory.LazyAttribute(lambda o: _identity_columns(o.identity)["format_option_ref"])
    legacy_format_id = factory.LazyAttribute(lambda o: _identity_columns(o.identity)["legacy_format_id"])
    legacy_format_key = factory.LazyAttribute(lambda o: _identity_columns(o.identity)["legacy_format_key"])

    # Mirrors the production default. Must stay an AdCP CreativeStatus member: a non-spec
    # value (this was "pending") made every factory-built creative reach list_creatives
    # through the reader's unparseable-status branch rather than through real status
    # semantics.
    status = "pending_review"
    data = factory.LazyFunction(lambda: {"assets": build_assets(image_spec("banner"))})

    class Params:
        """Traits for common creative configurations."""

        approved = factory.Trait(status="approved")


class CreativeAssignmentFactory(SQLAlchemyModelFactory):
    class Meta:
        model = CreativeAssignment
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"
        exclude = ("creative", "media_buy")

    assignment_id = factory.Sequence(lambda n: f"assignment_{n:04d}")
    creative = factory.SubFactory(CreativeFactory)
    tenant_id = factory.LazyAttribute(lambda o: o.creative.tenant_id)
    creative_id = factory.LazyAttribute(lambda o: o.creative.creative_id)
    principal_id = factory.LazyAttribute(lambda o: o.creative.principal_id)
    media_buy = factory.SubFactory(MediaBuyFactory, tenant=factory.SelfAttribute("..creative.tenant"))
    media_buy_id = factory.LazyAttribute(lambda o: o.media_buy.media_buy_id)
    package_id = factory.Sequence(lambda n: f"pkg_{n:04d}")
    weight = 100
