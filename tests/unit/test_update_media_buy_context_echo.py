"""Regression test: ``_update_media_buy_impl`` echoes the buyer's
context unchanged.

AdCP defines ``context`` as opaque correlation data the agent must
echo unchanged in the response (see
``adcp/types/.../update_media_buy_request.py:7427-7433`` —
"Opaque correlation data that is echoed unchanged in responses").

The buyer-supplied context must NOT be replaced by a stale value
(e.g. the original create_media_buy's context that was persisted in
``raw_request``).

Issue: #91 — surfaced after #87 cleared the
``account: Field required`` blocker. The lifecycle e2e test then
reached line 295 and failed asserting:

    {'e2e': 'create_media_buy'} == {'e2e': 'update_media_buy'}

Where ``{'e2e': 'create_media_buy'}`` was the persisted create's
context and ``{'e2e': 'update_media_buy'}`` was what the buyer
supplied on the update call.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_pause_resume_response_echoes_buyer_context():
    """The pause/resume success branch must echo ``request.context``."""
    from tests.harness import MediaBuyUpdateEnv

    with MediaBuyUpdateEnv() as env:
        env.set_media_buy(media_buy_id="mb-001")
        env.mock["adapter"].return_value.update_media_buy.return_value = type(
            "Stub",
            (),
            {"media_buy_id": "mb-001", "affected_packages": []},
        )()

        update_ctx = {"e2e": "update_media_buy", "trace_id": "abc-123"}
        response = env.call_impl(media_buy_id="mb-001", paused=False, context=update_ctx)

    if hasattr(response, "errors") and response.errors:
        pytest.fail(f"Update returned an error variant unexpectedly: {response.errors}")

    data = response.model_dump(mode="json", exclude_none=True)
    assert data.get("context") == update_ctx, (
        "AdCP requires response.context to echo the buyer-supplied request.context "
        "unchanged. Pause/resume branch missing context echo — see #91."
    )


@pytest.mark.asyncio
async def test_budget_update_response_echoes_buyer_context():
    """The budget-update success branch (line 1230 path) must echo
    ``request.context``. This is the path the lifecycle e2e exercises."""
    from tests.harness import MediaBuyUpdateEnv

    with MediaBuyUpdateEnv() as env:
        env.set_media_buy(media_buy_id="mb-001", currency="USD")
        # Adapter not invoked on budget-only updates — but stub anyway in case the
        # impl branches.
        env.mock["adapter"].return_value.update_media_buy.return_value = type(
            "Stub",
            (),
            {"media_buy_id": "mb-001", "affected_packages": []},
        )()
        env._uow_instance.media_buys.get_packages.return_value = []

        update_ctx = {"e2e": "update_media_buy", "trace_id": "xyz-456"}
        response = env.call_impl(media_buy_id="mb-001", budget=7500.0, context=update_ctx)

    if hasattr(response, "errors") and response.errors:
        pytest.fail(f"Update returned an error variant unexpectedly: {response.errors}")

    data = response.model_dump(mode="json", exclude_none=True)
    assert (
        data.get("context") == update_ctx
    ), "AdCP requires response.context to echo the buyer-supplied request.context unchanged."
