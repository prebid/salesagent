"""The three trust-root documents this agent publishes (#1291 A3, salesagent-z6nr.9).

Pure builders — no session, no request, no clock. They take a tenant, ONE
``SigningKeyRepository.publishable_at()`` result, and (for adagents.json) the
authorized-property records that back the claim, and return plain dicts. The
route layer (``src/routes/well_known.py``) owns the reads; this module owns the
SHAPE, which is what the schemas grade.

Two trust roots, two surfaces (AdCP 3.1.1 ``security.mdx``:1105-1111):

* **brand.json -> ``agents[].jwks_uri`` -> JWKS** is authoritative for REQUEST
  signatures and operator-side webhook signatures. This is the chain a verifier
  walks from ``get_adcp_capabilities``' ``identity.brand_json_url``.
* **adagents.json ``authorized_agents[].signing_keys[]``** is a publisher pin,
  authoritative for exactly one tuple — SELL-SIDE webhook delivery. It is not in
  the request-signing chain.

Both are built from the SAME ``publishable_at()`` result, so the pin and the
JWKS cannot drift into disagreeing about which keys exist. One query, two
projections.

**``revoked_at`` travels with the key into BOTH projections.** The schema's
justification for the grace window is that caches which have not yet refreshed
"still find the key and can evaluate the revocation marker". A JWKS entry
WITHOUT the marker is indistinguishable from a live key — and the JWKS is the
document that governs request signing, so omitting it there would leave a
revoked request-signing key silently verifiable for the whole window. Carrying
it is a deliberate SUPERSET of the per-JWK member table at
``security.mdx``:1067-1075: ``core/agent-signing-key.json`` is
``additionalProperties: true`` and RFC 7517 tolerates unknown members, so both
documents stay valid.

The JWK itself is ``row.public_jwk`` VERBATIM — ``adcp.signing.keygen`` already
emits the whole ``{kty, crv, alg, use: "sig", key_ops: ["verify"], adcp_use,
kid, x[, y]}`` table and A2 stores it unmodified. Re-deriving any member here
would be a second opinion about our own key.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from adcp import get_adcp_spec_version

# ``BrandDiscovery3`` is a POSITIONAL generated name — "the third oneOf variant in
# generation order", not "the brand-agent document". Aliased semantically (CLAUDE.md
# Pattern #1) so the call site says what it means, and NOT to ``BrandDiscovery``, which is
# the SDK's own RootModel union wrapper. A regeneration that adds or reorders variants
# would silently rebind this name to a different shape; ``extra: forbid`` catches that
# unless the new variant is a superset of our keys, so the binding itself is pinned by
# ``test_the_brand_agent_variant_is_still_the_one_we_bind`` in
# tests/unit/test_adcp_spec_version.py.
from adcp.types.generated_poc.brand import BrandDiscovery3 as LibraryBrandAgentDocument
from adcp.types.generated_poc.core.agent_signing_key import AgentSigningKey as LibraryAgentSigningKey
from pydantic import BaseModel, field_serializer

from src.core.agent_identity import agent_endpoint_urls, agent_entry_id, canonical_agent_url, jwks_uri
from src.core.signing._rfc3339 import rfc3339

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.core.database.models import AuthorizedProperty, SigningKey, Tenant


#: The schema origin every trust-root document points ``$schema`` at, DERIVED from the
#: pinned SDK rather than typed as a literal (#1757). The version here is the one the
#: documents are actually built against, so an ``adcp`` bump moves the served value with
#: the pin instead of leaving a stale literal that no test could see — the ``$schema`` pin
#: in ``tests/integration/test_trust_root_documents.py`` was a KEY-SET membership check,
#: which grades PRESENCE and is structurally blind to a wrong value.
#:
#: Same derivation the A2A extension URI already uses
#: (``adcp_a2a_server.py`` :2362), so the two cannot drift.
_SCHEMA_BASE = f"https://adcontextprotocol.org/schemas/{get_adcp_spec_version()}"

# ``brand_agent_entry.type`` — this agent sells inventory.
_SALES_AGENT_TYPE = "sales"


# ---------------------------------------------------------------------------
# The pinned models, EXTENDED so their timestamps render through OUR one formatter
# ---------------------------------------------------------------------------
#
# ONE SPELLING, BY CONSTRUCTION. ``src/core/signing/_rfc3339.py`` exists because this
# codebase already shipped two formatters that "happen to agree today" — its docstring
# says so — and the model conversion re-created that from the other side: pydantic renders
# a datetime as ``...Z`` while ``rfc3339()`` renders ``...+00:00``, so a converted document
# and an unconverted one served from the SAME ORIGIN carried two spellings of one format.
#
# The serializers below CALL ``rfc3339()`` rather than re-implementing ``isoformat()``. A
# second implementation that agrees today is exactly the fault being fixed, so agreement is
# structural: there is one formatter and every document routes through it.
#
# Subclassed rather than patched (CLAUDE.md Pattern #1, ``Library*`` alias) so an SDK bump
# changes no caller. Pinned by ``test_published_timestamps_render_one_spelling``.


class PublishedSigningKey(LibraryAgentSigningKey):
    """A published JWK whose ``revoked_at`` renders through :func:`rfc3339`."""

    @field_serializer("revoked_at")
    def _revoked_at(self, value: datetime | None) -> str | None:
        return rfc3339(value) if value is not None else None


class PublishedBrandDocument(LibraryBrandAgentDocument):
    """The brand-agent document whose ``last_updated`` renders through :func:`rfc3339`."""

    @field_serializer("last_updated")
    def _last_updated(self, value: datetime | None) -> str | None:
        return rfc3339(value) if value is not None else None


def _validated(model: type[BaseModel], document: dict[str, Any]) -> dict[str, Any]:
    """Round-trip *document* through its PINNED SDK model, or fail here.

    The point of the atom (#1757): the trust-root documents are schema-valid BY
    CONSTRUCTION rather than by a test that validates a dict assembled two lines earlier.
    O1 — "a third party fetches our JWKS and verifies" — only means something if an
    invalid document cannot leave this module.

    ``by_alias`` is load-bearing for brand.json: ``BrandDiscovery3`` carries ``$schema`` as
    ``field_schema`` with an alias, so dumping by field name would emit ``field_schema``
    on the wire. ``exclude_none`` keeps optional members absent rather than null, which is
    what the oneOf variants distinguish on.
    """
    return model.model_validate(document).model_dump(mode="json", by_alias=True, exclude_none=True)


def _published_jwk(key: SigningKey) -> dict[str, Any]:
    """One published JWK: the stored public JWK, plus its revocation marker.

    The marker is present only for a key inside its grace window, which is the
    only way a revoked key reaches this function at all.

    VALIDATED HERE, once. One projection carries one validation point: every
    route from a key to a published document goes through this function, so a
    JWKS entry and the same key's ``adagents.json`` entry cannot drift apart.
    They did before — ``build_jwks`` validated inline WITHOUT ``by_alias`` and
    ``build_adagents_json`` emitted this projection raw, which put three
    validation postures on one value inside one module and left a drift in the
    adagents copy invisible until a counterparty rejected a document this seller
    published.
    """
    jwk = dict(key.public_jwk)
    if key.revoked_at is not None:
        jwk["revoked_at"] = rfc3339(key.revoked_at)
    return _validated(PublishedSigningKey, jwk)


def _last_updated(keys: Sequence[SigningKey]) -> str | None:
    """When the published key set last changed, or None when it is empty.

    Derived from the keys rather than from the clock so two GETs of an unchanged
    document return an unchanged document — a ``last_updated`` that moved on
    every request would defeat the caching the ``Cache-Control`` header asks for.
    """
    moments = [moment for key in keys for moment in (key.created_at, key.revoked_at) if moment is not None]
    return rfc3339(max(moments)) if moments else None


def build_jwks(keys: Sequence[SigningKey]) -> dict[str, Any]:
    """The RFC 7517 JWKS served at ``/.well-known/jwks.json``.

    Authoritative for request-signature verification: this is where the
    brand.json hop lands.
    """
    return {"keys": [_published_jwk(key) for key in keys]}


def build_brand_json(tenant: Tenant, keys: Sequence[SigningKey]) -> dict[str, Any]:
    """The "Brand Agent" variant of brand.json for *tenant*.

    ONE ``agents[]`` entry per endpoint we actually serve, because step 5 of the
    discovery algorithm byte-equals the URL whose ``get_adcp_capabilities`` the
    counterparty invoked — and that is an endpoint, never a bare origin. The
    schema blesses this shape explicitly: "Multiple entries with the same type
    are permitted when they have distinct url values, such as one endpoint URL
    per tenant or property scope."

    Choosing this variant costs us ``authorized_operators[]`` (it exists only on
    the House Portfolio variant), which is the eTLD+1 delegation escape hatch of
    ``security.mdx``:1102 step 3. The requirement there is eTLD+1 EQUALITY;
    serving brand.json on the tenant's own host is sufficient and strictly
    stricter, and ``canonical_agent_url`` makes it true by construction.

    *keys* are not published here — brand.json points AT the JWKS rather than
    inlining it — but they date the document, so a counterparty polling
    brand.json can see the key set changed without diffing the JWKS.
    """
    document: dict[str, Any] = {
        "$schema": f"{_SCHEMA_BASE}/brand.json",
        "agents": [
            {
                "type": _SALES_AGENT_TYPE,
                "url": url,
                "id": agent_entry_id(tenant, transport),
                "jwks_uri": jwks_uri(tenant),
                "description": f"{tenant.name} sales agent ({transport.upper()} endpoint)"[:500],
            }
            for transport, url in sorted(agent_endpoint_urls(tenant).items())
        ],
    }
    if last_updated := _last_updated(keys):
        document["last_updated"] = last_updated
    return _validated(PublishedBrandDocument, document)


def _property_entry(prop: AuthorizedProperty) -> dict[str, Any]:
    """One ``core/property.json`` object built from an authorized-property record."""
    entry: dict[str, Any] = {
        "property_id": prop.property_id,
        "property_type": prop.property_type,
        "name": prop.name,
        "identifiers": prop.identifiers,
        "publisher_domain": prop.publisher_domain,
    }
    if prop.tags:
        entry["tags"] = list(prop.tags)
    return entry


def build_adagents_json(
    tenant: Tenant,
    keys: Sequence[SigningKey],
    authorizations: Sequence[AuthorizedProperty],
) -> dict[str, Any]:
    """The inline variant of adagents.json for *tenant*.

    *authorizations* are the tenant's existing authorized-property records — the
    document NEVER fabricates one. Fabricating an entry would mean
    self-attesting an authorization no publisher granted, and this file's entire
    purpose is to be a publisher's attestation. With no backing record the
    document claims nothing: an empty ``authorized_agents`` asserts *no sales
    authorization*, which the schema explicitly distinguishes from deny-all,
    authorize-all, and revocation.

    ``authorization_type: "inline_properties"`` carries the property objects on
    the entry itself, so the document is self-contained and needs no top-level
    ``properties[]`` for a consumer to resolve.

    The ``url`` here is the ORIGIN, not an endpoint: ``authorized-agent-base``
    mandates the OTHER comparison rule — "canonicalize both sides ..., not
    byte-equality" — and a canonical-by-construction producer satisfies both
    rules at once.
    """
    document: dict[str, Any] = {"$schema": f"{_SCHEMA_BASE}/adagents.json", "authorized_agents": []}

    if authorizations:
        entry: dict[str, Any] = {
            "authorization_type": "inline_properties",
            "properties": [_property_entry(prop) for prop in authorizations],
            "url": canonical_agent_url(tenant),
            "authorized_for": f"Advertising inventory on properties operated by {tenant.name}"[:500],
        }
        if keys:
            # Same projection as the JWKS — the sell-side-webhook pin and the
            # request-signing trust root describe one key set or our own
            # webhooks get rejected against our own published document.
            entry["signing_keys"] = [_published_jwk(key) for key in keys]
        document["authorized_agents"].append(entry)

    if last_updated := _last_updated(keys):
        document["last_updated"] = last_updated
    # NOT routed through ``AdcpAgentsAuthorization`` (#1757): that generated model is
    # STRICTER THAN THE PINNED SCHEMA our documents are graded against. It applies
    # ``minItems: 1`` to ``authorized_agents``, and this builder DELIBERATELY emits an
    # empty list when no authorized-property record backs a claim — "an empty
    # authorized_agents asserts NO sales authorization", which the schema distinguishes
    # from deny-all, authorize-all and revocation. The pinned schema accepts it (this
    # document passes ``validate_against_pinned_schema`` today); the model refuses it.
    # Converting would mean either fabricating an authorization we were never granted or
    # dropping the document — so this one stays a dict and is validated after the fact.
    return document
