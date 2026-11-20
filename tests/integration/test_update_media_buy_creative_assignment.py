"""Integration tests for update_media_buy creative assignment functionality."""

from datetime import UTC, datetime
from typing import Any

import pytest

from src.core.database.models import CreativeAssignment as DBAssignment
from src.core.schemas import UpdateMediaBuyRequest, UpdateMediaBuyResponse, UpdateMediaBuyResult
from tests.harness.media_buy_dual import MediaBuyDualEnv
from tests.helpers.media_buy_write_seam import (
    assert_status_move_carried_bookkeeping,
    read_media_buy_state,
)

#: The one package the creative-assignment tests update.
PACKAGE_ID = "pkg_default"


def _seed_buy_with_creatives(
    env: MediaBuyDualEnv, *, media_buy_id: str, package_id: str, creative_ids: list[str], **buy: Any
) -> tuple[Any, dict[str, Any]]:
    """A buy with one package whose product accepts the seeded creatives' format, and those creatives.

    The package's product is what ``_validate_creatives_for_assignment`` grades each creative's
    format against, so it is the harness's own product chain, and each creative is the
    factory's approved default in the format that product accepts. *buy* sets the buy's own
    columns (a status, an approval stamp). Returns the buy and the creative rows by id.
    """
    from tests.factories import CreativeFactory, MediaBuyFactory, MediaPackageFactory

    tenant, principal = env.setup_default_data(human_review_required=False)
    product, _ = env.setup_product_chain(tenant)
    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id=media_buy_id, **buy)
    MediaPackageFactory(
        media_buy=media_buy,
        package_id=package_id,
        package_config={"package_id": package_id, "product_id": product.product_id},
    )
    return media_buy, {
        creative_id: CreativeFactory(tenant=tenant, principal=principal, creative_id=creative_id, approved=True)
        for creative_id in creative_ids
    }


def _stored_assignments(env: MediaBuyDualEnv, media_buy_id: str) -> list[tuple[str, int]]:
    """The package's stored assignments as sorted ``(creative_id, weight)`` pairs, one per row.

    The test's session is expired first, so a row the update rewrote is read as stored.
    """
    env.get_session().expire_all()
    rows = env.query(DBAssignment, tenant_id=env.identity.tenant_id, media_buy_id=media_buy_id, package_id=PACKAGE_ID)
    return sorted((row.creative_id, row.weight) for row in rows)


def _update_package(env: MediaBuyDualEnv, media_buy_id: str, package_id: str, **package_update: Any) -> Any:
    """Send update_media_buy carrying *package_update* on *package_id*."""
    return env.call_impl(
        req=UpdateMediaBuyRequest(
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
            media_buy_id=media_buy_id,
            packages=[{"package_id": package_id, **package_update}],
        )
    )


@pytest.mark.requires_db
def test_update_media_buy_assigns_creatives_to_package(integration_db):
    """Test that update_media_buy can assign creatives to a package."""
    with MediaBuyDualEnv() as env:
        _seed_buy_with_creatives(
            env, media_buy_id="test_buy_123", package_id=PACKAGE_ID, creative_ids=["creative_1", "creative_2"]
        )

        response = _update_package(env, "test_buy_123", PACKAGE_ID, creative_ids=["creative_1", "creative_2"])

        assert isinstance(response, UpdateMediaBuyResult)
        assert isinstance(response, UpdateMediaBuyResponse)
        assert response.media_buy_id == "test_buy_123"
        assert response.affected_packages is not None
        assert len(response.affected_packages) == 1

        # The affected package names the package by its PINNED field. The assertions that
        # stood here read ``buyer_package_ref`` and ``changes_applied``, two non-spec fields
        # deleted under salesagent-gweku -- the first was write-only and the second duplicated
        # the ``_PackageChanges`` record that already carries it. What this test is for is the
        # DATABASE effect, asserted below, which is where the creative ids actually land.
        assert response.affected_packages[0].package_id == PACKAGE_ID

        # Each creative assigned exactly once.
        assert [cid for cid, _ in _stored_assignments(env, "test_buy_123")] == ["creative_1", "creative_2"]


@pytest.mark.requires_db
def test_creative_assignments_with_weights(integration_db):
    """UC-003-CA01: creative_assignments replaces all with specified weights.

    Tests that update_media_buy handles the creative_assignments field
    (as opposed to creative_ids) with weight values per AdCP spec.
    """
    with MediaBuyDualEnv() as env:
        _seed_buy_with_creatives(env, media_buy_id="test_buy_weights", package_id=PACKAGE_ID, creative_ids=["c1", "c2"])

        response = _update_package(
            env,
            "test_buy_weights",
            PACKAGE_ID,
            creative_assignments=[{"creative_id": "c1", "weight": 70}, {"creative_id": "c2", "weight": 30}],
        )

        assert isinstance(response, UpdateMediaBuyResult)
        assert isinstance(response, UpdateMediaBuyResponse)
        # adcp_error is the error channel UpdateMediaBuyResult declares (salesagent-jnqab).
        assert response.adcp_error is None, f"update_media_buy failed: {response.adcp_error}"

        assert _stored_assignments(env, "test_buy_weights") == [("c1", 70), ("c2", 30)]


@pytest.mark.requires_db
def test_creative_assignments_replaces_all(integration_db):
    """UC-003-CA01 / BR-RULE-024 INV-2: creative_assignments replaces ALL existing.

    When creative_assignments is sent, it should replace the entire set of
    assignments for that package, not just add/update. If c1 and c2 are assigned
    and we send creative_assignments=[c2, c3], the result should be only c2 and c3.
    """
    from tests.factories import CreativeAssignmentFactory

    with MediaBuyDualEnv() as env:
        media_buy, creatives = _seed_buy_with_creatives(
            env, media_buy_id="test_buy_replace", package_id=PACKAGE_ID, creative_ids=["c1", "c2", "c3"]
        )
        # Pre-existing assignments: c1 (weight 50) and c2 (weight 50)
        for creative_id in ("c1", "c2"):
            CreativeAssignmentFactory(
                creative=creatives[creative_id], media_buy=media_buy, package_id=PACKAGE_ID, weight=50
            )

        # Send creative_assignments with ONLY c2 and c3 — c1 should be REMOVED
        response = _update_package(
            env,
            "test_buy_replace",
            PACKAGE_ID,
            creative_assignments=[{"creative_id": "c2", "weight": 70}, {"creative_id": "c3", "weight": 30}],
        )

        assert isinstance(response, UpdateMediaBuyResult)
        assert isinstance(response, UpdateMediaBuyResponse)
        # adcp_error is the error channel UpdateMediaBuyResult declares (salesagent-jnqab).
        assert response.adcp_error is None, f"update_media_buy failed: {response.adcp_error}"

        # BR-RULE-024 INV-2: creative_assignments REPLACES ALL
        assert _stored_assignments(env, "test_buy_replace") == [("c2", 70), ("c3", 30)], "c1 was not removed"


# =============================================================================
# Approved-draft -> pending_creatives transition
#
# A media buy approved BEFORE it had creatives sits at status "draft" with
# approved_at stamped. Attaching creatives is what moves it on, so both package
# creative paths carry the same transition:
#   - creative_ids         -> src/core/tools/media_buy_update.py:949
#   - creative_assignments -> src/core/tools/media_buy_update.py:1185
# Each writes through MediaBuyRepository.update_status rather than assigning
# media_buy_obj.status directly, and that routing is the behaviour graded here:
# update_status bumps revision (the buyer's optimistic-concurrency token) and does
# NOT stamp confirmed_at: pending_creatives is deliberately absent from
# models._SELLER_COMMITTED_STATUSES, because a hold awaiting creative approval is
# not a seller commitment. A direct attribute assignment moves the
# buy with neither, which is invisible if the test only checks the status string.
# =============================================================================


@pytest.mark.requires_db
@pytest.mark.parametrize(
    ("creative_field", "package_update"),
    [
        pytest.param(
            "creative_ids",
            {"creative_ids": ["cr_draft"]},
            id="creative_ids",
        ),
        pytest.param(
            "creative_assignments",
            {"creative_assignments": [{"creative_id": "cr_draft", "weight": 100}]},
            id="creative_assignments",
        ),
    ],
)
def test_approved_draft_transitions_to_pending_creatives(integration_db, creative_field, package_update):
    """Attaching creatives to an approved draft moves it to pending_creatives via the repository.

    One case per transition site (creative_ids -> :949, creative_assignments ->
    :1185): each drives only its own branch, because each site is guarded on the
    field its case sends and the other field is absent from the request.
    """
    media_buy_id = f"mb_draft_{creative_field}"
    package_id = "pkg_draft"

    with MediaBuyDualEnv() as env:
        # The narrow precondition both transitions guard on: a buy that is BOTH status
        # "draft" AND has ``approved_at`` set -- the seller approved it before any creative
        # was attached -- and a package whose product accepts the creative's format, or the
        # shared gate refuses the update before either transition site is reached.
        _seed_buy_with_creatives(
            env,
            media_buy_id=media_buy_id,
            package_id=package_id,
            creative_ids=["cr_draft"],
            status="draft",
            approved_at=datetime.now(UTC),
        )

        before = read_media_buy_state(env.identity.tenant_id, media_buy_id, session=env.get_session())
        assert before.status == "draft", "fixture must start in the approved-but-creative-less draft state"
        assert before.confirmed_at is None, "draft is not a seller-confirmed status, so it starts unstamped"

        result = _update_package(env, media_buy_id, package_id, **package_update)

        assert isinstance(result, UpdateMediaBuyResult), f"update failed: {result!r}"

        after = read_media_buy_state(env.identity.tenant_id, media_buy_id, session=env.get_session())
        # The transition is a mutation of the buy, so it must carry the buy's mutation
        # bookkeeping — which only MediaBuyRepository.update_status does.
        assert_status_move_carried_bookkeeping(
            before,
            after,
            expected_status="pending_creatives",
            # confirms=False: pending_creatives is a HOLD, not a commitment. The buy is
            # waiting on creative approval and the ad server has not been contacted, so
            # there is nothing to record. Was confirms=True, which graded the defect Chris
            # reproduced on a real database: the hold stamped a write-once, buyer-visible
            # confirmed_at, and a buy that later failed ended `failed` still carrying it.
            # The pin decides it -- create-media-buy-response.json @ 3.1.1 says null "in
            # deferred or manual-approval flows until seller commitment occurs".
            confirms=False,
            subject=f"attaching {creative_field} to approved draft {media_buy_id}",
        )
