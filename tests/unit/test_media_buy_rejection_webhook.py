"""The seller rejection webhook MUST carry ``rejection_reason`` (MCP + A2A).

Pinned AdCP 3.1.1 ``core/media-buy.json`` defines ``rejection_reason``
("present only when status is 'rejected'"), and ``specification.mdx`` makes it a
MUST on the seller rejection notification: the webhook payload MUST include
``media_buy_id``, ``status: "rejected"``, and ``rejection_reason``.

The admin reject route (src/admin/blueprints/operations.py) records the reason on
the workflow step but previously dropped it from the notification result. These
tests assert on the SERIALIZED WIRE the buyer receives — the two-layer envelope
built by the adcp SDK payload builders — for both transports, and that the field
is omitted (not fabricated) on a non-rejection result. #1544.
"""

import json

from adcp import create_a2a_webhook_payload, create_mcp_webhook_payload
from adcp.types import GeneratedTaskStatus as AdcpTaskStatus

from src.core.schemas import CreateMediaBuySuccess
from src.core.schemas._base import Package
from src.core.webhook_validator import validate_webhook_task_type
from src.services.protocol_webhook_service import _to_wire_dict

_REASON = "brand safety: inventory not eligible for this advertiser"


def _rejected_result() -> CreateMediaBuySuccess:
    """The internal result the reject route sends — confirmed_at None for a rejected buy."""
    return CreateMediaBuySuccess(
        media_buy_id="mb_123",
        packages=[Package(package_id="pkg_1")],
        confirmed_at=None,
        revision=2,
        rejection_reason=_REASON,
    )


def test_mcp_rejection_webhook_carries_rejection_reason():
    payload = create_mcp_webhook_payload(
        task_id="step_1",
        task_type=validate_webhook_task_type("create_media_buy"),
        result=_rejected_result(),
        status=AdcpTaskStatus.rejected,
    )
    wire = _to_wire_dict(payload)
    assert _REASON in json.dumps(wire), f"MCP rejection webhook must carry rejection_reason; wire={wire}"


def test_a2a_rejection_webhook_carries_rejection_reason():
    payload = create_a2a_webhook_payload(
        task_id="step_1",
        status=AdcpTaskStatus.rejected,
        result=_rejected_result(),
        context_id="ctx_1",
    )
    wire = _to_wire_dict(payload)
    assert _REASON in json.dumps(wire), f"A2A rejection webhook must carry rejection_reason; wire={wire}"


def test_rejection_reason_omitted_when_absent():
    """A non-rejection result must NOT carry rejection_reason on the wire (present
    only when status is 'rejected', per the pinned schema)."""
    result = CreateMediaBuySuccess(media_buy_id="mb_123", packages=[Package(package_id="pkg_1")], revision=1)
    assert "rejection_reason" not in result.model_dump(mode="json", exclude_none=True)
