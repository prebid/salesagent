"""Failed creative uploads must FAIL the approval, never publish the buy (#1637).

Drives the REAL production branch (``raise_on_failed_creative_uploads``, the
manual-approval GAM push path in ``execute_approved_media_buy``): a ``failed``
per-asset status means the remote order exists but is missing creatives —
continuing would approve the order and publish a buy that cannot serve those
creatives. The branch must raise ``AdapterPostMutationIncomplete`` (post-mutation
by definition) while still enriching the assets that DID upload.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.base import AdapterPostMutationIncomplete
from src.core.tools.media_buy_create import raise_on_failed_creative_uploads


def _status(status: str, creative_id: str | None, message: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(status=status, creative_id=creative_id, message=message)


class TestFailedCreativeUploadsFailTheApproval:
    def test_any_failed_asset_raises_post_mutation_incomplete(self):
        creative = MagicMock()
        repo = MagicMock()
        with pytest.raises(AdapterPostMutationIncomplete) as exc_info:
            raise_on_failed_creative_uploads(
                [_status("failed", "cr_1", "asset rejected by GAM")],
                {"cr_1": creative},
                repo,
            )
        assert "cr_1" in str(exc_info.value)
        assert "asset rejected by GAM" in str(exc_info.value)
        repo.update_data.assert_not_called()

    def test_mixed_batch_enriches_successes_then_raises(self):
        """A partial batch keeps the successful assets' enrichment writebacks but the
        approval still fails — the buy must not proceed to order approval."""
        ok_creative = MagicMock()
        repo = MagicMock()
        enriched = {"enriched": True}
        ok_status = _status("uploaded", "cr_ok")
        with (
            patch("src.core.tools.media_buy_create._apply_creative_enrichment", return_value=enriched) as enrich,
            pytest.raises(AdapterPostMutationIncomplete) as exc_info,
        ):
            raise_on_failed_creative_uploads(
                [
                    ok_status,
                    _status("failed", "cr_bad", "creative too large"),
                ],
                {"cr_ok": ok_creative, "cr_bad": MagicMock()},
                repo,
            )
        enrich.assert_called_once_with(ok_creative, ok_status)
        repo.update_data.assert_called_once_with(ok_creative, enriched)
        assert "1 creative(s) failed" in str(exc_info.value)
        assert "cr_bad: creative too large" in str(exc_info.value)

    def test_all_successful_batch_does_not_raise(self):
        repo = MagicMock()
        with patch("src.core.tools.media_buy_create._apply_creative_enrichment", return_value=None):
            raise_on_failed_creative_uploads(
                [_status("uploaded", "cr_1"), _status("uploaded", None)],
                {"cr_1": MagicMock()},
                repo,
            )  # no exception

    def test_failure_is_the_post_mutation_type_not_a_handled_false(self):
        """The exception TYPE is the contract: the finalizer maps
        AdapterPostMutationIncomplete to manual_required (signal preserved), whereas a
        plain (False, msg) would terminal-fail the buy over a live partial order."""
        with pytest.raises(AdapterPostMutationIncomplete):
            raise_on_failed_creative_uploads([_status("failed", "cr_x", "boom")], {}, MagicMock())
