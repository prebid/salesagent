#!/usr/bin/env python3
"""
Initialize tenant management API key in production database.

This script creates a new tenant management API key if one doesn't exist,
or retrieves the existing key if already initialized.

Usage:
    uv run scripts/initialize_tenant_mgmt_api_key.py
"""

import sys

from src.admin.auth_helpers import get_api_key_from_config
from src.admin.sync_api import initialize_tenant_management_api_key


def main():
    print("🔑 Checking for existing tenant management API key...")

    # Check if key already exists
    existing_key = get_api_key_from_config("SYNC_API_KEY", "api_key")

    if existing_key:
        print(f"✅ API key already exists: {existing_key[:10]}...{existing_key[-4:]}")
        print(f"\nFull key: {existing_key}")
        return existing_key

    print("⚠️  No API key found. Initializing new key...")

    # Initialize new key
    new_key = initialize_tenant_management_api_key()

    print(f"✅ New API key created: {new_key[:10]}...{new_key[-4:]}")
    print(f"\nFull key: {new_key}")
    print("\n📋 Next steps:")
    print("1. Save this key securely (it won't be shown again)")
    print("2. Export it for use with sync scripts:")
    print(f"   export TENANT_MGMT_API_KEY='{new_key}'")
    print("3. Run the AccuWeather sync diagnostic:")
    print("   ./scripts/check_accuweather_sync.sh")

    return new_key


if __name__ == "__main__":
    try:
        key = main()
        sys.exit(0)
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
