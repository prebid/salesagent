"""Kevel / Triton / Xandr create_media_buy post-mutation boundary (#1637 parity with GAM).

Each of these adapters performs TWO sequential remote writes inside one
``create_media_buy`` call: a campaign/insertion-order create, then one
flight/line-item create per package. Before this fix, a failure on the SECOND
write (after the FIRST had already committed a real remote object) propagated
as a bare exception — indistinguishable from a failure BEFORE any remote
mutation happened. The approval finalizer classified that as an ordinary
failure, cleared the ``finalizing`` reconciliation state, and published
``failed`` — orphaning a real campaign/IO on the ad server with no tracking.

These tests pin the fix: a failure strictly AFTER the first remote write
succeeds must raise ``AdapterPostMutationIncomplete`` (the typed signal the
finalizer maps to manual reconciliation, not a terminal failure — see
``src/admin/services/media_buy_completion.py``'s ``except
AdapterPostMutationIncomplete`` branch). A failure on the FIRST write, before
anything remote exists, must NOT be reclassified — it stays an ordinary
exception (pre-mutation boundary is not moved earlier than the review asked).
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from src.adapters.base import AdapterPostMutationIncomplete
from src.adapters.kevel import Kevel
from src.adapters.triton_digital import TritonDigital

# mock_principal / sample_request / sample_packages fixtures are shared with
# test_adapter_packages_fix.py via tests/unit/conftest.py.


class TestKevelPostMutationBoundary:
    def _make_adapter(self, mock_principal):
        config = {"api_key": "test_key", "base_url": "https://api.kevel.com", "network_id": "456"}
        mock_principal.get_adapter_id = Mock(return_value="123")
        return Kevel(config=config, principal=mock_principal, dry_run=False, tenant_id="tenant_123")

    def test_flight_failure_after_campaign_created_raises_post_mutation_incomplete(
        self, mock_principal, sample_request, sample_packages
    ):
        """Campaign create succeeds, first flight create fails -> AdapterPostMutationIncomplete."""
        adapter = self._make_adapter(mock_principal)

        campaign_response = Mock()
        campaign_response.json.return_value = {"Id": 999}
        campaign_response.raise_for_status = Mock()

        flight_response = Mock()
        flight_response.raise_for_status = Mock(side_effect=RuntimeError("Kevel 500"))

        with patch("src.adapters.kevel.requests.post", side_effect=[campaign_response, flight_response]):
            with pytest.raises(AdapterPostMutationIncomplete, match="999"):
                adapter.create_media_buy(
                    request=sample_request,
                    packages=sample_packages,
                    start_time=datetime.now(),
                    end_time=datetime.now() + timedelta(days=30),
                )

    def test_campaign_creation_failure_is_not_reclassified(self, mock_principal, sample_request, sample_packages):
        """A failure on the CAMPAIGN create itself (no remote object exists yet) stays a plain exception."""
        adapter = self._make_adapter(mock_principal)

        campaign_response = Mock()
        campaign_response.raise_for_status = Mock(side_effect=RuntimeError("Kevel unreachable"))

        with patch("src.adapters.kevel.requests.post", side_effect=[campaign_response]):
            with pytest.raises(RuntimeError, match="Kevel unreachable"):
                adapter.create_media_buy(
                    request=sample_request,
                    packages=sample_packages,
                    start_time=datetime.now(),
                    end_time=datetime.now() + timedelta(days=30),
                )


class TestTritonPostMutationBoundary:
    def _make_adapter(self, mock_principal):
        config = {"auth_token": "test_token", "base_url": "https://api.tritondigital.com"}
        mock_principal.get_adapter_id = Mock(return_value="123")
        return TritonDigital(config=config, principal=mock_principal, dry_run=False, tenant_id="tenant_123")

    def test_flight_failure_after_campaign_created_raises_post_mutation_incomplete(
        self, mock_principal, sample_request, sample_packages
    ):
        adapter = self._make_adapter(mock_principal)

        campaign_response = Mock()
        campaign_response.json.return_value = {"id": 888}
        campaign_response.raise_for_status = Mock()

        flight_response = Mock()
        flight_response.raise_for_status = Mock(side_effect=RuntimeError("Triton 500"))

        with patch("src.adapters.triton_digital.requests.post", side_effect=[campaign_response, flight_response]):
            with pytest.raises(AdapterPostMutationIncomplete, match="888"):
                adapter.create_media_buy(
                    request=sample_request,
                    packages=sample_packages,
                    start_time=datetime.now(),
                    end_time=datetime.now() + timedelta(days=30),
                )

    def test_campaign_creation_failure_is_not_reclassified(self, mock_principal, sample_request, sample_packages):
        adapter = self._make_adapter(mock_principal)

        campaign_response = Mock()
        campaign_response.raise_for_status = Mock(side_effect=RuntimeError("Triton unreachable"))

        with patch("src.adapters.triton_digital.requests.post", side_effect=[campaign_response]):
            with pytest.raises(RuntimeError, match="Triton unreachable"):
                adapter.create_media_buy(
                    request=sample_request,
                    packages=sample_packages,
                    start_time=datetime.now(),
                    end_time=datetime.now() + timedelta(days=30),
                )


class TestXandrPostMutationBoundary:
    def test_line_item_failure_after_io_created_raises_post_mutation_incomplete(
        self, mock_principal, sample_request, sample_packages, make_xandr_test_adapter
    ):
        adapter = make_xandr_test_adapter(mock_principal)
        io_response = {"response": {"insertion-order": {"id": 555}}}

        with patch.object(adapter, "_make_request", side_effect=[io_response, RuntimeError("Xandr line-item 500")]):
            with pytest.raises(AdapterPostMutationIncomplete, match="555"):
                adapter.create_media_buy(
                    request=sample_request,
                    packages=sample_packages,
                    start_time=datetime.now(),
                    end_time=datetime.now() + timedelta(days=30),
                )

    def test_io_creation_failure_is_not_reclassified(
        self, mock_principal, sample_request, sample_packages, make_xandr_test_adapter
    ):
        adapter = make_xandr_test_adapter(mock_principal)

        with patch.object(adapter, "_make_request", side_effect=[RuntimeError("Xandr IO unreachable")]):
            with pytest.raises(RuntimeError, match="Xandr IO unreachable"):
                adapter.create_media_buy(
                    request=sample_request,
                    packages=sample_packages,
                    start_time=datetime.now(),
                    end_time=datetime.now() + timedelta(days=30),
                )
