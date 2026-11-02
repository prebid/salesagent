"""Smoke-test a deployed tenant over MCP.

    ADCP_TOKEN=<principal token> uv run --with fastmcp python deploy/aws/smoke-test.py https://acme.sales.example.com

Calls get_adcp_capabilities, list_creative_formats (every page) and get_products, and prints
what each returned. Exits non-zero when a call fails, or when a format's canonical_parameters
lack format_kind or params.
"""

import asyncio
import json
import os
import sys

from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport


async def call(client: Client, tool: str, args: dict) -> dict:
    result = await client.call_tool(tool, args, raise_on_error=True)
    return result.structured_content or json.loads(result.content[0].text)


async def main(origin: str, token: str) -> int:
    transport = StreamableHttpTransport(f"{origin.rstrip('/')}/mcp/", headers={"Authorization": f"Bearer {token}"})
    async with Client(transport) as client:
        caps = await call(client, "get_adcp_capabilities", {})
        print(
            "get_adcp_capabilities: protocols",
            caps.get("supported_protocols"),
            "versions",
            caps.get("adcp", {}).get("major_versions"),
        )

        formats, cursor = [], None
        while True:
            page = await call(client, "list_creative_formats", {"pagination": {"cursor": cursor}} if cursor else {})
            formats += page.get("formats", [])
            cursor = (page.get("pagination") or {}).get("cursor")
            if not (page.get("pagination") or {}).get("has_more"):
                break
        canonical = [f for f in formats if f.get("canonical_parameters") is not None]
        incomplete = [
            f["format_id"]["id"] for f in canonical if not {"format_kind", "params"} <= f["canonical_parameters"].keys()
        ]
        print(
            f"list_creative_formats: {len(formats)} formats, {len(canonical)} with canonical_parameters, "
            f"{len(incomplete)} of those missing format_kind or params"
        )

        products = await call(client, "get_products", {"buying_mode": "brief", "brief": "display advertising"})
        print("get_products:", [p["product_id"] for p in products.get("products", [])])
    return 1 if incomplete else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], os.environ["ADCP_TOKEN"])))
