"""Integration tests: property_list capability enforcement at the ``_impl`` boundary.

AdCP honest-declaration contract: a seller advertises property_list support
via ``ext.prebid.property_list_targeting`` in get_adcp_capabilities
(get_products.mdx), and one that cannot compile
``targeting_overlay.property_list`` rejects with ``UNSUPPORTED_FEATURE``
(error-code.json) rather than silently ignoring it — the spec's
Implementation Requirements make the rule a MUST ("Publishers MUST: ...
Validate Targeting: Reject media buys with targeting that cannot be
supported", targeting.mdx).

The runtime guard fires once per request in ``_create_media_buy_impl`` /
``_update_media_buy_impl`` — right after adapter resolution, before any
``dry_run`` / approval / execution branch — so every transport (REST, A2A,
MCP) and every adapter (mock, broadstreet, xandr, triton, kevel, GAM)
honors the contract uniformly.

MockAdServer now DECLARES support (its simulation persists the overlay and
round-trips it), so the reject tests here pin the capability back to False
via the ``non_compiling_adapter`` fixture — simulating an adapter with no
compile path. The boundary check reads the adapter CLASS attribute, so the
pin exercises the same production gate every non-compiling adapter hits.

Covers: UC-002 honest-declaration property_list reject
Covers: UC-003 honest-declaration property_list reject (update parity)
"""

from __future__ import annotations

import uuid

import pytest

from src.adapters.mock_ad_server import MockAdServer
from src.core.database.database_session import get_db_session
from src.core.exceptions import AdCPCapabilityNotSupportedError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import CreateMediaBuyRequest, UpdateMediaBuyRequest
from src.core.tools.media_buy_create import _create_media_buy_impl
from src.core.tools.media_buy_update import _update_media_buy_impl
from tests.factories import PrincipalFactory
from tests.helpers.adcp_factories import (
    TEST_PROPERTY_LIST_TARGETING_OVERLAY,
    create_test_package_request,
    create_test_property_list_create_params,
)
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
def capability_media_buy(capability_tenant):
    """Seed a media buy + package under the capability tenant for the update test.

    Data setup lives in the fixture (CLAUDE.md Pattern #8), not the test body.
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
    return media_buy_id


@pytest.fixture
def non_compiling_adapter(monkeypatch):
    """Pin the mock adapter to a no-compile-path declaration.

    The reject contract under test belongs to adapters WITHOUT a property_list
    compile path; MockAdServer declares support, so reject tests restore the
    False declaration explicitly.
    """
    monkeypatch.setattr(MockAdServer, "supports_property_list_targeting", False)


@pytest.fixture
def capability_tenant(integration_db):
    """Tenant on the mock adapter with a property-targeting-allowed product.

    MockAdServer declares ``supports_property_list_targeting = True`` (its
    simulation is the compile path), so reject tests pair this tenant with
    the ``non_compiling_adapter`` fixture to restore the False declaration
    the boundary check fires on. ``property_targeting_allowed=True``
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

    Delegates the request shape to the shared
    ``create_test_property_list_create_params`` factory so the wire and
    capability files exercise one definition; test bodies stay focused on
    contract assertions (error code, recovery, field, suggestion, envelope
    round-trip).
    """
    return CreateMediaBuyRequest(
        # per-call-unique: reused keys replay once the required-key change lands
        idempotency_key=f"prop-list-cap-{uuid.uuid4().hex}",
        **create_test_property_list_create_params("prod_property_targeting_allowed"),
    )


@pytest.mark.requires_db
async def test_create_rejects_property_list_when_adapter_unsupported(capability_tenant, non_compiling_adapter):
    """Adapter with ``supports_property_list_targeting = False`` rejects with UNSUPPORTED_FEATURE.

    Spec basis: ``error-code.json`` UNSUPPORTED_FEATURE + the two-layer envelope
    rules in ``error-handling.mdx``. The wire
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
    assert exc.suggestion is not None and "property_list_targeting" in exc.suggestion, (
        "suggestion must reference the create-targeting capability signal so the "
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
async def test_create_dry_run_false_path_also_rejects(capability_tenant, non_compiling_adapter):
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
    # MockAdServer declares True by default now; the explicit pin keeps this
    # test independent of that default (and of fixture ordering).
    monkeypatch.setattr(MockAdServer, "supports_property_list_targeting", True)
    request = _build_property_list_create_request()

    # The boundary check must NOT raise when the adapter declares support.
    # Downstream failures (e.g. mock-adapter execution returning an error
    # variant) are acceptable for this test's contract — we're proving the
    # *boundary check* doesn't fire, not that the mock adapter creates a
    # real media buy.
    try:
        result = await _create_media_buy_impl(req=request, identity=_make_identity())
    except AdCPCapabilityNotSupportedError as e:
        pytest.fail(
            "Adapter declared supports_property_list_targeting=True but the "
            f"boundary check rejected anyway: field={e.field!r} suggestion={e.suggestion!r}. "
            "This indicates raise_if_property_list_unsupported is not honoring the "
            "ClassVar override."
        )
    assert result.response is not None, (
        "boundary did not fire, but _impl must return a materialized "
        f"CreateMediaBuyResult (guards a missing await / silent None); got {result!r}"
    )


@pytest.mark.requires_db
async def test_create_accepts_request_without_property_list_on_unsupported_adapter(
    capability_tenant, non_compiling_adapter
):
    """No ``property_list`` on the request → boundary check does not fire.

    Negative-of-the-negative pin-test: if the check fired unconditionally
    on every package (regardless of whether property_list was set), this
    would fail with ``AdCPCapabilityNotSupportedError``. Default mock adapter
    has ``supports_property_list_targeting=False``; absent the field,
    nothing rejects.
    """
    start, end = future_iso_date_range()
    request = CreateMediaBuyRequest(
        idempotency_key=f"prop-list-cap-2-{uuid.uuid4().hex}",
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
        result = await _create_media_buy_impl(req=request, identity=_make_identity())
    except AdCPCapabilityNotSupportedError as e:
        pytest.fail(f"Boundary check raised on a request without property_list — false positive. field={e.field!r}")
    assert result.response is not None, (
        f"boundary did not fire, but _impl must return a materialized result; got {result!r}"
    )


# ─── update_media_buy boundary check (create/update symmetry) ───────────


@pytest.mark.requires_db
def test_update_rejects_property_list_when_adapter_unsupported(capability_media_buy, non_compiling_adapter):
    """``_update_media_buy_impl`` enforces the same boundary check as create.

    A buyer that snuck property_list past the original create (e.g.
    because they used a different adapter then) cannot retroactively add
    it via update against a non-compiling seller. Same wire envelope
    contract as create.

    Note: ``_update_media_buy_impl`` is synchronous (no ``async def``); the
    sibling create tests are ``async`` because ``_create_media_buy_impl`` is.
    """
    media_buy_id = capability_media_buy
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


@pytest.mark.requires_db
def test_update_accepts_and_recompiles_property_list_on_capable_adapter(capability_media_buy):
    """E3 update parity: a capable adapter receives the recompile call.

    The update-side sibling of ``test_create_accepts_property_list_when_adapter_supports``:
    MockAdServer declares support, so the boundary gate passes, the overlay
    persists, and the impl pushes the recompile through the adapter seam
    (``update_package_targeting``) — pinned here via a spy on the real
    MockAdServer method so the impl→adapter wiring is the thing proven.
    """
    from unittest.mock import patch as mock_patch

    media_buy_id = capability_media_buy
    request = UpdateMediaBuyRequest(
        media_buy_id=media_buy_id,
        packages=[
            {
                "package_id": "pkg_test_plcap",
                "targeting_overlay": TEST_PROPERTY_LIST_TARGETING_OVERLAY,
            }
        ],
    )

    from datetime import date
    from unittest.mock import ANY

    with (
        mock_patch.object(MockAdServer, "validate_targeting_update", autospec=True) as spy_validate,
        mock_patch.object(MockAdServer, "update_package_targeting", autospec=True) as spy_apply,
    ):
        # dry_run=False: the recompile push lives in the write phase, which
        # the dry_run early-return never reaches (the type gate, by contrast,
        # runs before it — pinned by the reject sibling above).
        result = _update_media_buy_impl(req=request, identity=_make_identity(dry_run=False))

    assert result is not None
    # autospec passes the adapter instance first (ANY); the rest are the
    # impl's exact positional arguments.
    spy_validate.assert_called_once_with(ANY, request.packages)
    spy_apply.assert_called_once_with(
        ANY,
        media_buy_id,
        "pkg_test_plcap",
        request.packages[0].targeting_overlay,
        date.today(),
    )
