"""Failed creative uploads must FAIL the approval, never publish the buy (#1637).

Unit coverage for the PURE batching/message logic of ``enrich_uploaded_creatives``
(the manual-approval GAM push path in ``execute_approved_media_buy``): a ``failed``
per-asset status means the remote order exists but is missing creatives, so the
function REPORTS it (returns its description) while still enriching the assets that
DID upload. It does NOT raise — the caller hoists the
``AdapterPostMutationIncomplete`` raise until AFTER its UoW commits, so the successful
enrichment writebacks survive. That persistence — a MIXED batch through the real
``execute_approved_media_buy`` path — is pinned by the integration test
``tests/integration/test_execute_approved_platform_ids.py``
(``TestMixedCreativeUploadPersistsEnrichmentThenFails``).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.core.tools.media_buy_create import enrich_uploaded_creatives


def _status(status: str, creative_id: str | None, message: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(status=status, creative_id=creative_id, message=message)


class TestEnrichUploadedCreatives:
    def test_any_failed_asset_is_reported_and_not_enriched(self):
        creative = MagicMock()
        repo = MagicMock()
        failed = enrich_uploaded_creatives(
            [_status("failed", "cr_1", "asset rejected by GAM")],
            {"cr_1": creative},
            repo,
        )
        assert failed == ["cr_1: asset rejected by GAM"]
        repo.update_data.assert_not_called()

    def test_mixed_batch_enriches_successes_and_reports_only_the_failure(self):
        """A partial batch enriches the successful assets AND reports the failed one —
        the function never raises, so the caller's UoW can commit the enrichments before
        failing the approval."""
        ok_creative = MagicMock()
        repo = MagicMock()
        enriched = {"enriched": True}
        ok_status = _status("uploaded", "cr_ok")
        with patch("src.core.tools.media_buy_create._apply_creative_enrichment", return_value=enriched) as enrich:
            failed = enrich_uploaded_creatives(
                [ok_status, _status("failed", "cr_bad", "creative too large")],
                {"cr_ok": ok_creative, "cr_bad": MagicMock()},
                repo,
            )
        enrich.assert_called_once_with(ok_creative, ok_status)
        repo.update_data.assert_called_once_with(ok_creative, enriched)
        assert failed == ["cr_bad: creative too large"]

    def test_all_successful_batch_reports_no_failures(self):
        repo = MagicMock()
        with patch("src.core.tools.media_buy_create._apply_creative_enrichment", return_value=None):
            failed = enrich_uploaded_creatives(
                [_status("uploaded", "cr_1"), _status("uploaded", None)],
                {"cr_1": MagicMock()},
                repo,
            )
        assert failed == []
