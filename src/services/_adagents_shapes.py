"""Shape helpers for ``authorized_agents[]`` entries shared between
:mod:`src.services.aao_lookup_service` (chip-state classifier) and
:mod:`src.services.property_discovery_service` (property syncer).

Both services need the same "is this entry bare?" / "where is our agent?"
predicates. Keeping them here avoids duplicate definitions drifting apart
when the AdCP spec adds a new selector field (see salesagent#377 and
adcp#4478 for the unbound-state context).
"""

from __future__ import annotations

from typing import Any

from adcp.adagents import normalize_url

# Every selector field the AdCP schema's authorized_agents oneOf
# discriminator pairs with an ``authorization_type``. Source of truth:
# https://adcontextprotocol.org/schemas/v1/adagents.json — the six oneOf
# variants. If the spec adds a new variant (e.g. adcp#4478's
# ``all_top_level_properties``), update this tuple so the bare-entry
# detector keeps matching the schema. Order is not meaningful.
_KNOWN_SELECTOR_FIELDS: tuple[str, ...] = (
    "property_ids",
    "property_tags",
    "properties",
    "publisher_properties",
    "signal_ids",
    "signal_tags",
)


def is_bare_entry(entry: dict[str, Any]) -> bool:
    """True when an ``authorized_agents`` entry carries no
    ``authorization_type`` AND none of the schema's selector fields.

    Bare entries don't match any ``oneOf`` branch and are therefore
    schema-invalid, but real publishers (wonderstruck.org, Raptive) ship
    them. The chip + property-sync layers interpret them permissively as
    "authorized for all top-level properties" — see the ``unbound`` state
    in :mod:`src.services.aao_lookup_service`.
    """
    if entry.get("authorization_type"):
        return False
    return not any(entry.get(field) for field in _KNOWN_SELECTOR_FIELDS)


def find_agent_entry(adagents: dict[str, Any], agent_url: str) -> dict[str, Any] | None:
    """Return the ``authorized_agents`` entry whose ``url`` matches
    ``agent_url`` under the SDK's protocol-insensitive normalization, or
    None if the agent isn't listed.

    Drives the unbound/pending fork: "we're listed but not bound" and
    "we're not listed at all" need different remediation, but the SDK's
    ``get_properties_by_agent`` collapses both into an empty list.
    """
    target = normalize_url(agent_url)
    for entry in adagents.get("authorized_agents", []) or []:
        if not isinstance(entry, dict):
            continue
        if normalize_url(entry.get("url", "")) == target:
            return entry
    return None


def top_level_properties(adagents: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the file's top-level ``properties[]`` array as dicts only.

    The permissive ``unbound`` resolution binds to this array when our
    agent's entry is bare. Filtering out non-dict entries keeps the
    permissive path defensive against malformed input — the SDK's strict
    path enforces the same invariant on typed bindings.
    """
    props = adagents.get("properties")
    if not isinstance(props, list):
        return []
    return [p for p in props if isinstance(p, dict)]
