"""Unit test to reproduce the exact customer webhook request.

This tests the specific code path that was causing:
'CreateMediaBuyResponse' object has no attribute 'message'

Regression test for: PR #339
Customer: Damascus-v1 test agent
Error: AttributeError when accessing response.message on CreateMediaBuyResponse
"""

import pytest

from src.core.schemas import (
    CreateMediaBuyResponse,
    GetProductsResponse,
    SyncCreativeResult,
    SyncCreativesResponse,
)


def test_create_media_buy_response_message_access():
    """Test that we can safely extract messages from CreateMediaBuyResponse.

    This reproduces the exact error the customer (Damascus-v1) was seeing:
    AttributeError: 'CreateMediaBuyResponse' object has no attribute 'message'

    The bug was on line 1382 in _handle_get_creatives_skill where we tried
    to access response.message, but CreateMediaBuyResponse doesn't have that field.
    """
    # Create a response like the one from create_media_buy
    response = CreateMediaBuyResponse(
        status="completed",
        buyer_ref="test-webhook-mb-001",
        media_buy_id="mb-12345",
    )

    # TEST 1: The OLD BROKEN pattern (what was causing the error)
    with pytest.raises(AttributeError, match="has no attribute 'message'"):
        # This is what line 1382 was doing - should raise AttributeError
        _ = response.message or "Default message"

    # TEST 2: The NEW SAFE pattern (our fix)
    # This is our fix - uses __str__ method
    message = str(response)
    assert isinstance(message, str), "Message must be a string"
    assert len(message) > 0, "Message must not be empty"
    assert "mb-12345" in message, "Message should contain media_buy_id"

    # TEST 3: Verify the A2A response dict construction works
    # This is what _handle_create_media_buy_skill does
    a2a_response = {
        "success": True,
        "media_buy_id": response.media_buy_id,
        "status": response.status,
        "message": str(response),  # The fix
    }
    assert a2a_response["message"] == "Media buy mb-12345 created successfully."


def test_other_response_types():
    """Test that str() pattern works for all response types.

    Verifies that using str(response) is safe for:
    - Responses with __str__() generating messages (GetProductsResponse)
    - Responses with explicit message fields (SyncCreativesResponse)
    """
    # Test GetProductsResponse (generates message via __str__())
    response1 = GetProductsResponse(products=[])
    msg1 = str(response1)
    assert msg1 == "No products matched your requirements."

    # Test SyncCreativesResponse (HAS .message field)
    response2 = SyncCreativesResponse(
        status="completed",
        message="Synced 1 creative",
        results=[SyncCreativeResult(buyer_ref="test-001", creative_id="cr-001", status="approved", action="created")],
    )
    msg2 = str(response2)
    assert "Synced 1 creative" in msg2
