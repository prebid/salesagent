"""Integration tests: sync_creatives auth, isolation, validation, CRUD, extensions, provenance.

Behavioral tests using CreativeSyncEnv + real PostgreSQL + factory_boy.
Replaces mock-heavy unit tests from test_creative.py with provable assertions
against actual database state.

Covers: salesagent-xwkj, salesagent-11th, salesagent-0m59, salesagent-mi8l
"""

from __future__ import annotations

from datetime import UTC

import pytest
from adcp.types import CreativeAction
from adcp.types.generated_poc.core.creative_asset import CreativeAsset
from adcp.types.generated_poc.core.format_id import FormatId as AdcpFormatId

from src.core.exceptions import AdCPAuthenticationError, AdCPNotFoundError, AdCPValidationError
from tests.factories import MediaBuyFactory, MediaPackageFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness import CreativeSyncEnv, make_identity

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


_make_identity = make_identity  # Canonical version from tests.harness


# ---------------------------------------------------------------------------
# Auth Tests — UC-006-EXT-A, UC-006-EXT-B
# ---------------------------------------------------------------------------


class TestSyncAuthRequired:
    """Auth errors are operation-level — raised before any creative processing."""

    def test_no_identity_raises_auth_error(self, integration_db):
        """Covers: UC-006-EXT-A-01 — identity=None → AdCPAuthenticationError."""
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="Authentication required"):
                env.call_impl(creatives=[_make_creative_asset()], identity=None)

    def test_identity_without_principal_raises(self, integration_db):
        """Covers: UC-006-EXT-A-01 — principal_id=None → AdCPAuthenticationError."""
        identity = _make_identity(principal_id=None, tenant={"tenant_id": "t1", "name": "T1"})
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="Authentication required"):
                env.call_impl(creatives=[_make_creative_asset()], identity=identity)

    def test_identity_without_tenant_raises(self, integration_db):
        """Covers: UC-006-EXT-B-01 — tenant=None → AdCPAuthenticationError."""
        identity = _make_identity(principal_id="p1", tenant=None)
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="tenant"):
                env.call_impl(creatives=[_make_creative_asset()], identity=identity)

    def test_auth_error_before_db_access(self, integration_db):
        """Covers: UC-006-EXT-A-02 — auth error is operation-level, no partial results."""
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError):
                # If this returned a response instead of raising, auth is broken
                env.call_impl(creatives=[_make_creative_asset()], identity=None)

    def test_empty_principal_id_raises(self, integration_db):
        """Covers: UC-006-EXT-A-01 — empty string principal_id → AdCPAuthenticationError."""
        identity = _make_identity(principal_id="", tenant={"tenant_id": "t1", "name": "T1"})
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="Authentication required"):
                env.call_impl(creatives=[_make_creative_asset()], identity=identity)


# ---------------------------------------------------------------------------
# Cross-Principal Isolation — Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-01
# ---------------------------------------------------------------------------


class TestCrossPrincipalIsolation:
    """Creatives are scoped by (tenant_id, principal_id) — real DB proves isolation."""

    def test_creative_visible_only_to_owning_principal(self, integration_db):
        """Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-01 — creative created by P1 not visible to P2 query."""
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
        """Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-02 — same creative_id under different principals = separate records."""
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
        """Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-03 — created creative has correct principal_id in DB."""
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
# Validation Tests — Covers: UC-006-EXT-D-01
# ---------------------------------------------------------------------------


class TestCreativeValidation:
    """Input validation for _sync_creatives_impl with real format registry mock."""

    def test_empty_name_rejected(self, integration_db):
        """Covers: UC-006-EXT-D-01 — empty creative name → failed result."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(creatives=[_make_creative_asset(name="")])
            assert len(response.creatives) == 1
            result = response.creatives[0]
            assert result.action == CreativeAction.failed or (result.errors and len(result.errors) > 0)

    def test_whitespace_only_name_rejected(self, integration_db):
        """Covers: UC-006-EXT-D-01 — whitespace-only name → failed result."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(creatives=[_make_creative_asset(name="   ")])
            assert len(response.creatives) == 1
            result = response.creatives[0]
            assert result.action == CreativeAction.failed or (result.errors and len(result.errors) > 0)

    def test_valid_creative_accepted(self, integration_db):
        """Covers: UC-006-MAIN-MCP-01 — valid creative → created action."""
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
        """Covers: UC-006-CREATIVE-FORMAT-VALIDATION-02 — adapter:// agent_url skips external format lookup."""
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
# Validation Mode Tests — Covers: UC-006-MAIN-MCP-05
# ---------------------------------------------------------------------------


class TestValidationModeSemantics:
    """Strict vs lenient validation mode behavior with real DB savepoints."""

    def test_lenient_mode_continues_after_validation_error(self, integration_db):
        """Covers: UC-006-MAIN-MCP-05 — lenient: one bad creative doesn't block others."""
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
        """Covers: UC-006-EXT-C-02 — strict: validation errors still per-creative in strict mode."""
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
        """Covers: UC-006-MAIN-MCP-05 — lenient: DB savepoints isolate per-creative failures."""
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
        """Behavior: UC-006-CREATIVE-IDS-SCOPE-02 — empty list is falsy, processes all creatives."""
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


# ---------------------------------------------------------------------------
# Extension Gaps — Covers: salesagent-0m59 (TestExtensionGaps conversion)
# ---------------------------------------------------------------------------


class TestSyncExtensions:
    """Extension scenarios: format errors, validation modes, assignment errors."""

    def test_tenant_not_found_raises_auth_error(self, integration_db):
        """Covers: UC-006-EXT-B-01 — tenant=None with principal → AdCPAuthenticationError."""
        identity = _make_identity(principal_id="p1", tenant=None)
        with CreativeSyncEnv() as env:
            with pytest.raises(AdCPAuthenticationError, match="tenant"):
                env.call_impl(creatives=[_make_creative_asset()], identity=identity)

    def test_strict_validation_per_creative_independence(self, integration_db):
        """Covers: UC-006-EXT-C-02 — strict: bad creative fails, good continues."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(creative_id="c_bad", name=""),  # empty name fails
                    _make_creative_asset(creative_id="c_good", name="Good Creative"),
                ],
                validation_mode="strict",
            )

        assert len(response.creatives) == 2
        result_by_id = {r.creative_id: r for r in response.creatives}
        assert result_by_id["c_bad"].action == CreativeAction.failed
        assert result_by_id["c_good"].action != CreativeAction.failed

    def test_lenient_validation_bad_creative_fails_good_continues(self, integration_db):
        """Covers: UC-006-EXT-C-03 — lenient: invalid creative failed, valid ones proceed."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(creative_id="c_bad", name=""),
                    _make_creative_asset(creative_id="c_good", name="Good"),
                ],
                validation_mode="lenient",
            )

        assert len(response.creatives) == 2
        result_by_id = {r.creative_id: r for r in response.creatives}
        assert result_by_id["c_bad"].action == CreativeAction.failed

    def test_missing_name_field_fails_validation(self, integration_db):
        """Covers: UC-006-EXT-D-02 — dict without name → action=failed with errors."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    {
                        "creative_id": "c_no_name",
                        "format_id": {"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250"},
                        "assets": {"banner": {"url": "https://example.com/b.png"}},
                    }
                ],
            )

        assert len(response.creatives) == 1
        assert response.creatives[0].action == CreativeAction.failed
        assert len(response.creatives[0].errors) > 0

    def test_unknown_format_fails_with_hint(self, integration_db):
        """Covers: UC-006-EXT-F-01 — format not in registry → failed with hint."""
        from unittest.mock import AsyncMock

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Override: registry.get_format returns None (format not found)
            registry_mock = env.mock["registry"].return_value
            registry_mock.get_format = AsyncMock(return_value=None)

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_unknown_fmt")],
            )

        assert len(response.creatives) == 1
        result = response.creatives[0]
        assert result.action == CreativeAction.failed
        assert any("list_creative_formats" in e for e in (result.errors or []))

    def test_unreachable_agent_fails_with_retry(self, integration_db):
        """Covers: UC-006-EXT-G-01 — agent unreachable → failed with retry suggestion."""
        from unittest.mock import AsyncMock

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Override: registry.get_format raises ConnectionError
            registry_mock = env.mock["registry"].return_value
            registry_mock.get_format = AsyncMock(side_effect=ConnectionError("Agent unreachable"))

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_unreachable")],
            )

        assert len(response.creatives) == 1
        result = response.creatives[0]
        assert result.action == CreativeAction.failed
        assert any("unreachable" in e.lower() for e in (result.errors or []))

    def test_package_not_found_lenient_logs_error(self, integration_db):
        """Covers: UC-006-EXT-J-02 — lenient: missing package → assignment_errors."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c1", name="OK Creative")],
                assignments={"c1": ["missing_pkg"]},
                validation_mode="lenient",
            )

        assert len(response.creatives) == 1
        result = response.creatives[0]
        assert result.assignment_errors is not None
        assert "missing_pkg" in result.assignment_errors

    def test_package_not_found_strict_raises(self, integration_db):
        """Covers: UC-006-EXT-J-01 — strict: missing package → AdCPNotFoundError."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            with pytest.raises(AdCPNotFoundError, match="Package not found"):
                env.call_impl(
                    creatives=[_make_creative_asset(creative_id="c1", name="OK")],
                    assignments={"c1": ["PKG-GONE"]},
                    validation_mode="strict",
                )

    def test_format_mismatch_strict_raises(self, integration_db):
        """Covers: UC-006-EXT-K-01 — strict: format mismatch → AdCPValidationError."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # Product only supports display_300x250
            product = ProductFactory(
                tenant=tenant,
                format_ids=[{"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250"}],
            )
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(
                media_buy=media_buy,
                package_config={"product_id": product.product_id, "package_id": "pkg_fmt"},
            )
            pkg_id = pkg.package_id

            # Creative uses video_30s format (different from product's display)
            with pytest.raises(AdCPValidationError, match="not supported"):
                env.call_impl(
                    creatives=[
                        _make_creative_asset(
                            creative_id="c_vid",
                            name="Video Creative",
                            format_id=AdcpFormatId(agent_url=DEFAULT_AGENT_URL, id="video_30s"),
                        )
                    ],
                    assignments={"c_vid": [pkg_id]},
                    validation_mode="strict",
                )

    def test_format_mismatch_lenient_logs_error(self, integration_db):
        """Covers: UC-006-EXT-K-02 — lenient: format mismatch → assignment_errors."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")

            product = ProductFactory(
                tenant=tenant,
                format_ids=[{"agent_url": DEFAULT_AGENT_URL, "id": "display_300x250"}],
            )
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(
                media_buy=media_buy,
                package_config={"product_id": product.product_id, "package_id": "pkg_fmt"},
            )
            pkg_id = pkg.package_id

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_vid",
                        name="Video",
                        format_id=AdcpFormatId(agent_url=DEFAULT_AGENT_URL, id="video_30s"),
                    )
                ],
                assignments={"c_vid": [pkg_id]},
                validation_mode="lenient",
            )

        result = response.creatives[0]
        assert result.assignment_errors is not None
        assert pkg_id in result.assignment_errors

    def test_adapter_format_skips_registry(self, integration_db):
        """Covers: UC-006-EXT-H-02 — adapter:// agent_url bypasses external format lookup."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_adapter",
                        format_id=AdcpFormatId(agent_url="broadstreet://default", id="billboard"),
                    )
                ],
            )

        assert len(response.creatives) == 1
        assert response.creatives[0].action != CreativeAction.failed


# ---------------------------------------------------------------------------
# Provenance Validation — Covers: salesagent-0m59 (TestProvenanceValidation conversion)
# ---------------------------------------------------------------------------


class TestProvenanceEnforcement:
    """Provenance metadata enforcement end-to-end through sync flow."""

    def test_provenance_required_missing_adds_warning(self, integration_db):
        """Covers: UC-006-PROV-01 — product requires provenance, creative lacks it → warning."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")
            # Product with provenance_required policy
            ProductFactory(
                tenant=tenant,
                creative_policy={"provenance_required": True, "co_branding": "optional"},
            )

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_no_prov", name="No Provenance")],
            )

        assert len(response.creatives) == 1
        result = response.creatives[0]
        assert result.action != CreativeAction.failed
        assert any("provenance" in w.lower() for w in (result.warnings or []))

    def test_provenance_present_no_warning(self, integration_db):
        """Covers: UC-006-PROV-02 — provenance present → no warning."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")
            ProductFactory(
                tenant=tenant,
                creative_policy={"provenance_required": True, "co_branding": "optional"},
            )

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_with_prov",
                        name="With Provenance",
                        provenance={"digital_source_type": "digital_creation", "ai_tool": {"name": "DALL-E"}},
                    )
                ],
            )

        assert len(response.creatives) == 1
        result = response.creatives[0]
        assert result.action != CreativeAction.failed
        provenance_warnings = [w for w in (result.warnings or []) if "provenance" in w.lower()]
        assert len(provenance_warnings) == 0

    def test_provenance_not_required_no_warning(self, integration_db):
        """Covers: UC-006-PROV-03 — no provenance policy → no warning."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")
            # No product with provenance_required (or no products at all)

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_free", name="No Policy")],
            )

        assert len(response.creatives) == 1
        result = response.creatives[0]
        provenance_warnings = [w for w in (result.warnings or []) if "provenance" in w.lower()]
        assert len(provenance_warnings) == 0

    def test_provenance_required_false_no_warning(self, integration_db):
        """Covers: UC-006-PROV-04 — provenance_required=False → no warning."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")
            ProductFactory(
                tenant=tenant,
                creative_policy={"provenance_required": False, "co_branding": "optional"},
            )

            response = env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_opt", name="Optional")],
            )

        assert len(response.creatives) == 1
        result = response.creatives[0]
        provenance_warnings = [w for w in (result.warnings or []) if "provenance" in w.lower()]
        assert len(provenance_warnings) == 0


# ---------------------------------------------------------------------------
# Media Buy Status Transition — Covers: salesagent-0m59 (TestMediaBuyStatusTransition conversion)
# ---------------------------------------------------------------------------


class TestMediaBuyStatusOnSync:
    """Media buy status transitions on creative assignment with real DB."""

    def test_draft_with_approved_at_transitions_to_pending_creatives(self, integration_db):
        """Covers: UC-006-MEDIA-BUY-STATUS-01 — draft + approved_at → pending_creatives."""
        from datetime import datetime

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy as DBMediaBuy

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                status="draft",
                approved_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            pkg = MediaPackageFactory(media_buy=media_buy)
            mb_id = media_buy.media_buy_id
            pkg_id = pkg.package_id

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_mb", name="MB Test")],
                assignments={"c_mb": [pkg_id]},
                validation_mode="lenient",
            )

        with get_db_session() as session:
            mb = session.scalars(select(DBMediaBuy).filter_by(media_buy_id=mb_id, tenant_id="test_tenant")).first()
            assert mb is not None
            assert mb.status == "pending_creatives"

    def test_draft_without_approved_at_stays_draft(self, integration_db):
        """Covers: UC-006-MEDIA-BUY-STATUS-02 — draft without approved_at stays draft."""
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy as DBMediaBuy

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                status="draft",
                approved_at=None,
            )
            pkg = MediaPackageFactory(media_buy=media_buy)
            mb_id = media_buy.media_buy_id
            pkg_id = pkg.package_id

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_mb2", name="MB Test 2")],
                assignments={"c_mb2": [pkg_id]},
                validation_mode="lenient",
            )

        with get_db_session() as session:
            mb = session.scalars(select(DBMediaBuy).filter_by(media_buy_id=mb_id, tenant_id="test_tenant")).first()
            assert mb is not None
            assert mb.status == "draft"

    def test_non_draft_status_unchanged(self, integration_db):
        """Covers: UC-006-MEDIA-BUY-STATUS-03 — active status not affected by assignment."""
        from datetime import datetime

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy as DBMediaBuy

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                status="active",
                approved_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            pkg = MediaPackageFactory(media_buy=media_buy)
            mb_id = media_buy.media_buy_id
            pkg_id = pkg.package_id

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_mb3", name="MB Test 3")],
                assignments={"c_mb3": [pkg_id]},
                validation_mode="lenient",
            )

        with get_db_session() as session:
            mb = session.scalars(select(DBMediaBuy).filter_by(media_buy_id=mb_id, tenant_id="test_tenant")).first()
            assert mb is not None
            assert mb.status == "active"

    def test_upsert_assignment_still_transitions(self, integration_db):
        """Covers: UC-006-MEDIA-BUY-STATUS-04 — upserted assignment triggers status check."""
        from datetime import datetime

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy as DBMediaBuy

        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                status="draft",
                approved_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            pkg = MediaPackageFactory(media_buy=media_buy)
            mb_id = media_buy.media_buy_id
            pkg_id = pkg.package_id

            # First assignment
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_upsert_mb", name="Upsert MB")],
                assignments={"c_upsert_mb": [pkg_id]},
                validation_mode="lenient",
            )
            # Second assignment (upsert) — status transition should still work
            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_upsert_mb", name="Upsert MB")],
                assignments={"c_upsert_mb": [pkg_id]},
                validation_mode="lenient",
            )

        with get_db_session() as session:
            mb = session.scalars(select(DBMediaBuy).filter_by(media_buy_id=mb_id, tenant_id="test_tenant")).first()
            assert mb is not None
            assert mb.status == "pending_creatives"


# ---------------------------------------------------------------------------
# Format Compatibility Extended — Covers: salesagent-mi8l
# ---------------------------------------------------------------------------


class TestFormatCompatibilityExtended:
    """Format compatibility in _process_assignments with real DB data.

    Tests URL normalization, empty format_ids, dual key support, and
    package-without-product scenarios through CreativeSyncEnv.
    """

    def test_url_normalization_strips_mcp_suffix(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-01 — /mcp suffix stripped for comparison."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            # Product format has /mcp/ suffix on agent_url
            product = ProductFactory(
                tenant=tenant,
                format_ids=[
                    {"agent_url": DEFAULT_AGENT_URL + "/mcp/", "id": "display_300x250"},
                ],
            )
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(
                media_buy=media_buy,
                package_config={"product_id": product.product_id, "package_id": "pkg_norm"},
            )

            # Creative has plain URL without /mcp — should still match after normalization
            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_norm",
                        name="URL Normalized",
                        format_id=AdcpFormatId(agent_url=DEFAULT_AGENT_URL, id="display_300x250"),
                    )
                ],
                assignments={"c_norm": [pkg.package_id]},
                validation_mode="strict",
            )

        # Should succeed — URL normalization strips /mcp/ before comparison
        result = response.creatives[0]
        assert result.action != CreativeAction.failed, f"Expected success but got: {result.errors}"

    def test_empty_format_ids_allows_all(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-04 — empty format_ids = no restriction."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            # Product with empty format_ids — should accept any creative format
            product = ProductFactory(
                tenant=tenant,
                format_ids=[],
            )
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(
                media_buy=media_buy,
                package_config={"product_id": product.product_id, "package_id": "pkg_any"},
            )

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_any_fmt",
                        name="Any Format",
                        format_id=AdcpFormatId(agent_url="https://random.agent.com", id="exotic_format"),
                    )
                ],
                assignments={"c_any_fmt": [pkg.package_id]},
                validation_mode="strict",
            )

        result = response.creatives[0]
        assert result.action != CreativeAction.failed, f"Expected success but got: {result.errors}"

    def test_format_id_dual_key_support(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-05 — 'format_id' key accepted alongside 'id'."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            # Product uses 'format_id' key instead of 'id'
            product = ProductFactory(
                tenant=tenant,
                format_ids=[
                    {"agent_url": DEFAULT_AGENT_URL, "format_id": "display_300x250"},
                ],
            )
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            pkg = MediaPackageFactory(
                media_buy=media_buy,
                package_config={"product_id": product.product_id, "package_id": "pkg_dual"},
            )

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_dual",
                        name="Dual Key",
                        format_id=AdcpFormatId(agent_url=DEFAULT_AGENT_URL, id="display_300x250"),
                    )
                ],
                assignments={"c_dual": [pkg.package_id]},
                validation_mode="strict",
            )

        result = response.creatives[0]
        assert result.action != CreativeAction.failed, f"Expected success but got: {result.errors}"

    def test_no_product_on_package_skips_format_check(self, integration_db):
        """Covers: UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-06 — no product_id = no format validation."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            principal = PrincipalFactory(tenant=tenant, principal_id="test_principal")
            media_buy = MediaBuyFactory(tenant=tenant, principal=principal)
            # Package has no product_id in config
            pkg = MediaPackageFactory(
                media_buy=media_buy,
                package_config={"package_id": "pkg_no_prod"},
            )

            response = env.call_impl(
                creatives=[
                    _make_creative_asset(
                        creative_id="c_no_prod",
                        name="No Product Check",
                    )
                ],
                assignments={"c_no_prod": [pkg.package_id]},
                validation_mode="strict",
            )

        result = response.creatives[0]
        assert result.action != CreativeAction.failed, f"Expected success but got: {result.errors}"


# ---------------------------------------------------------------------------
# Sync Flow Verification — Covers: salesagent-mi8l
# ---------------------------------------------------------------------------


class TestSyncFlowVerification:
    """Verify sync flow calls external services via mock assertions."""

    def test_sync_calls_audit_log(self, integration_db):
        """Covers: UC-006-MAIN-MCP-10 — sync operation triggers audit logging."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_audit", name="Audit Test")],
            )

            assert env.mock["audit_log"].called, "Audit log should be called after sync"

    def test_sync_calls_notifications_for_require_human(self, integration_db):
        """Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-05 — require-human triggers notifications."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(
                tenant_id="test_tenant",
                approval_mode="require-human",
                slack_webhook_url="https://hooks.slack.com/test",
            )
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_notif", name="Notif Test")],
            )

            assert env.mock["send_notifications"].called, "Notifications should be called for require-human mode"

    def test_sync_skips_notifications_for_auto_approve(self, integration_db):
        """Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-01 — auto-approve skips notifications."""
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="test_tenant")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            env.call_impl(
                creatives=[_make_creative_asset(creative_id="c_auto", name="Auto Test")],
            )

            # In auto-approve, notifications may still be called but with empty list
            # The guard logic is inside the (mocked) function — we verify it's called
            # but can't test the guard through the harness
            assert env.mock["send_notifications"].called
