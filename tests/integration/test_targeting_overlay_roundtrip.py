"""Integration tests: targeting_overlay JSON round-trip through real PostgreSQL.

Validates that the ``Targeting`` Pydantic model survives a write→read cycle
through the ``MediaPackage.package_config`` JSON column. The write path
mirrors ``media_buy_update.py:1185`` — assign a Pydantic ``Targeting`` model
into the JSON column and let ``JSONType``'s serializer (via
``pydantic_core.to_json``) coerce nested references to JSON primitives. The
read path is the same one ``_get_media_buys_impl`` uses, so any JSON
serialization surprise in ``package_config`` surfaces here as a real
PostgreSQL round-trip rather than a mock-data unit test.

Per PR #1276 round-6 reviewer feedback (item 7): the happy path for
``property_list`` survived only as a unit test with mocked data; this file
adds the missing real-DB coverage that would catch surprises like
``AnyUrl``-to-``str`` coercion failures or nested-model serialization bugs
the unit harness can't see.

Covers: UC-002-MAIN-14a
"""

from decimal import Decimal

import pytest
from adcp.types import PropertyListReference
from sqlalchemy import select
from sqlalchemy.orm import attributes

from src.core.config_loader import set_current_tenant
from src.core.database.database_session import get_db_session
from src.core.database.models import MediaPackage
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    CollectionListReference,
    GetMediaBuysRequest,
    MediaBuyStatus,
    Targeting,
)
from src.core.tools.media_buy_list import _get_media_buys_impl
from tests.factories import PrincipalFactory
from tests.utils.database_helpers import (
    add_targeting_test_product,
    seed_media_buy_with_package,
    seed_targeting_test_tenant,
)

pytestmark = pytest.mark.requires_db

TENANT_ID = "test_targeting_roundtrip"

# Newly seeded media buys land in pending_creatives, which the default
# status_filter (active-only) excludes. List all states a seeded buy can
# legitimately be in so the round-trip target is visible to the query.
_ALL_STATUSES = [
    MediaBuyStatus.active,
    MediaBuyStatus.pending_creatives,
    MediaBuyStatus.pending_start,
    MediaBuyStatus.completed,
    MediaBuyStatus.paused,
]


def _make_identity() -> ResolvedIdentity:
    """Build identity and set the tenant context var.

    ``_get_media_buys_impl`` calls ``get_principal_object`` which reads from
    the ``current_tenant`` ContextVar. In production the transport boundary
    sets it via ``resolve_identity()``; tests calling ``_impl`` directly
    must replicate that setup. Side-effect is intentional and localized
    to the test path.
    """
    identity = PrincipalFactory.make_identity(
        principal_id="test_adv",
        tenant_id=TENANT_ID,
        protocol="mcp",
        dry_run=True,
    )
    set_current_tenant(identity.tenant)
    return identity


@pytest.fixture
def roundtrip_tenant(integration_db):
    """Tenant with a product that allows property_list targeting."""
    with get_db_session() as session:
        seed_targeting_test_tenant(
            session,
            tenant_id=TENANT_ID,
            tenant_name="Targeting Roundtrip Publisher",
            subdomain="targeting-roundtrip",
            access_token="test_token_roundtrip",
        )
        add_targeting_test_product(
            session,
            tenant_id=TENANT_ID,
            product_id="prod_roundtrip",
            name="Display Ads (roundtrip)",
            property_targeting_allowed=True,
        )
        session.commit()
    yield TENANT_ID


def _persist_targeting_overlay(media_buy_id: str, package_id: str, overlay: Targeting) -> None:
    """Write a ``Targeting`` Pydantic model into ``MediaPackage.package_config``.

    Mirrors the write at ``media_buy_update.py:1185`` exactly: assign the
    Pydantic model into the JSON column and flag it as modified so
    SQLAlchemy serializes via the column type. This is the production code
    path — running it from a test exercises the same ``JSONType`` →
    ``pydantic_core.to_json`` serialization that real updates do.
    """
    with get_db_session() as session:
        media_package = session.scalars(
            select(MediaPackage).filter_by(media_buy_id=media_buy_id, package_id=package_id)
        ).first()
        assert media_package is not None, f"seed produced no package for {media_buy_id}/{package_id}"
        if media_package.package_config is None:
            media_package.package_config = {}
        media_package.package_config["targeting_overlay"] = overlay
        attributes.flag_modified(media_package, "package_config")
        session.commit()


@pytest.mark.requires_db
def test_property_list_roundtrips_through_postgres(roundtrip_tenant):
    """``property_list.list_id`` survives write→read through real Postgres.

    Exercises the full Pydantic→JSONType→Postgres→Pydantic SerDes cycle for
    ``targeting_overlay.property_list``. The write path uses the same
    assignment pattern production update_media_buy uses (Pydantic model
    into the JSON column); the read path is the production
    ``_get_media_buys_impl`` rehydration that calls ``Targeting(**raw)``.
    """
    media_buy_id = "mb_roundtrip_property"
    with get_db_session() as session:
        seed_media_buy_with_package(
            session,
            tenant_id=TENANT_ID,
            principal_id="test_adv",
            product_id="prod_roundtrip",
            media_buy_id=media_buy_id,
            package_id="pkg_roundtrip",
            budget=Decimal("5000.00"),
        )
        session.commit()

    overlay = Targeting(
        property_list=PropertyListReference(
            agent_url="https://gov.example",
            list_id="L_property_v1",
        ),
    )
    _persist_targeting_overlay(media_buy_id, "pkg_roundtrip", overlay)

    response = _get_media_buys_impl(
        GetMediaBuysRequest(media_buy_ids=[media_buy_id], status_filter=_ALL_STATUSES),
        identity=_make_identity(),
    )

    assert len(response.media_buys) == 1, "expected exactly one media buy for this id"
    buy = response.media_buys[0]
    assert len(buy.packages) == 1
    pkg = buy.packages[0]
    assert pkg.targeting_overlay is not None, "targeting_overlay must round-trip from package_config"
    assert pkg.targeting_overlay.property_list is not None, "property_list nested reference must survive JSON SerDes"
    assert pkg.targeting_overlay.property_list.list_id == "L_property_v1"
    # AnyUrl coercion: agent_url stays a string-equivalent through SerDes — guard
    # against pydantic_core mis-coercion that would emit an opaque dict here.
    assert "gov.example" in str(pkg.targeting_overlay.property_list.agent_url)


@pytest.mark.requires_db
def test_collection_list_roundtrips_through_postgres(roundtrip_tenant):
    """``collection_list.list_id`` survives write→read through real Postgres.

    Sister test to ``test_property_list_roundtrips_through_postgres`` — same
    SerDes cycle for ``collection_list`` (which uses ``CollectionListReference``).
    Per PR #1276 round-6 item 4, ``collection_list`` follows a different
    governance mechanism (per-capability declaration, not per-product flag),
    but its JSON storage shape is identical to ``property_list`` — so the
    same SerDes round-trip is relevant.
    """
    media_buy_id = "mb_roundtrip_collection"
    with get_db_session() as session:
        seed_media_buy_with_package(
            session,
            tenant_id=TENANT_ID,
            principal_id="test_adv",
            product_id="prod_roundtrip",
            media_buy_id=media_buy_id,
            package_id="pkg_roundtrip_c",
            budget=Decimal("5000.00"),
        )
        session.commit()

    overlay = Targeting(
        collection_list=CollectionListReference(
            agent_url="https://gov.example",
            list_id="C_collection_v1",
        ),
    )
    _persist_targeting_overlay(media_buy_id, "pkg_roundtrip_c", overlay)

    response = _get_media_buys_impl(
        GetMediaBuysRequest(media_buy_ids=[media_buy_id], status_filter=_ALL_STATUSES),
        identity=_make_identity(),
    )

    assert len(response.media_buys) == 1
    pkg = response.media_buys[0].packages[0]
    assert pkg.targeting_overlay is not None
    assert (
        pkg.targeting_overlay.collection_list is not None
    ), "collection_list nested reference must survive JSON SerDes"
    assert pkg.targeting_overlay.collection_list.list_id == "C_collection_v1"


@pytest.mark.requires_db
def test_both_lists_coexist_in_single_package(roundtrip_tenant):
    """A package can carry property_list AND collection_list — both round-trip.

    Per AdCP 3.0.6 ``core/targeting.json``, ``property_list`` and
    ``collection_list`` are independent fields; a buyer may set either or
    both. This test ensures nothing in the SerDes path silently drops the
    second list when the first is present (e.g. an ordering-sensitive
    serializer or a Pydantic discriminator misconfiguration).
    """
    media_buy_id = "mb_roundtrip_both"
    with get_db_session() as session:
        seed_media_buy_with_package(
            session,
            tenant_id=TENANT_ID,
            principal_id="test_adv",
            product_id="prod_roundtrip",
            media_buy_id=media_buy_id,
            package_id="pkg_roundtrip_both",
            budget=Decimal("5000.00"),
        )
        session.commit()

    overlay = Targeting(
        property_list=PropertyListReference(
            agent_url="https://gov.example",
            list_id="L_both_v1",
        ),
        collection_list=CollectionListReference(
            agent_url="https://gov.example",
            list_id="C_both_v1",
        ),
    )
    _persist_targeting_overlay(media_buy_id, "pkg_roundtrip_both", overlay)

    response = _get_media_buys_impl(
        GetMediaBuysRequest(media_buy_ids=[media_buy_id], status_filter=_ALL_STATUSES),
        identity=_make_identity(),
    )

    pkg = response.media_buys[0].packages[0]
    assert pkg.targeting_overlay is not None
    assert pkg.targeting_overlay.property_list is not None
    assert pkg.targeting_overlay.collection_list is not None
    assert pkg.targeting_overlay.property_list.list_id == "L_both_v1"
    assert pkg.targeting_overlay.collection_list.list_id == "C_both_v1"
