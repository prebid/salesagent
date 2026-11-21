#!/usr/bin/env python3
"""
Check if GAM prerequisites are configured.

Usage:
    python scripts/gam_prerequisites_check.py                  # the Admin UI's OAuth onboarding
    python scripts/gam_prerequisites_check.py --suite-network   # the network the GAM tests run against
    python scripts/gam_prerequisites_check.py --suite-network --skip

``--suite-network`` is the GAM configuration preflight: read-only, it creates nothing in GAM.
No GAM setting at all prints ``GAM: not configured`` and exits 0: the environment books
against no GAM network, and the GAM tests declare themselves unsupported. Otherwise it prints
``GAM: configured, network <code>`` and, cheapest first, checks that each setting
``ToolingSettings.gam_suite_network`` reads is present, that the credential parses as a service-account key, that the configured network
answers ``getCurrentNetwork`` under it, that the configured advertiser exists and is an
advertiser, and that each configured ad unit exists and is ACTIVE. It checks that the
configured ids exist and never compares them with known values: each contributor configures
their own network. ``run_all_tests.sh`` runs it before Docker or any suite starts.

``--skip`` (or ``GAM_SUITE_PREFLIGHT_SKIP``) runs no check, prints that the preflight was
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

from src.core.config import GamSuiteNetwork, GamSuiteNetworkUnconfigured, ToolingSettings, load_settings  # noqa: E402

#: What the preflight prints when no GAM setting is set at all, and exits 0. run_all_tests.sh
#: prints the same line itself, without uv, when the environment holds no GAM_* variable.
NOT_CONFIGURED = "GAM: not configured"
#: What the preflight prints when it is skipped, and exits 0.
SKIPPED = "GAM: preflight skipped (--skip or GAM_SUITE_PREFLIGHT_SKIP), no check ran"


def adapter_config_columns(network: GamSuiteNetwork) -> dict[str, str]:
    """The ``adapter_config`` columns of a seller onboarded to *network* by service account.

    One mapping for every writer of such a row: the preflight's in-memory row, and the GAM
    tests' tenant (``tests/utils/tenant_setup.onboard_gam``).
    """
    return {
        "adapter_type": "google_ad_manager",
        "gam_network_code": network.network_code,
        "gam_auth_method": "service_account",
        "gam_service_account_json": network.service_account_json,
    }


def network_client_manager(network: GamSuiteNetwork) -> "GAMClientManager":
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

    row = AdapterConfig(tenant_id="gam-suite-network", **adapter_config_columns(network))
    return GAMClientManager(AdapterConfigRepository.get_gam_config(row), network.network_code)


def check_suite_network(*, skip: bool) -> int:
    """Run the GAM configuration preflight; print one line per check and return the exit code."""
    tooling = ToolingSettings()
    if skip or tooling.gam_suite_preflight_skip:
        print(SKIPPED)
        return 0

    try:
        network = tooling.gam_suite_network()
    except GamSuiteNetworkUnconfigured as unconfigured:
        print("GAM suite-network preflight (read-only; creates nothing in GAM)")
        for name in unconfigured.missing:
            print(f"  FAIL settings present: {name}")
        return 1
    if network is None:
        # No GAM setting at all: this environment books against no GAM network, which is a
        # configuration, not a failure. The GAM tests declare themselves unsupported here.
        print(NOT_CONFIGURED)
        return 0
    # The record of which network this run books against, compared with nothing.
    print(f"GAM: configured, network {network.network_code}")
    print("GAM suite-network preflight (read-only; creates nothing in GAM)")
    print("  PASS settings present")
    # The one fact about the credential that may be shown: that this process has it, and its size.
    print(f"  credential: present, {len(network.service_account_json)} characters")

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
    parser.add_argument("--suite-network", action="store_true", help="check the network the GAM tests run against")
    parser.add_argument("--skip", action="store_true", help="with --suite-network: run no check and record the skip")
    args = parser.parse_args()
    if args.suite_network:
        return check_suite_network(skip=args.skip)

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
