"""Capability + storyboard applicability guard for the UC-002 idempotency phases.

``create_media_buy`` implements verbatim replay, so the seller advertises the
``supported=true`` discriminant with its replay window. The generated UC-002
feature keeps the upstream replay scenario LIVE (graded against production —
the proof that restoring replay cannot silently regress), the boundary outline
uses the exact-length fixture token instead of a hand-counted literal, and the
remaining supported=true phases production does not yet implement (in-flight
tracking and its error-detail siblings) stay visible but unwired.
"""

from pathlib import Path

from src.core.config_loader import current_tenant
from src.core.database.repositories.idempotency_attempt import DEFAULT_REPLAY_TTL
from src.core.tools.capabilities import _get_adcp_capabilities_impl

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATED_UC002 = PROJECT_ROOT / "tests" / "bdd" / "features" / "BR-UC-002-create-media-buy.feature"
LOCAL_OVERLAYS = PROJECT_ROOT / "tests" / "bdd" / "overlays" / "BR-UC-002-create-media-buy.feature"

LIVE_REPLAY_SCENARIO = "T-UC-002-v31-idempotency-replay"
BOUNDARY_SCENARIO = "T-UC-002-v31-idempotency-pattern-invalid"
# Upstream supported=true phases visible in the generated feature but NOT wired
# to the BDD harness. The name is about scenario wiring, not production: -expired
# and -canonical-comparison DO ship (AdCPIdempotencyExpiredError and the RFC 8785
# canonicalizer), and both are graded at the real wire in
# tests/integration/test_idempotency_wire_matrix.py — so this is a wiring gap,
# not a coverage floor. -in-flight and -error-conflict-details are the genuinely
# unimplemented pair (production emits SERVICE_UNAVAILABLE with retry_after, and
# a detail-free conflict).
REMAINING_UNWIRED_SCENARIOS = frozenset(
    {
        "T-UC-002-v31-idempotency-in-flight",
        "T-UC-002-v31-idempotency-expired",
        "T-UC-002-v31-idempotency-canonical-comparison",
        "T-UC-002-v31-error-conflict-details",
    }
)


def test_advertised_idempotency_matches_the_implemented_replay():
    """Pin the supported=true discriminant to the implemented replay window."""
    current_tenant.set(None)
    capability = _get_adcp_capabilities_impl(None, None).adcp.idempotency

    assert capability.supported is True
    dumped = capability.model_dump(mode="json", exclude_none=True)
    assert dumped["replay_ttl_seconds"] == int(DEFAULT_REPLAY_TTL.total_seconds()), (
        "The advertised replay window must equal the window the replay cache actually enforces"
    )


def test_generated_replay_scenario_is_live_and_boundary_fixture_durable():
    """Pin the live upstream replay scenario and the exact boundary fixture through regeneration."""
    generated_text = GENERATED_UC002.read_text()

    # The upstream replay scenario grades production replay directly — no
    # local overlay may reconcile it away again while replay is implemented.
    assert f"@{LIVE_REPLAY_SCENARIO}" in generated_text
    assert "v3.1 idempotency_key replay returns existing media buy without re-execution" in generated_text
    assert 'the response should include the previously created "media_buy_id"' in generated_text
    assert "no new ad platform order should have been created" in generated_text

    assert all(f"@{scenario_id}" in generated_text for scenario_id in REMAINING_UNWIRED_SCENARIOS), (
        "Keep the unimplemented upstream supported=true phases visible; they are not passing claims."
    )

    assert f"@{BOUNDARY_SCENARIO}" in generated_text
    assert "| <256 chars>" in generated_text
    assert "Local scenario overlays applied" in generated_text

    local_text = LOCAL_OVERLAYS.read_text()
    assert f"@{BOUNDARY_SCENARIO}" in local_text
    assert "| <256 chars>" in local_text
    # The supported=false replay reconciliation was removed with the restore;
    # its return would silently un-grade production replay.
    assert f"@{LIVE_REPLAY_SCENARIO}" not in local_text
