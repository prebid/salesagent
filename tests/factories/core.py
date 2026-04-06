"""Factory_boy factories for core tenant-related models.

Factories: TenantFactory, CurrencyLimitFactory, PropertyTagFactory, PublisherPartnerFactory
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import factory
from factory import LazyAttribute, LazyFunction, RelatedFactory, Sequence, SubFactory

from src.core.database.models import (
    AdapterConfig,
    AuthorizedProperty,
    CurrencyLimit,
    PropertyTag,
    PublisherPartner,
    Tenant,
)

_now = lambda: datetime.now(UTC)  # noqa: E731


class TenantFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Tenant
        sqlalchemy_session = None  # Bound dynamically by IntegrationEnv
        sqlalchemy_session_persistence = "commit"

    tenant_id = Sequence(lambda n: f"tenant_{n:04d}")
    name = LazyAttribute(lambda o: f"Test Publisher {o.tenant_id}")
    subdomain = LazyAttribute(lambda o: f"pub-{o.tenant_id}")
    is_active = True
    billing_plan = "standard"
    ad_server = "mock"
    authorized_emails = factory.LazyFunction(lambda: ["test@example.com"])
    authorized_domains = factory.LazyFunction(lambda: ["example.com"])
    # Explicit timestamps — Docker DB may lack server_default from migrations
    created_at = LazyFunction(_now)
    updated_at = LazyFunction(_now)

    @classmethod
    def make_tenant(cls, tenant_id: str = "test_tenant", **overrides: Any) -> dict[str, Any]:
        """Build a tenant dict without DB persistence.

        Uses same defaults as TenantFactory fields.
        Pass **overrides for domain fields (approval_mode, gemini_api_key, etc).
        """
        subdomain = f"pub-{tenant_id}".replace("_", "-")
        tenant: dict[str, Any] = {
            "tenant_id": tenant_id,
            "name": f"Test Publisher {tenant_id}",
            "subdomain": subdomain,
            "ad_server": "mock",
        }
        tenant.update(overrides)
        return tenant

    # Auto-create required CurrencyLimit (USD) for budget validation
    currency_usd = RelatedFactory(
        "tests.factories.core.CurrencyLimitFactory",
        factory_related_name="tenant",
    )


class CurrencyLimitFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = CurrencyLimit
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    currency_code = "USD"
    min_package_budget = Decimal("100.00")
    created_at = LazyFunction(_now)
    updated_at = LazyFunction(_now)


class PublisherPartnerFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = PublisherPartner
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    publisher_domain = Sequence(lambda n: f"publisher-{n:04d}.com")
    display_name = LazyAttribute(lambda o: f"Publisher {o.publisher_domain}")
    is_verified = True
    sync_status = "success"
    created_at = LazyFunction(_now)
    updated_at = LazyFunction(_now)


class AdapterConfigFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = AdapterConfig
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    adapter_type = "mock"


def set_adapter_test_behavior(env: Any, tenant_id: str, **behavior: Any) -> None:
    """Write test_behavior into AdapterConfig.config_json for E2E adapter control.

    The Docker-hosted mock adapter reads config_json.test_behavior before each
    operation (create/update). This lets BDD Given steps inject error simulation
    and other behaviors that cross the process boundary.

    Also ensures mock_manual_approval_required is set when manual_approval_required
    is specified in behavior, so the production adapter_helpers.py picks it up.

    Args:
        env: The harness IntegrationEnv instance (has _session).
        tenant_id: The tenant to configure.
        **behavior: Keys like manual_approval_required, fail_on_create,
            fail_on_update, error_message.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import attributes

    session = env._session
    assert session is not None, "No DB session — env must be entered"

    row = session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first()

    if row is None:
        row = AdapterConfig(tenant_id=tenant_id, adapter_type="mock")
        session.add(row)

    # Merge test_behavior into config_json
    config_json = dict(row.config_json or {})
    config_json["test_behavior"] = behavior
    row.config_json = config_json
    attributes.flag_modified(row, "config_json")

    # Mirror manual_approval_required to the dedicated column so
    # adapter_helpers.py (which reads mock_manual_approval_required) picks it up.
    if "manual_approval_required" in behavior:
        row.mock_manual_approval_required = behavior["manual_approval_required"]

    session.commit()


class AuthorizedPropertyFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = AuthorizedProperty
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    property_id = Sequence(lambda n: f"prop_{n:04d}")
    property_type = "website"
    name = LazyAttribute(lambda o: f"Test Property {o.property_id}")
    identifiers = LazyAttribute(lambda o: [{"type": "domain", "value": o.publisher_domain}])
    tags = factory.LazyFunction(lambda: ["all_inventory"])
    publisher_domain = "testpublisher.example.com"
    verification_status = "verified"
    created_at = LazyFunction(_now)
    updated_at = LazyFunction(_now)


class PropertyTagFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = PropertyTag
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    tag_id = Sequence(lambda n: f"tag_{n:04d}")
    name = LazyAttribute(lambda o: f"Tag {o.tag_id}")
    description = LazyAttribute(lambda o: f"Description for {o.name}")
    created_at = LazyFunction(_now)
    updated_at = LazyFunction(_now)
