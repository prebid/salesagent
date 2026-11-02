#!/usr/bin/env python3
"""
Check if GAM prerequisites are configured.

Usage:
    python scripts/gam_prerequisites_check.py

Returns:
    Exit code 0 if all prerequisites met, 1 otherwise
"""

import sys
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import load_settings  # noqa: E402


def main():
    """Check GAM prerequisites and print status."""
    print("Checking GAM Prerequisites...\n")

    all_good = True

    # The environment, read once for this script. A malformed credential is reported
    # the way the server would report it at startup.
    try:
        auth = load_settings().auth
    except ValidationError as e:
        print(f"  Configuration is malformed:\n{e}\n")
        return 1

    # Check OAuth credentials
    client_id = auth.gam_oauth_client_id
    client_secret = auth.gam_oauth_client_secret

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
    gcp_project = auth.gcp_project_id
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
