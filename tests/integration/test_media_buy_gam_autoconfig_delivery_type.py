"""Regression (PR1399 R3): GAM auto-config must normalize a guaranteed product's
delivery_type to its enum *value*, not str(Enum).

src/core/tools/media_buy_create.py auto-generates a default GAM
implementation_config when a product is missing one. It derives the
``delivery_type`` string and passes it to
``GAMProductConfigService.generate_default_config``, which branches on
``delivery_type == "guaranteed"``. ``Product.delivery_type`` is a plain
``DeliveryType`` enum, so ``str(delivery_type)`` yields
``"DeliveryType.guaranteed"`` -- the equality fails and a GUARANTEED product
silently gets the NON_GUARANTEED (PRICE_PRIORITY) config. The fix routes the
value through ``enum_value()``.

This drives the real ``_create_media_buy_impl`` GAM auto-config path and calls
through to the real config generator; only the captured arguments/output are
asserted.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from src.adapters.google_ad_manager import GoogleAdManager
from src.core.schemas import CreateMediaBuyRequest
from src.services.gam_product_config_service import GAMProductConfigService
from tests.factories import PricingOptionFactory, ProductFactory
from tests.factories.core import PropertyTagFactory
from tests.harness.media_buy_create import MediaBuyCreateEnv

_TENANT_ID = "gam-autocfg-tenant"
_PRINCIPAL_ID = "gam-autocfg-principal"

#: The canonical creative agent a stored legacy reference is namespaced to. Spelled here
#: rather than imported from ``format_resolver`` so the fixture states the row's own shape
#: instead of deriving it from the code under test.
_CREATIVE_AGENT = "https://creative.adcontextprotocol.org"


def _future(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _make_request(product_id: str) -> CreateMediaBuyRequest:
    return CreateMediaBuyRequest(
        account={"account_id": "acct_test"},
        brand={"domain": "testbrand.com"},
        start_time=_future(1),
        end_time=_future(8),
        packages=[{"product_id": product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
        idempotency_key=f"gam-autoconfig-{product_id}",
    )


def _auto_configure(product_id: str, **product_columns: Any) -> dict[str, Any]:
    """Buy a guaranteed GAM product that has no implementation_config; return what was seeded.

    The spy CALLS THROUGH to the real generator, so ``config`` is the genuine seeded
    document and not a mock's answer, and ``delivery_type`` is the string the call site
    handed it. *product_columns* set the product's two format arms.

    No ``human_review_required=`` kwarg: it only ever seeded an in-memory tenant override,
    and this env's ``call_impl`` dispatches at ``invoke_tool``, so the REAL resolver loads
    the tenant from its row. The kwarg agreed with the row
    (``TenantFactory.human_review_required = False``) purely by accident, so it decided
    nothing; the row is the one source.
    """
    real_generate = GAMProductConfigService.generate_default_config
    captured: dict[str, Any] = {}

    def _spy(delivery_type, formats=None, *, format_options=None):
        config = real_generate(delivery_type, formats=formats, format_options=format_options)
        captured["delivery_type"] = delivery_type
        captured["config"] = config
        return config

    with MediaBuyCreateEnv(tenant_id=_TENANT_ID, principal_id=_PRINCIPAL_ID) as env:
        tenant, _principal = env.setup_default_data()
        PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
        product = ProductFactory(
            tenant=tenant,
            product_id=product_id,
            delivery_type="guaranteed",
            implementation_config=None,  # force the auto-config branch
            property_tags=["all_inventory"],
            **product_columns,
        )
        PricingOptionFactory(product=product, pricing_model="cpm", currency="USD", is_fixed=True)

        # Enter the GAM auto-config branch (guarded by adapter class name).
        env.mock["adapter"].return_value.__class__ = GoogleAdManager

        with patch.object(GAMProductConfigService, "generate_default_config", side_effect=_spy):
            env.call_impl(req=_make_request(product_id))

    assert captured, "GAM auto-config branch did not run -- generate_default_config was never called"
    return captured


def _placeholder(width: int, height: int) -> dict[str, Any]:
    return {"width": width, "height": height, "expected_creative_count": 1, "is_native": False}


@pytest.mark.requires_db
class TestGAMAutoConfigDeliveryTypeNormalization:
    def test_guaranteed_product_autoconfig_receives_normalized_delivery_type(self, integration_db):
        """A guaranteed product missing implementation_config must auto-generate a
        GUARANTEED (STANDARD line item) GAM config, not the non_guaranteed default.

        ``format_options`` is not asserted here: this product is legacy-only
        (``ProductFactory`` defaults the canonical arm to None), so the value would be None
        whether the call site passes it or the spy defaults it -- an assertion that cannot
        tell those apart. The call site is graded by
        ``test_canonical_autoconfig_seeds_the_declared_size`` below, with a product that
        declares one.
        """
        captured = _auto_configure("prod_guar")

        # The bug passes str(DeliveryType.guaranteed) == 'DeliveryType.guaranteed'.
        assert captured["delivery_type"] == "guaranteed"
        # ... which makes generate_default_config fall through to non_guaranteed.
        assert captured["config"]["line_item_type"] == "STANDARD"

    def test_canonical_autoconfig_seeds_the_declared_size(self, integration_db):
        """The auto-config path hands the product's CANONICAL arm to the seeder.

        The fourth call site of ``generate_default_config`` and the only one outside the
        admin. Before this, it passed ``formats`` alone, so a canonically-authored product
        was auto-configured with the 300x250 fallback contradicting its own declaration --
        and that value is the one GAM traffics, because ``gam/managers/orders.py`` falls
        back to ``impl_config["creative_placeholders"]`` for a package naming no
        ``format_ids``, which is exactly this product.

        970x250 and not 300x250: the fallback is 300x250, so an assertion expecting it
        would pass whether the canonical arm is read or ignored. If the call site stopped
        passing ``format_options``, the real generator would fall back and 300x250 would
        fail this assertion.
        """
        captured = _auto_configure(
            "prod_canonical",
            # Canonically authored: the canonical arm on AND the legacy arm off, which
            # is what makes ``formats`` empty and the canonical arm the only input.
            format_options=[{"format_kind": "image", "params": {"width": 970, "height": 250}}],
            format_ids=None,
        )

        assert captured["config"]["creative_placeholders"] == [_placeholder(970, 250)]

    def test_legacy_only_autoconfig_seeds_the_size_the_arm_encodes(self, integration_db):
        """The DEPRECATED arm still reaches the seeder on the buyer's path.

        Replaces the grader stage 5 deletes --
        ``tests/admin/...::test_a_legacy_only_product_is_seeded_from_the_entry_it_stores`` --
        which POSTed a legacy-only product to the admin create handler. A4 refuses that by
        design ("Do not author a new product from format_ids alone"), so that test asserted
        behaviour the pin forbids and is deleted rather than re-grounded. The OBLIGATION is
        still live here: A4 deliberately preserves ``format_ids`` on existing and ingested
        rows, so a legacy product bought on a GAM tenant with no ``implementation_config``
        reaches this seeding call with a populated legacy arm.

        ``display_728x90_image`` carries its dimensions IN THE ID, which
        ``upgrade_legacy_format_id`` projects; the sibling below grades an entry that
        carries them beside an id that encodes none.

        728x90, never 300x250: the seeder's fallback is 300x250, so an assertion expecting
        it would pass whether the legacy arm is read or ignored.

        WHY THIS ASSERTS THE SEEDED VALUE AND NOT SUCCESS ON THE WIRE. Measured:
        ``_creative_placeholders`` cannot return an empty list -- it answers the 300x250
        fallback when no declaration yields a slot -- so ``validate_config``'s
        ``if not placeholders`` refusal is unreachable from a freshly seeded config, and a
        buy succeeds whichever size is seeded. ``Product.implementation_config`` is also
        ``Field(exclude=True)``, so the seeded placeholder is on no transport's wire by
        construction. The value is the only thing that differs, so the value is what is
        asserted.
        """
        captured = _auto_configure(
            "prod_legacy",
            # The mirror of the canonical control above: the legacy arm ON and the
            # canonical arm OFF, so the deprecated arm is the seeder's only input.
            format_ids=[{"agent_url": _CREATIVE_AGENT, "id": "display_728x90_image"}],
            format_options=None,
        )

        assert captured["config"]["creative_placeholders"] == [_placeholder(728, 90)]

    def test_legacy_only_autoconfig_seeds_the_size_the_entry_stores(self, integration_db):
        """A stored legacy entry's own width and height reach the seeder (salesagent-rlirf).

        ``display_image`` is a TEMPLATE id: it encodes no size, so the only place 970x250
        exists is the entry's ``width`` / ``height`` -- the shape the removed admin format
        picker stored for a parameterized display selection, and which A4 preserves on
        existing rows. ``create_media_buy`` used to hand the seeder ``[fmt.id for fmt in
        format_ids]``, which dropped both fields before the seeder's first branch ("keep
        the width and height a stored entry carries") could read them, so this product was
        trafficked at the 300x250 fallback with only a server log line to say so.

        970x250 is neither the fallback nor anything the id derives, so the assertion fails
        on exactly that discard. Measured at the seeder: handed the bare id
        ``"display_image"`` it answers ``[{width: 300, height: 250, ...}]``, and handed the
        reference it answers ``[{width: 970, height: 250, ...}]``.
        """
        captured = _auto_configure(
            "prod_legacy_entry",
            format_ids=[{"agent_url": _CREATIVE_AGENT, "id": "display_image", "width": 970, "height": 250}],
            format_options=None,
        )

        assert captured["config"]["creative_placeholders"] == [_placeholder(970, 250)]
