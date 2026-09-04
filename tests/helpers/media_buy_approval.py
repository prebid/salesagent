"""Seeding and boundary helpers for tests that drive a media-buy approval for real.

An admin approval route calls ``execute_approved_media_buy``, which is the sole
post-adapter writer of the media-buy row. A test that patches that callee has
removed the only writer, so it can no longer assert anything about what the
approval persisted. The one thing worth patching is the AD-SERVER boundary
(``ADAPTER_BOUNDARY``), which leaves the callee, both units of work, and every
commit running for real.

Getting a buy through that callee needs more than a ``MediaBuyFactory`` row — see
``seed_pending_buy`` — and getting a creative through the asset gate needs more
than a ``CreativeFactory`` row — see ``uploadable_creative``. Both are here rather
than in one test module because the workflows, operations and creatives approval
routes all need them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

# The ad-server boundary: the one seam worth patching in an approval test. Patched
# on the defining module because ``execute_approved_media_buy`` resolves it as a
# module global.
ADAPTER_BOUNDARY = "src.core.tools.media_buy_create._execute_adapter_media_buy_creation"


def adapter_success(*_args: Any, **_kwargs: Any) -> Any:
    """What a healthy adapter hands back: an order id and no packages to map."""
    from src.core.schemas import CreateMediaBuySuccess

    return CreateMediaBuySuccess.carrier(media_buy_id=f"gam_order_{uuid.uuid4().hex[:8]}", packages=[])


def adapter_failure(*_args: Any, **_kwargs: Any) -> Any:
    """What a sick adapter does: raise out of the boundary call."""
    raise RuntimeError("ad server rejected the order")


def login_as(client: Any, *, tenant_id: str, email: str = "test@example.com", super_admin: bool = True) -> None:
    """Authenticate an admin test client as a user scoped to ``tenant_id``.

    ``super_admin=False`` is what makes a session tell ``@require_tenant_access()``
    apart from its absence: a super admin is allowed across tenants by design, so a
    refusal test authenticated as one grades nothing.
    """
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {"email": email, "is_super_admin": super_admin}
        sess["email"] = email
        sess["tenant_id"] = tenant_id
        sess["test_user"] = email
        sess["test_user_role"] = "super_admin" if super_admin else "tenant_admin"
        sess["test_user_name"] = email
        sess["test_tenant_id"] = tenant_id


def run_approval(media_buy_id: str, tenant_id: str, *, approved_by: str = "approver@example.com") -> Any:
    """Call the single post-adapter writer the way a route does, and return its result.

    ``execute_approved_media_buy`` returns a typed ``ApprovalResult``; it used to return
    ``tuple[bool, str | None]``, and a caller that unpacks the tuple now raises
    ``TypeError``. One spelling of the call lives here so the three integration modules
    that drive it directly cannot drift on the keyword arguments.
    """
    from src.core.tools.media_buy_create import execute_approved_media_buy

    return execute_approved_media_buy(
        media_buy_id,
        tenant_id,
        approved_by=approved_by,
        approved_at=datetime.now(UTC),
    )


def uploadable_creative(creative_factory: Any, **kwargs: Any) -> Any:
    """A creative that survives the asset gate an approval runs before uploading.

    ``_build_adapter_asset_from_creative`` resolves the creative's format spec and
    then pulls url/width/height out of ``data["assets"]`` via
    ``extract_media_url_and_dimensions``. ``CreativeFactory``'s defaults do not get
    through it: the format ``display_300x250`` is a legacy id absent from the
    reference catalog, and the asset is keyed ``banner``, which is neither an
    asset_id any format spec declares nor a member of production's
    ``MEDIA_ASSET_FALLBACK_IDS``. Both extraction priorities miss and the buy fails
    the gate on a seeding artifact rather than on anything under test.

    ``display_300x250_image`` IS in the catalog (served offline — ``ADCP_TESTING``
    is set autouse — so no creative agent is contacted), and it declares exactly the
    two asset_ids seeded here. ``banner_image`` is also in the fallback allowlist, so
    the extraction succeeds whether or not the spec resolves.
    """
    from tests.factories.creative_asset import build_assets, image_spec, url_spec

    kwargs.setdefault("format", "display_300x250_image")
    kwargs.setdefault(
        "data",
        {
            "assets": build_assets(
                image_spec("banner_image"),
                url_spec("click_url", url="https://advertiser.example.com/landing", url_type="clickthrough"),
            )
        },
    )
    return creative_factory(**kwargs)


@dataclass(frozen=True)
class SeededBuy:
    """The rows one approval needs: the ORM objects, plus the ids routes are addressed by."""

    tenant: Any
    principal: Any
    media_buy: Any
    context_id: str
    step_id: str

    @property
    def tenant_id(self) -> str:
        return self.tenant.tenant_id

    @property
    def media_buy_id(self) -> str:
        return self.media_buy.media_buy_id


def seed_pending_buy(*, starts_in_days: int, status: str = "pending_approval") -> SeededBuy:
    """Seed a tenant + product + pending-approval buy + its approval step.

    The buy carries everything ``execute_approved_media_buy`` reconstructs from: a
    ``raw_request`` that revalidates as a ``CreateMediaBuyRequest`` (``MediaBuyFactory``'s
    default omits ``packages[].pricing_option_id``, and the route 500s on that before
    reaching anything under test), a ``MediaPackage`` naming a product that exists, and
    resolved ``start_time``/``end_time`` — which are also what
    ``resolve_flight_window_status`` reads, so ``starts_in_days`` decides the status the
    flight-window rule implies.

    ``status`` seeds the buy's starting state — ``pending_creatives`` for the
    creative-approval route, which unblocks a buy that is already waiting rather than
    approving one from scratch.

    Requires the factories to be bound to a session (the ``factory_session`` fixture).
    """
    from src.core.context_manager import ContextManager
    from tests.factories import (
        MediaBuyFactory,
        MediaPackageFactory,
        PricingOptionFactory,
        PrincipalFactory,
        ProductFactory,
        TenantFactory,
    )

    suffix = uuid.uuid4().hex[:8]
    start_time = datetime.now(UTC) + timedelta(days=starts_in_days)
    end_time = start_time + timedelta(days=30)

    tenant = TenantFactory(tenant_id=f"t_appr_{suffix}", ad_server="mock")
    principal = PrincipalFactory(tenant=tenant, principal_id=f"p_appr_{suffix}")
    product = ProductFactory(tenant=tenant, product_id=f"prod_appr_{suffix}")
    PricingOptionFactory(product=product)

    media_buy_id = f"mb_appr_{suffix}"
    media_buy = MediaBuyFactory(
        tenant=tenant,
        principal=principal,
        media_buy_id=media_buy_id,
        status=status,
        start_date=start_time.date(),
        end_date=end_time.date(),
        start_time=start_time,
        end_time=end_time,
        raw_request={
            "brand": {"domain": "testbrand.com"},
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "idempotency_key": f"approval-{suffix}",
            "packages": [
                {
                    "product_id": product.product_id,
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                }
            ],
        },
    )
    MediaPackageFactory(
        media_buy=media_buy,
        package_id=f"pkg_appr_{suffix}",
        package_config={"product_id": product.product_id, "budget": 5000.0},
    )

    cm = ContextManager()
    context = cm.create_context(tenant_id=tenant.tenant_id, principal_id=principal.principal_id)
    step = cm.create_workflow_step(
        context_id=context.context_id,
        step_type="approval",
        owner="publisher",
        status="requires_approval",
        tool_name="create_media_buy",
        request_data={},
        object_mappings=[{"object_type": "media_buy", "object_id": media_buy_id, "action": "approve"}],
    )
    return SeededBuy(tenant, principal, media_buy, context.context_id, step.step_id)


def attach_approved_creative(seeded: SeededBuy, *, package_id: str = "pkg_ready") -> Any:
    """Attach one approved, uploadable creative so #1696 ready-arm / sole writer can run.

    ``seed_pending_buy`` omits assignments; admin finalize holds on zero assignments
    (#1696). Ready-path / adapter-failure tests that must reach
    ``execute_approved_media_buy`` call this after seeding.
    """
    from tests.factories import CreativeAssignmentFactory, CreativeFactory

    creative = uploadable_creative(
        CreativeFactory,
        tenant=seeded.tenant,
        principal=seeded.principal,
        status="approved",
    )
    CreativeAssignmentFactory(creative=creative, media_buy=seeded.media_buy, package_id=package_id)
    return creative
