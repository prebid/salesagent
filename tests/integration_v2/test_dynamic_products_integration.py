"""Integration tests for dynamic product variant generation.

Tests the full pipeline in src/services/dynamic_products.py:
- Pure functions: activation key extraction, variant ID generation, name/description customization
- DB-dependent: variant creation from templates, archival of expired variants
- Registry-mocked: generate_variants_for_brief with mock signals

The signals agent registry is mocked at the registry.get_signals() level,
letting all dynamic_products.py code run while isolating HTTP concerns.

beads: salesagent-bsrb
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.services.dynamic_products import (
    archive_expired_variants,
    create_variant_from_template,
    customize_description,
    customize_name,
    extract_activation_key,
    generate_variant_id,
    generate_variants_for_brief,
    generate_variants_from_signals,
)
from tests.factories import ProductFactory, TenantFactory
from tests.harness._base import IntegrationEnv

# ---------------------------------------------------------------------------
# Pure function tests (no DB, no mocking)
# ---------------------------------------------------------------------------


class TestExtractActivationKey:
    """extract_activation_key parses activation keys from signal deployments."""

    def test_matches_our_agent_url(self):
        """Returns activation key from deployment matching our URL."""
        signal = {
            "deployments": [
                {
                    "destination": {"agent_url": "https://other.example.com/mcp"},
                    "is_live": True,
                    "activation_key": {"type": "key_value", "key": "wrong", "value": "wrong"},
                },
                {
                    "destination": {"agent_url": "https://our.example.com/mcp"},
                    "is_live": True,
                    "activation_key": {"type": "key_value", "key": "audience", "value": "premium"},
                },
            ]
        }
        result = extract_activation_key(signal, "https://our.example.com/mcp")
        assert result == {"type": "key_value", "key": "audience", "value": "premium"}

    def test_segment_id_type(self):
        """Returns segment_id activation key."""
        signal = {
            "deployments": [
                {
                    "destination": {"agent_url": "https://our.example.com/mcp"},
                    "is_live": True,
                    "activation_key": {"type": "segment_id", "segment_id": "seg_123"},
                },
            ]
        }
        result = extract_activation_key(signal, "https://our.example.com/mcp")
        assert result == {"type": "segment_id", "segment_id": "seg_123"}

    def test_fallback_to_first_live_deployment(self):
        """When no URL match, uses first live deployment."""
        signal = {
            "deployments": [
                {"is_live": False, "activation_key": {"type": "key_value", "key": "k", "value": "v"}},
                {
                    "is_live": True,
                    "activation_key": {"type": "key_value", "key": "fallback", "value": "yes"},
                },
            ]
        }
        result = extract_activation_key(signal, "https://nonexistent.com/mcp")
        assert result == {"type": "key_value", "key": "fallback", "value": "yes"}

    def test_no_url_provided_uses_first_live(self):
        """When our_agent_url is None, uses first live deployment."""
        signal = {
            "deployments": [
                {
                    "destination": {"agent_url": "https://any.com/mcp"},
                    "is_live": True,
                    "activation_key": {"type": "key_value", "key": "k1", "value": "v1"},
                },
            ]
        }
        result = extract_activation_key(signal, None)
        assert result == {"type": "key_value", "key": "k1", "value": "v1"}

    def test_no_deployments_returns_none(self):
        """Returns None when signal has no deployments."""
        assert extract_activation_key({}, "https://our.com/mcp") is None
        assert extract_activation_key({"deployments": []}, "https://our.com/mcp") is None

    def test_not_live_returns_none(self):
        """Returns None when matching deployment is not live."""
        signal = {
            "deployments": [
                {
                    "destination": {"agent_url": "https://our.com/mcp"},
                    "is_live": False,
                    "activation_key": {"type": "key_value", "key": "k", "value": "v"},
                },
            ]
        }
        assert extract_activation_key(signal, "https://our.com/mcp") is None

    def test_malformed_key_value_missing_value(self):
        """Returns None when key_value type is missing required fields."""
        signal = {
            "deployments": [
                {
                    "destination": {"agent_url": "https://our.com/mcp"},
                    "is_live": True,
                    "activation_key": {"type": "key_value", "key": "audience"},
                    # missing "value"
                },
            ]
        }
        assert extract_activation_key(signal, "https://our.com/mcp") is None


class TestGenerateVariantId:
    """generate_variant_id creates deterministic IDs from template + activation key."""

    def test_key_value_deterministic(self):
        """Same inputs produce same variant ID."""
        ak = {"type": "key_value", "key": "audience", "value": "premium"}
        id1 = generate_variant_id("template_1", ak)
        id2 = generate_variant_id("template_1", ak)
        assert id1 == id2
        assert id1.startswith("template_1__variant_")

    def test_segment_id_deterministic(self):
        """Segment ID type produces consistent hash."""
        ak = {"type": "segment_id", "segment_id": "seg_abc"}
        variant_id = generate_variant_id("tmpl", ak)
        assert variant_id.startswith("tmpl__variant_")
        assert len(variant_id) > len("tmpl__variant_")

    def test_different_keys_produce_different_ids(self):
        """Different activation keys produce different variant IDs."""
        ak1 = {"type": "key_value", "key": "audience", "value": "premium"}
        ak2 = {"type": "key_value", "key": "audience", "value": "budget"}
        assert generate_variant_id("tmpl", ak1) != generate_variant_id("tmpl", ak2)


class TestCustomizeName:
    """customize_name applies template macros or default patterns."""

    def test_default_with_signal_name(self):
        """Default pattern: template_name - signal_name."""
        signal = {"name": "Premium Audience"}
        ak = {"type": "key_value", "key": "k", "value": "v"}
        result = customize_name("Display 300x250", signal, ak)
        assert result == "Display 300x250 - Premium Audience"

    def test_fallback_key_value(self):
        """When no signal name, uses key=value format."""
        signal = {}
        ak = {"type": "key_value", "key": "audience", "value": "premium"}
        result = customize_name("Display", signal, ak)
        assert result == "Display - audience=premium"

    def test_fallback_segment_id(self):
        """When no signal name with segment_id type."""
        signal = {}
        ak = {"type": "segment_id", "segment_id": "seg_123"}
        result = customize_name("Display", signal, ak)
        assert result == "Display - Segment seg_123"

    def test_custom_template_with_macros(self):
        """Custom template string with macro substitution."""
        signal = {"name": "Auto Intenders", "description": "Car buyers"}
        ak = {"type": "key_value", "key": "aud", "value": "auto"}
        result = customize_name(
            "Base Product",
            signal,
            ak,
            variant_name_template="{{name}} ({{signal.name}})",
        )
        assert result == "Base Product (Auto Intenders)"

    def test_no_signal_name_no_ak_returns_template(self):
        """Returns template name when no signal info available."""
        signal = {}
        ak = {"type": "unknown"}
        result = customize_name("Base Product", signal, ak)
        assert result == "Base Product"


class TestCustomizeDescription:
    """customize_description applies template macros or default patterns."""

    def test_appends_signal_description(self):
        """Default: appends signal description to template description."""
        signal = {"description": "High-intent auto buyers"}
        ak = {}
        result = customize_description("Base description", signal, ak, "any brief")
        assert result == "Base description\n\nHigh-intent auto buyers"

    def test_no_template_description_uses_signal(self):
        """When template has no description, uses signal description."""
        signal = {"description": "Signal desc"}
        result = customize_description(None, signal, {}, "brief")
        assert result == "Signal desc"

    def test_no_descriptions_returns_none(self):
        """Returns None when both template and signal have no description."""
        result = customize_description(None, {}, {}, "brief")
        assert result is None

    def test_template_description_no_signal_passes_through(self):
        """Template description returned as-is when no signal description."""
        result = customize_description("Original desc", {}, {}, "brief")
        assert result == "Original desc"

    def test_custom_template_with_macros(self):
        """Custom template with macro substitution."""
        signal = {"name": "Premium", "data_provider": "DataCo"}
        ak = {"key": "aud", "value": "premium"}
        result = customize_description(
            "Base",
            signal,
            ak,
            "brief",
            variant_description_template="{{description}} by {{signal.data_provider}}",
        )
        assert result == "Base by DataCo"


# ---------------------------------------------------------------------------
# Integration tests (real DB + mock registry)
# ---------------------------------------------------------------------------

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _make_signal(
    segment_id: str = "seg_1",
    name: str = "Test Signal",
    our_url: str = "https://our.example.com/mcp",
    key_type: str = "key_value",
    key: str = "audience",
    value: str = "premium",
) -> dict:
    """Create a realistic signal dict matching signals agent response format."""
    activation_key = (
        {"type": key_type, "key": key, "value": value}
        if key_type == "key_value"
        else {"type": key_type, "segment_id": value}
    )
    return {
        "signal_agent_segment_id": segment_id,
        "name": name,
        "description": f"Description for {name}",
        "data_provider": "TestProvider",
        "coverage_percentage": 85.0,
        "deployments": [
            {
                "type": "platform",
                "platform": "web",
                "is_live": True,
                "deployed_at": "2025-01-01T00:00:00Z",
                "destination": {"agent_url": our_url},
                "activation_key": activation_key,
            }
        ],
    }


def _make_template(tenant, product_id: str = "template_1"):
    """Create a dynamic product template in the DB via factory."""
    return ProductFactory(
        tenant=tenant,
        product_id=product_id,
        name="Dynamic Template",
        description="Template for variants",
        delivery_measurement={"provider": "publisher", "notes": "Test"},
        is_dynamic=True,
        signals_agent_ids=["agent_1"],
        max_signals=10,
        variant_ttl_days=30,
    )


@pytest.fixture
def factory_env(integration_db):
    """Bind factories to a DB session for integration tests."""
    with IntegrationEnv() as env:
        yield env


class TestGenerateVariantsFromSignals:
    """generate_variants_from_signals creates Product variants in the DB."""

    def test_creates_new_variant(self, factory_env):
        """Creates a new variant Product from template + signal."""
        tenant = TenantFactory(tenant_id="test-gvfs-new", subdomain="gvfs-new")
        template = _make_template(tenant)
        signals = [_make_signal(segment_id="seg_1", name="Premium")]

        variants = generate_variants_from_signals(
            factory_env._session, template, signals, "test brief", "https://our.example.com/mcp"
        )

        assert len(variants) == 1
        v = variants[0]
        assert v.is_dynamic_variant is True
        assert v.parent_product_id == "template_1"
        assert "Premium" in v.name
        assert v.activation_key is not None
        assert v.signal_metadata["signal_agent_segment_id"] == "seg_1"
        factory_env._session.commit()

    def test_updates_existing_variant(self, factory_env):
        """Re-generating from same signal updates expiration, not duplicates."""
        tenant = TenantFactory(tenant_id="test-gvfs-update", subdomain="gvfs-update")
        template = _make_template(tenant)
        signals = [_make_signal()]

        # First call — creates
        v1 = generate_variants_from_signals(
            factory_env._session, template, signals, "brief", "https://our.example.com/mcp"
        )
        factory_env._session.flush()
        first_id = v1[0].product_id
        first_sync = v1[0].last_synced_at

        # Second call — updates
        v2 = generate_variants_from_signals(
            factory_env._session, template, signals, "brief", "https://our.example.com/mcp"
        )

        assert len(v2) == 1
        assert v2[0].product_id == first_id
        assert v2[0].last_synced_at >= first_sync
        factory_env._session.commit()

    def test_skips_signal_without_activation_key(self, factory_env):
        """Signal with no matching deployment is skipped."""
        tenant = TenantFactory(tenant_id="test-gvfs-skip", subdomain="gvfs-skip")
        template = _make_template(tenant)
        # Signal with no deployments -> no activation key
        signals = [{"signal_agent_segment_id": "seg_bad", "deployments": []}]

        variants = generate_variants_from_signals(
            factory_env._session, template, signals, "brief", "https://our.example.com/mcp"
        )
        factory_env._session.commit()

        assert len(variants) == 0

    def test_multiple_signals_create_multiple_variants(self, factory_env):
        """Each signal with valid activation key creates a separate variant."""
        tenant = TenantFactory(tenant_id="test-gvfs-multi", subdomain="gvfs-multi")
        template = _make_template(tenant)
        signals = [
            _make_signal(segment_id="seg_1", name="Signal A", value="a"),
            _make_signal(segment_id="seg_2", name="Signal B", value="b"),
        ]

        variants = generate_variants_from_signals(
            factory_env._session, template, signals, "brief", "https://our.example.com/mcp"
        )

        assert len(variants) == 2
        names = {v.name for v in variants}
        assert any("Signal A" in n for n in names)
        assert any("Signal B" in n for n in names)
        factory_env._session.commit()


class TestGenerateVariantsForBrief:
    """generate_variants_for_brief is the top-level pipeline entry point."""

    @pytest.mark.asyncio
    async def test_no_templates_returns_empty(self, factory_env):
        """Returns empty list when no dynamic templates exist."""
        TenantFactory(tenant_id="test-gvfb-none", subdomain="gvfb-none")
        factory_env._commit_factory_data()

        result = await generate_variants_for_brief("test-gvfb-none", "test brief")
        assert result == []

    @pytest.mark.asyncio
    async def test_templates_with_signals_creates_variants(self, factory_env):
        """Full pipeline: templates in DB + mock signals -> variants created."""
        tenant = TenantFactory(tenant_id="test-gvfb-full", subdomain="gvfb-full")
        _make_template(tenant)
        factory_env._commit_factory_data()

        mock_signals = [_make_signal(name="Auto Intenders")]

        mock_registry = AsyncMock()
        mock_registry.get_signals = AsyncMock(return_value=mock_signals)

        with patch(
            "src.services.dynamic_products.get_signals_agent_registry",
            return_value=mock_registry,
        ):
            result = await generate_variants_for_brief(
                "test-gvfb-full", "automotive ads", "https://our.example.com/mcp"
            )

        assert len(result) >= 1
        # Variants are detached from session -- verify via DB read-back
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Product as ProductModel

        with get_db_session() as read_session:
            stmt = select(ProductModel).filter_by(tenant_id="test-gvfb-full", is_dynamic_variant=True)
            variants = read_session.scalars(stmt).all()
            assert len(variants) >= 1
            assert any("Auto Intenders" in v.name for v in variants)

    @pytest.mark.asyncio
    async def test_registry_error_returns_empty(self, factory_env):
        """Pipeline catches registry errors and returns empty list."""
        tenant = TenantFactory(tenant_id="test-gvfb-err", subdomain="gvfb-err")
        _make_template(tenant)
        factory_env._commit_factory_data()

        mock_registry = AsyncMock()
        mock_registry.get_signals = AsyncMock(side_effect=RuntimeError("Connection refused"))

        with patch(
            "src.services.dynamic_products.get_signals_agent_registry",
            return_value=mock_registry,
        ):
            result = await generate_variants_for_brief("test-gvfb-err", "brief")

        assert result == []

    @pytest.mark.asyncio
    async def test_empty_signals_returns_empty(self, factory_env):
        """Pipeline returns empty list when signals agent returns no signals."""
        tenant = TenantFactory(tenant_id="test-gvfb-empty", subdomain="gvfb-empty")
        _make_template(tenant)
        factory_env._commit_factory_data()

        mock_registry = AsyncMock()
        mock_registry.get_signals = AsyncMock(return_value=[])

        with patch(
            "src.services.dynamic_products.get_signals_agent_registry",
            return_value=mock_registry,
        ):
            result = await generate_variants_for_brief("test-gvfb-empty", "brief")

        assert result == []


class TestArchiveExpiredVariants:
    """archive_expired_variants marks expired variant products as archived."""

    def test_archives_expired_variant(self, factory_env):
        """Expired variant gets archived_at set."""
        tenant = TenantFactory(tenant_id="test-archive-exp", subdomain="archive-exp")
        ProductFactory(
            tenant=tenant,
            product_id="expired_variant",
            name="Expired Variant",
            is_dynamic_variant=True,
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        factory_env._commit_factory_data()

        count = archive_expired_variants("test-archive-exp")
        assert count == 1

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Product as ProductModel

        with get_db_session() as read_session:
            v = read_session.scalars(
                select(ProductModel).filter_by(product_id="expired_variant", tenant_id="test-archive-exp")
            ).first()
            assert v.archived_at is not None

    def test_keeps_non_expired_variant(self, factory_env):
        """Non-expired variant is not archived."""
        tenant = TenantFactory(tenant_id="test-archive-keep", subdomain="archive-keep")
        ProductFactory(
            tenant=tenant,
            product_id="active_variant",
            name="Active Variant",
            is_dynamic_variant=True,
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        factory_env._commit_factory_data()

        count = archive_expired_variants("test-archive-keep")
        assert count == 0

    def test_tenant_filter(self, factory_env):
        """Only archives variants for the specified tenant."""
        tenant_a = TenantFactory(tenant_id="test-archive-a", subdomain="archive-a")
        tenant_b = TenantFactory(tenant_id="test-archive-b", subdomain="archive-b")
        for tenant in [tenant_a, tenant_b]:
            ProductFactory(
                tenant=tenant,
                product_id=f"expired_{tenant.subdomain}",
                name=f"Expired {tenant.subdomain}",
                is_dynamic_variant=True,
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )
        factory_env._commit_factory_data()

        count = archive_expired_variants("test-archive-a")
        assert count == 1


class TestCreateVariantFromTemplate:
    """create_variant_from_template builds a Product with correct fields."""

    def test_creates_variant_with_correct_fields(self, factory_env):
        """Variant inherits template fields and gets variant-specific metadata."""
        tenant = TenantFactory(tenant_id="test-cvft-fields", subdomain="cvft-fields")
        template = _make_template(tenant)

        signal = _make_signal(name="Premium Users")
        ak = {"type": "key_value", "key": "audience", "value": "premium"}

        variant = create_variant_from_template(template, signal, ak, "template_1__variant_abc", "video ads")

        assert variant.product_id == "template_1__variant_abc"
        assert variant.tenant_id == "test-cvft-fields"
        assert variant.is_dynamic is False
        assert variant.is_dynamic_variant is True
        assert variant.parent_product_id == "template_1"
        assert variant.activation_key == ak
        assert variant.signal_metadata["name"] == "Premium Users"
        assert variant.expires_at is not None
