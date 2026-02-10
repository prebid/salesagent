#!/usr/bin/env python3
"""
Check if GAM prerequisites are configured.

Usage:
    python scripts/gam_prerequisites_check.py

Returns:
    Exit code 0 if all prerequisites met, 1 otherwise
"""

import os
import sys


def main():
    """Check GAM prerequisites and print status."""
    print("Checking GAM Prerequisites...\n")

    all_good = True

    # Check OAuth credentials
    client_id = os.environ.get("GAM_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GAM_OAUTH_CLIENT_SECRET")

    if client_id:
        print("  GAM_OAUTH_CLIENT_ID is set")
    else:
        print("  GAM_OAUTH_CLIENT_ID is not set")
        all_good = False

    if client_secret:
        print("  GAM_OAUTH_CLIENT_SECRET is set")
    else:
        print("  GAM_OAUTH_CLIENT_SECRET is not set")
        all_good = False

    # Check service account provisioning capability
    gcp_project = os.environ.get("GCP_PROJECT_ID")
    if gcp_project:
        print(f"  GCP_PROJECT_ID is set ({gcp_project})")
        print("     Service account auto-provisioning available")
    else:
        print("  GCP_PROJECT_ID not set")
        print("     Service account auto-provisioning unavailable")
        print("     Manual service account upload still supported")

    print()

    if not all_good:
        print("GAM OAuth prerequisites not fully configured\n")
        print("To use GAM with OAuth authentication:")
        print("  1. Go to https://console.cloud.google.com/apis/credentials")
        print("  2. Create OAuth 2.0 Client ID (Web application)")
        print("  3. Add redirect URI: http://localhost:8001/tenant/callback/gam")
        print("  4. Set credentials in .env file:")
        print("     GAM_OAUTH_CLIENT_ID=your-client-id")
        print("     GAM_OAUTH_CLIENT_SECRET=your-client-secret")
        print("  5. Restart: docker-compose restart\n")
        print("Alternative: Use Service Account authentication via Admin UI")
        print("(No OAuth setup required)\n")
        return 1
    else:
        print("All GAM OAuth prerequisites configured!")
        print("You can now use OAuth authentication with GAM.\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
