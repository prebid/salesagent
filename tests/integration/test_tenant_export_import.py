"""Round-trip and collision-mode tests for tenant_export.

Exercises the export/import library against a real Postgres instance:
seed a tenant with representative data, export, delete, import, verify
row counts and key field round-trip.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select, text

from src.core.database.models import (
    AdapterConfig,
    AuditLog,
    Creative,
    CurrencyLimit,
    MediaBuy,
    MediaPackage,
    Principal,
    Product,
    PropertyTag,
    Tenant,
)
from src.core.database.tenant_export import (
    BundleSchemaMismatchError,
    TenantAlreadyExistsError,
    TenantImportCollisionError,
    TenantNotFoundError,
    delete_tenant_data,
    discover_tenant_scoped_tables,
    export_tenant,
    import_tenant,
)
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _ExportEnv(IntegrationEnv):
    """Bare integration env — no external patches needed for raw DB work."""

    EXTERNAL_PATCHES: dict[str, str] = {}
    use_real_db = True

    def get_session(self):
        self._commit_factory_data()
        return self._session


def _seed_tenant(session, tenant_id: str) -> dict:
    """Create a tenant with one row in each of several tenant-scoped tables.

    Returns a dict of identifiers we'll later assert survived the round-trip.
    """
    from tests.factories import (
        AdapterConfigFactory,
        AuditLogFactory,
        CreativeFactory,
        MediaBuyFactory,
        MediaPackageFactory,
        PrincipalFactory,
        ProductFactory,
        PropertyTagFactory,
        TenantFactory,
    )

    tenant = TenantFactory(
        tenant_id=tenant_id,
        name="Round Trip Co",
        is_embedded=False,
    )
    PropertyTagFactory(tenant=tenant, tag_id="all_inventory")
    AdapterConfigFactory(tenant=tenant, adapter_type="mock")
    principal = PrincipalFactory(
        tenant=tenant,
        principal_id=f"{tenant_id}_buyer",
        access_token=f"{tenant_id}_token_xyz",
        name="Acme Buyer",
    )
    product = ProductFactory(
        tenant=tenant,
        product_id=f"{tenant_id}_prod",
        name="Gold Inventory",
    )
    media_buy = MediaBuyFactory(
        tenant=tenant,
        principal=principal,
        media_buy_id=f"{tenant_id}_mb_001",
    )
    MediaPackageFactory(media_buy=media_buy, package_id=f"{tenant_id}_pkg_001")
    CreativeFactory(
        tenant=tenant,
        principal=principal,
        creative_id=f"{tenant_id}_cr_001",
    )
    AuditLogFactory(tenant=tenant, operation="test_round_trip")

    session.commit()

    return {
        "tenant_id": tenant_id,
        "principal_id": principal.principal_id,
        "access_token": principal.access_token,
        "product_id": product.product_id,
        "media_buy_id": media_buy.media_buy_id,
    }


def _count_rows(session, tenant_id: str) -> dict[str, int]:
    """Sample row counts across tenant-scoped tables — enough to detect drops."""
    return {
        "principals": session.scalar(
            select(func.count()).select_from(Principal).where(Principal.tenant_id == tenant_id)
        ),
        "products": session.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id)),
        "media_buys": session.scalar(select(func.count()).select_from(MediaBuy).where(MediaBuy.tenant_id == tenant_id)),
        "media_packages": session.scalar(
            select(func.count())
            .select_from(MediaPackage)
            .where(MediaPackage.media_buy_id.in_(select(MediaBuy.media_buy_id).where(MediaBuy.tenant_id == tenant_id)))
        ),
        "creatives": session.scalar(select(func.count()).select_from(Creative).where(Creative.tenant_id == tenant_id)),
        # Exclude tenant.imported — that's an audit record the importer emits,
        # not user data being round-tripped.
        "audit_logs": session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.tenant_id == tenant_id, AuditLog.operation != "tenant.imported")
        ),
        "property_tags": session.scalar(
            select(func.count()).select_from(PropertyTag).where(PropertyTag.tenant_id == tenant_id)
        ),
        "adapter_config": session.scalar(
            select(func.count()).select_from(AdapterConfig).where(AdapterConfig.tenant_id == tenant_id)
        ),
        "currency_limits": session.scalar(
            select(func.count()).select_from(CurrencyLimit).where(CurrencyLimit.tenant_id == tenant_id)
        ),
    }


class TestDiscoverTenantScopedTables:
    """Sanity-check the reflection helper without touching the DB."""

    def test_includes_direct_tenant_id_tables(self):
        names = {t.name for t in discover_tenant_scoped_tables()}
        for expected in ("principals", "products", "media_buys", "creatives", "audit_logs"):
            assert expected in names, f"{expected} should be tenant-scoped"

    def test_includes_transitive_tables(self):
        """Tables without a tenant_id column but reachable via FK should appear."""
        names = {t.name for t in discover_tenant_scoped_tables()}
        # media_packages → media_buys → tenant_id
        assert "media_packages" in names
        # strategy_states → strategies → tenant_id
        assert "strategy_states" in names

    def test_excludes_tenants_table(self):
        """The tenants table itself is handled separately by callers."""
        names = {t.name for t in discover_tenant_scoped_tables()}
        assert "tenants" not in names

    def test_excludes_global_tables(self):
        names = {t.name for t in discover_tenant_scoped_tables()}
        assert "superadmin_config" not in names
        assert "alembic_version" not in names

    def test_order_is_fk_safe(self):
        """Parents appear before children — required for insert order."""
        order = [t.name for t in discover_tenant_scoped_tables()]
        # media_buys must come before media_packages (FK dependency)
        assert order.index("media_buys") < order.index("media_packages")


class TestRoundTrip:
    """Export → delete → import → assert state matches."""

    def test_round_trip_preserves_counts_and_tokens(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            seeded = _seed_tenant(session, "rt_basic")
            counts_before = _count_rows(session, "rt_basic")

            bundle = export_tenant(session.connection(), "rt_basic")
            session.commit()

            # Bundle should be JSON-serializable as-is.
            reloaded = json.loads(json.dumps(bundle))

            delete_tenant_data(session.connection(), "rt_basic")
            session.commit()
            assert session.scalar(select(func.count()).select_from(Tenant).where(Tenant.tenant_id == "rt_basic")) == 0

            summary = import_tenant(session.connection(), reloaded)
            session.commit()

            counts_after = _count_rows(session, "rt_basic")
            assert counts_after == counts_before, f"row counts diverged: before={counts_before}, after={counts_after}"

            principal = session.scalars(
                select(Principal).where(Principal.principal_id == seeded["principal_id"])
            ).first()
            assert principal is not None
            assert principal.access_token == seeded["access_token"], (
                "principal access_token must round-trip — buyers' integrations depend on it"
            )

            assert summary["tenant_id"] == "rt_basic"
            assert summary["rows"] > 0


class TestCollisionModes:
    def test_fail_mode_raises_on_existing_tenant(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_collide_fail")
            bundle = export_tenant(session.connection(), "rt_collide_fail")
            session.commit()

            with pytest.raises(TenantAlreadyExistsError):
                import_tenant(session.connection(), bundle, mode="fail")

    def test_replace_mode_overwrites(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            seeded = _seed_tenant(session, "rt_collide_replace")
            bundle = export_tenant(session.connection(), "rt_collide_replace")
            session.commit()

            # Mutate the live tenant's name so we can detect that replace wiped it.
            session.info["super_admin_override"] = True
            tenant = session.scalars(select(Tenant).where(Tenant.tenant_id == "rt_collide_replace")).first()
            tenant.name = "MUTATED"
            session.commit()

            import_tenant(session.connection(), bundle, mode="replace")
            session.commit()

            restored = session.scalars(select(Tenant).where(Tenant.tenant_id == "rt_collide_replace")).first()
            assert restored is not None
            assert restored.name == "Round Trip Co", "replace mode should restore the bundle's tenant.name"

            principal = session.scalars(
                select(Principal).where(Principal.principal_id == seeded["principal_id"])
            ).first()
            assert principal is not None
            assert principal.access_token == seeded["access_token"]


class TestFlipToEmbedded:
    def test_flip_to_embedded_sets_flag(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_flip")
            bundle = export_tenant(session.connection(), "rt_flip")
            session.commit()

            delete_tenant_data(session.connection(), "rt_flip")
            session.commit()

            import_tenant(session.connection(), bundle, flip_to_embedded=True)
            session.commit()

            tenant = session.scalars(select(Tenant).where(Tenant.tenant_id == "rt_flip")).first()
            assert tenant is not None
            assert tenant.is_embedded is True


class TestTargetTenantId:
    def test_retargets_all_rows(self, integration_db):
        """Simulates a cross-deployment move — source tenant is gone, retargeted bundle lands fresh.

        Same-database retargeting hits unique constraints on subdomain /
        virtual_host / public_agent_url; that's a feature, not a bug —
        those columns identify the tenant in the outside world.
        """
        with _ExportEnv() as env:
            session = env.get_session()
            seeded = _seed_tenant(session, "rt_src")
            counts_before = _count_rows(session, "rt_src")
            bundle = export_tenant(session.connection(), "rt_src")
            session.commit()

            delete_tenant_data(session.connection(), "rt_src")
            session.commit()

            import_tenant(session.connection(), bundle, target_tenant_id="rt_dst")
            session.commit()

            dst_count = _count_rows(session, "rt_dst")
            assert dst_count == counts_before, (
                f"retarget should preserve row counts: before={counts_before}, after={dst_count}"
            )

            dst_principal = session.scalars(select(Principal).where(Principal.tenant_id == "rt_dst")).first()
            assert dst_principal is not None
            assert dst_principal.access_token == seeded["access_token"]


class TestCloneOnSameDeployment:
    """Retargeting on the same DB: source stays in place, clone gets distinct surrogate PKs.

    The original ``--target-tenant-id`` path collided on autoincrement int PKs
    (gam_inventory.id, audit_logs.log_id, etc.) because the bundle preserved
    those values and the source's rows still owned them. Fix: when retargeting,
    strip autoincrement int PKs so Postgres re-allocates from the sequence,
    then remap any FK references using the old→new ID map.
    """

    def test_clone_with_source_alive_succeeds(self, integration_db):
        from src.core.database.models import AuditLog, InventoryProfile
        from tests.factories import (
            AuditLogFactory,
            InventoryProfileFactory,
            PrincipalFactory,
            ProductFactory,
            TenantFactory,
        )

        with _ExportEnv() as env:
            session = env.get_session()
            source = TenantFactory(tenant_id="rt_clone_src", name="Source")
            principal = PrincipalFactory(
                tenant=source,
                principal_id="rt_clone_src_buyer",
                access_token="rt_clone_src_token",
            )
            profile = InventoryProfileFactory(tenant=source, profile_id="rt_clone_src_profile")
            ProductFactory(
                tenant=source,
                product_id="rt_clone_src_prod",
                inventory_profile_id=profile.id,
            )
            AuditLogFactory(tenant=source, operation="clone_test_event")
            session.commit()

            source_profile_id = profile.id
            source_audit_count = session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.tenant_id == "rt_clone_src", AuditLog.operation == "clone_test_event")
            )
            assert source_audit_count == 1

            bundle = export_tenant(session.connection(), "rt_clone_src")
            session.commit()

            # Same-deployment clones must rewrite globally-unique columns the
            # pre-flight check protects (subdomain, virtual_host, access_token).
            # Mimicking what an operator does manually before importing a clone.
            bundle["tenant"]["subdomain"] = "pub-rt-clone-dst"
            for row in bundle["tables"].get("principals", []):
                if row.get("access_token") == "rt_clone_src_token":
                    row["access_token"] = "rt_clone_dst_token"

            # Retarget while source is still alive — this is the clone scenario
            # that originally collided on gam_inventory.id et al.
            import_tenant(session.connection(), bundle, target_tenant_id="rt_clone_dst")
            session.commit()

            # Both tenants exist.
            assert (
                session.scalar(select(func.count()).select_from(Tenant).where(Tenant.tenant_id == "rt_clone_src")) == 1
            )
            assert (
                session.scalar(select(func.count()).select_from(Tenant).where(Tenant.tenant_id == "rt_clone_dst")) == 1
            )

            # The clone's inventory_profile got a NEW autoincrement id.
            dst_profile = session.scalars(
                select(InventoryProfile).where(InventoryProfile.tenant_id == "rt_clone_dst")
            ).first()
            assert dst_profile is not None
            assert dst_profile.id != source_profile_id, (
                "clone must get a fresh autoincrement PK; sharing source's would collide"
            )

            # The clone's product FK must point at the CLONE's profile, not the source's.
            dst_product = session.scalars(
                select(Product).where(Product.tenant_id == "rt_clone_dst", Product.product_id == "rt_clone_src_prod")
            ).first()
            assert dst_product is not None
            assert dst_product.inventory_profile_id == dst_profile.id, (
                f"FK should be remapped: expected {dst_profile.id}, got {dst_product.inventory_profile_id}"
            )

            # Source data is untouched.
            src_profile = session.scalars(
                select(InventoryProfile).where(InventoryProfile.tenant_id == "rt_clone_src")
            ).first()
            assert src_profile is not None
            assert src_profile.id == source_profile_id

            # Access token preserved on the clone (buyers' integrations keep working).
            # Token rewritten in the bundle pre-import; the buyer who got
            # rt_clone_dst_token issued separately can now talk to the clone.
            dst_principal = session.scalars(select(Principal).where(Principal.tenant_id == "rt_clone_dst")).first()
            assert dst_principal is not None
            assert dst_principal.access_token == "rt_clone_dst_token"

    def test_retarget_without_source_still_works(self, integration_db):
        """Regression: the original retarget test path (delete source, then retarget) still passes.

        Strip-and-remap should be a superset of the original behavior — when the
        source is gone, the new PKs Postgres allocates may or may not equal the
        source's old PKs, but the data lands correctly either way.
        """
        from tests.factories import (
            InventoryProfileFactory,
            PrincipalFactory,
            ProductFactory,
            TenantFactory,
        )

        with _ExportEnv() as env:
            session = env.get_session()
            source = TenantFactory(tenant_id="rt_clone_solo_src", name="Solo Source")
            principal = PrincipalFactory(
                tenant=source,
                principal_id="rt_clone_solo_buyer",
                access_token="rt_clone_solo_token",
            )
            profile = InventoryProfileFactory(tenant=source, profile_id="rt_clone_solo_profile")
            ProductFactory(
                tenant=source,
                product_id="rt_clone_solo_prod",
                inventory_profile_id=profile.id,
            )
            session.commit()

            bundle = export_tenant(session.connection(), "rt_clone_solo_src")
            session.commit()
            delete_tenant_data(session.connection(), "rt_clone_solo_src")
            session.commit()

            import_tenant(session.connection(), bundle, target_tenant_id="rt_clone_solo_dst")
            session.commit()

            dst_product = session.scalars(select(Product).where(Product.tenant_id == "rt_clone_solo_dst")).first()
            assert dst_product is not None
            # FK should still point to a real profile in the clone, not stale source ID.
            from src.core.database.models import InventoryProfile

            referenced_profile = session.scalars(
                select(InventoryProfile).where(InventoryProfile.id == dst_product.inventory_profile_id)
            ).first()
            assert referenced_profile is not None
            assert referenced_profile.tenant_id == "rt_clone_solo_dst"

    def test_clone_remaps_string_pk_and_rewrites_fk(self, integration_db):
        """Single-column string PKs (media_buys.media_buy_id, etc.) get fresh IDs and FKs follow.

        Regression for #416: same-deployment clone via --target-tenant-id used
        to collide on string PKs (proposals.proposal_id was the original
        failure). The remap mints new IDs and rewrites every FK pointing at
        them; this test exercises the media_buys → media_packages chain.
        """
        from src.core.database.models import MediaBuy, MediaPackage
        from tests.factories import (
            MediaBuyFactory,
            MediaPackageFactory,
            PrincipalFactory,
            TenantFactory,
        )

        with _ExportEnv() as env:
            session = env.get_session()
            source = TenantFactory(tenant_id="rt_str_src", name="String PK Source")
            principal = PrincipalFactory(
                tenant=source,
                principal_id="rt_str_src_buyer",
                access_token="rt_str_src_token",
            )
            media_buy = MediaBuyFactory(
                tenant=source,
                principal=principal,
                media_buy_id="mb_str_pk_clone_001",
            )
            MediaPackageFactory(media_buy=media_buy, package_id="pkg_str_pk_clone_a")
            MediaPackageFactory(media_buy=media_buy, package_id="pkg_str_pk_clone_b")
            session.commit()

            source_media_buy_id = media_buy.media_buy_id

            bundle = export_tenant(session.connection(), "rt_str_src")
            session.commit()

            # Rewrite globally-unique columns the pre-flight protects.
            bundle["tenant"]["subdomain"] = "pub-rt-str-dst"
            for row in bundle["tables"].get("principals", []):
                if row.get("access_token") == "rt_str_src_token":
                    row["access_token"] = "rt_str_dst_token"

            import_tenant(session.connection(), bundle, target_tenant_id="rt_str_dst")
            session.commit()

            # Source media_buy still exists with its original ID.
            assert (
                session.scalars(select(MediaBuy).where(MediaBuy.media_buy_id == source_media_buy_id)).first()
                is not None
            )

            # Clone has its own media_buy with a DIFFERENT id.
            dst_media_buy = session.scalars(select(MediaBuy).where(MediaBuy.tenant_id == "rt_str_dst")).first()
            assert dst_media_buy is not None
            assert dst_media_buy.media_buy_id != source_media_buy_id, (
                "clone must mint a fresh media_buy_id; sharing source's would collide"
            )
            # Prefix preservation: original was "mb_…" so the clone should be too.
            assert dst_media_buy.media_buy_id.startswith("mb_"), (
                f"expected prefix-preserving rename, got {dst_media_buy.media_buy_id!r}"
            )

            # The two media_packages should now reference the CLONE's media_buy_id,
            # not the source's. Composite PK (media_buy_id, package_id) — the
            # tuple is unique because media_buy_id is remapped, so package_id
            # values can stay as-is.
            dst_packages = session.scalars(
                select(MediaPackage).where(MediaPackage.media_buy_id == dst_media_buy.media_buy_id)
            ).all()
            assert len(dst_packages) == 2, "both packages should be re-linked to the clone's media_buy"
            assert {p.package_id for p in dst_packages} == {"pkg_str_pk_clone_a", "pkg_str_pk_clone_b"}

            # Source's media_packages still attached to source's media_buy.
            source_packages = session.scalars(
                select(MediaPackage).where(MediaPackage.media_buy_id == source_media_buy_id)
            ).all()
            assert {p.package_id for p in source_packages} == {"pkg_str_pk_clone_a", "pkg_str_pk_clone_b"}


class TestStripSecrets:
    def test_strip_secrets_wipes_encrypted_columns(self, integration_db):
        from src.core.utils.encryption import encrypt_api_key

        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_secrets")

            # Stamp an encrypted-at-rest credential we expect to be stripped.
            tenant = session.scalars(select(Tenant).where(Tenant.tenant_id == "rt_secrets")).first()
            session.info["super_admin_override"] = True
            tenant._gemini_api_key = encrypt_api_key("ya29.fake-key")
            session.commit()

            bundle = export_tenant(session.connection(), "rt_secrets", strip_secrets=True)
            session.commit()

            assert bundle["tenant"]["gemini_api_key"] is None, "strip_secrets must wipe tenants.gemini_api_key"

    def test_default_export_preserves_encrypted_columns(self, integration_db):
        from src.core.utils.encryption import encrypt_api_key

        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_secrets_keep")
            tenant = session.scalars(select(Tenant).where(Tenant.tenant_id == "rt_secrets_keep")).first()
            session.info["super_admin_override"] = True
            ciphertext = encrypt_api_key("ya29.preserve-me")
            tenant._gemini_api_key = ciphertext
            session.commit()

            bundle = export_tenant(session.connection(), "rt_secrets_keep")
            session.commit()

            assert bundle["tenant"]["gemini_api_key"] == ciphertext

    def test_strip_secrets_wipes_plaintext_bearer_credentials(self, integration_db):
        """Bearer credentials that are NOT Fernet-encrypted must also be wiped."""
        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_plaintext")
            tenant = session.scalars(select(Tenant).where(Tenant.tenant_id == "rt_plaintext")).first()
            session.info["super_admin_override"] = True
            tenant.admin_token = "plaintext-admin-bearer"
            tenant.slack_webhook_url = "https://hooks.slack.com/services/T000/B000/SECRET"
            tenant.slack_audit_webhook_url = "https://hooks.slack.com/services/T000/B001/SECRET"
            tenant.hitl_webhook_url = "https://example.com/hitl-callback"
            session.commit()

            bundle = export_tenant(session.connection(), "rt_plaintext", strip_secrets=True)
            session.commit()

            assert bundle["tenant"]["admin_token"] is None
            assert bundle["tenant"]["slack_webhook_url"] is None
            assert bundle["tenant"]["slack_audit_webhook_url"] is None
            assert bundle["tenant"]["hitl_webhook_url"] is None
            # access_token is intentionally preserved — buyers' integrations depend on it.
            principal_rows = bundle["tables"]["principals"]
            assert principal_rows[0]["access_token"] == "rt_plaintext_token_xyz"


class TestCollisionPreflight:
    """Pre-flight collision check on globally-unique columns."""

    def test_subdomain_collision_aborts_with_clear_message(self, integration_db):
        from tests.factories import TenantFactory

        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_collide_subdomain")
            bundle = export_tenant(session.connection(), "rt_collide_subdomain")
            session.commit()

            delete_tenant_data(session.connection(), "rt_collide_subdomain")
            session.commit()

            # Park a different tenant on the same subdomain — the import
            # would otherwise hit IntegrityError when inserting tenants.
            TenantFactory(tenant_id="rt_squatter", subdomain="pub-rt_collide_subdomain")
            session.commit()

            with pytest.raises(TenantImportCollisionError) as exc_info:
                import_tenant(session.connection(), bundle)
            assert "subdomain" in str(exc_info.value)
            assert "rt_squatter" in str(exc_info.value)

    def test_access_token_collision_detected(self, integration_db):
        from tests.factories import PrincipalFactory, TenantFactory

        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_collide_token")
            bundle = export_tenant(session.connection(), "rt_collide_token")
            session.commit()

            delete_tenant_data(session.connection(), "rt_collide_token")
            session.commit()

            # Place the same access_token under a different tenant.
            other_tenant = TenantFactory(tenant_id="rt_other_tenant", subdomain="pub-rt-other")
            PrincipalFactory(
                tenant=other_tenant,
                principal_id="rt_other_buyer",
                access_token="rt_collide_token_token_xyz",
            )
            session.commit()

            with pytest.raises(TenantImportCollisionError) as exc_info:
                import_tenant(session.connection(), bundle)
            assert "access_token" in str(exc_info.value)


class TestStrictColumnFiltering:
    """When alembic revisions match, unknown columns are a bug — not noise."""

    def test_unknown_column_with_matching_alembic_raises(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_strict")
            bundle = export_tenant(session.connection(), "rt_strict")
            session.commit()
            delete_tenant_data(session.connection(), "rt_strict")
            session.commit()

            # Spike a fake column into the bundle's principal rows.
            bundle["tables"]["principals"][0]["nonexistent_column"] = "boom"
            # Force schema_matches=True by setting both revisions equal to a known string.
            bundle["alembic_revision"] = "matched-revision"

            # Inject the same revision into the DB so the match check passes.
            from sqlalchemy import inspect as sa_inspect

            if sa_inspect(session.connection()).has_table("alembic_version"):
                db_rev = session.connection().exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
                bundle["alembic_revision"] = db_rev
            else:
                # Without alembic_version, schema_matches stays False; the test would
                # exercise the lenient path. Skip in that case.
                pytest.skip("alembic_version table missing — strict path requires a real migration")

            with pytest.raises(BundleSchemaMismatchError) as exc_info:
                import_tenant(session.connection(), bundle)
            assert "nonexistent_column" in str(exc_info.value)

    def test_unknown_column_with_schema_drift_warns_and_continues(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_drift")
            bundle = export_tenant(session.connection(), "rt_drift")
            session.commit()
            delete_tenant_data(session.connection(), "rt_drift")
            session.commit()

            bundle["tables"]["principals"][0]["nonexistent_column"] = "boom"

            summary = import_tenant(session.connection(), bundle, require_alembic_match=False)
            session.commit()

            assert summary["rows"] > 0


class TestImportAuditLog:
    def test_import_writes_audit_log_row(self, integration_db):
        from src.core.database.models import AuditLog

        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_audit")
            audit_count_before = session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.tenant_id == "rt_audit",
                    AuditLog.operation == "tenant.imported",
                )
            )
            bundle = export_tenant(session.connection(), "rt_audit")
            session.commit()
            delete_tenant_data(session.connection(), "rt_audit")
            session.commit()

            import_tenant(session.connection(), bundle, mode="fail", flip_to_embedded=True)
            session.commit()

            audit_row = session.scalars(
                select(AuditLog).where(
                    AuditLog.tenant_id == "rt_audit",
                    AuditLog.operation == "tenant.imported",
                )
            ).first()
            assert audit_row is not None
            assert audit_row.success is True
            assert audit_row.details["flip_to_embedded"] is True
            assert audit_row.details["mode"] == "fail"
            assert audit_row.details["rows"] > 0
            assert audit_count_before == 0


class TestSchemaMismatch:
    def test_old_schema_version_rejected(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            _seed_tenant(session, "rt_schema")
            bundle = export_tenant(session.connection(), "rt_schema")
            session.commit()
            delete_tenant_data(session.connection(), "rt_schema")
            session.commit()

            bundle["schema_version"] = 999

            with pytest.raises(BundleSchemaMismatchError):
                import_tenant(session.connection(), bundle)


class TestExportMissingTenant:
    def test_export_unknown_tenant_raises(self, integration_db):
        with _ExportEnv() as env:
            session = env.get_session()
            with pytest.raises(TenantNotFoundError):
                export_tenant(session.connection(), "does_not_exist")


class TestDeleteTenantDataSuspendsTriggers:
    """Validation triggers like prevent_empty_pricing_options must not block bulk
    tenant deletes — the parent product is going away in the same transaction.

    Production has a ``prevent_empty_pricing_options`` BEFORE-DELETE trigger on
    ``pricing_options``. During the first prod dry-run of import_tenant for
    tenant_wonderstruck the delete path hit:
        Cannot delete last pricing option for product prod_xyz
        CONTEXT: PL/pgSQL function prevent_empty_pricing_options()

    This test installs a similar trigger and verifies delete_tenant_data
    suspends user triggers for the duration of the delete loop.
    """

    _TRIGGER_FN = "test_delete_block_pricing_options"
    _TRIGGER_NAME = "test_delete_block_pricing_options_trg"

    def _install_trigger(self, session) -> None:
        session.execute(
            text(f"""
                CREATE OR REPLACE FUNCTION {self._TRIGGER_FN}()
                RETURNS TRIGGER AS $$
                BEGIN
                    RAISE EXCEPTION
                        'TEST: prevent_empty_pricing_options-like trigger on product %',
                        OLD.product_id;
                END;
                $$ LANGUAGE plpgsql;
            """)
        )
        session.execute(
            text(f"""
                CREATE TRIGGER {self._TRIGGER_NAME}
                BEFORE DELETE ON pricing_options
                FOR EACH ROW
                EXECUTE FUNCTION {self._TRIGGER_FN}();
            """)
        )
        session.commit()

    def _drop_trigger(self, session) -> None:
        session.execute(text(f"DROP TRIGGER IF EXISTS {self._TRIGGER_NAME} ON pricing_options"))
        session.execute(text(f"DROP FUNCTION IF EXISTS {self._TRIGGER_FN}()"))
        session.commit()

    def test_delete_succeeds_through_blocking_trigger(self, integration_db):
        from tests.factories import PricingOptionFactory, ProductFactory, TenantFactory

        with _ExportEnv() as env:
            session = env.get_session()
            tenant = TenantFactory(tenant_id="rt_trigger", name="Trigger Test")
            product = ProductFactory(tenant=tenant, product_id="rt_trigger_prod")
            PricingOptionFactory(product=product, pricing_model="cpm")
            session.commit()

            self._install_trigger(session)
            try:
                # Sanity: trigger actually fires on a piecemeal delete.
                with pytest.raises(Exception, match="prevent_empty_pricing_options-like"):
                    session.execute(text("DELETE FROM pricing_options WHERE tenant_id = 'rt_trigger'"))
                session.rollback()

                # Real test: bulk delete via delete_tenant_data succeeds despite the trigger.
                delete_tenant_data(session.connection(), "rt_trigger")
                session.commit()

                remaining = session.scalar(
                    select(func.count()).select_from(Tenant).where(Tenant.tenant_id == "rt_trigger")
                )
                assert remaining == 0, "tenant row must be deleted"
            finally:
                # Re-bind a clean session to drop the trigger — the prior commit
                # closed any aborted-tx state.
                self._drop_trigger(session)

    def test_triggers_reenabled_after_delete(self, integration_db):
        """After delete_tenant_data returns, triggers fire normally on the affected tables."""
        from tests.factories import PricingOptionFactory, ProductFactory, TenantFactory

        with _ExportEnv() as env:
            session = env.get_session()
            tenant1 = TenantFactory(tenant_id="rt_reenable_a", name="Reenable A")
            product1 = ProductFactory(tenant=tenant1, product_id="rt_reenable_a_prod")
            PricingOptionFactory(product=product1, pricing_model="cpm")

            # Separate tenant whose pricing_options must REMAIN protected.
            tenant2 = TenantFactory(tenant_id="rt_reenable_b", name="Reenable B")
            product2 = ProductFactory(tenant=tenant2, product_id="rt_reenable_b_prod")
            PricingOptionFactory(product=product2, pricing_model="cpm")
            session.commit()

            self._install_trigger(session)
            try:
                delete_tenant_data(session.connection(), "rt_reenable_a")
                session.commit()

                # The trigger must fire again for tenant B's pricing_options.
                with pytest.raises(Exception, match="prevent_empty_pricing_options-like"):
                    session.execute(text("DELETE FROM pricing_options WHERE tenant_id = 'rt_reenable_b'"))
                session.rollback()
            finally:
                self._drop_trigger(session)
                # Clean up tenant B (using the now-fixed delete_tenant_data).
                delete_tenant_data(session.connection(), "rt_reenable_b")
                session.commit()
