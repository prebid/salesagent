"""Regression test: partition/boundary 'valid' assertions must check response CONTENT.

Bug salesagent-14g: _assert_partition_or_boundary() for expected="valid" only
checks `assert "response" in ctx` — it never inspects the response content.
This means partition tests pass even when the field under test is completely
ignored by production code.

This test calls _assert_partition_or_boundary() with a response that has
WRONG content for the field being tested. The function SHOULD reject it
(raise AssertionError) but currently accepts it (bug).

When the bug is fixed, these tests will pass.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _get_assert_fn():
    """Import the assertion function under test."""
    from tests.bdd.steps.domain.uc004_delivery import _assert_partition_or_boundary

    return _assert_partition_or_boundary


class TestPartitionAssertionStrength:
    """Verify _assert_partition_or_boundary rejects wrong-content responses for 'valid' rows."""

    def test_valid_status_filter_rejects_wrong_statuses(self):
        """valid + status_filter field → response with WRONG statuses must be rejected.

        The scenario sends status_filter=["paused"] but the response contains
        only "active" media buys. A correct assertion verifies returned
        statuses match the filter.
        """
        fn = _get_assert_fn()

        mock_delivery = MagicMock()
        mock_delivery.status = "active"  # WRONG — filter asked for "paused"
        mock_response = MagicMock()
        mock_response.media_buy_deliveries = [mock_delivery]

        ctx = {
            "response": mock_response,
            "media_buys": {"mb-1": {"status": "active"}, "mb-2": {"status": "paused"}},
            "request_params": {"status_filter": ["paused"]},
        }

        # The assertion function MUST raise for wrong content.
        # If it doesn't raise, the valid branch is too weak (bug present).
        raised = False
        try:
            fn(ctx, "valid", "status_filter")
        except AssertionError:
            raised = True

        assert raised, (
            "BUG salesagent-14g: _assert_partition_or_boundary('valid', 'status_filter') "
            "accepted a response with wrong statuses — it only checks 'response exists', "
            "not response content"
        )

    def test_valid_resolution_rejects_wrong_media_buys(self):
        """valid + resolution field → response with WRONG media buys must be rejected."""
        fn = _get_assert_fn()

        mock_delivery = MagicMock()
        mock_delivery.media_buy_id = "mb-999"  # WRONG — requested mb-001
        mock_response = MagicMock()
        mock_response.media_buy_deliveries = [mock_delivery]

        ctx = {
            "response": mock_response,
            "request_params": {"media_buy_ids": ["mb-001"]},
        }

        raised = False
        try:
            fn(ctx, "valid", "resolution")
        except AssertionError:
            raised = True

        assert raised, (
            "BUG salesagent-14g: _assert_partition_or_boundary('valid', 'resolution') "
            "accepted a response with wrong media_buy_ids — it only checks 'response exists'"
        )
