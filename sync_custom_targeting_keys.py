#!/usr/bin/env python3
"""
Sync custom targeting keys from GAM to database.

This script fetches all custom targeting keys from GAM API and stores the
name → ID mapping in adapter_config.custom_targeting_keys.

This mapping is required for the GAM adapter to resolve key names to IDs
when creating line items with custom targeting.
"""


from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Principal, Tenant
from src.core.helpers.adapter_helpers import get_adapter


def sync_keys():
    """Sync custom targeting keys from GAM."""
    print("\n" + "=" * 80)
    print("SYNCING CUSTOM TARGETING KEYS FROM GAM")
    print("=" * 80)

    # Get tenant and principal
    with get_db_session() as session:
        stmt = select(Tenant).filter_by(tenant_id="default")
        tenant = session.scalars(stmt).first()

        if not tenant:
            print("❌ Tenant 'default' not found")
            return

        stmt = select(Principal).filter_by(tenant_id="default", principal_id="ci-test-principal")
        principal = session.scalars(stmt).first()

        if not principal:
            print("❌ Principal 'ci-test-principal' not found")
            return

        print(f"✓ Found tenant: {tenant.name}")
        print(f"✓ Found principal: {principal.name}")

    # Initialize GAM adapter using helper (sets tenant context)
    print("\nInitializing GAM adapter...")
    from src.core.auth import set_current_tenant

    set_current_tenant({"tenant_id": "default", "name": tenant.name, "ad_server": "google_ad_manager"})

    adapter = get_adapter(principal, dry_run=False)

    # Sync keys
    print("\nFetching custom targeting keys from GAM...")
    result = adapter.targeting_manager.sync_custom_targeting_keys()

    if result["errors"]:
        print("\n❌ Sync failed:")
        for error in result["errors"]:
            print(f"   - {error}")
        return

    print(f"\n✅ Successfully synced {result['count']} custom targeting keys!")
    print("\nKeys synced:")
    for key_name, key_id in result["synced_keys"].items():
        print(f"   {key_name}: {key_id}")

    # Show AXE key mappings if configured
    print("\n" + "=" * 80)
    print("AXE KEY MAPPINGS")
    print("=" * 80)

    axe_include = adapter.targeting_manager.axe_include_key
    axe_exclude = adapter.targeting_manager.axe_exclude_key
    axe_macro = adapter.targeting_manager.axe_macro_key

    if axe_include and axe_include in result["synced_keys"]:
        print(f"✓ AXE Include Key: {axe_include} → {result['synced_keys'][axe_include]}")
    else:
        print(f"⚠️  AXE Include Key: {axe_include} → NOT FOUND IN GAM")
        print(f"   Create '{axe_include}' custom targeting key in GAM UI")

    if axe_exclude and axe_exclude in result["synced_keys"]:
        print(f"✓ AXE Exclude Key: {axe_exclude} → {result['synced_keys'][axe_exclude]}")
    else:
        print(f"⚠️  AXE Exclude Key: {axe_exclude} → NOT FOUND IN GAM")
        print(f"   Create '{axe_exclude}' custom targeting key in GAM UI")

    if axe_macro and axe_macro in result["synced_keys"]:
        print(f"✓ AXE Macro Key: {axe_macro} → {result['synced_keys'][axe_macro]}")
    else:
        print(f"⚠️  AXE Macro Key: {axe_macro} → NOT FOUND IN GAM")
        print(f"   Create '{axe_macro}' custom targeting key in GAM UI")

    print("\n" + "=" * 80)
    print("✅ SYNC COMPLETE")
    print("=" * 80)
    print("\nYou can now run: uv run python test_gam_integration.py")


if __name__ == "__main__":
    sync_keys()
