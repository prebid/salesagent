"""The tenant's one peer creative agent row has one configuration (salesagent-ol033.20.30.4).

``PeerCreativeAgentMixin`` points the tenant's single ``creative_agents`` row at a peer, and
two configurations write it: ``set_served_catalogue`` (the seller's catalog is exactly these
formats, the standard agent turned off) and ``host_reference_format`` (the tenant's agent
also publishes a capture format, the standard agent still federated -- what
``catalog_agent_url`` does on e2e). Called together they left whichever ran last and lost
the other's catalog without a sound. The mixin now refuses the second configuration, in
either order, before it writes anything.

The control is the re-seed: the SAME configuration called twice re-points the row, which is
what a catalog Given does each time it grows the catalog. A refusal that fired on any second
call would fail it.
"""

from __future__ import annotations

import pytest

from tests.factories.format import AGENT_URL, FormatFactory
from tests.harness.creative_sync import CreativeSyncEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

#: A format the reference capture carries, so ``host_reference_format`` can publish it.
_HOSTED_FORMAT_ID = "display_300x250_image"


def _catalogue(format_id: str) -> list:
    return [FormatFactory(format_id={"id": format_id, "agent_url": AGENT_URL})]


def test_a_hosted_format_after_a_seeded_catalog_is_refused(integration_db):
    with CreativeSyncEnv() as env:
        env.setup_default_data()
        env.set_served_catalogue(_catalogue("peer_banner"))
        seeded_url = env.peer_agent_url()

        with pytest.raises(ValueError, match="already configured by seed_own_creative_agent"):
            env.host_reference_format(_HOSTED_FORMAT_ID)

        assert env.peer_agent_url() == seeded_url


def test_a_seeded_catalog_after_a_hosted_format_is_refused(integration_db):
    with CreativeSyncEnv() as env:
        env.setup_default_data()
        hosted_url = env.host_reference_format(_HOSTED_FORMAT_ID)

        with pytest.raises(ValueError, match="already configured by host_reference_format"):
            env.set_served_catalogue(_catalogue("peer_banner"))

        assert env.peer_agent_url() == hosted_url


def test_a_re_seeded_catalog_re_points_the_row(integration_db):
    with CreativeSyncEnv() as env:
        env.setup_default_data()
        env.set_served_catalogue(_catalogue("peer_banner"))
        first_url = env.peer_agent_url()

        env.set_served_catalogue(_catalogue("peer_banner_wide"))

        assert env.peer_agent_url() != first_url
        assert [fid.agent_url for fid in env.seeded_format_ids()] == [env.peer_agent_url()]
