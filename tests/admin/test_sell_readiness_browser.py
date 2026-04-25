"""Browser-driven admin E2E tests that complement BDD coverage."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import psycopg2
import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.admin.browser_flow_helpers import browser_page, build_admin_test_session, login_as_tenant_admin
from tests.e2e.adcp_request_builder import build_adcp_media_buy_request, get_test_date_range, parse_tool_result

pytestmark = [
    pytest.mark.admin,
    pytest.mark.e2e,
    pytest.mark.requires_server,
    pytest.mark.slow,
]

TENANT_ID = "ci-test"
TEST_BRAND = {"domain": "testbrand.com"}


def _run(coro):
    """Run async MCP helpers from synchronous browser tests."""
    return asyncio.run(coro)


async def _call_mcp_tool(
    live_server: dict[str, str],
    auth_token: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    headers = {
        "x-adcp-auth": auth_token,
        "x-adcp-tenant": TENANT_ID,
    }
    transport = StreamableHttpTransport(url=f"{live_server['mcp']}/mcp/", headers=headers)
    async with Client(transport=transport) as client:
        result = await client.call_tool(tool_name, arguments)
    return parse_tool_result(result)


def _discover_reference_product(live_server: dict[str, str], auth_token: str) -> dict[str, Any]:
    products_data = _run(
        _call_mcp_tool(
            live_server,
            auth_token,
            "get_products",
            {
                "brand": TEST_BRAND,
                "brief": "",
                "context": {"e2e": "browser_ui_reference_product"},
            },
        )
    )
    assert products_data["products"], "Expected at least one discoverable product in ci-test tenant"
    return products_data["products"][0]


def _lookup_product(live_server: dict[str, str], auth_token: str, product_id: str) -> dict[str, Any]:
    products_data = _run(
        _call_mcp_tool(
            live_server,
            auth_token,
            "get_products",
            {
                "brand": TEST_BRAND,
                "brief": "",
                "context": {"e2e": "browser_ui_verify_product"},
            },
        )
    )
    for product in products_data["products"]:
        if product["product_id"] == product_id:
            return product
    raise AssertionError(f"Product {product_id} was not discoverable through get_products")


def _create_property_selection(base_url: str, tenant_id: str) -> dict[str, str]:
    session = build_admin_test_session(base_url, tenant_id)
    suffix = uuid.uuid4().hex[:8]
    publisher_domain = f"browser-{suffix}.example.com"
    tag_id = f"ui_browser_{suffix}"

    tag_response = session.post(
        f"{base_url}/tenant/{tenant_id}/property-tags/create",
        data={
            "tag_id": tag_id,
            "name": f"Browser Tag {suffix}",
            "description": "Browser E2E property tag",
        },
        allow_redirects=False,
        timeout=20,
    )
    assert tag_response.status_code in {302, 303}, f"Property tag setup failed: {tag_response.status_code}"

    property_response = session.post(
        f"{base_url}/tenant/{tenant_id}/authorized-properties/create",
        data=[
            ("property_type", "website"),
            ("name", f"Browser Property {suffix}"),
            ("publisher_domain", publisher_domain),
            ("identifier_type_0", "domain"),
            ("identifier_value_0", publisher_domain),
            ("tags", tag_id),
        ],
        allow_redirects=False,
        timeout=20,
    )
    assert property_response.status_code in {302, 303}, (
        f"Authorized property setup failed: {property_response.status_code}"
    )

    return {
        "publisher_domain": publisher_domain,
        "tag_id": tag_id,
        "selection_value": f"{publisher_domain}:{tag_id}",
    }


def _create_product_via_form(
    base_url: str,
    tenant_id: str,
    format_ref: dict[str, Any],
    property_selection: dict[str, str],
    product_id: str,
    product_name: str,
) -> None:
    session = build_admin_test_session(base_url, tenant_id)
    response = session.post(
        f"{base_url}/tenant/{tenant_id}/products/add",
        data={
            "product_id": product_id,
            "name": product_name,
            "description": "Seed product for browser edit flow",
            "delivery_type": "non_guaranteed",
            "formats": json.dumps([format_ref]),
            "pricing_model_0": "cpm_fixed",
            "currency_0": "USD",
            "rate_0": "10.00",
            "min_spend_0": "100.00",
            "property_mode": "tags",
            "selected_property_tags": property_selection["selection_value"],
            "delivery_measurement_provider": "Nielsen",
        },
        allow_redirects=False,
        timeout=20,
    )
    assert response.status_code in {302, 303}, f"Seed product creation failed: {response.status_code}"


def _set_mock_manual_approval(live_server: dict[str, str], required: bool) -> None:
    with psycopg2.connect(live_server["postgres"]) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT tenant_id FROM tenants WHERE subdomain = %s", (TENANT_ID,))
            tenant_row = cursor.fetchone()
            assert tenant_row, f"Tenant {TENANT_ID} not found"
            cursor.execute(
                """
                INSERT INTO adapter_config (tenant_id, adapter_type, mock_manual_approval_required)
                VALUES (%s, 'mock', %s)
                ON CONFLICT (tenant_id)
                DO UPDATE SET adapter_type = 'mock', mock_manual_approval_required = EXCLUDED.mock_manual_approval_required
                """,
                (tenant_row[0], required),
            )


def _get_principal_record(live_server: dict[str, str], principal_id: str) -> tuple[str, str, str] | None:
    with psycopg2.connect(live_server["postgres"]) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT principal_id, name, access_token
                FROM principals
                WHERE tenant_id = %s AND principal_id = %s
                """,
                (TENANT_ID, principal_id),
            )
            return cursor.fetchone()


def _get_media_buy_state(live_server: dict[str, str], media_buy_id: str) -> dict[str, str | None]:
    with psycopg2.connect(live_server["postgres"]) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT mb.status, ws.status, ws.error_message
                FROM media_buys mb
                LEFT JOIN object_workflow_mapping owm
                    ON owm.object_type = 'media_buy' AND owm.object_id = mb.media_buy_id
                LEFT JOIN workflow_steps ws
                    ON ws.step_id = owm.step_id
                WHERE mb.media_buy_id = %s
                ORDER BY ws.created_at DESC NULLS LAST
                LIMIT 1
                """,
                (media_buy_id,),
            )
            row = cursor.fetchone()
    assert row, f"Media buy {media_buy_id} not found"
    return {
        "media_buy_status": row[0],
        "workflow_status": row[1],
        "workflow_error": row[2],
    }


def _create_pending_media_buy(live_server: dict[str, str], auth_token: str) -> str:
    _set_mock_manual_approval(live_server, True)
    product = _discover_reference_product(live_server, auth_token)
    pricing_options = product.get("pricing_options", [])
    assert pricing_options, f"Product {product['product_id']} must expose pricing_options"

    start_time, end_time = get_test_date_range(days_from_now=1, duration_days=7)
    create_result = _run(
        _call_mcp_tool(
            live_server,
            auth_token,
            "create_media_buy",
            build_adcp_media_buy_request(
                product_ids=[product["product_id"]],
                total_budget=2500.0,
                start_time=start_time,
                end_time=end_time,
                brand=TEST_BRAND,
                pricing_option_id=pricing_options[0]["pricing_option_id"],
                context={"e2e": "browser_media_buy_review"},
            ),
        )
    )
    media_buy_id = create_result["media_buy_id"]
    state = _get_media_buy_state(live_server, media_buy_id)
    assert state["media_buy_status"] == "pending_approval", (
        f"Expected pending approval media buy, got {state['media_buy_status']}"
    )
    return media_buy_id


def _fill_product_form(
    page,
    *,
    product_id: str,
    product_name: str,
    description: str,
    delivery_measurement_provider: str,
    format_ref: dict[str, Any],
    property_selection: dict[str, str],
) -> None:
    if page.locator("#product_id").count():
        page.locator("#product_id").fill(product_id)

    page.locator("#name").fill(product_name)
    page.locator("#description").fill(description)
    page.select_option("#delivery_type", "non_guaranteed")
    page.wait_for_selector("select[name='pricing_model_0']")
    page.select_option("select[name='pricing_model_0']", "cpm_fixed")
    page.select_option("select[name='currency_0']", "USD")
    page.locator("input[name='rate_0']").fill("12.50")
    page.locator("input[name='min_spend_0']").fill("100.00")
    page.locator("#delivery_measurement_provider").fill(delivery_measurement_provider)
    page.evaluate(
        """
        (formats) => {
            document.getElementById('formats-data').value = JSON.stringify(formats);
        }
        """,
        [format_ref],
    )
    page.locator(f"input[name='selected_property_tags'][value='{property_selection['selection_value']}']").check()


def test_add_product_browser_flow(docker_services_e2e, live_server, test_auth_token):
    """Create a product in the browser and verify downstream discoverability."""
    reference_product = _discover_reference_product(live_server, test_auth_token)
    format_ref = reference_product["format_ids"][0]
    property_selection = _create_property_selection(live_server["admin"], TENANT_ID)
    product_id = f"browser-prod-{uuid.uuid4().hex[:8]}"
    product_name = f"Browser Product {uuid.uuid4().hex[:6]}"

    with browser_page(live_server["admin"]) as page:
        login_as_tenant_admin(page, TENANT_ID)
        page.goto(f"/tenant/{TENANT_ID}/products/add", wait_until="networkidle")
        _fill_product_form(
            page,
            product_id=product_id,
            product_name=product_name,
            description="Created via browser UI E2E",
            delivery_measurement_provider="comScore",
            format_ref=format_ref,
            property_selection=property_selection,
        )
        page.get_by_role("button", name="Create Product").click()
        page.wait_for_url(f"**/tenant/{TENANT_ID}/products", wait_until="networkidle")
        page.wait_for_selector(f"text={product_id}")

    discovered = _lookup_product(live_server, test_auth_token, product_id)
    assert discovered["name"] == product_name
    assert discovered["delivery_measurement"]["provider"] == "comScore"


def test_edit_product_browser_flow(docker_services_e2e, live_server, test_auth_token):
    """Edit a product in the browser and verify downstream discoverability changes."""
    reference_product = _discover_reference_product(live_server, test_auth_token)
    format_ref = reference_product["format_ids"][0]
    property_selection = _create_property_selection(live_server["admin"], TENANT_ID)
    product_id = f"browser-edit-{uuid.uuid4().hex[:8]}"
    original_name = f"Browser Seed {uuid.uuid4().hex[:6]}"
    updated_name = f"Browser Updated {uuid.uuid4().hex[:6]}"

    _create_product_via_form(
        live_server["admin"],
        TENANT_ID,
        format_ref,
        property_selection,
        product_id,
        original_name,
    )

    with browser_page(live_server["admin"]) as page:
        login_as_tenant_admin(page, TENANT_ID)
        page.goto(f"/tenant/{TENANT_ID}/products/{product_id}/edit", wait_until="networkidle")
        assert page.locator("#name").input_value() == original_name
        page.locator("#name").fill(updated_name)
        page.locator("#description").fill("Updated through browser UI E2E")
        page.locator("#delivery_measurement_provider").fill("IAS")
        page.get_by_role("button", name="Save Changes").click()
        page.wait_for_url(f"**/tenant/{TENANT_ID}/products", wait_until="networkidle")
        page.wait_for_selector(f"text={updated_name}")

    discovered = _lookup_product(live_server, test_auth_token, product_id)
    assert discovered["name"] == updated_name
    assert discovered["delivery_measurement"]["provider"] == "IAS"


def test_create_principal_browser_flow(docker_services_e2e, live_server):
    """Create a principal in the browser and verify persisted state."""
    principal_id = f"browser-principal-{uuid.uuid4().hex[:8]}"
    principal_name = f"Browser Principal {uuid.uuid4().hex[:6]}"

    with browser_page(live_server["admin"]) as page:
        login_as_tenant_admin(page, TENANT_ID)
        page.goto(f"/tenant/{TENANT_ID}/principals/create", wait_until="networkidle")
        page.locator("#principal_id").fill(principal_id)
        page.locator("#name").fill(principal_name)
        page.get_by_role("button", name="Add Advertiser").click()
        page.wait_for_url(f"**/tenant/{TENANT_ID}/settings*", wait_until="networkidle")
        page.wait_for_selector(f"text={principal_name}")

    record = _get_principal_record(live_server, principal_id)
    assert record is not None, "Principal should be persisted"
    assert record[1] == principal_name
    assert len(record[2]) > 0, "Principal access token should be generated"


def test_workflow_approval_browser_flow(docker_services_e2e, live_server, test_auth_token):
    """Approve a pending media buy in the browser and verify the workflow outcome."""
    media_buy_id = _create_pending_media_buy(live_server, test_auth_token)

    with browser_page(live_server["admin"]) as page:
        login_as_tenant_admin(page, TENANT_ID)
        page.goto(f"/tenant/{TENANT_ID}/media-buy/{media_buy_id}", wait_until="networkidle")
        assert page.locator("text=Manual Approval Required").count() > 0, "Media buy should require manual approval"
        page.locator("#approveBtn").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("#approveBtn", state="detached")

    state = _get_media_buy_state(live_server, media_buy_id)
    assert state["workflow_status"] == "approved"
    assert state["media_buy_status"] != "pending_approval"


def test_workflow_rejection_browser_flow(docker_services_e2e, live_server, test_auth_token):
    """Reject a pending media buy in the browser and verify the terminal state."""
    media_buy_id = _create_pending_media_buy(live_server, test_auth_token)
    rejection_reason = "Rejected by browser UI E2E"

    with browser_page(live_server["admin"]) as page:
        login_as_tenant_admin(page, TENANT_ID)
        page.goto(f"/tenant/{TENANT_ID}/media-buy/{media_buy_id}", wait_until="networkidle")
        assert page.locator("text=Manual Approval Required").count() > 0, "Media buy should require manual approval"
        page.get_by_role("button", name="Reject").click()
        page.locator("#reason").fill(rejection_reason)
        page.get_by_role("button", name="Reject Media Buy").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("text=REJECTED")
        assert not page.locator("#approveBtn").count(), "Rejected media buy should not remain actionable"

    state = _get_media_buy_state(live_server, media_buy_id)
    assert state["workflow_status"] == "rejected"
    assert state["media_buy_status"] == "rejected"
    assert rejection_reason in (state["workflow_error"] or "")
