"""Capability applicability guard for the generated UC-002 idempotency phases.

The derivative adcp-req storyboard currently leaves supported=true replay
phases unconditional and carries a fragile hand-counted maxLength fixture. The
compiler reconciles the live replay id to supported=false behavior and replaces
the boundary value with an exact-length token; remaining upstream-only replay
phases stay visible but are not claimed as passing scenarios for this seller.
"""

from pathlib import Path

from src.core.config_loader import current_tenant
from src.core.tools.capabilities import _get_adcp_capabilities_impl

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATED_UC002 = PROJECT_ROOT / "tests" / "bdd" / "features" / "BR-UC-002-create-media-buy.feature"
LOCAL_RECONCILIATION = PROJECT_ROOT / "tests" / "bdd" / "overlays" / "BR-UC-002-create-media-buy.feature"

RECONCILED_REPLAY_SCENARIO = "T-UC-002-v31-idempotency-replay"
RECONCILED_BOUNDARY_SCENARIO = "T-UC-002-v31-idempotency-pattern-invalid"
REMAINING_SUPPORTED_TRUE_ONLY_SCENARIOS = frozenset(
    {
        "T-UC-002-v31-idempotency-in-flight",
        "T-UC-002-v31-idempotency-expired",
        "T-UC-002-v31-idempotency-canonical-comparison",
        "T-UC-002-v31-error-conflict-details",
    }
)


def test_generated_idempotency_reconciliations_are_durable():
    """Pin the false discriminant and exact boundary fixture through regeneration."""
    current_tenant.set(None)
    capability = _get_adcp_capabilities_impl(None, None).adcp.idempotency

    assert capability.supported is False
    assert capability.model_dump(mode="json") == {"supported": False}
    assert not hasattr(capability, "replay_ttl_seconds")
    assert not hasattr(capability, "in_flight_max_seconds")

    generated_text = GENERATED_UC002.read_text()
    assert all(f"@{scenario_id}" in generated_text for scenario_id in REMAINING_SUPPORTED_TRUE_ONLY_SCENARIOS), (
        "Keep the unreconciled upstream supported=true-only phases visible; "
        "they are not supported=false passing claims."
    )
    assert f"@{RECONCILED_REPLAY_SCENARIO}" in generated_text
    assert "Local scenario overlays applied" in generated_text
    assert "Advertised unsupported idempotency_key does not suppress create execution" in generated_text
    assert 'the response should include a newly created "media_buy_id"' in generated_text
    assert "exactly one new media buy should have been persisted" in generated_text
    assert "the response should not be marked as replayed" in generated_text
    assert f"@{RECONCILED_BOUNDARY_SCENARIO}" in generated_text
    assert "| <256 chars>" in generated_text

    local_text = LOCAL_RECONCILIATION.read_text()
    assert "runner/fixture defects" in local_text
    assert f"@{RECONCILED_REPLAY_SCENARIO}" in local_text
    assert "exactly one new media buy should have been persisted" in local_text
    assert f"@{RECONCILED_BOUNDARY_SCENARIO}" in local_text
    assert "| <256 chars>" in local_text
