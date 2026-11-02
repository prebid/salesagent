"""Integration tests for creative entity (v3.6 migration batch).

Tests derived from unit test stubs in tests/unit/test_creative.py.
These verify the SAME behaviors with real PostgreSQL instead of mocks.

Iron Rule: If an integration test fails, the production code is wrong --
never adjust the expected behavior from the unit test stub.

Covers:
- Cross-principal isolation (BR-RULE-034)
- Approval workflow modes (BR-RULE-037)
- Batch sync with real DB
- Upsert by triple key
- Format compatibility during assignment (BR-RULE-039)
- Media buy status transition on creative assignment (BR-RULE-040)
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from adcp.types import AccountReference
from adcp.types.generated_poc.creative.sync_creatives_request import Assignment
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from src.core.database.models import (
    CurrencyLimit,
    MediaBuy,
    MediaPackage,
    Principal,
)
from src.core.database.models import Product as DBProduct
from src.core.schemas import CreativeStatusEnum, SyncCreativesRequest, SyncCreativesResponse
from tests.factories.creative_asset import build_assets, image_spec
from tests.factories.principal import plaintext_token_for
from tests.helpers.credentials import credential_headers
from tests.helpers.envelope_assertions import raises_adcp
from tests.utils.database_helpers import create_tenant_with_timestamps

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_AGENT_URL = "https://test-agent.example.com"
DEFAULT_FORMAT_ID = "display_300x250_image"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _headers(tenant_id: str, principal_id: str) -> dict[str, str]:
    """The headers a call arrives with: this caller's credential and the seller it names.

    ``invoke_tool`` takes HEADERS, never an identity -- the resolver is their one reader --
    so a test states the credential and the tenant and nothing else. Everything the
    identity used to be handed (the principal, and the tenant's domain fields such as
    ``approval_mode``) is read off the ROWS the fixtures seed.
    """
    return credential_headers(token=plaintext_token_for(principal_id), tenant=tenant_id)


def _make_creative_dict(
    creative_id: str = "c_test_1",
    name: str = "Test Creative",
    format_id: str = DEFAULT_FORMAT_ID,
    agent_url: str = DEFAULT_AGENT_URL,
) -> dict:
    return {
        "creative_id": creative_id,
        "name": name,
        "format_id": {"agent_url": agent_url, "id": format_id},
        # url/width/height are PRE-3.x. CreativeAssetRequest forbids extras and 3.1.1 puts
        # the media reference inside ``assets``. They survived only because nothing built
        # the request model on the sync path; every transport does now. Backwards
        # compatibility, where wanted, belongs in RequestCompatMiddleware -- not in the
        # tool signature or _impl.
        "assets": build_assets(image_spec("banner")),
    }


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


ACCOUNT_ID = "acct_v3"


def _seed_account_for(tenant_id: str, principal_ids: tuple[str, ...]) -> None:
    """Add the Account sync-creatives-request.json requires, and grant these principals access.

    Principal ids are passed in rather than queried: reading them back would need a session
    in a test body, which test_architecture_repository_pattern.py forbids -- and the caller
    already knows them, having just created them.
    """
    from tests.factories import AccountFactory, AgentAccountAccessFactory

    AccountFactory(tenant_id=tenant_id, account_id=ACCOUNT_ID)
    for pid in principal_ids:
        AgentAccountAccessFactory(tenant_id=tenant_id, principal_id=pid, account_id=ACCOUNT_ID)


@pytest.fixture(autouse=True)
def mock_format_registry():
    """Mock creative agent format registry to avoid real HTTP calls."""
    from tests.helpers.adcp_factories import create_test_format

    mock_formats = {
        DEFAULT_FORMAT_ID: create_test_format(
            format_id=DEFAULT_FORMAT_ID,
            name="Display 300x250 Image",
            type="display",
        ),
        "video_instream_15s": create_test_format(
            format_id="video_instream_15s",
            name="Video Instream 15s",
            type="video",
        ),
    }

    with patch("src.core.creative_agent_registry.CreativeAgentRegistry.get_format") as mock_get:

        def get_format_side_effect(agent_url, format_id, **_kwargs):
            return mock_formats.get(format_id)

        mock_get.side_effect = get_format_side_effect
        yield mock_get


# ---------------------------------------------------------------------------
# Test class: Cross-Principal Isolation (BR-RULE-034)
# ---------------------------------------------------------------------------


def _sync_creatives(**kwargs):
    """Build a SyncCreativesRequest from flat fields, then dispatch it at the boundary.

    ``invoke_tool`` is the path every transport takes -- identity resolution, account
    resolution and the idempotency probe included -- so a call site here reaches production
    the way a buyer does. It takes the request's HEADERS and the transport label; the
    caller is resolved inside, from the credential those headers present.
    """
    import asyncio

    from src.core.resolved_identity import TransportProtocol
    from src.core.tools._boundary import invoke_tool

    headers = kwargs.pop("headers")
    kwargs.pop("ctx", None)
    return asyncio.run(invoke_tool("sync_creatives", SyncCreativesRequest(**kwargs), headers, TransportProtocol.MCP))


class TestCrossPrincipalIsolation:
    """BR-RULE-034: Cross-principal creative isolation with real DB.

    Unit stubs: TestCrossPrincipalIsolation in test_creative.py
    Spec: UNSPECIFIED (implementation-defined multi-tenant isolation).
    """

    TENANT_ID = "iso_tenant"

    @pytest.fixture(autouse=True)
    def setup_tenant(self, integration_db, bound_factory_session):
        """Create tenant with two principals."""
        with get_db_session() as session:
            tenant = create_tenant_with_timestamps(
                tenant_id=self.TENANT_ID,
                name="Isolation Test Tenant",
                subdomain="iso-test",
                is_active=True,
                ad_server="mock",
                approval_mode="auto-approve",
            )
            session.add(tenant)
            session.add(
                CurrencyLimit(
                    tenant_id=self.TENANT_ID,
                    currency_code="USD",
                    min_package_budget=100.0,
                    max_daily_package_spend=10000.0,
                )
            )
            session.add(
                Principal.with_token(
                    plaintext_token_for("principal_1"),
                    tenant_id=self.TENANT_ID,
                    principal_id="principal_1",
                    name="Principal 1",
                    platform_mappings={"mock": {"id": "p1"}},
                )
            )
            session.add(
                Principal.with_token(
                    plaintext_token_for("principal_2"),
                    tenant_id=self.TENANT_ID,
                    principal_id="principal_2",
                    name="Principal 2",
                    platform_mappings={"mock": {"id": "p2"}},
                )
            )
            session.commit()

        # ``account`` is in sync-creatives-request.json /required, so every sync call in
        # this file needs one that the calling principal can reach. Seeded through the
        # factories (CLAUDE.md) rather than extending the hand-rolled block above.
        _seed_account_for(self.TENANT_ID, ("principal_1", "principal_2"))

    def _sync_one(self, principal_id: str, creative_id: str = "c_shared") -> SyncCreativesResponse:
        return _sync_creatives(
            creatives=[_make_creative_dict(creative_id=creative_id)],
            # Both are in sync-creatives-request.json /required, and every transport builds
            # SyncCreativesRequest now, so omitting either is a rejected request rather than
            # a call that reaches the impl.
            # A FRESH key per call, as sync-creatives-request.json directs ("Use a fresh
            # UUID v4 for each request"). These were derived from the creative_id or
            # hardcoded, so a test syncing the same creative twice with different content
            # reused one key across two payloads -- correctly an IDEMPOTENCY_CONFLICT now
            # that sync_creatives honours the key.
            idempotency_key=f"sync-{uuid4().hex}",
            account=AccountReference(root={"account_id": ACCOUNT_ID}),
            headers=_headers(self.TENANT_ID, principal_id),
        )

    def test_creative_lookup_filters_by_principal(self):
        """Creative upsert lookup uses tenant_id + principal_id + creative_id triple.

        Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-01
        Spec: UNSPECIFIED (implementation-defined multi-tenant isolation).
        Unit stub: TestCrossPrincipalIsolation::test_creative_lookup_filters_by_principal
        """
        result = self._sync_one("principal_1", "c_filter_test")
        assert result is not None
        assert len(result.creatives) == 1

        # Verify DB row has correct principal
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(
                tenant_id=self.TENANT_ID,
                principal_id="principal_1",
                creative_id="c_filter_test",
            )
            db_row = session.scalars(stmt).first()
            assert db_row is not None
            assert db_row.principal_id == "principal_1"

    def test_same_creative_id_different_principal_creates_new(self):
        """Same creative_id under different principals creates separate DB records.

        Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-02
        Unit stub: TestCrossPrincipalIsolation::test_same_creative_id_different_principal_creates_new
        """
        self._sync_one("principal_1", "c_shared")
        self._sync_one("principal_2", "c_shared")

        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(
                tenant_id=self.TENANT_ID,
                creative_id="c_shared",
            )
            rows = session.scalars(stmt).all()
            assert len(rows) == 2
            principal_ids = {r.principal_id for r in rows}
            assert principal_ids == {"principal_1", "principal_2"}

    def test_new_creative_stamped_with_principal_id(self):
        """New creative DB record has principal_id from identity.

        Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-03
        Spec: UNSPECIFIED (implementation-defined multi-tenant isolation).
        Unit stub: TestCrossPrincipalIsolation::test_new_creative_stamped_with_principal_id
        """
        self._sync_one("principal_1", "c_stamp_test")

        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(
                tenant_id=self.TENANT_ID,
                creative_id="c_stamp_test",
            )
            db_row = session.scalars(stmt).first()
            assert db_row is not None
            assert db_row.principal_id == "principal_1"


# ---------------------------------------------------------------------------
# Test class: Approval Workflow (BR-RULE-037)
# ---------------------------------------------------------------------------


class TestApprovalWorkflow:
    """BR-RULE-037: Creative approval modes with real DB.

    Unit stubs: TestApprovalWorkflow in test_creative.py
    Spec: UNSPECIFIED (implementation-defined approval workflow).
    """

    @pytest.fixture(autouse=True)
    def bind_factories(self, integration_db, bound_factory_session):
        """Bind the factories; each test seeds the tenant whose mode it grades.

        The mode used to ride on a hand-built TenantContext handed to ``_impl``. The
        boundary loads the tenant from its ROW now (``TenantContext.load``), so
        ``approval_mode`` has to BE the row's -- which means one tenant per mode rather
        than one tenant and three identities.
        """

    def _seed(self, tenant_id: str, **tenant_fields) -> str:
        """A tenant with *tenant_fields*, a principal in it, and the account it can reach.

        Returns the principal_id, derived from the tenant so the tokens stay distinct:
        ``PrincipalFactory`` derives the (globally unique) ``token_hash`` from the
        principal_id alone.
        """
        from tests.factories import PrincipalFactory, TenantFactory

        principal_id = f"{tenant_id}_buyer"
        tenant = TenantFactory(tenant_id=tenant_id, **tenant_fields)
        PrincipalFactory(tenant=tenant, principal_id=principal_id, name="Test Advertiser")
        # ``account`` is in sync-creatives-request.json /required, so every sync call in
        # this file needs one that the calling principal can reach.
        _seed_account_for(tenant_id, (principal_id,))
        return principal_id

    def _sync(self, tenant_id: str, principal_id: str, creative_id: str) -> SyncCreativesResponse:
        return _sync_creatives(
            creatives=[_make_creative_dict(creative_id=creative_id)],
            # Both are in sync-creatives-request.json /required, and every transport builds
            # SyncCreativesRequest now, so omitting either is a rejected request rather than
            # a call that reaches the impl.
            idempotency_key=f"sync-{uuid4().hex}",
            account=AccountReference(root={"account_id": ACCOUNT_ID}),
            headers=_headers(tenant_id, principal_id),
        )

    def _get_db_status(self, tenant_id: str, creative_id: str) -> str | None:
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(
                tenant_id=tenant_id,
                creative_id=creative_id,
            )
            row = session.scalars(stmt).first()
            return row.status if row else None

    def test_auto_approve_sets_approved_status(self):
        """Auto-approve mode sets creative status to approved in DB.

        Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-01
        Spec: UNSPECIFIED (implementation-defined approval workflow).
        Unit stub: TestApprovalWorkflow::test_auto_approve_sets_approved_status
        """
        tenant_id = "approval_auto"
        principal_id = self._seed(tenant_id, approval_mode="auto-approve")
        self._sync(tenant_id, principal_id, "c_auto")
        assert self._get_db_status(tenant_id, "c_auto") == CreativeStatusEnum.approved.value

    def test_require_human_sets_pending_review(self):
        """Require-human mode sets creative status to pending_review in DB.

        Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-02
        Spec: UNSPECIFIED (implementation-defined approval workflow).
        Unit stub: TestApprovalWorkflow::test_require_human_sets_pending_review
        """
        tenant_id = "approval_human"
        principal_id = self._seed(tenant_id, approval_mode="require-human")
        self._sync(tenant_id, principal_id, "c_human")
        assert self._get_db_status(tenant_id, "c_human") == CreativeStatusEnum.pending_review.value

    def test_default_approval_mode_is_require_human(self):
        """Tenant with no approval_mode defaults to require-human.

        Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-04
        Spec: UNSPECIFIED (implementation-defined approval workflow).
        Unit stub: TestApprovalWorkflow::test_default_approval_mode_is_require_human
        """
        # No approval_mode: the tenant COLUMN defaults to "require-human"
        # (models.Tenant.approval_mode), which is the default under test. It used to be
        # spelled as a tenant dict with the key omitted, which graded TenantContext's own
        # field default instead of the row's.
        tenant_id = "approval_default"
        principal_id = self._seed(tenant_id)
        self._sync(tenant_id, principal_id, "c_default")
        assert self._get_db_status(tenant_id, "c_default") == CreativeStatusEnum.pending_review.value


# ---------------------------------------------------------------------------
# Test class: Batch Sync (real DB)
# ---------------------------------------------------------------------------


class TestBatchSync:
    """Batch sync and upsert with real DB.

    Unit stubs: TestSyncCreativesE2E in test_creative.py
    """

    TENANT_ID = "batch_tenant"
    PRINCIPAL_ID = "batch_principal"

    @pytest.fixture(autouse=True)
    def setup_tenant(self, integration_db, bound_factory_session):
        with get_db_session() as session:
            tenant = create_tenant_with_timestamps(
                tenant_id=self.TENANT_ID,
                name="Batch Test Tenant",
                subdomain="batch-test",
                is_active=True,
                ad_server="mock",
                approval_mode="auto-approve",
            )
            session.add(tenant)
            session.add(
                CurrencyLimit(
                    tenant_id=self.TENANT_ID,
                    currency_code="USD",
                    min_package_budget=100.0,
                    max_daily_package_spend=10000.0,
                )
            )
            session.add(
                Principal.with_token(
                    plaintext_token_for(self.PRINCIPAL_ID),
                    tenant_id=self.TENANT_ID,
                    principal_id=self.PRINCIPAL_ID,
                    name="Batch Advertiser",
                    platform_mappings={"mock": {"id": "batch_adv"}},
                )
            )
            session.commit()

        # ``account`` is in sync-creatives-request.json /required, so every sync call in
        # this file needs one that the calling principal can reach. Seeded through the
        # factories (CLAUDE.md) rather than extending the hand-rolled block above.
        _seed_account_for(self.TENANT_ID, (self.PRINCIPAL_ID,))

    def _creds(self) -> dict[str, str]:
        return _headers(self.TENANT_ID, self.PRINCIPAL_ID)

    def test_batch_sync_multiple_creatives(self):
        """Batch of N creatives produces N per-creative results and N DB rows.

        Covers: UC-006-MAIN-MCP-02
        Unit stub: TestSyncCreativesE2E::test_batch_sync_multiple_creatives
        """

        creatives = [_make_creative_dict(creative_id=f"c_{i}", name=f"Creative {i}") for i in range(5)]
        result = _sync_creatives(
            creatives=creatives,
            # Both are in sync-creatives-request.json /required.
            idempotency_key=f"sync-{uuid4().hex}",
            account=AccountReference(root={"account_id": ACCOUNT_ID}),
            headers=self._creds(),
        )

        assert len(result.creatives) == 5
        result_ids = {r.creative_id for r in result.creatives}
        expected_ids = {f"c_{i}" for i in range(5)}
        assert result_ids == expected_ids

        # Verify all 5 rows in DB
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID)
            rows = session.scalars(stmt).all()
            assert len(rows) == 5

    def test_upsert_by_triple_key(self):
        """First sync creates, second sync updates (action=updated).

        Covers: UC-006-MAIN-MCP-03
        Unit stub: TestSyncCreativesE2E::test_upsert_by_triple_key
        """

        headers = self._creds()

        # First sync: create
        result1 = _sync_creatives(
            creatives=[_make_creative_dict(creative_id="c_upsert", name="Original Name")],
            # Both are in sync-creatives-request.json /required.
            idempotency_key=f"sync-{uuid4().hex}",
            account=AccountReference(root={"account_id": ACCOUNT_ID}),
            headers=headers,
        )
        action1 = result1.creatives[0].action
        if hasattr(action1, "value"):
            action1 = action1.value
        assert action1 == "created"

        # Second sync: update
        result2 = _sync_creatives(
            creatives=[_make_creative_dict(creative_id="c_upsert", name="Updated Name")],
            # Both are in sync-creatives-request.json /required.
            idempotency_key=f"sync-{uuid4().hex}",
            account=AccountReference(root={"account_id": ACCOUNT_ID}),
            headers=headers,
        )
        action2 = result2.creatives[0].action
        if hasattr(action2, "value"):
            action2 = action2.value
        assert action2 == "updated"

        # Still just one row in DB (upsert, not duplicate)
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(
                tenant_id=self.TENANT_ID,
                creative_id="c_upsert",
            )
            rows = session.scalars(stmt).all()
            assert len(rows) == 1


# ---------------------------------------------------------------------------
# Test class: Format Compatibility (BR-RULE-039)
# ---------------------------------------------------------------------------


class TestFormatCompatibility:
    """BR-RULE-039: Assignment format compatibility with real DB.

    Unit stub: TestFormatCompatibility::test_format_mismatch_strict_raises
    Spec: UNSPECIFIED (implementation-defined format compatibility logic).
    """

    TENANT_ID = "fmt_compat_tenant"
    PRINCIPAL_ID = "fmt_compat_principal"

    @pytest.fixture(autouse=True)
    def setup_tenant(self, integration_db, bound_factory_session):
        with get_db_session() as session:
            tenant = create_tenant_with_timestamps(
                tenant_id=self.TENANT_ID,
                name="Format Compat Tenant",
                subdomain="fmt-compat-test",
                is_active=True,
                ad_server="mock",
                approval_mode="auto-approve",
            )
            session.add(tenant)
            session.add(
                CurrencyLimit(
                    tenant_id=self.TENANT_ID,
                    currency_code="USD",
                    min_package_budget=100.0,
                    max_daily_package_spend=10000.0,
                )
            )
            session.add(
                Principal.with_token(
                    plaintext_token_for(self.PRINCIPAL_ID),
                    tenant_id=self.TENANT_ID,
                    principal_id=self.PRINCIPAL_ID,
                    name="Format Compat Advertiser",
                    platform_mappings={"mock": {"id": "fmt_adv"}},
                )
            )
            # Product that only supports video_instream_15s
            product = DBProduct(
                tenant_id=self.TENANT_ID,
                product_id="prod_video_only",
                name="Video Only Product",
                description="Only supports video",
                format_ids=[
                    {"agent_url": DEFAULT_AGENT_URL, "id": "video_instream_15s"},
                ],
                targeting_template={},
                delivery_type="non_guaranteed",
                properties=[{"publisher_domain": "test.com", "selection_type": "all"}],
            )
            session.add(product)

            # Media buy with package pointing to the video-only product
            mb = MediaBuy(
                tenant_id=self.TENANT_ID,
                media_buy_id="mb_fmt_test",
                principal_id=self.PRINCIPAL_ID,
                order_name="Format Test Order",
                advertiser_name="Format Compat Advertiser",
                status="active",
                budget=5000.0,
                start_date=datetime.now(UTC).date(),
                end_date=(datetime.now(UTC) + timedelta(days=30)).date(),
                raw_request={"packages": [{"package_id": "pkg_video", "paused": False}]},
            )
            session.add(mb)
            session.commit()

            pkg = MediaPackage(
                media_buy_id="mb_fmt_test",
                package_id="pkg_video",
                package_config={"package_id": "pkg_video", "product_id": "prod_video_only"},
            )
            session.add(pkg)
            session.commit()

        # ``account`` is in sync-creatives-request.json /required, so every sync call in
        # this file needs one that the calling principal can reach. Seeded through the
        # factories (CLAUDE.md) rather than extending the hand-rolled block above.
        _seed_account_for(self.TENANT_ID, (self.PRINCIPAL_ID,))

    def test_format_mismatch_strict_raises(self):
        """Strict mode: display creative assigned to video-only package raises error.

        Covers: UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-02
        Spec: UNSPECIFIED (implementation-defined format compatibility logic).
        Unit stub: TestFormatCompatibility::test_format_mismatch_strict_raises
        """
        from src.core.exceptions import AdCPValidationError

        headers = _headers(self.TENANT_ID, self.PRINCIPAL_ID)

        # First sync the display creative (so it exists in DB)
        _sync_creatives(
            creatives=[_make_creative_dict(creative_id="c_display")],
            # Both are in sync-creatives-request.json /required.
            idempotency_key=f"sync-{uuid4().hex}",
            account=AccountReference(root={"account_id": ACCOUNT_ID}),
            headers=headers,
        )

        # Now try to assign it to the video-only package in strict mode.
        #
        # The CODE is the oracle, not the typed class: this dispatches at the boundary,
        # which answers every failure with a response and raises AdcpFailure carrying it,
        # so the raise site's exception never reaches the caller (tests/CLAUDE.md §
        # Error verification policy).
        #
        # And the code is VALIDATION_ERROR, not CREATIVE_REJECTED: 3.1.1's enum reserves
        # CREATIVE_REJECTED for "Creative failed content policy review", so
        # _assignments.py routes a format the product does not accept to
        # AdCPValidationError, converged with the create and update paths (reversing
        # #1417). The old expectation predates that.
        with raises_adcp(AdCPValidationError):
            _sync_creatives(
                creatives=[_make_creative_dict(creative_id="c_display")],
                # list[Assignment], not the {creative_id: [package_id]} map: that map was an
                # internal-only spelling of the same relation and is not what
                # sync-creatives-request.json declares.
                assignments=[Assignment(creative_id="c_display", package_id="pkg_video")],
                validation_mode="strict",
                # Both are in sync-creatives-request.json /required.
                idempotency_key=f"sync-{uuid4().hex}",
                account=AccountReference(root={"account_id": ACCOUNT_ID}),
                headers=headers,
            )


# ---------------------------------------------------------------------------
# Test class: Media Buy Status Transition (BR-RULE-040)
# ---------------------------------------------------------------------------


class TestMediaBuyStatusTransition:
    """BR-RULE-040: Media buy status transitions on creative assignment.

    Unit stub: TestMediaBuyStatusTransition::test_draft_with_approved_at_transitions
    Spec: UNSPECIFIED (implementation-defined status machine).
    """

    TENANT_ID = "mb_status_tenant"
    PRINCIPAL_ID = "mb_status_principal"

    @pytest.fixture(autouse=True)
    def setup_tenant(self, integration_db, bound_factory_session):
        with get_db_session() as session:
            tenant = create_tenant_with_timestamps(
                tenant_id=self.TENANT_ID,
                name="MB Status Tenant",
                subdomain="mb-status-test",
                is_active=True,
                ad_server="mock",
                approval_mode="auto-approve",
            )
            session.add(tenant)
            session.add(
                CurrencyLimit(
                    tenant_id=self.TENANT_ID,
                    currency_code="USD",
                    min_package_budget=100.0,
                    max_daily_package_spend=10000.0,
                )
            )
            session.add(
                Principal.with_token(
                    plaintext_token_for(self.PRINCIPAL_ID),
                    tenant_id=self.TENANT_ID,
                    principal_id=self.PRINCIPAL_ID,
                    name="MB Status Advertiser",
                    platform_mappings={"mock": {"id": "mb_status_adv"}},
                )
            )
            # Product matching the creative format
            product = DBProduct(
                tenant_id=self.TENANT_ID,
                product_id="prod_display",
                name="Display Product",
                description="Supports display creatives",
                format_ids=[
                    {"agent_url": DEFAULT_AGENT_URL, "id": DEFAULT_FORMAT_ID},
                ],
                targeting_template={},
                delivery_type="non_guaranteed",
                properties=[{"publisher_domain": "test.com", "selection_type": "all"}],
            )
            session.add(product)

            # Draft media buy WITH approved_at set
            mb = MediaBuy(
                tenant_id=self.TENANT_ID,
                media_buy_id="mb_draft_approved",
                principal_id=self.PRINCIPAL_ID,
                order_name="Draft Approved Order",
                advertiser_name="MB Status Advertiser",
                status="draft",
                budget=5000.0,
                start_date=datetime.now(UTC).date(),
                end_date=(datetime.now(UTC) + timedelta(days=30)).date(),
                approved_at=datetime.now(UTC),
                raw_request={"packages": [{"package_id": "pkg_draft", "paused": False}]},
            )
            session.add(mb)
            session.commit()

            pkg = MediaPackage(
                media_buy_id="mb_draft_approved",
                package_id="pkg_draft",
                package_config={"package_id": "pkg_draft", "product_id": "prod_display"},
            )
            session.add(pkg)
            session.commit()

        # ``account`` is in sync-creatives-request.json /required, so every sync call in
        # this file needs one that the calling principal can reach. Seeded through the
        # factories (CLAUDE.md) rather than extending the hand-rolled block above.
        _seed_account_for(self.TENANT_ID, (self.PRINCIPAL_ID,))

    def test_draft_with_approved_at_transitions(self):
        """Draft media buy with approved_at transitions to pending_creatives on assignment.

        Covers: UC-006-MEDIA-BUY-STATUS-01
        Spec: UNSPECIFIED (implementation-defined status machine).
        Unit stub: TestMediaBuyStatusTransition::test_draft_with_approved_at_transitions
        """

        # Sync creative and assign to draft media buy's package
        _sync_creatives(
            creatives=[_make_creative_dict(creative_id="c_transition")],
            # list[Assignment], per sync-creatives-request.json.
            assignments=[Assignment(creative_id="c_transition", package_id="pkg_draft")],
            # Both are in sync-creatives-request.json /required.
            idempotency_key=f"sync-{uuid4().hex}",
            account=AccountReference(root={"account_id": ACCOUNT_ID}),
            headers=_headers(self.TENANT_ID, self.PRINCIPAL_ID),
        )

        # Verify media buy status changed
        with get_db_session() as session:
            stmt = select(MediaBuy).filter_by(media_buy_id="mb_draft_approved")
            mb = session.scalars(stmt).first()
            assert mb is not None
            assert mb.status == "pending_creatives"
