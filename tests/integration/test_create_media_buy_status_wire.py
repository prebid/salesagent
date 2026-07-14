"""create_media_buy success wire emits the media_buy_status lifecycle field.

Regression for the pre-existing gap tracked in GitHub #1326 (partial — the
create-success emission only): _create_media_buy_impl computes the lifecycle
via _determine_media_buy_status and feeds it to valid_actions_for_status, but
CreateMediaBuySuccess.sync_success(...) is built WITHOUT media_buy_status —
exclude_none serialization silently drops the None-defaulted inherited field,
so the wire never carries the lifecycle the buyer needs.

Spec grounding (AdCP 3.1.1, upstream v3.1.1 tag —
dist/schemas/3.1.1/media-buy/create-media-buy-response.json): the
CreateMediaBuySuccess variant defines media_buy_status ("Initial media buy
status... at the top level of flat-on-the-wire MCP responses, the status key
is reserved for the envelope TaskStatus"). Conformance storyboard
dist/compliance/3.1.1/domains/media-buy/scenarios/pending_creatives_to_start.yaml
step create_buy_no_creatives grades field_value media_buy_status ==
"pending_creatives" ALONGSIDE field_value status == "completed" (scenario
gated by requires_capability media_buy.creative_approval_mode == auto_approve).
The deprecated per-buy legacy "status" lifecycle slot must NOT be emitted —
the top-level status slot belongs to the envelope TaskStatus.

Dry-run sibling: the spec is silent on dry-run, so production is authoritative
(see tests/integration/test_media_buy_dry_run_status.py) — the dry-run branch
previews the would-be outcome, and its existing valid_actions source is
MediaBuyStatus.pending_start, so the emitted media_buy_status must be
"pending_start" (coupled to the same single value source; the spec forbids
divergent lifecycle emission).

Wire faithfulness: CreateMediaBuyResult._serialize (src/core/schemas/_base.py)
produces the transport-invariant body — result.model_dump(mode="json") is the
same serializer every transport emits, and it is exactly the exclude_none path
that drops the field today (reverting the media_buy_status kwarg goes red).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.harness.media_buy_create import MediaBuyCreateEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# All lifecycle values _determine_media_buy_status can return — members of the
# adcp 6.6 MediaBuyStatus enum. Kept literal so the test pins the wire contract
# rather than echoing production's enum import.
_LIFECYCLE_VALUES = {"completed", "pending_creatives", "pending_start", "active"}


def _create_kwargs(product, *, domain: str) -> dict:
    """Create kwargs with packages WITHOUT creative_ids.

    has_creatives=False drives _determine_media_buy_status to
    "pending_creatives" — the storyboard create_buy_no_creatives step.
    """
    now = datetime.now(UTC)
    return {
        "brand": {"domain": domain},
        "packages": [{"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
        "start_time": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time": (now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "po_number": "STATUS-WIRE-1",
    }


def test_create_success_wire_carries_media_buy_status_distinct_from_envelope_status(integration_db):
    """Main success branch: the wire emits media_buy_status = "pending_creatives".

    Storyboard pending_creatives_to_start.yaml step create_buy_no_creatives:
    a fresh auto-approved buy with no creatives assigned reports the
    pending_creatives lifecycle in media_buy_status while the top-level status
    slot carries the envelope TaskStatus "completed" — both present, distinct.
    """
    with MediaBuyCreateEnv() as env:
        _tenant, _principal, product, _pricing = env.setup_media_buy_data()

        result = env.call_impl(**_create_kwargs(product, domain="status-wire.example.com"))

    envelope = result.model_dump(mode="json")

    assert envelope["status"] == "completed", (
        f"top-level status slot is reserved for the envelope TaskStatus, got {envelope.get('status')!r}"
    )
    assert "media_buy_status" in envelope, (
        "create-success wire must carry the media_buy_status lifecycle field "
        "(spec 3.1.1 create-media-buy-response.json; storyboard "
        "pending_creatives_to_start.yaml step create_buy_no_creatives) — "
        "exclude_none dropped it because sync_success was built without the kwarg"
    )
    assert envelope["media_buy_status"] == "pending_creatives", (
        f"a fresh buy with no creatives assigned must report lifecycle "
        f"'pending_creatives', got {envelope['media_buy_status']!r}"
    )
    assert envelope["media_buy_status"] in _LIFECYCLE_VALUES
    assert envelope["media_buy_status"] != envelope["status"], (
        "the lifecycle and the envelope TaskStatus are distinct fields on this "
        "wire — identical values here would mean the lifecycle leaked into (or "
        "was copied from) the envelope slot"
    )


def test_create_dry_run_wire_carries_media_buy_status_pending_start(integration_db):
    """Dry-run branch: the simulated success previews media_buy_status = "pending_start".

    Spec silent on dry-run -> production authoritative: the branch already
    derives valid_actions from MediaBuyStatus.pending_start; the emitted
    media_buy_status must come from that same single value source.
    """
    with MediaBuyCreateEnv(dry_run=True) as env:
        _tenant, _principal, product, _pricing = env.setup_media_buy_data()

        result = env.call_impl(**_create_kwargs(product, domain="status-wire-dry.example.com"))

    envelope = result.model_dump(mode="json")
    response_envelope = result.response.model_dump(mode="json")

    # Branch-proof: only the dry-run branch mints a "dry_run_"-prefixed
    # media_buy_id (no adapter call, no persisted buy).
    assert response_envelope["media_buy_id"].startswith("dry_run_"), (
        "guard must exercise the dry_run branch — non-simulated media_buy_id returned"
    )
    assert envelope["status"] == "completed", (
        f"dry_run previews the would-be completed envelope, got {envelope.get('status')!r}"
    )
    assert "media_buy_status" in envelope, (
        "dry-run create-success wire must carry the media_buy_status lifecycle "
        "preview — the branch's valid_actions already derive from pending_start, "
        "and the lifecycle emission must come from that same value source"
    )
    assert envelope["media_buy_status"] == "pending_start", (
        f"dry-run simulated lifecycle must preview 'pending_start' (the value "
        f"already feeding valid_actions_for_status on this branch), got "
        f"{envelope['media_buy_status']!r}"
    )
