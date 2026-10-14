"""Integration tests: update_media_buy's targeting_overlay refusal field. GH #2410.

update_media_buy merged every package update's violations into one `details` dict
before raising once, so it could never name which package failed -- and because the
merge was `dict.update` keyed by violation kind (e.g. "geo_overlaps"), a second
package's violation of the SAME kind silently overwrote the first's, dropping it
from the response entirely. Raising on the first package update carrying a
violation (matching create_media_buy, #1417) fixes both: the field names the
actual package, and no violation is lost to an overwrite.

AdCP 3.1.1 core/error.json `field`: a JSONPath-lite path from the request root.
No storyboard asserts this field's value for a targeting_overlay refusal (verified
against the UC-003 @boundary / @partition targeting_overlay scenarios in
BR-UC-003-update-media-buy.feature, both single-package and neither checking
errors[0].field), so this corrects an ungraded value.
"""

import uuid

import pytest

from src.core.database.database_session import get_db_session
from src.core.exceptions import AdCPInvalidRequestError
from src.core.schemas import UpdateMediaBuyRequest
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.factories import MediaBuyFactory, MediaPackageFactory
from tests.utils.database_helpers import (
    account_identity_for_tenant,
    bind_factories_to_session,
    seed_targeting_test_tenant,
)

pytestmark = pytest.mark.requires_db

TENANT_ID = "test_targeting_overlay_update_field_path"
ACCOUNT_ID = "acct_test"


def _make_identity():
    return account_identity_for_tenant(TENANT_ID, "test_adv", account_id=ACCOUNT_ID)


@pytest.fixture
def two_package_media_buy(integration_db):
    """A tenant and a media buy with two packages, pkg_a and pkg_b, sized for update."""
    with get_db_session() as session:
        seed_targeting_test_tenant(
            session,
            tenant_id=TENANT_ID,
            tenant_name="Targeting Overlay Update Publisher",
            subdomain="targeting-overlay-update",
            access_token="test_token",
        )

        with bind_factories_to_session(session):
            from sqlalchemy import select

            from src.core.database.models import Principal, Tenant

            tenant = session.scalars(select(Tenant).filter_by(tenant_id=TENANT_ID)).first()
            principal = session.scalars(
                select(Principal).filter_by(tenant_id=TENANT_ID, principal_id="test_adv")
            ).first()

            mb = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_targeting_update",
                tenant_id=TENANT_ID,
                principal_id="test_adv",
                status="pending_creatives",
            )
            MediaPackageFactory(media_buy=mb, media_buy_id="mb_targeting_update", package_id="pkg_a")
            MediaPackageFactory(media_buy=mb, media_buy_id="mb_targeting_update", package_id="pkg_b")

        session.commit()

    yield "mb_targeting_update"


def test_geo_overlap_on_the_only_package_update_names_packages_zero(two_package_media_buy):
    """Updating one package with a geo overlap reports packages[0], not the bare field name."""
    request = UpdateMediaBuyRequest(
        account={"account_id": ACCOUNT_ID},
        idempotency_key=f"test-idem-{uuid.uuid4().hex}",
        media_buy_id=two_package_media_buy,
        packages=[
            {
                "package_id": "pkg_a",
                "targeting_overlay": {"geo_countries": ["US"], "geo_countries_exclude": ["US"]},
            }
        ],
    )

    with pytest.raises(AdCPInvalidRequestError) as excinfo:
        _update_media_buy_impl(req=request, identity=_make_identity())

    assert excinfo.value.field == "packages[0].targeting_overlay"


def test_geo_overlap_on_the_second_package_update_names_packages_one(two_package_media_buy):
    """The violating package's position in the REQUEST is reported, not a hardcoded 0."""
    request = UpdateMediaBuyRequest(
        account={"account_id": ACCOUNT_ID},
        idempotency_key=f"test-idem-{uuid.uuid4().hex}",
        media_buy_id=two_package_media_buy,
        packages=[
            {"package_id": "pkg_a", "targeting_overlay": {"geo_countries": ["US"]}},
            {
                "package_id": "pkg_b",
                "targeting_overlay": {"geo_countries": ["CA"], "geo_countries_exclude": ["CA"]},
            },
        ],
    )

    with pytest.raises(AdCPInvalidRequestError) as excinfo:
        _update_media_buy_impl(req=request, identity=_make_identity())

    assert excinfo.value.field == "packages[1].targeting_overlay"


def test_first_violating_package_wins_when_both_packages_violate(two_package_media_buy):
    """Two violations of the SAME kind no longer collapse into one merged, unattributed error.

    Regression guard for the data loss the old `dict.update`-then-raise-once shape had:
    pkg_b's geo_overlaps would silently overwrite pkg_a's under the same details key, and
    the buyer never learned pkg_a was rejected at all. Raising on the first violation
    reports pkg_a -- index 0 -- and pkg_a's own values, not a merged or later package's.
    """
    request = UpdateMediaBuyRequest(
        account={"account_id": ACCOUNT_ID},
        idempotency_key=f"test-idem-{uuid.uuid4().hex}",
        media_buy_id=two_package_media_buy,
        packages=[
            {
                "package_id": "pkg_a",
                "targeting_overlay": {"geo_countries": ["US"], "geo_countries_exclude": ["US"]},
            },
            {
                "package_id": "pkg_b",
                "targeting_overlay": {"geo_countries": ["CA"], "geo_countries_exclude": ["CA"]},
            },
        ],
    )

    with pytest.raises(AdCPInvalidRequestError) as excinfo:
        _update_media_buy_impl(req=request, identity=_make_identity())

    assert excinfo.value.field == "packages[0].targeting_overlay"
    geo_overlaps = excinfo.value.details.geo_overlaps
    assert geo_overlaps is not None
    assert any(entry.get("values") == ["US"] for entry in geo_overlaps)
    assert not any(entry.get("values") == ["CA"] for entry in geo_overlaps)
