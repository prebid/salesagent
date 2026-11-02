#!/usr/bin/env python3
"""
Initialize the tenant management API key in a database.

The key is shown exactly once, here, when it is minted. The database keeps only
sha256(key) and a 12-character display prefix, so a key already in place cannot be
printed again — this script reports its prefix and refuses to rotate it unless asked.

Usage:
    uv run scripts/initialize_tenant_mgmt_api_key.py           # mint if none exists
    uv run scripts/initialize_tenant_mgmt_api_key.py --rotate  # replace the existing key
"""

import argparse
import sys

from src.admin.sync_api import mint_tenant_management_api_key, tenant_management_api_key_prefix


def main(rotate: bool) -> str | None:
    print("🔑 Checking for an existing tenant management API key...")

    existing_prefix = tenant_management_api_key_prefix()

    if existing_prefix and not rotate:
        print(f"✅ A key is already in place (starts with {existing_prefix}).")
        print("\nIts plaintext is not stored and cannot be shown again.")
        print("Re-run with --rotate to replace it. The old key stops working immediately.")
        return None

    if existing_prefix:
        print(f"♻️  Rotating the existing key (starts with {existing_prefix}).")
    else:
        print("⚠️  No API key found. Minting one...")

    new_key = mint_tenant_management_api_key()

    print(f"✅ API key minted: {new_key[:12]}...{new_key[-4:]}")
    print(f"\nFull key: {new_key}")
    print("\n📋 Next steps:")
    print("1. Save this key securely — this is the only time it is shown")
    print("2. Export it for use with sync scripts:")
    print(f"   export TENANT_MGMT_API_KEY='{new_key}'")
    print("3. Run the AccuWeather sync diagnostic:")
    print("   ./scripts/check_accuweather_sync.sh")

    return new_key


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rotate", action="store_true", help="replace an existing key")
    args = parser.parse_args()
    try:
        main(args.rotate)
        sys.exit(0)
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
