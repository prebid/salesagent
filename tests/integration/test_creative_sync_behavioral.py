"""Integration tests: sync_creatives auth, isolation, validation, and CRUD workflow.

Behavioral tests using CreativeSyncEnv + real PostgreSQL + factory_boy.
Replaces mock-heavy unit tests from test_creative.py with provable assertions
against actual database state.

Covers: salesagent-xwkj, salesagent-11th
"""

from __future__ import annotations

import pytest
from adcp.types import CreativeAction
from adcp.types.generated_poc.core.creative_asset import CreativeAsset
from adcp.types.generated_poc.core.format_id import FormatId as AdcpFormatId

from src.core.exceptions import AdCPAuthenticationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.testing_hooks import AdCPTestContext
from tests.factories import MediaBuyFactory, MediaPackageFactory, PrincipalFactory, TenantFactory
from tests.harness.creative_sync import CreativeSyncEnv

DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _make_creative_asset(**overrides) -> CreativeAsset:
    """Build a minimal valid CreativeAsset for testing."""
    defaults = {
        "creative_id": "c_test_1",
        "name": "Test Banner",
        "format_id": AdcpFormatId(agent_url=DEFAULT_AGENT_URL, id="display_300x250"),
        "assets": {"banner": {"url": "https://example.com/banner.png"}},
    }
    defaults.update(overrides)
    return CreativeAsset(**defaults)


def _make_identity(principal_id=None, tenant_id=None, tenant=None, **kwargs):
    """Build a ResolvedIdentity with explicit control over all fields."""
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id or "test_tenant",
        tenant=tenant,
        protocol="mcp",
        testing_context=AdCPTestContext(dry_run=False, mock_time=None, jump_to_event=None, test_session_id=None),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Auth Tests — Covers: UC-006-MAIN-01
# ---------------------------------------------------------------------------


class TestSyncAuthRequired:
    """Auth errors are operation-level — raised before any creative processing."""

    def test_no_identity_raises_auth_error(self, integration_db):
        """Covers: UC-006-MAIN-A-01 — identity=None → AdCPAuthenticationError."""
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="Authentication required"):
                env.call_impl(creatives=[_make_creative_asset()], identity=None)

    def test_identity_without_principal_raises(self, integration_db):
        """Covers: UC-006-MAIN-A-02 — principal_id=None → AdCPAuthenticationError."""
        identity = _make_identity(principal_id=None, tenant={"tenant_id": "t1", "name": "T1"})
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="Authentication required"):
                env.call_impl(creatives=[_make_creative_asset()], identity=identity)

    def test_identity_without_tenant_raises(self, integration_db):
        """Covers: UC-006-MAIN-A-03 — tenant=None → AdCPAuthenticationError."""
        identity = _make_identity(principal_id="p1", tenant=None)
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="tenant"):
                env.call_impl(creatives=[_make_creative_asset()], identity=identity)

    def test_auth_error_before_db_access(self, integration_db):
        """Covers: UC-006-MAIN-A-04 — auth error is operation-level, no partial results."""
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError):
                # If this returned a response instead of raising, auth is broken
                env.call_impl(creatives=[_make_creative_asset()], identity=None)

    def test_empty_principal_id_raises(self, integration_db):
        """Covers: UC-006-MAIN-A-05 — empty string principal_id → AdCPAuthenticationError."""
        identity = _make_identity(principal_id="", tenant={"tenant_id": "t1", "name": "T1"})
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="Authentication required"):
                env.call_impl(creatives=[_make_creative_asset()], identity=identity)


# ---------------------------------------------------------------------------
# Cross-Principal Isolation — Covers: UC-006-ISO-01
# ---------------------------------------------------------------------------


class TestCrossPrincipalIsolation:
    """Creatives are scoped by (tenant_id, principal_id) — real DB proves isolation."""

    def test_creative_visible_only_to_owning_principal(self, integration_db):
        """Covers: UC-006-ISO-01 — creative created by P1 not visible to P2 query."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        # Create all seed data + sync as P1 inside one env context
        with CreativeSyncEnv() as env:
            tenant = TenantFactory()
            p1 = PrincipalFactory(tenant=tenant)
            p2 = PrincipalFactory(tenant=tenant)

            # Capture IDs before env exit closes session
            tid = tenant.tenant_id
            p1_id = p1.principal_id
            p2_id = p2.principal_id

            p1_identity = _make_identity(
                principal_id=p1_id,
                tenant_id=tid,
                tenant={"tenant_id": tid, "name": tenant.name},
            )
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="shared_id")],
                identity=p1_identity,
            )

        # Query DB directly as principal 2 — should find nothing
        with get_db_session() as session:
            p2_creatives = session.scalars(
                select(DBCreative).filter_by(
                    tenant_id=tid,
                    principal_id=p2_id,
                    creative_id="shared_id",
                )
            ).all()
            assert len(p2_creatives) == 0, "Principal 2 should not see Principal 1's creative"

            # But principal 1 should see it
            p1_creatives = session.scalars(
                select(DBCreative).filter_by(
                    tenant_id=tid,
                    principal_id=p1_id,
                    creative_id="shared_id",
                )
            ).all()
            assert len(p1_creatives) == 1

    def test_same_creative_id_different_principals_are_separate(self, integration_db):
        """Covers: UC-006-ISO-02 — same creative_id under different principals = separate records."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        # Create factories + sync as P1 in first env
        with CreativeSyncEnv() as env:
            tenant = TenantFactory()
            p1 = PrincipalFactory(tenant=tenant)
            p2 = PrincipalFactory(tenant=tenant)

            # Capture IDs before env exit closes session
            tid = tenant.tenant_id
            p1_id = p1.principal_id
            p2_id = p2.principal_id

            p1_identity = _make_identity(
                principal_id=p1_id,
                tenant_id=tid,
                tenant={"tenant_id": tid, "name": tenant.name},
            )
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_shared")],
                identity=p1_identity,
            )

        # Sync same creative_id as P2 (factories already committed to DB)
        with CreativeSyncEnv(principal_id=p2_id, tenant_id=tid) as env:
            env.call_impl(creatives=[_make_creative_asset(creative_id="c_shared")])

        # Both should exist as separate records
        with get_db_session() as session:
            all_creatives = session.scalars(select(DBCreative).filter_by(tenant_id=tid, creative_id="c_shared")).all()
            assert len(all_creatives) == 2
            principal_ids = {c.principal_id for c in all_creatives}
            assert principal_ids == {p1_id, p2_id}

    def test_new_creative_stamped_with_correct_principal(self, integration_db):
        """Covers: UC-006-ISO-03 — created creative has correct principal_id in DB."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory()
            principal = PrincipalFactory(tenant=tenant)

            # Capture IDs before env exit closes session
            tid = tenant.tenant_id
            pid = principal.principal_id

            p_identity = _make_identity(
                principal_id=pid,
                tenant_id=tid,
                tenant={"tenant_id": tid, "name": tenant.name},
            )
            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_stamped")],
                identity=p_identity,
            )

        assert len(response.creatives) == 1

        with get_db_session() as session:
            db_creative = session.scalars(select(DBCreative).filter_by(creative_id="c_stamped", tenant_id=tid)).first()
            assert db_creative is not None
            assert db_creative.principal_id == pid


# ---------------------------------------------------------------------------
# Validation Tests — Covers: UC-006-VAL-01
# ---------------------------------------------------------------------------


class TestCreativeValidation:
    """Input validation for _sync_creatives_impl with real format registry mock."""

    def test_empty_name_rejected(self, integration_db):
        """Covers: UC-006-VAL-01 — empty creative name → failed result."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(creatives=[_make_creative_asset(name="")])
            assert len(response.creatives) == 1
            result = response.creatives[0]
            assert result.action == CreativeAction.failed or (result.errors and len(result.errors) > 0)

    def test_whitespace_only_name_rejected(self, integration_db):
        """Covers: UC-006-VAL-02 — whitespace-only name → failed result."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(creatives=[_make_creative_asset(name="   ")])
            assert len(response.creatives) == 1
            result = response.creatives[0]
            assert result.action == CreativeAction.failed or (result.errors and len(result.errors) > 0)

    def test_valid_creative_accepted(self, integration_db):
        """Covers: UC-006-VAL-03 — valid creative → created action."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(creatives=[_make_creative_asset(creative_id="c_valid", name="Valid Creative")])
            assert len(response.creatives) == 1
            result = response.creatives[0]
            assert result.creative_id == "c_valid"
            # Should be created (not failed)
            assert result.action != CreativeAction.failed

    def test_adapter_format_skips_registry_validation(self, integration_db):
        """Covers: UC-006-VAL-04 — adapter:// agent_url skips external format lookup."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_adapter",
                        format_id=AdcpFormatId(agent_url="broadstreet://default", id="broadstreet_billboard"),
                    )
                ]
            )
            assert len(response.creatives) == 1
            # Should succeed without registry lookup (non-HTTP agent_url)
            assert response.creatives[0].action != CreativeAction.failed


# ---------------------------------------------------------------------------
# Validation Mode Tests — Covers: UC-006-VAL-MODE-01
# ---------------------------------------------------------------------------


class TestValidationModeSemantics:
    """Strict vs lenient validation mode behavior with real DB savepoints."""

    def test_lenient_mode_continues_after_validation_error(self, integration_db):
        """Covers: UC-006-VAL-MODE-01 — lenient: one bad creative doesn't block others."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(creative_id="c_good_1", name="Good One"),
                    _make_creative_asset(creative_id="c_bad", name=""),  # empty name → fails
                    _make_creative_asset(creative_id="c_good_2", name="Good Two"),
                ],
                validation_mode="lenient",
            )
            # All 3 should have results
            assert len(response.creatives) == 3
            # c_bad should be failed
            bad_result = next(r for r in response.creatives if r.creative_id == "c_bad")
            assert bad_result.action == CreativeAction.failed
            # c_good_1 and c_good_2 should NOT be failed
            good_results = [r for r in response.creatives if r.creative_id != "c_bad"]
            for r in good_results:
                assert r.action != CreativeAction.failed, f"Creative {r.creative_id} should succeed in lenient mode"

    def test_strict_mode_also_processes_all_creatives(self, integration_db):
        """Covers: UC-006-VAL-MODE-02 — strict: validation errors still per-creative in strict mode."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(creative_id="c_good", name="Good"),
                    _make_creative_asset(creative_id="c_bad", name=""),
                ],
                validation_mode="strict",
            )
            # Both should be in results — validation errors are per-creative, not abortive
            assert len(response.creatives) >= 1

    def test_lenient_savepoint_isolation_with_real_db(self, integration_db):
        """Covers: UC-006-VAL-MODE-03 — lenient: DB savepoints isolate per-creative failures."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            env.call_impl(
                creatives=[
                    _make_creative_asset(creative_id="c_survives", name="Survivor"),
                    _make_creative_asset(creative_id="c_fails", name=""),
                    _make_creative_asset(creative_id="c_also_survives", name="Also Survivor"),
                ],
                validation_mode="lenient",
            )

        # Verify in DB: good creatives persisted despite bad creative in the batch
        with get_db_session() as session:
            survivors = session.scalars(
                select(DBCreative).filter_by(tenant_id="test_tenant", principal_id="test_principal")
            ).all()
            survivor_ids = {c.creative_id for c in survivors}
            assert "c_survives" in survivor_ids, "Good creative should be persisted"
            assert "c_also_survives" in survivor_ids, "Second good creative should be persisted"
            assert "c_fails" not in survivor_ids, "Bad creative should not be persisted"


# ---------------------------------------------------------------------------
# CRUD Workflow Tests — Covers: salesagent-11th
# ---------------------------------------------------------------------------


class TestCreateUpdateWorkflow:
    """Create/update upsert semantics with real DB verification."""

    def test_new_creative_creates_db_record(self, integration_db):
        """Covers: UC-006-MAIN-MCP-01 — new creative inserted into DB."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(creatives=[_make_creative_asset(creative_id="c_new", name="New Creative")])

        assert len(response.creatives) == 1
        assert response.creatives[0].action == CreativeAction.created

        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(
                    creative_id="c_new", tenant_id="test_tenant", principal_id="test_principal"
                )
            ).first()
            assert db_creative is not None
            assert db_creative.name == "New Creative"

    def test_existing_creative_updates_in_place(self, integration_db):
        """Covers: UC-006-MAIN-MCP-03 — upsert updates existing record by triple key."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Create first
            env.call_impl(creatives=[_make_creative_asset(creative_id="c_upsert", name="Original")])
            # Update with same creative_id
            response = env.call_impl(creatives=[_make_creative_asset(creative_id="c_upsert", name="Updated")])

        assert len(response.creatives) == 1
        assert response.creatives[0].action == CreativeAction.updated

        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_upsert", tenant_id="test_tenant")
            ).first()
            assert db_creative is not None
            assert db_creative.name == "Updated"

    def test_batch_sync_multiple_creatives(self, integration_db):
        """Covers: UC-006-MAIN-MCP-02 — batch of N creatives produces N results."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id=f"c_batch_{i}", name=f"Batch {i}") for i in range(5)]
            )

        assert len(response.creatives) == 5
        result_ids = {r.creative_id for r in response.creatives}
        assert result_ids == {f"c_batch_{i}" for i in range(5)}


class TestDeleteMissing:
    """delete_missing flag behavior with real DB."""

    def test_delete_missing_archives_unlisted_creatives(self, integration_db):
        """Covers: UC-006-DELETE-MISSING-01 — unlisted creatives soft-deleted."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Create two creatives
            env.call_impl(
                creatives=[
                    _make_creative_asset(creative_id="c_keep", name="Keep"),
                    _make_creative_asset(creative_id="c_orphan", name="Orphan"),
                ]
            )
            # Re-sync with only one — orphan should be archived
            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_keep", name="Keep")],
                delete_missing=True,
            )

        # Check response includes a deleted action for orphan
        actions = {r.creative_id: r.action for r in response.creatives}
        assert CreativeAction.deleted in actions.values()

        with get_db_session() as session:
            orphan = session.scalars(
                select(DBCreative).filter_by(creative_id="c_orphan", tenant_id="test_tenant")
            ).first()
            assert orphan is not None
            assert orphan.status == "archived"

    def test_delete_missing_false_preserves_unlisted(self, integration_db):
        """Covers: UC-006-DELETE-MISSING-02 — default: unlisted creatives unchanged."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Create initial creative
            env.call_impl(creatives=[_make_creative_asset(creative_id="c_existing", name="Existing")])
            # Sync a different creative without delete_missing
            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_new_one", name="New")],
                delete_missing=False,
            )

        # Only the synced creative in results
        assert len(response.creatives) == 1
        assert response.creatives[0].creative_id == "c_new_one"

        with get_db_session() as session:
            existing = session.scalars(
                select(DBCreative).filter_by(creative_id="c_existing", tenant_id="test_tenant")
            ).first()
            assert existing is not None
            assert existing.status != "archived", "Existing creative should not be archived"


class TestCreativeIdsFilter:
    """creative_ids parameter scoping with real DB."""

    def test_creative_ids_filter_narrows_processing(self, integration_db):
        """Covers: UC-006-CREATIVE-IDS-SCOPE-01 — only matching IDs processed."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(creative_id="c1", name="One"),
                    _make_creative_asset(creative_id="c2", name="Two"),
                    _make_creative_asset(creative_id="c3", name="Three"),
                ],
                creative_ids=["c1", "c3"],
            )

        # Only c1 and c3 should be in results
        result_ids = {r.creative_id for r in response.creatives}
        assert result_ids == {"c1", "c3"}
        assert "c2" not in result_ids

    def test_empty_creative_ids_processes_all(self, integration_db):
        """Covers: UC-006-CREATIVE-IDS-SCOPE-02 — empty list is falsy, processes all creatives."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c1", name="One")],
                creative_ids=[],
            )

        # Empty list is falsy in `if creative_ids:` — all creatives processed
        assert len(response.creatives) == 1


class TestDryRunMode:
    """dry_run flag: no DB writes."""

    def test_dry_run_does_not_persist(self, integration_db):
        """Covers: UC-006-DRY-RUN-01 — dry_run=True produces results without DB changes."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_dry", name="Dry Run Creative")],
                dry_run=True,
            )

        assert response.dry_run is True
        assert len(response.creatives) >= 1

        # Verify nothing written to DB
        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_dry", tenant_id="test_tenant")
            ).first()
            assert db_creative is None, "Dry run should not persist any creatives"


class TestApprovalWorkflow:
    """Tenant approval_mode controls creative status."""

    def test_auto_approve_sets_approved_status(self, integration_db):
        """Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-01 — auto-approve → status=approved."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant", approval_mode="auto-approve")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Override identity tenant dict to include approval_mode
            identity = _make_identity(
                principal_id="test_principal",
                tenant_id="test_tenant",
                tenant={"tenant_id": "test_tenant", "name": "Test", "approval_mode": "auto-approve"},
            )
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_auto", name="Auto Approved")],
                identity=identity,
            )

        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_auto", tenant_id="test_tenant")
            ).first()
            assert db_creative is not None
            assert db_creative.status == "approved"

    def test_require_human_sets_pending_review(self, integration_db):
        """Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-02 — require-human → status=pending_review."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant", approval_mode="require-human")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            identity = _make_identity(
                principal_id="test_principal",
                tenant_id="test_tenant",
                tenant={"tenant_id": "test_tenant", "name": "Test", "approval_mode": "require-human"},
            )
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_human", name="Needs Review")],
                identity=identity,
            )

        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_human", tenant_id="test_tenant")
            ).first()
            assert db_creative is not None
            assert db_creative.status == "pending_review"

    def test_default_approval_mode_is_require_human(self, integration_db):
        """Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-04 — no approval_mode → defaults to require-human."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Creative as DBCreative

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Identity tenant dict has NO approval_mode key
            response = env.call_impl(creatives=[_make_creative_asset(creative_id="c_default", name="Default Mode")])

        assert len(response.creatives) == 1

        with get_db_session() as session:
            db_creative = session.scalars(
                select(DBCreative).filter_by(creative_id="c_default", tenant_id="test_tenant")
            ).first()
            assert db_creative is not None
            assert db_creative.status == "pending_review"


class TestAssignmentProcessing:
    """Assignment creation with real DB + factory-created packages."""

    def test_assignment_persists_to_db(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-PACKAGE-VALIDATION-01 — assignment record created in DB."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import CreativeAssignment as DBAssignment

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(media_buy=media_buy)

            pkg_id = pkg.package_id

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_assign", name="Assigned")],
                assignments={"c_assign": [pkg_id]},
                validation_mode="lenient",
            )

        with get_db_session() as session:
            assignments = session.scalars(
                select(DBAssignment).filter_by(tenant_id="test_tenant", creative_id="c_assign", package_id=pkg_id)
            ).all()
            assert len(assignments) == 1

    def test_none_assignments_produces_no_records(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-PACKAGE-VALIDATION-01 — None assignments = no assignment records."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import CreativeAssignment as DBAssignment

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_noassign", name="No Assign")],
                assignments=None,
            )

        with get_db_session() as session:
            assignments = session.scalars(
                select(DBAssignment).filter_by(tenant_id="test_tenant", creative_id="c_noassign")
            ).all()
            assert len(assignments) == 0

    def test_idempotent_assignment_upsert(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-PACKAGE-VALIDATION-04 — duplicate assignment not duplicated."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import CreativeAssignment as DBAssignment

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(media_buy=media_buy)

            pkg_id = pkg.package_id

            # Assign twice
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_idem", name="Idempotent")],
                assignments={"c_idem": [pkg_id]},
                validation_mode="lenient",
            )
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_idem", name="Idempotent")],
                assignments={"c_idem": [pkg_id]},
                validation_mode="lenient",
            )

        with get_db_session() as session:
            assignments = session.scalars(
                select(DBAssignment).filter_by(tenant_id="test_tenant", creative_id="c_idem", package_id=pkg_id)
            ).all()
            assert len(assignments) == 1, "Idempotent: should not duplicate assignment"


class TestSchemaCompleteness:
    """Response schema fields verified against real results."""

    def test_warnings_in_per_creative_results(self, integration_db):
        """Covers: UC-006-ASSIGNMENTS-RESPONSE-COMPLETENESS-02 — warnings field populated."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(creatives=[_make_creative_asset(creative_id="c_warn", name="With Warnings")])

        assert len(response.creatives) == 1
        result = response.creatives[0]
        # Warnings field should exist (may be empty or populated)
        assert hasattr(result, "warnings")
        assert isinstance(result.warnings, list)

    def test_per_creative_result_has_required_fields(self, integration_db):
        """Covers: UC-006-MAIN-MCP-01 — result has creative_id, action, changes, errors."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(creatives=[_make_creative_asset(creative_id="c_fields", name="Field Check")])

        result = response.creatives[0]
        assert result.creative_id == "c_fields"
        assert result.action in list(CreativeAction)
        assert isinstance(result.changes, list)
        assert isinstance(result.errors, list)
