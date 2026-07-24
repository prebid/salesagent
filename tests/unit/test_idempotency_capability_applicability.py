"""Capability + storyboard applicability guard for the UC-002 idempotency phases.

``idempotency.supported`` is ONE agent-wide claim, not per-tool
(``get-adcp-capabilities-response.json`` v3.1.1 models it as a discriminated
union on a single boolean). ``create_media_buy`` implements verbatim replay,
but the other twelve ``require_idempotency_key(`` call sites — including
``update_media_buy`` — validate and accept the key without deduplicating, so
the seller advertises ``supported=false`` as the honest blanket declaration;
advertising ``true`` would tell every buyer, for every mutating call, that a
blind retry is safe, which is false for twelve of thirteen call sites.

This DOES cost real external conformance credit: the published storyboard
(``dist/compliance/3.1.1/universal/idempotency.yaml``) grades its replay /
changed-payload-conflict / fresh-key phases only for sellers declaring
``supported: true``, and a future conformance runner implementing that
precondition gate will skip grading create_media_buy's real replay behavior
here. That is accepted deliberately — a false blanket promise across twelve
sites is a worse defect than an accurate but conservative one on the
thirteenth — and is tracked at #1607 (extend dedupe through the same
``IdempotencyAttemptRepository`` to the remaining call sites, at which point
``true`` becomes accurate again).

The generated UC-002 feature keeps the upstream replay scenario LIVE
regardless: it drives a real ``create_media_buy`` call twice through
``MediaBuyCreateEnv`` and grades the actual replay behavior directly (see
``_UC002_IDEMPOTENCY_WIRED`` in ``tests/bdd/conftest.py``), not the
capabilities declaration — so it stays a true claim about production
independent of what this file pins for the capability block. The boundary
outline uses the exact-length fixture token instead of a hand-counted
literal, and the remaining supported=true phases production does not yet
implement (in-flight tracking and its error-detail siblings) stay visible but
unwired.
"""

from pathlib import Path

from src.core.config_loader import current_tenant
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


def test_advertised_idempotency_is_the_honest_blanket_declaration():
    """The capability block must not claim a guarantee twelve of thirteen sites don't keep.

    Regression guard for the reviewed defect: an agent-wide ``supported=true``
    was previously justified purely by create_media_buy's real replay, while
    update_media_buy/sync_accounts/sync_creatives silently re-execute a
    retried request. Flip this back to ``True`` only alongside evidence that
    every ``require_idempotency_key(`` call site actually deduplicates.
    """
    current_tenant.set(None)
    capability = _get_adcp_capabilities_impl(None, None).adcp.idempotency

    assert capability.supported is False
    assert not hasattr(capability, "replay_ttl_seconds"), (
        "IdempotencyUnsupported must not carry replay_ttl_seconds — the discriminated union forbids it"
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
    # This scenario grades create_media_buy's real replay behavior directly
    # (via MediaBuyCreateEnv), independent of what the capabilities block
    # advertises — a local overlay removing it here because the agent-wide
    # capability now declares `false` would silently un-grade a real,
    # verified behavior for the wrong reason.
    assert f"@{LIVE_REPLAY_SCENARIO}" not in local_text
