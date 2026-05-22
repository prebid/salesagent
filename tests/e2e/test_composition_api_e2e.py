"""Composition API buyer-facing e2e path.

Exercises the deployed HTTP stack:
admin REST authoring -> persisted catalog/signal rows -> buyer MCP discovery.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg2
import pytest
import requests
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.e2e.adcp_request_builder import parse_tool_result

TENANT_SUBDOMAIN = "ci-test"


def _db_params(live_server: dict[str, object]) -> dict:
    params = live_server["postgres_params"]
    assert isinstance(params, dict)
    return params


@contextmanager
def _temporary_management_api_key(live_server: dict[str, object], api_key: str) -> Iterator[None]:
    params = _db_params(live_server)
    previous: str | None
    with psycopg2.connect(**params) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT config_value FROM superadmin_config WHERE config_key = 'tenant_management_api_key'")
            row = cursor.fetchone()
            previous = str(row[0]) if row else None
            cursor.execute(
                """
                INSERT INTO superadmin_config (config_key, config_value, description, updated_by, updated_at)
                VALUES ('tenant_management_api_key', %s, 'Composition e2e key', 'pytest', now())
                ON CONFLICT (config_key) DO UPDATE
                SET config_value = EXCLUDED.config_value,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = now()
                """,
                (api_key,),
            )
    try:
        yield
    finally:
        with psycopg2.connect(**params) as conn:
            with conn.cursor() as cursor:
                if previous is None:
                    cursor.execute("DELETE FROM superadmin_config WHERE config_key = 'tenant_management_api_key'")
                else:
                    cursor.execute(
                        """
                        UPDATE superadmin_config
                        SET config_value = %s,
                            updated_by = 'pytest-restore',
                            updated_at = now()
                        WHERE config_key = 'tenant_management_api_key'
                        """,
                        (previous,),
                    )


def _cleanup_composition_rows(
    live_server: dict[str, object], tenant_id: str, product_id: str, signal_id: str, profile_id: str
) -> None:
    params = _db_params(live_server)
    with psycopg2.connect(**params) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM products WHERE tenant_id = %s AND product_id = %s",
                (tenant_id, product_id),
            )
            cursor.execute(
                "DELETE FROM tenant_signals WHERE tenant_id = %s AND signal_id = %s",
                (tenant_id, signal_id),
            )
            cursor.execute(
                "DELETE FROM inventory_profiles WHERE tenant_id = %s AND profile_id = %s",
                (tenant_id, profile_id),
            )


def _resolve_ci_tenant_id(live_server: dict[str, object]) -> str:
    params = _db_params(live_server)
    with psycopg2.connect(**params) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT tenant_id FROM tenants WHERE subdomain = %s", (TENANT_SUBDOMAIN,))
            row = cursor.fetchone()
    assert row is not None, "CI seed tenant must exist before composition e2e setup"
    return str(row[0])


def _composition_url(live_server: dict[str, str], tenant_id: str, path: str) -> str:
    return f"{live_server['admin']}/admin/api/v1/tenants/{tenant_id}{path}"


def _post_json(live_server: dict[str, str], tenant_id: str, api_key: str, path: str, payload: dict) -> dict:
    response = requests.post(
        _composition_url(live_server, tenant_id, path),
        json=payload,
        headers={"X-Tenant-Management-API-Key": api_key},
        timeout=10,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def _inventory_profile_payload(profile_id: str) -> dict:
    return {
        "profile_id": profile_id,
        "name": "E2E Homepage Sports Bundle",
        "description": "Display inventory authored by the composition e2e test.",
        "inventory_config": {
            "ad_units": ["e2e_home", "e2e_sports"],
            "placements": ["e2e_top"],
            "include_descendants": True,
        },
        "format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
        "publisher_properties": [
            {
                "publisher_domain": "publisher.example.com",
                "property_tags": ["sports"],
                "selection_type": "by_tag",
            }
        ],
        "constraints": {
            "formats": ["display_300x250"],
            "channels": ["display"],
            "targeting_dimensions": ["audience"],
        },
    }


def _signal_payload(signal_id: str) -> dict:
    return {
        "signal_id": signal_id,
        "name": "E2E Sports Fans",
        "description": "Publisher-declared sports audience.",
        "value_type": "binary",
        "categories": [],
        "adapter_config": {"kind": "audience_segment", "segment_id": "e2e-98765"},
        "data_provider": "publisher_1p",
        "targeting_dimension": "audience",
    }


def _product_payload(product_id: str, profile_id: str) -> dict:
    return {
        "product_id": product_id,
        "name": "E2E Wholesale Sports Display",
        "description": "Profile-backed non-guaranteed wholesale product.",
        "inventory_profile_id": profile_id,
        "delivery_type": "non_guaranteed",
        "channels": ["display"],
        "countries": ["US"],
        "signal_targeting_allowed": True,
        "pricing_options": [
            {
                "pricing_model": "cpm",
                "currency": "USD",
                "is_fixed": False,
                "price_guidance": {"floor": 3.0, "p50": 4.0, "p75": 5.0},
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_authored_inventory_signal_and_product_reach_buyer_discovery(
    docker_services_e2e,
    live_server,
    test_auth_token,
):
    suffix = uuid.uuid4().hex[:8]
    profile_id = f"e2e_homepage_sports_{suffix}"
    signal_id = f"e2e_sports_fans_{suffix}"
    product_id = f"e2e_wholesale_sports_display_{suffix}"
    api_key = f"composition-e2e-{uuid.uuid4().hex}"

    tenant_id = _resolve_ci_tenant_id(live_server)

    try:
        with _temporary_management_api_key(live_server, api_key):
            profile = _post_json(
                live_server,
                tenant_id,
                api_key,
                "/inventory-profiles",
                _inventory_profile_payload(profile_id),
            )
            assert profile["profile_id"] == profile_id

            signal = _post_json(live_server, tenant_id, api_key, "/signals", _signal_payload(signal_id))
            assert signal["signal_id"] == signal_id
            assert "adapter_config" not in signal

            product = _post_json(live_server, tenant_id, api_key, "/products", _product_payload(product_id, profile_id))
            assert product["product_id"] == product_id
            assert product["inventory_profile_id"] == profile_id
            assert product["pricing_options"][0]["pricing_option_id"] == "cpm_usd_auction"

            transport = StreamableHttpTransport(
                url=f"{live_server['mcp']}/mcp/",
                headers={"x-adcp-auth": test_auth_token, "x-adcp-tenant": TENANT_SUBDOMAIN},
            )
            async with Client(transport=transport) as client:
                products_result = await client.call_tool(
                    "get_products",
                    {
                        "brief": "wholesale sports display inventory",
                        "brand": {"domain": "buyer.example"},
                    },
                )
                products_data = parse_tool_result(products_result)
                discovered_products = {item["product_id"]: item for item in products_data["products"]}
                assert product_id in discovered_products
                discovered_product = discovered_products[product_id]
                assert discovered_product["pricing_options"][0]["pricing_option_id"] == "cpm_usd_auction"
                assert discovered_product["format_ids"][0]["id"] == "display_300x250"
                assert discovered_product["publisher_properties"][0]["property_tags"] == ["sports"]

                signals_result = await client.call_tool("get_signals", {})
                signals_data = parse_tool_result(signals_result)
                discovered_signal_ids = {item["signal_agent_segment_id"] for item in signals_data["signals"]}
                assert signal_id in discovered_signal_ids
    finally:
        _cleanup_composition_rows(
            live_server,
            tenant_id,
            product_id,
            signal_id,
            profile_id,
        )
