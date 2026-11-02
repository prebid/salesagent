"""This seller reads signing keys from ONE location, and refuses a counterparty that moves them.

``<agent origin>/.well-known/jwks.json``. security.mdx @ v3.1.1 step 6 makes that the
default — "defaulting to ``/.well-known/jwks.json`` at the origin of ``A`` when absent" —
and permits ``agents[].jwks_uri`` to name somewhere else instead. This seller declines the
second half, so key discovery is a pure function of the agent URL.

The cost was measured, not assumed: ``compliance/universal/signed-requests.yaml`` at 3.1.1
contains **zero** occurrences of ``jwks_uri`` and **zero** of ``key_origins`` across its 12
positive and 28 negative vectors. The two codes the indirection exists to produce —
``request_signature_key_origin_mismatch`` and ``_missing`` — are mandated by the prose and
graded by nothing.
"""

from __future__ import annotations

import pytest
from adcp.signing.agent_resolver import AgentResolution

from src.core.signing.verifier import WELL_KNOWN_JWKS_PATH, _jwks_is_well_known, build_registry_resolution

_AGENT = "https://agent.example/mcp/"


def _resolution(jwks_uri: str, *, agent_url: str = _AGENT) -> AgentResolution:
    return AgentResolution(
        agent_url=agent_url,
        brand_json_url="https://brand.example/.well-known/brand.json",
        agent_entry={"type": "sales", "url": agent_url, "jwks_uri": jwks_uri},
        jwks_uri=jwks_uri,
        jwks={"keys": []},
        fetched_at=0.0,
    )


def test_the_well_known_location_at_the_agent_origin_is_accepted() -> None:
    """The one location, derived from the agent URL and nothing else."""
    assert _jwks_is_well_known(_resolution("https://agent.example/.well-known/jwks.json"))


@pytest.mark.parametrize(
    "jwks_uri, why",
    [
        ("https://keys.agent.example/.well-known/jwks.json", "a keys subdomain is a different origin"),
        ("https://brand.example/.well-known/jwks.json", "the brand.json origin is not the agent origin"),
        ("https://agent.example/keys/jwks.json", "right origin, wrong path"),
        ("https://agent.example/.well-known/jwks.json?v=2", "a query string is not the well-known URI"),
        ("http://agent.example/.well-known/jwks.json", "scheme is part of the origin"),
    ],
)
def test_anywhere_else_is_refused(jwks_uri: str, why: str) -> None:
    """Each of these is permitted by the spec's step 6 and refused by this seller.

    Refused rather than silently accepted: the caller records
    ``request_signature_jwks_untrusted`` and logs the location it looked at, so the
    counterparty's operator gets a diagnosable answer instead of a key that never matches.
    """
    assert not _jwks_is_well_known(_resolution(jwks_uri)), why


def test_a_registry_entry_derives_the_location_rather_than_declaring_one() -> None:
    """The other path that produces a resolution answers the same question by construction.

    A configured counterparty carries its JWKS inline and names no location, so the entry
    cannot point its keys somewhere the walk would refuse -- the two paths cannot disagree
    about where a counterparty's keys live.
    """
    resolution = build_registry_resolution({"agent_url": _AGENT, "jwks": {"keys": []}})

    assert resolution.jwks_uri == f"https://agent.example{WELL_KNOWN_JWKS_PATH}"
    assert _jwks_is_well_known(resolution)


def test_an_agent_url_with_no_origin_cannot_resolve_a_location() -> None:
    """No origin, no derivable location — so nothing to accept.

    A resolution this malformed should not reach here, and if it does the answer is refusal
    rather than a comparison against a half-built string.
    """
    assert not _jwks_is_well_known(_resolution("https://agent.example/.well-known/jwks.json", agent_url="not-a-url"))
