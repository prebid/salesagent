#!/usr/bin/env python3
"""
Quick test of sync API - minimal example.

Usage:
    python quick_sync_test.py
"""

import os
import sys

import requests

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.admin.sync_api import mint_tenant_management_api_key
from src.core.config import get_settings


def main():
    # Configuration
    BASE_URL = "http://localhost:8001"

    print("Quick Sync API Test")
    print("=" * 40)

    # 1. Get API key. The stored key is a hash, so a key already in place cannot be
    # recovered: either SYNC_API_KEY carries the plaintext, or we mint (and rotate to)
    # a fresh one that this process is then the only holder of.
    print("\n1. Getting API key...")
    api_key = get_settings().auth.sync_api_key
    if not api_key:
        print("   Minting a new API key (rotates any existing one)...")
        api_key = mint_tenant_management_api_key()
    print(f"   API Key: {api_key[:12]}...")

    # Set up headers
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    # 2. List tenants
    print("\n2. Listing GAM tenants...")
    response = requests.get(f"{BASE_URL}/api/v1/sync/tenants", headers=headers)
    if response.status_code == 200:
        data = response.json()
        print(f"   Found {data['total']} tenant(s)")

        for tenant in data["tenants"][:3]:  # Show first 3
            print(f"   - {tenant['name']} ({tenant['tenant_id']})")
            if tenant.get("last_sync"):
                print(f"     Last sync: {tenant['last_sync']['completed_at'][:19]}")
    else:
        print(f"   Error: {response.status_code}")
        print(f"   {response.text}")
        return

    # 3. Get sync stats
    print("\n3. Getting sync statistics...")
    response = requests.get(f"{BASE_URL}/api/v1/sync/stats", headers=headers)
    if response.status_code == 200:
        stats = response.json()
        print("   Status counts (last 24h):")
        for status, count in stats["status_counts"].items():
            print(f"   - {status}: {count}")
    else:
        print(f"   Error: {response.status_code}")

    print("\n" + "=" * 40)
    print("Test complete!")
    print("\nTo trigger a sync, run:")
    print("  python test_sync_api.py --wait")
    print("\nYour API key for direct calls:")
    print(f"  {api_key}")


if __name__ == "__main__":
    main()
