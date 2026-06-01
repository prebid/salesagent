"""Integration tests: property_list capability enforcement at the ``_impl`` boundary.

AdCP honest-declaration contract (``core/targeting.json:179``,
``update-media-buy-request.json:64``): a seller whose adapter cannot
compile ``targeting_overlay.property_list`` MUST reject the request with
``UNSUPPORTED_FEATURE`` rather than silently drop the field.

The runtime guard fires once per request in ``_create_media_buy_impl`` /
``_update_media_buy_impl`` — right after adapter resolution, before any
``dry_run`` / approval / execution branch — so every transport (REST, A2A,
MCP) and every adapter (mock, broadstreet, xandr, triton, kevel, GAM)
honors the contract uniformly.

Pin-test: flip ``MockAdServer.supports_property_list_targeting`` to ``True``
and ``test_create_rejects_property_list_when_adapter_unsupported`` must
fail — that proves the boundary check is what's actually rejecting, not
some upstream validation.

Covers: UC-002 honest-declaration property_list reject
Covers: UC-003 honest-declaration property_list reject (update parity)
"""

from __future__ import annotations

import pytest

from src.adapters.mock_ad_server import MockAdServer
from src.core.database.database_session import get_db_session
from src.core.exceptions import AdCPCapabilityNotSupportedError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import CreateMediaBuyRequest, UpdateMediaBuyRequest
from src.core.tools.media_buy_create import _create_media_buy_impl
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.factories import PrincipalFactory
from tests.helpers.adcp_factories import TEST_PROPERTY_LIST_TARGETING_OVERLAY, create_test_package_request
from tests.utils.database_helpers import (
    future_iso_date_range,
    seed_media_buy_with_package,
    seed_property_list_capability_tenant,
)

pytestmark = pytest.mark.requires_db

TENANT_ID = "test_property_list_capability"


def _make_identity(dry_run: bool = True) -> ResolvedIdentity:
    """Identity for property_list capability tests.

    The boundary check fires in ``_create_media_buy_impl`` immediately
    after ``get_adapter()`` resolution and BEFORE the dry_run / approval
    gates, so both ``dry_run=True`` and ``dry_run=False`` exercise the
    same check.

    Default ``dry_run=True`` skips the setup-checklist guard so the test
    tenant doesn't need full SSO + adagents.json configuration.
    ``test_create_dry_run_false_path_also_rejects`` flips this to prove
    the check is not short-circuited by dry_run.
    """
    return PrincipalFactory.make_identity(
        principal_id="test_adv",
        tenant_id=TENANT_ID,
        protocol="mcp",
        dry_run=dry_run,
    )


@pytest.fixture
def capability_tenant(integration_db):
    """Tenant on the mock adapter with a property-targeting-allowed product.

    The mock adapter declares ``supports_property_list_targeting = False``
    (default), so the boundary check fires for any package whose
    ``targeting_overlay.property_list`` is non-None. Tests that need the
    True path monkeypatch the ClassVar. ``property_targeting_allowed=True``
    on the product ensures the product-flag gate doesn't fire first and
    mask the adapter-capability rejection.
    """
    with get_db_session() as session:
        seed_property_list_capability_tenant(
            session,
            tenant_id=TENANT_ID,
            tenant_name="Property List Capability Publisher",
            subdomain="prop-list-cap",
            access_token="test_token_property_list_cap",
            product_id="prod_property_targeting_allowed",
            product_name="Display Ads (property targeting allowed)",
            property_targeting_allowed=True,
        )
        session.commit()
    yield TENANT_ID


def _build_property_list_create_request() -> CreateMediaBuyRequest:
    """Canonical create request used across this file's reject/accept variants.

    Centralizing the request body here keeps the test bodies focused on
    contract assertions (error code, recovery, field, suggestion, envelope
    round-trip) and removes the structural overlap with
    ``test_property_targeting_allowed_enforcement.py`` whose tests build a
    nearly identical request to exercise the product-flag gate.
    """
    start, end = future_iso_date_range()
    return CreateMediaBuyRequest(
        brand={"domain": "testbrand.com"},
        packages=[
            create_test_package_request(
                product_id="prod_property_targeting_allowed",
                budget=5000.0,
                pricing_option_id="cpm_usd_fixed",
                targeting_overlay=TEST_PROPERTY_LIST_TARGETING_OVERLAY,
            )
        ],
        start_time=start,
        end_time=end,
    )


@pytest.mark.requires_db
async def test_create_rejects_property_list_when_adapter_unsupported(capability_tenant):
    """Adapter with ``supports_property_list_targeting = False`` rejects with UNSUPPORTED_FEATURE.

    Spec basis: ``error-code.json:189``, ``error-handling.mdx:467``. The wire
    envelope must carry ``code=UNSUPPORTED_FEATURE``, ``recovery=correctable``,
    machine-actionable ``field`` and ``suggestion`` so the buyer agent can drop
    the field and retry without a human.
    """
    request = _build_property_list_create_request()

    with pytest.raises(AdCPCapabilityNotSupportedError) as excinfo:
        await _create_media_buy_impl(req=request, identity=_make_identity())

    exc = excinfo.value
    # Wire-shape contract: every field must be present.
    assert exc.error_code == "UNSUPPORTED_FEATURE"
    assert exc.recovery == "correctable", (
        "AdCP error-handling.mdx:467 requires the seller-incapacity case to "
        "be correctable so the buyer agent can drop the field and retry. "
        "A terminal classification would dead-end the buyer."
    )
    assert exc.field == "packages[0].targeting_overlay.property_list", (
        f"field must identify the exact offending package; got {exc.field!r}"
    )
    assert exc.suggestion is not None and "property_list_filtering" in exc.suggestion, (
        "suggestion must reference the canonical capability flag so the "
        "buyer agent can locate a capable seller via get_adcp_capabilities"
    )

    # Wire envelope round-trip: spec-compliant ``{"errors": [...]}`` shape
    # carries all five fields through to the response body. The dedicated
    # 3-transport wire tests (test_property_list_unsupported_wire.py) assert on
    # the actual wire shapes; this _impl-level check asserts on the
    # reconstructed envelope from the raised exception.
    envelope = exc.to_adcp_error()
    assert envelope["errors"][0]["code"] == "UNSUPPORTED_FEATURE"
    assert envelope["errors"][0]["recovery"] == "correctable"
    assert envelope["errors"][0]["field"] == "packages[0].targeting_overlay.property_list"
    assert envelope["errors"][0]["suggestion"]


@pytest.mark.requires_db
async def test_create_dry_run_false_path_also_rejects(capability_tenant):
    """Boundary check fires on the real-execution path, not just dry_run.

    The pre-refactor adapter-layer check fired AFTER the dry_run gate, so a
    dry_run=False request could reach adapter execution before being
    rejected. The ``_impl``-level check must fire on both paths uniformly
    so a buyer cannot bypass capability enforcement by toggling dry_run.

    Uses ``test_session_id`` to bypass the production setup-checklist gate
    (SSO + Authorized Properties) so the test can exercise the real-execution
    branch on a minimal test tenant.
    """
    from src.core.testing_hooks import AdCPTestContext

    request = _build_property_list_create_request()
    identity = PrincipalFactory.make_identity(
        principal_id="test_adv",
        tenant_id=TENANT_ID,
        protocol="mcp",
        testing_context=AdCPTestContext(
            dry_run=False,
            mock_time=None,
            jump_to_event=None,
            test_session_id="prop-list-dryrun-false-session",
        ),
    )

    with pytest.raises(AdCPCapabilityNotSupportedError) as excinfo:
        await _create_media_buy_impl(req=request, identity=identity)

    exc = excinfo.value
    assert exc.error_code == "UNSUPPORTED_FEATURE"
    assert exc.recovery == "correctable"
    assert exc.field == "packages[0].targeting_overlay.property_list"


@pytest.mark.requires_db
async def test_create_accepts_property_list_when_adapter_supports(capability_tenant, monkeypatch):
    """Adapter that declares ``supports_property_list_targeting = True`` accepts the field.

    Pin-test: if the boundary check were removed, this test would still
    pass — but its sibling
    ``test_create_rejects_property_list_when_adapter_unsupported`` would
    start passing for the wrong reason (no rejection ever happens). The
    pair together prove the check is *gated* on the ClassVar, not
    always-on or always-off.
    """
    monkeypatch.setattr(MockAdServer, "supports_property_list_targeting", True)
    request = _build_property_list_create_request()

    # The boundary check must NOT raise when the adapter declares support.
    # Downstream failures (e.g. mock-adapter execution returning an error
    # variant) are acceptable for this test's contract — we're proving the
    # *boundary check* doesn't fire, not that the mock adapter creates a
    # real media buy.
    try:
        await _create_media_buy_impl(req=request, identity=_make_identity())
    except AdCPCapabilityNotSupportedError as e:
        pytest.fail(
            "Adapter declared supports_property_list_targeting=True but the "
            f"boundary check rejected anyway: field={e.field!r} suggestion={e.suggestion!r}. "
            "This indicates raise_if_property_list_unsupported is not honoring the "
            "ClassVar override."
        )


@pytest.mark.requires_db
async def test_create_accepts_request_without_property_list_on_unsupported_adapter(capability_tenant):
    """No ``property_list`` on the request → boundary check does not fire.

    Negative-of-the-negative pin-test: if the check fired unconditionally
    on every package (regardless of whether property_list was set), this
    would fail with ``AdCPCapabilityNotSupportedError``. Default mock adapter
    has ``supports_property_list_targeting=False``; absent the field,
    nothing rejects.
    """
    start, end = future_iso_date_range()
    request = CreateMediaBuyRequest(
        brand={"domain": "testbrand.com"},
        packages=[
            create_test_package_request(
                product_id="prod_property_targeting_allowed",
                budget=5000.0,
                pricing_option_id="cpm_usd_fixed",
                # no property_list, no collection_list — just bare targeting
                targeting_overlay={},
            )
        ],
        start_time=start,
        end_time=end,
    )

    # Same expectation as the supports=True case: the boundary check
    # specifically must not fire. Downstream failures are not this test's
    # contract.
    try:
        await _create_media_buy_impl(req=request, identity=_make_identity())
    except AdCPCapabilityNotSupportedError as e:
        pytest.fail(f"Boundary check raised on a request without property_list — false positive. field={e.field!r}")


# ─── update_media_buy boundary check (create/update symmetry) ───────────


@pytest.mark.requires_db
def test_update_rejects_property_list_when_adapter_unsupported(capability_tenant):
    """``_update_media_buy_impl`` enforces the same boundary check as create.

    A buyer that snuck property_list past the original create (e.g.
    because they used a different adapter then) cannot retroactively add
    it via update against a non-compiling seller. Same wire envelope
    contract as create.

    Note: ``_update_media_buy_impl`` is synchronous (no ``async def``); the
    sibling create tests are ``async`` because ``_create_media_buy_impl`` is.
    """
    media_buy_id = "mb_test_plcap"
    with get_db_session() as session:
        seed_media_buy_with_package(
            session,
            tenant_id=capability_tenant,
            principal_id="test_adv",
            product_id="prod_property_targeting_allowed",
            media_buy_id=media_buy_id,
            package_id="pkg_test_plcap",
        )
        session.commit()
    request = UpdateMediaBuyRequest(
        media_buy_id=media_buy_id,
        packages=[
            {
                "package_id": "pkg_test_plcap",
                "targeting_overlay": TEST_PROPERTY_LIST_TARGETING_OVERLAY,
            }
        ],
    )

    with pytest.raises(AdCPCapabilityNotSupportedError) as excinfo:
        _update_media_buy_impl(req=request, identity=_make_identity())

    exc = excinfo.value
    assert exc.error_code == "UNSUPPORTED_FEATURE"
    assert exc.recovery == "correctable"
    assert exc.field == "packages[0].targeting_overlay.property_list"
    assert exc.suggestion is not None
