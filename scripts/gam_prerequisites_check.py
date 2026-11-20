#!/usr/bin/env python3
"""
Check if GAM prerequisites are configured.

Usage:
    python scripts/gam_prerequisites_check.py                  # the Admin UI's OAuth onboarding
    python scripts/gam_prerequisites_check.py --test-network   # the network the GAM tests run against
    python scripts/gam_prerequisites_check.py --test-network --skip

``--test-network`` is the GAM configuration preflight: read-only, it creates nothing in GAM.
Cheapest first, it checks that each setting ``ToolingSettings.gam_test_network`` reads is
present, that the credential parses as a service-account key, that the configured network
answers ``getCurrentNetwork`` under it, that the configured advertiser exists and is an
advertiser, and that each configured ad unit exists and is ACTIVE. It checks that the
configured ids exist and never compares them with known values: each contributor configures
their own network. ``run_all_tests.sh`` runs it before Docker or any suite starts.

``--skip`` (or ``GAM_TEST_PREFLIGHT_SKIP``) runs no check, prints that the preflight was
skipped, and exits 0, so the suites run and the GAM tests fail where they fail.

Returns:
    Exit code 0 if all prerequisites met, 1 otherwise
"""

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if TYPE_CHECKING:
    from src.adapters.gam import GAMClientManager

from src.core.config import GamTestNetwork, GamTestNetworkUnconfigured, ToolingSettings, load_settings  # noqa: E402

#: The line every preflight outcome ends with, the record a run log keeps.
SKIPPED = "GAM test-network preflight SKIPPED (--skip or GAM_TEST_PREFLIGHT_SKIP): no check ran"


def network_client_manager(network: GamTestNetwork) -> "GAMClientManager":
    """The GAM client manager a seller onboarded to *network* has, built the way the server builds it.

    An in-memory ``adapter_config`` row holding what the GAM tests write (nothing is
    persisted), so the key is encrypted as the stored column is and ``ENCRYPTION_KEY`` must
    be set; ``AdapterConfigRepository.get_gam_config`` turns the row into the adapter config
    (``build_gam_config_from_adapter``), and ``GAMClientManager`` builds the client from it,
    as ``GoogleAdManager`` does. Shared by the preflight and the tests that call the network.
    """
    from src.adapters.gam import GAMClientManager
    from src.core.database.models import AdapterConfig
    from src.core.database.repositories.adapter_config import AdapterConfigRepository

    row = AdapterConfig(tenant_id="gam-test-network", **network.adapter_config_columns())
    return GAMClientManager(AdapterConfigRepository.get_gam_config(row), row.gam_network_code)


def check_test_network(*, skip: bool) -> int:
    """Run the GAM configuration preflight; print one line per check and return the exit code."""
    tooling = ToolingSettings()
    if skip or tooling.gam_test_preflight_skip:
        print(SKIPPED)
        return 0

    print("GAM test-network preflight (read-only; creates nothing in GAM)")
    try:
        network = tooling.gam_test_network()
    except GamTestNetworkUnconfigured as unconfigured:
        for name in unconfigured.missing:
            print(f"  FAIL settings present: {name}")
        return 1
    print("  PASS settings present")
    # A record of which network this run used, compared with nothing.
    print(f"  network code (configured): {network.network_code}")

    from src.adapters.gam.utils.health_check import HealthStatus

    try:
        manager = network_client_manager(network)
        manager.auth_manager.get_credentials()
    except Exception as refusal:  # reported by type and message; the key itself is never printed
        print(
            f"  FAIL credential parses as a service-account key on the server's path: {type(refusal).__name__}: {refusal}"
        )
        return 1
    print("  PASS credential parses as a service-account key on the server's path")

    checker = manager.get_health_checker()
    network_check = "network answers getCurrentNetwork"
    results = [(network_check, checker.check_authentication())]
    later = [f"advertiser {network.advertiser_id} exists and is an advertiser"]
    later += [f"ad unit {ad_unit_id} exists and is ACTIVE" for ad_unit_id in network.ad_unit_ids]
    if results[0][1].status is HealthStatus.HEALTHY:
        results.append((later[0], checker.check_permissions(network.advertiser_id)))
        results += [
            (check, checker.check_inventory_access([ad_unit_id]))
            for check, ad_unit_id in zip(later[1:], network.ad_unit_ids, strict=True)
        ]
    failed = 0
    for check, result in results:
        if result.status is HealthStatus.HEALTHY:
            print(f"  PASS {check}")
        else:
            failed += 1
            print(f"  FAIL {check}: {result.message} {result.details}")
    if len(results) == 1 and failed:
        for check in later:
            print(f"  NOT RUN {check} (needs: {network_check})")
    return 1 if failed else 0


def main():
    """Check GAM prerequisites and print status."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--test-network", action="store_true", help="check the network the GAM tests run against")
    parser.add_argument("--skip", action="store_true", help="with --test-network: run no check and record the skip")
    args = parser.parse_args()
    if args.test_network:
        return check_test_network(skip=args.skip)

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
