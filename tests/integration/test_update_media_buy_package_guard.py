"""Package-existence guard: real create→update flow + raw_request-only buys.

The guard in ``_update_media_buy_impl`` resolves referenced packages against
``media_packages`` rows, which ``create_media_buy`` dual-writes from
``response.packages``. Two exposures make that precondition soft:

1. Media buys created BEFORE the dual-write landed (8367e0a1f) have their
   packages only in ``MediaBuy.raw_request`` — no backfill migration exists.
2. An adapter that returns a created buy with empty ``response.packages``
   writes zero rows (there is no request-derived fallback at create time,
   because package_ids are adapter-assigned).

``MediaBuyRepository.package_exists_or_raise`` therefore falls back to
``raw_request`` before raising, so a valid package reference on such a buy
does not surface a spurious buyer-facing PACKAGE_NOT_FOUND. These tests pin
that contract end to end:

- deletion oracle: removing the raw_request fallback in
  ``package_exists_or_raise`` makes ``test_update_with_creative_ids_succeeds_
  on_raw_request_only_buy`` fail with AdCPPackageNotFoundError at the guard.
- regression net: if create's dual-write ever stops writing rows, the
  real-flow test fails loudly instead of the gap going unnoticed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import CreativeAssignment as DBAssignment
from src.core.schemas import UpdateMediaBuyError, UpdateMediaBuyRequest
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.factories import CreativeFactory, MediaBuyFactory
from tests.helpers.adcp_factories import create_test_format
from tests.integration.conftest import seed_error_test_tenant
from tests.integration.media_buy_helpers import _make_create_request

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

DEFAULT_FORMAT_ID = "display_300x250"


@pytest.fixture(autouse=True)
def mock_format_spec():
    """Mock _get_format_spec_sync — asyncio.run() inside a running loop fails
    under pytest-asyncio (same shim as test_creative_assignment_principal_id)."""
    mock_formats = {
        DEFAULT_FORMAT_ID: create_test_format(format_id=DEFAULT_FORMAT_ID, name="Display 300x250", type="display"),
    }
    with patch(
        "src.core.tools.media_buy_create._get_format_spec_sync",
        side_effect=lambda agent_url, format_id: mock_formats.get(format_id),
    ):
        yield


@pytest.fixture
def _seeded(integration_db):
    """Factory-seeded tenant/principal/product inside one IntegrationEnv.

    The env stays open for the test's lifetime so factory calls in the test
    body (creatives, legacy media buy) share the bound session.
    """
    from tests.harness._base import IntegrationEnv

    with IntegrationEnv():
        yield seed_error_test_tenant(
            tenant_id="pkg_guard_test",
            principal_id="pkg_guard_principal",
            access_token="pkg_guard_token",
            product_id="guaranteed_display",
            subdomain="pkgguard",
            tenant_name="Package Guard Test Tenant",
            protocol="mcp",
        )


def _seed_creatives(seeded: dict, creative_ids: list[str]) -> None:
    for cid in creative_ids:
        CreativeFactory(
            tenant=seeded["tenant"],
            principal=seeded["principal"],
            creative_id=cid,
            status="ready",
            data={
                "url": "https://example.com/creative.jpg",
                "width": 300,
                "height": 250,
                "platform_creative_id": f"mock_{cid}",
            },
        )


class TestPackageGuardRealCreateFlow:
    @pytest.mark.asyncio
    async def test_update_survives_real_create_flow_package_reference(self, _seeded):
        """A buy created through the REAL create path (adapter-populated
        response.packages → media_packages dual-write) must accept an update
        referencing its own package. Fails with PACKAGE_NOT_FOUND if the
        create-side dual-write ever regresses to writing zero rows."""
        from src.core.database.models import MediaPackage
        from src.core.tools.media_buy_create import _create_media_buy_impl

        identity = _seeded["identity"]
        creative_ids = ["c_guard_real_1", "c_guard_real_2"]
        _seed_creatives(_seeded, creative_ids)

        create_req = _make_create_request(
            packages=[
                {
                    "product_id": "guaranteed_display",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                }
            ],
        )
        create_result = await _create_media_buy_impl(req=create_req, identity=identity)
        assert create_result.status in ("completed", "submitted"), f"create failed: {create_result.status}"
        media_buy_id = create_result.response.media_buy_id

        # Reference the package exactly as a buyer would: from the create flow.
        with get_db_session() as session:
            rows = session.scalars(select(MediaPackage).where(MediaPackage.media_buy_id == media_buy_id)).all()
            assert rows, (
                "create_media_buy wrote no media_packages rows for a mock-adapter "
                "buy — the dual-write regressed, and the package guard below "
                "would now be exercising the raw_request fallback instead of rows"
            )
            package_id = rows[0].package_id

        update_req = UpdateMediaBuyRequest(
            media_buy_id=media_buy_id,
            packages=[{"package_id": package_id, "creative_ids": creative_ids}],
        )
        result = _update_media_buy_impl(req=update_req, identity=identity)
        assert not isinstance(result, UpdateMediaBuyError), f"update failed: {result}"


class TestPackageGuardRawRequestFallback:
    def test_update_with_creative_ids_succeeds_on_raw_request_only_buy(self, _seeded):
        """A buy whose packages live ONLY in raw_request (pre-dual-write buy,
        or adapter that returned empty response.packages) must not fail its
        own update with a spurious PACKAGE_NOT_FOUND. Deletion oracle:
        removing the raw_request fallback in package_exists_or_raise makes
        this raise AdCPPackageNotFoundError at the guard."""
        identity = _seeded["identity"]
        creative_ids = ["c_guard_legacy_1"]
        _seed_creatives(_seeded, creative_ids)

        now = datetime.now(UTC)
        # Deliberately NO MediaPackageFactory row — packages recorded only in
        # raw_request, matching the pre-dual-write shape.
        media_buy = MediaBuyFactory(
            tenant=_seeded["tenant"],
            principal=_seeded["principal"],
            media_buy_id="mb_raw_request_only",
            status="active",
            start_date=now.date(),
            end_date=(now + timedelta(days=30)).date(),
            start_time=now,
            end_time=now + timedelta(days=30),
            raw_request={"packages": [{"package_id": "pkg_legacy", "impressions": 100000}]},
        )

        update_req = UpdateMediaBuyRequest(
            media_buy_id=media_buy.media_buy_id,
            packages=[{"package_id": "pkg_legacy", "creative_ids": creative_ids}],
        )
        result = _update_media_buy_impl(req=update_req, identity=identity)
        assert not isinstance(result, UpdateMediaBuyError), f"update failed: {result}"

        # The assignment actually landed — the fallback admitted a real
        # package, it did not just suppress an error.
        with get_db_session() as session:
            assignments = session.scalars(
                select(DBAssignment).where(
                    DBAssignment.tenant_id == "pkg_guard_test",
                    DBAssignment.media_buy_id == "mb_raw_request_only",
                )
            ).all()
            assert assignments, "expected creative assignment on the raw_request-only buy"
