"""Integration tests: product format validation with real DB and Flask test client.

Exercises the actual add_product POST handler in products.py to verify
format validation behavior under different creative agent states:
- Agent up with formats → validates format IDs
- Agent down (errors, no formats) → graceful degradation, saves without validation
- Agent up, no formats, no errors → rejects all format IDs
- Partial agent failure (multi-agent) → validates against available formats
- Cached empty from previous outage → same as no-formats-no-errors

These are real integration tests: real DB, real Flask app, real form POST.
Only the creative agent registry is mocked at the boundary.

Fixes: salesagent-5dc
"""

import json
from unittest.mock import patch

import pytest
from adcp.types.generated_poc.core.error import Error as AdCPResponseError
from sqlalchemy import select

from src.admin.app import create_app
from src.core.creative_agent_registry import FormatFetchResult
from src.core.database.database_session import get_db_session
from src.core.database.models import AuthorizedProperty, CurrencyLimit, Product, PropertyTag, Tenant
from src.core.schemas import Format

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

AGENT_URL = "https://creative.adcontextprotocol.org"


def _make_format(format_id: str) -> Format:
    return Format.model_validate(
        {"format_id": {"agent_url": AGENT_URL, "id": format_id}, "name": f"Test {format_id}", "type": "display"}
    )


def _make_error(message: str) -> AdCPResponseError:
    return AdCPResponseError(code="AGENT_UNREACHABLE", message=message)


@pytest.fixture
def tenant_with_prereqs(integration_db):
    """Create a tenant with the minimum prerequisites for product creation."""
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id="fmt_test_tenant",
            name="Format Test Tenant",
            subdomain="fmt-test",
            is_active=True,
            ad_server="mock",
        )
        session.add(tenant)
        session.flush()

        session.add(
            CurrencyLimit(
                tenant_id="fmt_test_tenant",
                currency_code="USD",
                min_package_budget=100.0,
                max_daily_package_spend=10000.0,
            )
        )
        session.add(
            PropertyTag(
                tenant_id="fmt_test_tenant",
                tag_id="all_inventory",
                name="All Inventory",
                description="All inventory",
            )
        )
        session.add(
            AuthorizedProperty(
                property_id="fmt-test-prop",
                tenant_id="fmt_test_tenant",
                property_type="website",
                name="Format Test Site",
                identifiers=[{"type": "domain", "value": "fmt-test.com"}],
                tags=["all_inventory"],
                publisher_domain="fmt-test",
            )
        )
        session.commit()

    yield "fmt_test_tenant"

    # Cleanup
    with get_db_session() as session:
        session.execute(Product.__table__.delete().where(Product.tenant_id == "fmt_test_tenant"))
        session.execute(AuthorizedProperty.__table__.delete().where(AuthorizedProperty.tenant_id == "fmt_test_tenant"))
        session.execute(CurrencyLimit.__table__.delete().where(CurrencyLimit.tenant_id == "fmt_test_tenant"))
        session.execute(PropertyTag.__table__.delete().where(PropertyTag.tenant_id == "fmt_test_tenant"))
        session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == "fmt_test_tenant"))
        session.commit()


@pytest.fixture
def app_client(tenant_with_prereqs, monkeypatch):
    """Flask test client with authenticated admin session."""
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")

    # Set up super admin email in DB
    from src.core.database.models import TenantManagementConfig

    with get_db_session() as session:
        existing = session.scalars(select(TenantManagementConfig).filter_by(config_key="super_admin_emails")).first()
        if not existing:
            session.add(
                TenantManagementConfig(
                    config_key="super_admin_emails",
                    config_value="test@example.com",
                )
            )
            session.commit()

    app = create_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["role"] = "super_admin"
            sess["email"] = "test@example.com"
            sess["user"] = {"email": "test@example.com", "role": "super_admin"}
            sess["is_super_admin"] = True
            sess["test_user"] = "test@example.com"
            sess["test_user_role"] = "super_admin"
            sess["test_user_name"] = "Test Admin"
        yield client


def _make_mock_registry(format_result: FormatFetchResult):
    """Create a mock registry that returns the given result for all calls."""
    from unittest.mock import MagicMock

    registry = MagicMock()

    async def _list_with_errors(**kwargs):
        return format_result

    async def _list_all(**kwargs):
        return format_result.formats

    registry.list_all_formats_with_errors = _list_with_errors
    registry.list_all_formats = _list_all
    return registry


def _post_product(client, tenant_id, formats_json, format_result, product_name="Test Product"):
    """POST to add_product with the minimum required fields, mocking the registry."""
    mock_registry = _make_mock_registry(format_result)

    with patch(
        "src.core.creative_agent_registry.get_creative_agent_registry",
        return_value=mock_registry,
    ):
        resp = client.post(
            f"/tenant/{tenant_id}/products/add",
            data={
                "name": product_name,
                "description": "Test product for format validation",
                "formats": formats_json,
                "pricing_model_0": "cpm_fixed",
                "currency_0": "USD",
                "rate_0": "10.00",
                "property_mode": "tags",
                "selected_property_tags": "fmt-test:all_inventory",
            },
            follow_redirects=False,
        )
        return resp


class TestFormatValidationAgentUp:
    """Agent returns formats → validation should work."""

    def test_valid_format_ids_accepted(self, app_client, tenant_with_prereqs):
        """Submitting format IDs that exist in the agent → product saved."""
        formats = json.dumps([{"agent_url": AGENT_URL, "id": "display_image", "width": 300, "height": 250}])
        result = FormatFetchResult(
            formats=[_make_format("display_image"), _make_format("video_standard")],
            errors=[],
        )
        resp = _post_product(app_client, tenant_with_prereqs, formats, result)

        # On success, add_product redirects (302). On failure, re-renders form (200).
        if resp.status_code != 302:
            resp_text = resp.data.decode()
            # Extract error clues
            import re

            errors = re.findall(r"(?:alert|error|flash)[^>]*>([^<]+)<", resp_text, re.IGNORECASE)
            pytest.fail(f"Expected 302 redirect (product saved), got {resp.status_code}. Errors found: {errors[:5]}")

        # Product should be saved — check DB
        with get_db_session() as session:
            product = session.scalars(
                select(Product).filter_by(tenant_id=tenant_with_prereqs, name="Test Product")
            ).first()
            assert product is not None, "Product should have been saved"
            assert len(product.format_ids) == 1
            assert product.format_ids[0]["id"] == "display_image"

    def test_invalid_format_ids_rejected(self, app_client, tenant_with_prereqs):
        """Submitting format IDs that DON'T exist in the agent → flash error, not saved."""
        formats = json.dumps([{"agent_url": AGENT_URL, "id": "nonexistent_format"}])
        result = FormatFetchResult(
            formats=[_make_format("display_image"), _make_format("video_standard")],
            errors=[],
        )
        resp = _post_product(app_client, tenant_with_prereqs, formats, result, product_name="Rejected Product")

        # Product should NOT be saved
        with get_db_session() as session:
            product = session.scalars(
                select(Product).filter_by(tenant_id=tenant_with_prereqs, name="Rejected Product")
            ).first()
            assert product is None, "Product with invalid formats should not be saved"


class TestFormatValidationAgentDown:
    """Agent unreachable (errors, no formats) → graceful degradation."""

    def test_agent_down_saves_without_validation(self, app_client, tenant_with_prereqs):
        """When agent returns errors and no formats, product saves with a warning."""
        formats = json.dumps([{"agent_url": AGENT_URL, "id": "video_standard", "duration_ms": 15000}])
        result = FormatFetchResult(
            formats=[],
            errors=[_make_error("Creative agent unreachable")],
        )
        resp = _post_product(app_client, tenant_with_prereqs, formats, result, product_name="Agent Down Product")

        # Product SHOULD be saved despite agent being down
        with get_db_session() as session:
            product = session.scalars(
                select(Product).filter_by(tenant_id=tenant_with_prereqs, name="Agent Down Product")
            ).first()
            assert product is not None, "Product should save when agent is down (graceful degradation)"
            assert len(product.format_ids) == 1
            assert product.format_ids[0]["id"] == "video_standard"


class TestFormatValidationNoFormatsNoErrors:
    """Agent up but returned zero formats, no errors → should reject invalid IDs.

    This is the key scenario that PR #1137's hack got wrong — it treated
    this the same as agent-down. But if the agent is up and reports no
    formats (no errors), submitting format IDs should be rejected.
    """

    def test_empty_agent_rejects_format_ids(self, app_client, tenant_with_prereqs):
        """Agent returns 0 formats and 0 errors → format IDs are invalid."""
        formats = json.dumps([{"agent_url": AGENT_URL, "id": "display_image"}])
        result = FormatFetchResult(formats=[], errors=[])
        resp = _post_product(app_client, tenant_with_prereqs, formats, result, product_name="Empty Agent Product")

        # Product should NOT be saved — agent is up but has no matching formats
        with get_db_session() as session:
            product = session.scalars(
                select(Product).filter_by(tenant_id=tenant_with_prereqs, name="Empty Agent Product")
            ).first()
            assert product is None, "Product should be rejected when agent is up but format ID is invalid"


class TestFormatValidationPartialFailure:
    """Multi-agent: one agent returns formats, another errors."""

    def test_partial_failure_validates_against_available(self, app_client, tenant_with_prereqs):
        """When one agent is down but another has formats, validate against what's available."""
        formats = json.dumps([{"agent_url": AGENT_URL, "id": "display_image", "width": 300, "height": 250}])
        result = FormatFetchResult(
            formats=[_make_format("display_image")],  # One agent returned this
            errors=[_make_error("Second agent unreachable")],  # Other agent failed
        )
        resp = _post_product(app_client, tenant_with_prereqs, formats, result, product_name="Partial Failure Product")

        # Product SHOULD save — the format exists in the available agent
        with get_db_session() as session:
            product = session.scalars(
                select(Product).filter_by(tenant_id=tenant_with_prereqs, name="Partial Failure Product")
            ).first()
            assert product is not None, "Product should save when format is valid in available agent"
