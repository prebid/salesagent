"""A pending-approval create_media_buy emits the SUBMITTED envelope, not a confirmed Success.

Regression for PR #1567 round-2 blocker 2 (adcp bump — adcp 5.7->6.6 bump, round-2 review
blocker 2). adcp 6.6 (spec 3.1.1) made status/confirmed_at/revision required on
the raw CreateMediaBuySuccess envelope, and our subclass defaults them
(status="completed", confirmed_at=<now>, revision=1) via sync_success(). Both
manual-approval branches of _create_media_buy_impl return that factory while the
protocol envelope status is "submitted" — so the wire asserts the seller
CONFIRMED (confirmed_at set, revision issued) a buy that is awaiting human
approval and not yet committed.

Spec grounding (pinned 3.1.1,
tests/fixtures/adcp_schemas_pinned/media-buy/create-media-buy-response.json):
the response oneOf has exactly three mutually exclusive shapes; the pending
case is "CreateMediaBuySubmitted" — required ``status`` (const "submitted") +
``task_id``; "the media_buy_id and packages land on the task's completion
artifact, not this envelope". Storyboard: T-UC-002-alt-manual (POST-S7..S10 —
buyer tracks the pending buy via task_id). update_media_buy already emits its
Submitted variant on the sibling path (commit b8b7e751b).

Wire faithfulness: CreateMediaBuyResult._serialize (src/core/schemas/_base.py)
produces the transport-invariant body — response.model_dump() with the envelope
status overriding — so asserting on the serialized result exercises the same
shape every transport emits.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.harness.media_buy_create import MediaBuyCreateEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _create_kwargs(product):
    now = datetime.now(UTC)
    return {
        "brand": {"domain": "pending-approval-wire.example.com"},
        "packages": [{"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
        "start_time": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time": (now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "po_number": "PENDING-WIRE-1",
    }


def _assert_submitted_shape(envelope: dict) -> None:
    """Assert the pinned-3.1.1 CreateMediaBuySubmitted shape on the wire body."""
    assert envelope["status"] == "submitted", f"expected submitted envelope, got {envelope['status']!r}"
    assert envelope.get("task_id"), (
        "a submitted create must carry the required task_id the buyer polls for the outcome "
        "(pinned create-media-buy-response.json, CreateMediaBuySubmitted.required)"
    )
    # The submitted variant has NO confirmation fields — confirmed_at/revision
    # assert seller commitment, and media_buy_id belongs on the task's
    # completion artifact, not this envelope. Their presence makes the wire
    # claim a not-yet-committed buy is confirmed (and breaks the spec's
    # mutually-exclusive oneOf discrimination against the Success shape).
    for confirmation_field in ("confirmed_at", "revision", "media_buy_id"):
        assert envelope.get(confirmation_field) is None, (
            f"submitted (pending-approval) create must not carry {confirmation_field!r}, "
            f"got {envelope.get(confirmation_field)!r} — the buy is NOT confirmed yet"
        )
    # The schema-compatible replay marker is omitted when False. This seller
    # advertises idempotency unsupported, so create never sets it True.
    assert "replayed" not in envelope, (
        f"fresh submitted create leaked replayed={envelope.get('replayed')!r} — "
        "the marker is omitted when False (uniform with fresh Success)"
    )


def test_adapter_manual_approval_create_emits_submitted_not_confirmed(integration_db):
    """Manual-approval branch (adapter requires approval): submitted shape on the wire."""
    with MediaBuyCreateEnv() as env:
        _tenant, _principal, product, _pricing = env.setup_media_buy_data()
        adapter = env.mock["adapter"].return_value
        adapter.manual_approval_required = True
        adapter.manual_approval_operations = ["create_media_buy"]

        result = env.call_impl(**_create_kwargs(product))

    _assert_submitted_shape(result.model_dump(mode="json"))


def test_config_approval_create_emits_submitted_not_confirmed(integration_db):
    """Config-approval branch (tenant auto_create_media_buys=False): submitted shape on the wire."""
    with MediaBuyCreateEnv(auto_create_media_buys=False) as env:
        _tenant, _principal, product, _pricing = env.setup_media_buy_data()

        result = env.call_impl(**_create_kwargs(product))

    _assert_submitted_shape(result.model_dump(mode="json"))


def test_same_key_submitted_create_executes_again(integration_db):
    """With supported=false, a same-key retry creates a new submitted task."""
    import uuid as _uuid

    with MediaBuyCreateEnv(auto_create_media_buys=False) as env:
        _tenant, _principal, product, _pricing = env.setup_media_buy_data()
        kwargs = _create_kwargs(product)
        kwargs["idempotency_key"] = f"pending-replay-{_uuid.uuid4().hex}"
        ctx_mgr = env.mock["context_mgr"].return_value

        first = env.call_impl(**dict(kwargs))
        second = env.call_impl(**dict(kwargs))

    first_env = first.model_dump(mode="json")
    second_env = second.model_dump(mode="json")
    _assert_submitted_shape(first_env)
    _assert_submitted_shape(second_env)
    assert second_env["task_id"] != first_env["task_id"]
    assert ctx_mgr.create_workflow_step.call_count == 2
