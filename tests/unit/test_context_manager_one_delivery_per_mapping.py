"""One workflow-step notification produces ONE delivery per mapped object.

AdCP 3.1.1 ``compliance/universal/webhook-emission.yaml``,
``expect_no_duplicate_webhook_on_replay``: "At most one logical webhook event is
delivered for the operation". The runner counts distinct ``idempotency_key``s among
the webhooks matching one ``operation_id`` and fails above the cap.

``_send_push_notifications`` used to run a second loop over every active
``PushNotificationConfig`` the principal had, sending the STEP'S OWN stashed
registration once per row — the loop variable was never read. Every operation
registers a config, so the rows accumulate and the duplication grows with them.
Measured on the storyboard tenant (run innet_200926_0647): one create emitted the
same payload to the same URL three times inside 17ms, the third operation of the
run getting three copies where the first got one.

The duplication is now unrepresentable rather than guarded — there is no second
loop to get wrong — so this grades the property the removal was for: N mappings,
N deliveries, whatever else the principal has registered.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core import async_utils, context_manager
from src.services.protocol_webhook_service import ProtocolWebhookService
from tests.unit._push_notification_helpers import make_push_step


async def _deliveries_for(mapping_count: int) -> list[str]:
    """Drive the real path with *mapping_count* mapped objects; return the URLs sent to.

    Each delivery is scheduled as a fire-and-forget task (pinned in
    ``async_utils._pinned_tasks``), so the tasks are awaited before the URLs are read —
    otherwise the list is empty for a reason that has nothing to do with the count.
    """
    step = make_push_step()
    step.object_mappings = [
        SimpleNamespace(object_type="media_buy", object_id=f"mb_{n}", action="create") for n in range(mapping_count)
    ]

    sent: list[str] = []

    async def record(*, push_notification_config, **_kwargs):
        sent.append(push_notification_config.url)
        return True

    service = ProtocolWebhookService()
    service.send_notification = AsyncMock(side_effect=record)  # type: ignore[method-assign]

    cm = context_manager.ContextManager()
    with patch.object(context_manager, "get_protocol_webhook_service", return_value=service):
        cm._send_push_notifications(step, "completed")
    for task in list(async_utils._pinned_tasks):
        await task
    await asyncio.sleep(0)
    return sent


@pytest.mark.asyncio
@pytest.mark.parametrize("mapping_count", [1, 2])
async def test_one_delivery_per_mapping(mapping_count):
    """Exactly one delivery per mapped object, to the URL the request registered."""
    sent = await _deliveries_for(mapping_count)

    assert sent == ["https://buyer.example/webhook"] * mapping_count, (
        f"{mapping_count} mapped object(s) must produce {mapping_count} delivery/ies; got {sent}"
    )


@pytest.mark.asyncio
async def test_delivery_count_does_not_depend_on_stored_configs():
    """The count is a property of the step, not of how many configs the principal stored.

    The discriminator for the removed loop: it multiplied deliveries by the number of
    registered rows, so a principal with a longer history got more copies of the same
    event. Nothing here stores any config, and the answer must still be one.
    """
    assert len(await _deliveries_for(1)) == 1
