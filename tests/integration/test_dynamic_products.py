"""Integration tests for dynamic product variant generation.

Tests the full pipeline: DB templates → mock signals agent → variant generation.
Only the signals agent registry is mocked — all DB operations, activation key
parsing, variant ID generation, and template expansion use real code paths.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from scripts.setup.seed_products import whole_publisher_selections
from src.core.database.database_session import get_db_session
from src.core.database.models import Product
from src.services.dynamic_products import (
    archive_expired_variants,
    generate_variants_for_brief,
)
from tests.factories import ProductFactory, SignalFactory
from tests.harness._base import BareIntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

OUR_AGENT_URL = "https://sales.example.com"


def _get_variants(env: BareIntegrationEnv, tenant_id: str) -> list[Product]:
    """Read persisted variants through the harness's open session and tenant scope."""
    from src.core.database.repositories.product import ProductRepository

    session = env.get_session()
    session.expire_all()
    return [product for product in ProductRepository(session, tenant_id).list_all() if product.is_dynamic_variant]


def _make_signal(
    segment_id: str = "seg_001",
    name: str = "Auto Intenders",
    description: str = "Users researching vehicles",
    data_provider: str = "Oracle Data Cloud",
    coverage_percentage: int = 85,
    agent_url: str = OUR_AGENT_URL,
    is_live: bool = True,
    key_type: str = "key_value",
    key: str = "axe_segment",
    value: str = "auto_intender_123",
) -> dict:
    return SignalFactory.payload(
        signal_agent_segment_id=segment_id,
        name=name,
        description=description,
        data_provider=data_provider,
        coverage_percentage=float(coverage_percentage),
        agent_url=agent_url,
        is_live=is_live,
        key_type=key_type,
        key=key,
        value=value,
    )


def _create_dynamic_template(
    factory_env,
    tenant_id: str,
    product_id: str = "tmpl_001",
    name: str = "Display Template",
    signals_agent_ids: list[str] | None = None,
    max_signals: int = 5,
    countries: list[str] | None = None,
    variant_name_template: str | None = None,
    variant_description_template: str | None = None,
    variant_ttl_days: int | None = None,
) -> Product:
    """Create a dynamic product template in the DB."""
    if signals_agent_ids is None:
        signals_agent_ids = ["sig_agent_1"]
    if countries is None:
        countries = ["US"]

    return ProductFactory(
        tenant=factory_env.ensure_tenant(tenant_id),
        product_id=product_id,
        name=name,
        description=f"Dynamic template for {name}",
        dynamic_template=True,
        signals_agent_ids=signals_agent_ids,
        max_signals=max_signals,
        countries=countries,
        variant_name_template=variant_name_template,
        variant_description_template=variant_description_template,
        variant_ttl_days=variant_ttl_days,
    )


@pytest.fixture(autouse=True)
def factory_env(integration_db):
    with BareIntegrationEnv() as env:
        yield env


# ---------------------------------------------------------------------------
# generate_variants_for_brief — integration tests
# ---------------------------------------------------------------------------


class TestGenerateVariantsForBrief:
    """End-to-end tests for generate_variants_for_brief with mock registry."""

    def test_no_templates_persists_nothing(self, integration_db, factory_env):
        """No dynamic templates in DB → persists no variants."""
        factory_env.ensure_tenant("test_tenant")

        with patch("src.services.dynamic_products.get_signals_agent_registry") as mock_get:
            registry = AsyncMock()
            mock_get.return_value = registry
            result = asyncio.run(generate_variants_for_brief("test_tenant", "display ads", OUR_AGENT_URL))

        assert result is None
        assert _get_variants(factory_env, "test_tenant") == []
        registry.get_signals.assert_not_called()

    def test_templates_with_signals_generates_variants(self, integration_db, factory_env):
        """Templates + signals → variants with correct fields."""
        factory_env.ensure_tenant("test_tenant")
        _create_dynamic_template(factory_env, "test_tenant", product_id="tmpl_001")

        signals = [
            _make_signal(segment_id="seg_001", name="Auto Intenders", value="auto_123"),
            _make_signal(segment_id="seg_002", name="Sports Fans", value="sports_456"),
        ]

        with patch("src.services.dynamic_products.get_signals_agent_registry") as mock_get:
            registry = AsyncMock()
            registry.get_signals = AsyncMock(return_value=signals)
            mock_get.return_value = registry

            result = asyncio.run(generate_variants_for_brief("test_tenant", "targeting brief", OUR_AGENT_URL))

        assert result is None
        variants = _get_variants(factory_env, "test_tenant")
        assert len(variants) == 2
        for variant in variants:
            assert variant.is_dynamic_variant is True
            assert variant.is_dynamic is False
            assert variant.parent_product_id == "tmpl_001"
            assert variant.activation_key is not None
            assert variant.signal_metadata is not None
            assert variant.tenant_id == "test_tenant"

    def test_signals_agent_persists_nothing(self, integration_db, factory_env):
        """Signals agent returns empty list → no variants generated."""
        factory_env.ensure_tenant("test_tenant")
        _create_dynamic_template(factory_env, "test_tenant")

        with patch("src.services.dynamic_products.get_signals_agent_registry") as mock_get:
            registry = AsyncMock()
            registry.get_signals = AsyncMock(return_value=[])
            mock_get.return_value = registry

            result = asyncio.run(generate_variants_for_brief("test_tenant", "brief", OUR_AGENT_URL))

        assert result is None
        assert _get_variants(factory_env, "test_tenant") == []

    def test_max_signals_limits_variants(self, integration_db, factory_env):
        """max_signals=2 limits variant generation to 2 signals."""
        factory_env.ensure_tenant("test_tenant")
        _create_dynamic_template(factory_env, "test_tenant", max_signals=2)

        signals = [_make_signal(segment_id=f"seg_{i}", name=f"Signal {i}", value=f"val_{i}") for i in range(5)]

        with patch("src.services.dynamic_products.get_signals_agent_registry") as mock_get:
            registry = AsyncMock()
            registry.get_signals = AsyncMock(return_value=signals)
            mock_get.return_value = registry

            result = asyncio.run(generate_variants_for_brief("test_tenant", "brief", OUR_AGENT_URL))

        assert result is None
        assert len(_get_variants(factory_env, "test_tenant")) == 2

    def test_multiple_templates(self, integration_db, factory_env):
        """Multiple templates × signals → variants from each template."""
        factory_env.ensure_tenant("test_tenant")
        _create_dynamic_template(factory_env, "test_tenant", product_id="tmpl_A", name="Template A")
        _create_dynamic_template(factory_env, "test_tenant", product_id="tmpl_B", name="Template B")

        signals = [_make_signal(segment_id="seg_001", value="val_001")]

        with patch("src.services.dynamic_products.get_signals_agent_registry") as mock_get:
            registry = AsyncMock()
            registry.get_signals = AsyncMock(return_value=signals)
            mock_get.return_value = registry

            result = asyncio.run(generate_variants_for_brief("test_tenant", "brief", OUR_AGENT_URL))

        assert result is None
        variants = _get_variants(factory_env, "test_tenant")
        assert len(variants) == 2
        parent_ids = {v.parent_product_id for v in variants}
        assert parent_ids == {"tmpl_A", "tmpl_B"}

    def test_variant_id_is_deterministic(self, integration_db, factory_env):
        """Same template + signal → same variant ID across calls."""
        factory_env.ensure_tenant("test_tenant")
        _create_dynamic_template(factory_env, "test_tenant")

        signals = [_make_signal()]

        with patch("src.services.dynamic_products.get_signals_agent_registry") as mock_get:
            registry = AsyncMock()
            registry.get_signals = AsyncMock(return_value=signals)
            mock_get.return_value = registry

            result1 = asyncio.run(generate_variants_for_brief("test_tenant", "brief", OUR_AGENT_URL))

        # Second call — same signal, should reuse existing variant
        with patch("src.services.dynamic_products.get_signals_agent_registry") as mock_get:
            registry = AsyncMock()
            registry.get_signals = AsyncMock(return_value=signals)
            mock_get.return_value = registry

            result2 = asyncio.run(generate_variants_for_brief("test_tenant", "brief", OUR_AGENT_URL))

        assert result1 is None
        assert len(_get_variants(factory_env, "test_tenant")) == 1
        assert result2 is None
        assert len(_get_variants(factory_env, "test_tenant")) == 1
        # Both calls produce same variant — verify via DB
        variants = _get_variants(factory_env, "test_tenant")
        assert len(variants) == 1  # Only one variant, reused

    def test_existing_variant_updated_not_duplicated(self, integration_db, factory_env):
        """Second call with same signal updates existing variant, doesn't create duplicate."""
        factory_env.ensure_tenant("test_tenant")
        _create_dynamic_template(factory_env, "test_tenant")

        signals = [_make_signal()]

        with patch("src.services.dynamic_products.get_signals_agent_registry") as mock_get:
            registry = AsyncMock()
            registry.get_signals = AsyncMock(return_value=signals)
            mock_get.return_value = registry

            asyncio.run(generate_variants_for_brief("test_tenant", "brief", OUR_AGENT_URL))
            result2 = asyncio.run(generate_variants_for_brief("test_tenant", "brief", OUR_AGENT_URL))

        # Should be the same variant (updated, not new)
        assert result2 is None
        variants = _get_variants(factory_env, "test_tenant")
        assert len(variants) == 1

    def test_variant_has_signal_metadata(self, integration_db, factory_env):
        """Variant stores signal metadata for traceability."""
        factory_env.ensure_tenant("test_tenant")
        _create_dynamic_template(factory_env, "test_tenant")

        signals = [
            _make_signal(
                segment_id="seg_meta",
                name="Metadata Signal",
                description="Signal with metadata",
                data_provider="Test Provider",
                coverage_percentage=92,
            )
        ]

        with patch("src.services.dynamic_products.get_signals_agent_registry") as mock_get:
            registry = AsyncMock()
            registry.get_signals = AsyncMock(return_value=signals)
            mock_get.return_value = registry

            result = asyncio.run(generate_variants_for_brief("test_tenant", "brief", OUR_AGENT_URL))

        assert result is None
        variants = _get_variants(factory_env, "test_tenant")
        assert len(variants) == 1
        meta = variants[0].signal_metadata
        assert meta["signal_agent_segment_id"] == "seg_meta"
        assert meta["name"] == "Metadata Signal"
        assert meta["data_provider"] == "Test Provider"
        assert meta["coverage_percentage"] == 92

    def test_variant_name_customized(self, integration_db, factory_env):
        """Variant name includes signal name by default."""
        factory_env.ensure_tenant("test_tenant")
        _create_dynamic_template(factory_env, "test_tenant", name="Display Banner")

        signals = [_make_signal(name="Sports Fans")]

        with patch("src.services.dynamic_products.get_signals_agent_registry") as mock_get:
            registry = AsyncMock()
            registry.get_signals = AsyncMock(return_value=signals)
            mock_get.return_value = registry

            result = asyncio.run(generate_variants_for_brief("test_tenant", "brief", OUR_AGENT_URL))

        assert result is None
        variants = _get_variants(factory_env, "test_tenant")
        assert len(variants) == 1
        assert "Sports Fans" in variants[0].name
        assert "Display Banner" in variants[0].name

    def test_variant_name_template_macro(self, integration_db, factory_env):
        """Custom variant_name_template with macros is expanded."""
        factory_env.ensure_tenant("test_tenant")
        _create_dynamic_template(
            factory_env,
            "test_tenant",
            name="Banner",
            variant_name_template="{{name}} ({{signal.name}})",
        )

        signals = [_make_signal(name="Auto Intenders")]

        with patch("src.services.dynamic_products.get_signals_agent_registry") as mock_get:
            registry = AsyncMock()
            registry.get_signals = AsyncMock(return_value=signals)
            mock_get.return_value = registry

            result = asyncio.run(generate_variants_for_brief("test_tenant", "brief", OUR_AGENT_URL))

        assert result is None
        variants = _get_variants(factory_env, "test_tenant")
        assert len(variants) == 1
        assert variants[0].name == "Banner (Auto Intenders)"

    def test_signal_without_matching_deployment_skipped(self, integration_db, factory_env):
        """Signal with no live deployment matching our URL is skipped."""
        factory_env.ensure_tenant("test_tenant")
        _create_dynamic_template(factory_env, "test_tenant")

        signals = [_make_signal(is_live=False)]

        with patch("src.services.dynamic_products.get_signals_agent_registry") as mock_get:
            registry = AsyncMock()
            registry.get_signals = AsyncMock(return_value=signals)
            mock_get.return_value = registry

            result = asyncio.run(generate_variants_for_brief("test_tenant", "brief", OUR_AGENT_URL))

        assert result is None
        assert _get_variants(factory_env, "test_tenant") == []

    def test_variant_has_expiration(self, integration_db, factory_env):
        """Variant has expires_at set based on template TTL."""
        factory_env.ensure_tenant("test_tenant")
        _create_dynamic_template(factory_env, "test_tenant", variant_ttl_days=14)

        signals = [_make_signal()]

        with patch("src.services.dynamic_products.get_signals_agent_registry") as mock_get:
            registry = AsyncMock()
            registry.get_signals = AsyncMock(return_value=signals)
            mock_get.return_value = registry

            result = asyncio.run(generate_variants_for_brief("test_tenant", "brief", OUR_AGENT_URL))

        assert result is None
        variants = _get_variants(factory_env, "test_tenant")
        assert len(variants) == 1
        assert variants[0].expires_at is not None
        # Should expire roughly 14 days from now
        expected_min = datetime.now(UTC) + timedelta(days=13)
        expected_max = datetime.now(UTC) + timedelta(days=15)
        assert expected_min < variants[0].expires_at < expected_max

    def test_template_without_signals_agent_ids_skipped(self, integration_db, factory_env):
        """Template with signals_agent_ids=[] (empty list) is skipped."""
        factory_env.ensure_tenant("test_tenant")
        _create_dynamic_template(factory_env, "test_tenant", signals_agent_ids=[])

        signals = [_make_signal()]

        with patch("src.services.dynamic_products.get_signals_agent_registry") as mock_get:
            registry = AsyncMock()
            registry.get_signals = AsyncMock(return_value=signals)
            mock_get.return_value = registry

            result = asyncio.run(generate_variants_for_brief("test_tenant", "brief", OUR_AGENT_URL))

        # Empty signals_agent_ids → skipped in the template loop
        assert result is None
        assert _get_variants(factory_env, "test_tenant") == []


# ---------------------------------------------------------------------------
# archive_expired_variants
# ---------------------------------------------------------------------------


class TestArchiveExpiredVariants:
    """Tests for archive_expired_variants()."""

    def test_expired_variants_archived(self, integration_db, factory_env):
        """Expired variants get archived_at set."""
        factory_env.ensure_tenant("test_tenant")

        with get_db_session() as session:
            variant = Product(
                tenant_id="test_tenant",
                product_id="variant_expired",
                name="Expired Variant",
                format_ids=[],
                targeting_template={},
                delivery_type="standard",
                publisher_selections=whole_publisher_selections(session, "test_tenant"),
                is_dynamic_variant=True,
                parent_product_id="tmpl_001",
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )
            session.add(variant)
            session.commit()

        count = archive_expired_variants("test_tenant")
        assert count == 1

        with get_db_session() as session:
            from sqlalchemy import select

            archived = session.scalars(select(Product).filter_by(product_id="variant_expired")).first()
            assert archived.archived_at is not None

    def test_non_expired_variants_untouched(self, integration_db, factory_env):
        """Non-expired variants are not archived."""
        factory_env.ensure_tenant("test_tenant")

        with get_db_session() as session:
            variant = Product(
                tenant_id="test_tenant",
                product_id="variant_active",
                name="Active Variant",
                format_ids=[],
                targeting_template={},
                delivery_type="standard",
                publisher_selections=whole_publisher_selections(session, "test_tenant"),
                is_dynamic_variant=True,
                parent_product_id="tmpl_001",
                expires_at=datetime.now(UTC) + timedelta(days=10),
            )
            session.add(variant)
            session.commit()

        count = archive_expired_variants("test_tenant")
        assert count == 0

    def test_already_archived_not_rearchived(self, integration_db, factory_env):
        """Already-archived variants are not re-archived."""
        factory_env.ensure_tenant("test_tenant")

        with get_db_session() as session:
            variant = Product(
                tenant_id="test_tenant",
                product_id="variant_already_archived",
                name="Already Archived",
                format_ids=[],
                targeting_template={},
                delivery_type="standard",
                publisher_selections=whole_publisher_selections(session, "test_tenant"),
                is_dynamic_variant=True,
                parent_product_id="tmpl_001",
                expires_at=datetime.now(UTC) - timedelta(days=5),
                archived_at=datetime.now(UTC) - timedelta(days=1),
            )
            session.add(variant)
            session.commit()

        count = archive_expired_variants("test_tenant")
        assert count == 0

    def test_tenant_filter_scoping(self, integration_db, factory_env):
        """archive_expired_variants only affects specified tenant."""
        factory_env.ensure_tenant("tenant_a")
        factory_env.ensure_tenant("tenant_b")

        with get_db_session() as session:
            for tid in ["tenant_a", "tenant_b"]:
                variant = Product(
                    tenant_id=tid,
                    product_id=f"variant_expired_{tid}",
                    name=f"Expired in {tid}",
                    format_ids=[],
                    targeting_template={},
                    delivery_type="standard",
                    publisher_selections=whole_publisher_selections(session, tid),
                    is_dynamic_variant=True,
                    parent_product_id="tmpl_001",
                    expires_at=datetime.now(UTC) - timedelta(days=1),
                )
                session.add(variant)
            session.commit()

        count = archive_expired_variants("tenant_a")
        assert count == 1

        # tenant_b variant should still be unarchived
        with get_db_session() as session:
            from sqlalchemy import select

            b_variant = session.scalars(select(Product).filter_by(product_id="variant_expired_tenant_b")).first()
            assert b_variant.archived_at is None

    def test_no_tenant_archives_all(self, integration_db, factory_env):
        """archive_expired_variants(tenant_id=None) archives all tenants."""
        factory_env.ensure_tenant("tenant_x")
        factory_env.ensure_tenant("tenant_y")

        with get_db_session() as session:
            for tid in ["tenant_x", "tenant_y"]:
                variant = Product(
                    tenant_id=tid,
                    product_id=f"variant_all_{tid}",
                    name=f"Expired in {tid}",
                    format_ids=[],
                    targeting_template={},
                    delivery_type="standard",
                    publisher_selections=whole_publisher_selections(session, tid),
                    is_dynamic_variant=True,
                    parent_product_id="tmpl_001",
                    expires_at=datetime.now(UTC) - timedelta(days=1),
                )
                session.add(variant)
            session.commit()

        count = archive_expired_variants(tenant_id=None)
        assert count == 2
