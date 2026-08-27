"""GOVERNANCE_REVOCATIONS_PATH must match what the SDK's consumer derives.

#1291 A5 follow-up, salesagent-z6nr.27. The SDK exposes no path CONSTANT for
this (unlike ``AGENT_ENDPOINT_PATHS``, discovered from the running app at
``agent_identity.py:50-56``) — only an f-string inside
``CachingRevocationChecker.from_issuer_origin``. This pins our path constant
against that private derivation via its ``_revocation_uri`` attribute, so a
divergence is a wired test failure, not a counterparty finding our list 404s.
"""

from __future__ import annotations

from adcp.signing.revocation_fetcher import CachingRevocationChecker

from src.core.agent_identity import GOVERNANCE_REVOCATIONS_PATH


def test_our_path_constant_matches_the_sdk_consumers_derived_uri():
    checker = CachingRevocationChecker.from_issuer_origin(
        "https://x.example",
        jwks_resolver=lambda kid: None,
    )
    # Private attribute, not a public API -- this pin is what documents that
    # relationship and catches a divergence at test time, not at deploy time.
    assert checker._revocation_uri.endswith(GOVERNANCE_REVOCATIONS_PATH), (
        f"our GOVERNANCE_REVOCATIONS_PATH {GOVERNANCE_REVOCATIONS_PATH!r} must match the suffix "
        f"the SDK's own consumer derives; got {checker._revocation_uri!r}"
    )
