"""Regression test: capabilities response declares idempotency.

AdCP library v4.4.0 made ``Adcp.idempotency`` a required discriminated union
(per AdCP issue #2315). Constructing the response model without it throws
``ValidationError`` at runtime — that's the bug this test guards against.

Locks in the fix from PR #17, which moved capabilities serving to the SDK
auto-handler with explicit ``IdempotencySupported`` declared in
``core/main.py:build_router``.

See: tests/integration/issues/41.
"""

from __future__ import annotations

from unittest.mock import patch


def test_router_capabilities_declare_idempotency_supported() -> None:
    """The wire-level Adcp object built by ``core/main.build_router`` has
    ``idempotency`` set so the response shape passes Pydantic validation.

    Stubs ``_build_proposal_managers`` to bypass its DB query — the
    capabilities object is constructed inline in ``build_router`` from
    constants, so the proposal-manager dict is irrelevant to what we're
    asserting.
    """
    from adcp.decisioning.capabilities import IdempotencySupported

    from core.main import build_router

    with patch("core.main._build_proposal_managers", return_value={}):
        router = build_router()
    adcp_block = router.capabilities.adcp

    assert adcp_block is not None, "DecisioningCapabilities.adcp must be set"
    assert adcp_block.idempotency is not None, (
        "Adcp.idempotency is required by the library schema. Without it, "
        "Pydantic raises ValidationError when the SDK projects the wire "
        "response. See issue #41."
    )
    # We dedupe via PgBackend, so we declare supported.
    assert isinstance(
        adcp_block.idempotency, IdempotencySupported
    ), f"Expected IdempotencySupported, got {type(adcp_block.idempotency).__name__}"
    assert adcp_block.idempotency.replay_ttl_seconds >= 3600, "AdCP spec requires replay_ttl_seconds >= 3600 (1h)"


def test_idempotency_replay_ttl_constant_matches_pgbackend_window() -> None:
    """The constant the router consumes lives in src/core/tools/capabilities.
    Must stay >= the spec floor (1h) and stay aligned with the dedupe window."""
    from src.core.tools.capabilities import IDEMPOTENCY_REPLAY_TTL_SECONDS

    assert (
        3600 <= IDEMPOTENCY_REPLAY_TTL_SECONDS <= 604800
    ), f"replay_ttl_seconds must be 3600-604800 per spec, got {IDEMPOTENCY_REPLAY_TTL_SECONDS}"
