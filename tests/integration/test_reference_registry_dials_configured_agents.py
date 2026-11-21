"""The testing-mode registry answers from the reference capture for the operator agent only.

``ReferenceFormatsRegistry`` (every test deployment, and the e2e server) serves the
committed capture in place of the standard creative agent. A creative agent the tenant
configured is a different agent: looking one of its formats up must dial it, on the
single-format path as on the listing path. Before the rule lived in one place the
single-format path answered the capture for ANY agent_url, so a peer's format could not be
resolved and a capture id was "found" at an agent that never published it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.core.creative_agent_registry import ReferenceFormatsRegistry
from src.core.format_cache import load_reference_formats
from tests.harness.creative_formats import CreativeFormatsEnv
from tests.helpers.peer_creative_agent import new_peer_key

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_PEER_FORMAT_ID = "acme_peer_banner"

#: A format the peer LISTS but declares under another agent's URL. core/format-id.json
#: identifies a format by its declaring agent and its id, so it is not the peer's format and
#: a lookup at the peer's URL must not answer with it (salesagent-ol033.20.23).
_RELISTED_FORMAT_ID = "acme_relisted_banner"
_RELISTED_ENTRY = {
    "format_id": {"agent_url": "https://peer-creatives.example", "id": _RELISTED_FORMAT_ID},
    "name": "Acme Relisted Banner",
}


def _own_entry(peer_url: str) -> dict[str, Any]:
    """The peer's own format, declared under the URL the peer is dialled at, as an agent states it."""
    return {"format_id": {"agent_url": peer_url, "id": _PEER_FORMAT_ID}, "name": "Acme Peer Banner"}


def test_a_configured_agents_format_is_looked_up_from_that_agent(integration_db):
    """The peer's own format resolves; a capture id, or an id it lists for another agent, does not."""
    capture_id = load_reference_formats()[0].format_id.id
    with CreativeFormatsEnv() as env:
        key = new_peer_key()
        peer_url = env.peer_url(key)
        env.serve_peer_catalog(key, {"formats": [_own_entry(peer_url), _RELISTED_ENTRY]})
        env.configure_peer_agent(peer_url, owner="test_reference_registry_dials_configured_agents")
        registry = ReferenceFormatsRegistry()
        peer_format = asyncio.run(registry.get_format(agent_url=peer_url, format_id=_PEER_FORMAT_ID))
        capture_format = asyncio.run(registry.get_format(agent_url=peer_url, format_id=capture_id))
        relisted_format = asyncio.run(registry.get_format(agent_url=peer_url, format_id=_RELISTED_FORMAT_ID))

    assert peer_format is not None, "the configured agent's format was not resolved from the agent"
    assert peer_format.name == "Acme Peer Banner"
    assert capture_format is None, (
        f"{capture_id!r} resolved at the configured agent's URL, which never published it: "
        "the reference capture answered for a non-operator agent"
    )
    assert relisted_format is None, (
        f"{_RELISTED_FORMAT_ID!r} resolved at the configured agent's URL although the agent lists it under "
        f"{_RELISTED_ENTRY['format_id']['agent_url']!r}: the lookup matched the id alone"
    )
