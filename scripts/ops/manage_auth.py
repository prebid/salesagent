#!/usr/bin/env python3
"""Manage authentication tokens for AdCP Buy Server."""

import json
import secrets
import sqlite3
import sys

from rich.console import Console
from rich.table import Table

console = Console()
DB_FILE = "adcp.db"


def list_principals():
    """List all principals and their tokens."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT principal_id, name, access_token, platform_mappings
        FROM principals
        ORDER BY name
    """
    )

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        console.print("[yellow]No principals found.[/yellow]")
        return

    table = Table(title="Principals and Access Tokens")
    table.add_column("Principal ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Token", style="yellow")
    table.add_column("Platform Mappings", style="blue")

    for row in rows:
        mappings = json.loads(row[3])
        mapping_str = ", ".join([f"{k}: {v}" for k, v in mappings.items()])
        table.add_row(row[0], row[1], row[2][:20] + "...", mapping_str[:50] + "...")

    console.print(table)


def create_principal(principal_id: str, name: str, token: str = None):
    """Create a new principal with token."""
    if not token:
        token = f"tok_{secrets.token_urlsafe(32)}"

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Default platform mappings
    default_mappings = {
        "gam_advertiser_id": None,
        "triton_advertiser_id": None,
        "mock_advertiser_id": f"mock-{principal_id}",
    }

    try:
        cursor.execute(
            """
            INSERT INTO principals (principal_id, name, platform_mappings, access_token)
            VALUES (?, ?, ?, ?)
        """,
            (principal_id, name, json.dumps(default_mappings), token),
        )

        conn.commit()
        console.print(f"[green]✓ Created principal '{name}' ({principal_id})[/green]")
        console.print(f"[yellow]Token: {token}[/yellow]")
        console.print("\n[cyan]Platform mappings can be updated later or set via environment variables.[/cyan]")

    except sqlite3.IntegrityError as e:
        console.print(f"[red]Error: Principal or token already exists - {e}[/red]")
    finally:
        conn.close()


def update_token(principal_id: str, new_token: str = None):
    """Update token for a principal."""
    if not new_token:
        new_token = f"tok_{secrets.token_urlsafe(32)}"

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE principals
        SET access_token = ?
        WHERE principal_id = ?
    """,
        (new_token, principal_id),
    )

    if cursor.rowcount > 0:
        conn.commit()
        console.print(f"[green]✓ Updated token for principal '{principal_id}'[/green]")
        console.print(f"[yellow]New token: {new_token}[/yellow]")
    else:
        console.print(f"[red]Principal '{principal_id}' not found.[/red]")

    conn.close()


def delete_principal(principal_id: str):
    """Delete a principal."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("DELETE FROM principals WHERE principal_id = ?", (principal_id,))

    if cursor.rowcount > 0:
        conn.commit()
        console.print(f"[green]✓ Deleted principal '{principal_id}'[/green]")
    else:
        console.print(f"[red]Principal '{principal_id}' not found.[/red]")

    conn.close()


def show_demo():
    """Show demo authentication info."""
    console.print("\n[bold cyan]Demo Authentication Tokens[/bold cyan]")
    console.print("\nThe following demo tokens are available in development mode:")

    demo_data = [
        ("purina", "Purina Pet Foods", "purina_secret_token_abc123"),
        ("acme_corp", "Acme Corporation", "acme_secret_token_xyz789"),
    ]

    table = Table()
    table.add_column("Principal", style="cyan")
    table.add_column("Company", style="green")
    table.add_column("Token", style="yellow")

    for principal_id, name, token in demo_data:
        table.add_row(principal_id, name, token)

    console.print(table)

    console.print("\n[bold]Example usage:[/bold]")
    console.print("./client_mcp.py --token 'purina_secret_token_abc123' --test")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Manage AdCP Buy Server authentication")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # List command
    subparsers.add_parser("list", help="List all principals")

    # Demo command
    subparsers.add_parser("demo", help="Show demo authentication info")

    # Create command
    create_parser = subparsers.add_parser("create", help="Create a new principal")
    create_parser.add_argument("principal_id", help="Principal ID")
    create_parser.add_argument("--name", required=True, help="Display name")
    create_parser.add_argument("--token", help="Custom token (auto-generated if not provided)")

    # Update token command
    update_parser = subparsers.add_parser("update-token", help="Update principal token")
    update_parser.add_argument("principal_id", help="Principal ID")
    update_parser.add_argument("--token", help="New token (auto-generated if not provided)")

    # Delete command
    delete_parser = subparsers.add_parser("delete", help="Delete a principal")
    delete_parser.add_argument("principal_id", help="Principal ID")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "list":
        list_principals()
    elif args.command == "demo":
        show_demo()
    elif args.command == "create":
        create_principal(args.principal_id, args.name, args.token)
    elif args.command == "update-token":
        update_token(args.principal_id, args.token)
    elif args.command == "delete":
        delete_principal(args.principal_id)


if __name__ == "__main__":
    main()
