"""Buyer-facing property identifier matching, shared by intersection and adapters.

Matching semantics come from the SDK primitives ``adcp.adagents.domain_matches``
and ``adcp.adagents.identifiers_match`` — the AdCP reference implementation of
the ``Identifier.value`` grammar (spec 3.1.0-beta.3, ``core/identifier.json``):

- ``example.com`` matches the base domain plus the ``www.``/``m.`` subdomains
- ``edition.example.com`` matches exactly that subdomain
- ``*.example.com`` matches ALL subdomains but NOT the base domain

Known SDK divergence (do not hand-roll around it): the SDK's bare-domain
www/m expansion only fires for two-label patterns, so e.g. ``bbc.co.uk``
does not select ``www.bbc.co.uk`` even though the grammar prose has no
label-count restriction. Tracked upstream against the SDK; salesagent stays
byte-faithful to the reference matcher until it changes.

This module only SHAPES our data into the dict form the SDK matchers accept and
fixes the pattern DIRECTION: the buyer's property_list identifiers are the
pattern side (a buyer's ``*.espn.com`` selects a property identified by
``sports.espn.com``); concrete property identifiers (``AuthorizedProperty``
rows, Kevel site hosts) are the property side. A wildcard on the property side
is treated as a literal — the same direction ``verify_agent_authorization``
uses in the SDK.

Operator-side sync association (``property_discovery_service``) deliberately
does NOT use this module: deciding which adagents.json properties belong to a
configured ``publisher_domain`` is an onboarding heuristic, not the buyer-value
grammar.
"""

from __future__ import annotations

import urllib.parse

from adcp.adagents import domain_matches, identifiers_match
from adcp.types import Identifier


def identifier_dicts(identifiers: list[Identifier]) -> list[dict[str, str]]:
    """Shape typed ``Identifier`` objects into the ``[{"type", "value"}]`` dicts
    the SDK matchers accept.

    ``Identifier.type`` is a real ``PropertyIdentifierTypes`` enum on every
    validation path (construction, ``model_validate``, JSON round-trip), so
    ``.value`` is read directly — an untyped object reaching here is a bug and
    should fail loud.
    """
    return [{"type": ident.type.value, "value": ident.value} for ident in identifiers]


def property_matches_buyer_list(
    property_identifiers: list[dict] | None,
    buyer_identifier_dicts: list[dict[str, str]],
) -> bool:
    """True when any property identifier matches any buyer identifier, type-aware.

    Delegates to ``adcp.adagents.identifiers_match``: types must match;
    ``domain``-type pairs use the spec value grammar (buyer side is the
    pattern); every other type requires exact value equality.
    """
    return identifiers_match(property_identifiers or [], buyer_identifier_dicts)


def buyer_identifier_matches_host(ident: Identifier, host: str) -> bool:
    """True when a buyer identifier selects ``host`` (a concrete bare hostname).

    ``domain``-type identifiers use the spec value grammar via the SDK's
    ``domain_matches`` (so ``*.espn.com`` selects ``sports.espn.com`` and bare
    ``espn.com`` also selects ``www.``/``m.``). Other host-shaped types
    (``subdomain``) require exact host equality — a subdomain identifier names
    that specific host.
    """
    if ident.type.value == "domain":
        return domain_matches(host, ident.value)
    return host == host_from_url_or_host(ident.value)


def host_from_url_or_host(value: str) -> str:
    """Extract the bare lowercase host from a URL-or-host string.

    Kevel ``Site.Url`` values arrive as full URLs (``https://www.espn.com/x``);
    matching needs the TRUE host. No prefix stripping happens here — the spec
    grammar (``domain_matches``) decides what a buyer pattern selects, so
    collapsing ``www.espn.com`` to ``espn.com`` would break wildcard selection
    of the real subdomain.
    """
    if not value:
        return ""
    # Parse both forms the same way so the port is stripped identically: a bare
    # host[:port] has no scheme, so give it the ``//`` netloc prefix. ``.hostname``
    # drops the port and lowercases, and handles bracketed IPv6. A trailing FQDN
    # dot (``espn.com.``) is stripped so the subdomain exact-equality path
    # normalizes the same way the SDK ``domain_matches`` path already does —
    # otherwise ``sports.espn.com.`` would resolve to nothing while the domain
    # form ``espn.com.`` still matches.
    parsed = urllib.parse.urlparse(value if "://" in value else f"//{value}")
    return (parsed.hostname or "").strip().rstrip(".").lower()
