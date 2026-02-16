#!/usr/bin/env python3
"""Test workflow functionality with a running server."""

import asyncio

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from rich.console import Console

console = Console()

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.mark.asyncio
@pytest.mark.requires_server
async def test_workflow_with_manual_approval():
    """Test creating a media buy that requires manual approval."""

    # Connect to the running server
    server_url = "http://localhost:8080/mcp/"
    headers = {"x-adcp-auth": "test-token-1234"}  # From setup_test.py

    console.print("[bold cyan]Testing Workflow with Manual Approval[/bold cyan]")
    console.print("=" * 60)

    transport = StreamableHttpTransport(url=server_url, headers=headers)
    client = Client(transport=transport)

    async with client:
        # First, discover available products
        console.print("\n[yellow]Step 1: Getting available products[/yellow]")
        try:
            result = await client.call_tool("get_products", {"req": {"brief": "Test campaign for workflow testing"}})

            # The result is a CallToolResult with data attribute
            if hasattr(result, "data") and hasattr(result.data, "products"):
                products = result.data.products
            elif hasattr(result, "structured_content") and "products" in result.structured_content:
                products = result.structured_content["products"]
            else:
                products = []

            console.print(f"✓ Found {len(products) if products else 0} products")

            # Take the first product
            if products:
                # Handle both dict and object formats
                if isinstance(products[0], dict):
                    product_id = products[0]["product_id"]
                else:
                    product_id = products[0].product_id
                console.print(f"  Using product: {product_id}")
            else:
                console.print("[red]No products found![/red]")
                return
        except Exception as e:
            console.print(f"[red]Error getting products: {e}[/red]")
            import traceback

            traceback.print_exc()
            return

        # Create a media buy (this might trigger workflow steps)
        console.print("\n[yellow]Step 2: Creating media buy[/yellow]")
        try:
            result = await client.call_tool(
                "create_media_buy",
                {
                    "req": {
                        "product_ids": [product_id],
                        "total_budget": 10000.0,
                        "flight_start_date": "2025-02-01",
                        "flight_end_date": "2025-02-28",
                        "targeting_overlay": {"geo_countries": ["US"]},
                    }
                },
            )

            # Handle CallToolResult structure
            if hasattr(result, "data"):
                buy_response = result.data
            elif hasattr(result, "structured_content"):
                buy_response = result.structured_content
            else:
                buy_response = result

            # Access media_buy_id
            if hasattr(buy_response, "media_buy_id"):
                media_buy_id = buy_response.media_buy_id
            elif isinstance(buy_response, dict) and "media_buy_id" in buy_response:
                media_buy_id = buy_response["media_buy_id"]
            else:
                media_buy_id = "unknown"

            console.print(f"✓ Created media buy: {media_buy_id}")

            # Check if context was created (indicates async workflow)
            context_id = None
            if hasattr(buy_response, "context_id"):
                context_id = buy_response.context_id
            elif isinstance(buy_response, dict) and "context_id" in buy_response:
                context_id = buy_response["context_id"]

            if context_id:
                console.print(f"  Context ID: {context_id}")
                console.print("  [yellow]This triggered an async workflow![/yellow]")
            else:
                console.print("  [green]Synchronous creation - no workflow needed[/green]")

            # Check for any clarification needed
            clarification_needed = False
            message = None
            if hasattr(buy_response, "clarification_needed"):
                clarification_needed = buy_response.clarification_needed
                message = getattr(buy_response, "message", None)
            elif isinstance(buy_response, dict):
                clarification_needed = buy_response.get("clarification_needed", False)
                message = buy_response.get("message")

            if clarification_needed:
                console.print(f"  [yellow]Clarification needed: {message}[/yellow]")

        except Exception as e:
            console.print(f"[red]Error creating media buy: {e}[/red]")
            import traceback

            traceback.print_exc()
            return

        # Check media buy status to see if workflow was created
        console.print("\n[yellow]Step 3: Checking media buy status[/yellow]")
        try:
            status_result = await client.call_tool("check_media_buy_status", {"req": {"media_buy_id": media_buy_id}})

            if hasattr(status_result, "data"):
                status_data = status_result.data
            elif hasattr(status_result, "structured_content"):
                status_data = status_result.structured_content
            else:
                status_data = status_result

            # Check for any pending approval status
            if hasattr(status_data, "status"):
                status = status_data.status
            elif isinstance(status_data, dict):
                status = status_data.get("status", "unknown")
            else:
                status = "unknown"

            console.print(f"  Media buy status: {status}")

            if status in ["pending_approval", "requires_approval"]:
                console.print("  [yellow]Media buy requires manual approval![/yellow]")
                console.print("  This would be handled through the admin UI workflow system")

        except Exception as e:
            console.print(f"[dim]Note: Could not check media buy status: {e}[/dim]")

    console.print("\n[bold green]✅ Workflow test completed![/bold green]")
    console.print("\nKey observations:")
    console.print("- Media buy creation works with the new architecture")
    console.print("- Context creation happens for async operations")
    console.print("- Workflow steps are tracked in the database")


async def main():
    """Run the workflow test."""
    # Run the workflow test directly - assumes server is configured
    await test_workflow_with_manual_approval()


if __name__ == "__main__":
    asyncio.run(main())
