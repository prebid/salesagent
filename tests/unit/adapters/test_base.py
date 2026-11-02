from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.adapters.base import AdapterCreateRequest
from src.adapters.mock_ad_server import MockAdServer
from src.core.schemas import CreateMediaBuyRequest, FormatId, MediaPackage, PackageRequest, Principal

pytestmark = pytest.mark.unit

# Default agent URL for creating FormatId objects
DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"


def make_format_id(format_id: str) -> FormatId:
    """Helper to create FormatId objects with default agent URL."""
    return FormatId(agent_url=DEFAULT_AGENT_URL, id=format_id)


@pytest.fixture
def sample_packages():
    """A fixture to create a sample list of media packages for use in tests."""
    return [
        MediaPackage(
            package_id="pkg_1",
            name="Guaranteed Banner",
            delivery_type="guaranteed",
            cpm=15.0,
            impressions=333333,  # 5000 budget / 15 CPM * 1000
            budget=5000.0,  # Budget as float in MediaPackage (internal adapter format)
            format_ids=[
                make_format_id("display_300x250"),
                make_format_id("display_728x90"),
            ],
        )
    ]


def test_mock_ad_server_create_media_buy(sample_packages, mocker):
    """
    Tests that the MockAdServer correctly creates a media buy
    when a create_media_buy request is received.
    """
    # Arrange
    principal = Principal(
        principal_id="test_principal",
        name="Test Principal",
        platform_mappings={"mock": {"advertiser_id": "test_advertiser"}},
    )

    # No tenant patch: the adapter is given its tenant_id, and the ambient tenant
    # ContextVar an adapter used to read is deleted.
    adapter = MockAdServer({}, principal, tenant_id="test_tenant")
    start_time = datetime.now(UTC)
    end_time = start_time + timedelta(days=30)

    # The carrier, built as the tool builds it from a one-package 5000 request.
    request = AdapterCreateRequest.from_buyer_request(
        CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "sports.example.com"},  # Required per AdCP spec
            idempotency_key="unit-test-key-mockbase-0001",
            packages=[
                PackageRequest(
                    product_id="pkg_1",
                    budget=5000.0,
                    pricing_option_id="test_pricing",
                    format_ids=[
                        make_format_id("display_300x250"),
                        make_format_id("display_728x90"),
                    ],
                )
            ],
            start_time=start_time,
            end_time=end_time,
            po_number="PO-12345",
        )
    )

    # Act
    response = adapter.create_media_buy(
        request=request, packages=sample_packages, start_time=start_time, end_time=end_time
    )

    # Assert
    assert response.media_buy_id == "buy_PO-12345"

    # Check the internal state of the mock server
    internal_buy = adapter._media_buys.get("buy_PO-12345")
    assert internal_buy is not None
    assert internal_buy["total_budget"] == 5000
    assert len(internal_buy["packages"]) == 1
    assert internal_buy["packages"][0].package_id == "pkg_1"


class TestAdapterCreateRequestCarriesOnlyWhatAdaptersRead:
    """The create side's carrier: what the tool hands an adapter, and what it refuses.

    An adapter takes the buy to place, never the buyer's ``CreateMediaBuyRequest`` —
    which is why the approval executor no longer has to rebuild one, nor invent the
    ``idempotency_key`` and ``account`` a request must carry.
    """

    def test_projection_off_a_buyer_request_sums_the_packages(self):
        req = CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "sports.example.com"},
            idempotency_key="unit-test-key-carrier-0001",
            packages=[
                PackageRequest(
                    product_id="p1",
                    budget=3000.0,
                    pricing_option_id="test_pricing",
                    format_ids=[make_format_id("display_300x250")],
                ),
                PackageRequest(
                    product_id="p2",
                    budget=1500.0,
                    pricing_option_id="test_pricing",
                    format_ids=[make_format_id("display_728x90")],
                ),
            ],
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC) + timedelta(days=7),
            po_number="PO-999",
        )

        carrier = AdapterCreateRequest.from_buyer_request(req)

        assert carrier.total_budget == Decimal("4500.0")
        assert carrier.po_number == "PO-999"
        assert carrier.brand.domain == "sports.example.com"
        # A request from a buyer has not been through the approval queue.
        assert carrier.already_approved is False

    def test_both_sources_project_to_the_same_carrier(self):
        """A buyer's request and the ROW that persisted it produce the same carrier.

        There are two sources for one shape: the request path reads the DTO, and the
        approval replay reads ``media_buys.raw_request`` — which is exactly
        ``req.model_dump(mode="json", by_alias=True)`` as
        ``MediaBuyRepository.create_from_request`` writes it, which is why the dump is
        taken here rather than typed out.

        The assertion walks ``_buyer_supplied_fields()`` instead of naming fields,
        because a typed list is what failed: the replay used to hand-build its carrier
        and omitted ``push_notification_config``, so GAM's background order-approval
        webhook had no target and a buy approved off the queue never told the buyer.
        """
        req = CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "sports.example.com"},
            idempotency_key="unit-test-key-carrier-0002",
            packages=[
                PackageRequest(
                    product_id="p1",
                    budget=2500.0,
                    pricing_option_id="test_pricing",
                    format_ids=[make_format_id("display_300x250")],
                )
            ],
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC) + timedelta(days=7),
            po_number="PO-777",
            push_notification_config={
                "url": "https://buyer.example.com/adcp-hook",
                "authentication": {"schemes": ["Bearer"], "credentials": "c" * 40},
            },
        )
        persisted = req.model_dump(mode="json", by_alias=True)

        from_request = AdapterCreateRequest.from_buyer_request(req)
        from_row = AdapterCreateRequest.from_persisted_request(persisted, total_budget=req.get_total_budget())

        for name in AdapterCreateRequest._buyer_supplied_fields():
            # Unset on BOTH sides would make the equality below vacuous, so a field this
            # request does not exercise fails here rather than passing silently.
            assert getattr(from_request, name) is not None, f"{name} unexercised by this request"
            assert getattr(from_row, name) == getattr(from_request, name), name

        assert from_row.total_budget == from_request.total_budget == Decimal("2500.0")
        # The one field the two sources are MEANT to disagree on.
        assert from_request.already_approved is False
        assert from_row.already_approved is True

    def test_a_field_no_adapter_reads_is_refused_at_construction(self):
        """``extra="forbid"``: the replay cannot smuggle a request-only field through.

        The fabricated idempotency key this deletes was accepted silently by the DTO
        because the DTO declares the field. Here it is a construction error.
        """
        with pytest.raises(ValidationError) as exc_info:
            AdapterCreateRequest(po_number="PO-1", idempotency_key="synthesised-by-the-seller")

        assert "idempotency_key" in str(exc_info.value)
        assert "extra_forbidden" in str(exc_info.value)
