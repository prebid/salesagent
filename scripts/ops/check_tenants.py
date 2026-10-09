#!/usr/bin/env python3
"""
Quick script to check all tenants and their adapter configurations
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, SyncJob, Tenant
from src.core.http_utils import validate_virtual_host


def check_all_tenants():
    """List all tenants and their configurations"""
    with get_db_session() as session:
        # Get all tenants with their adapter configs
        tenants_with_adapters = (
            session.query(Tenant, AdapterConfig)
            .outerjoin(AdapterConfig, Tenant.tenant_id == AdapterConfig.tenant_id)
            .order_by(Tenant.name)
            .all()
        )

        print("📋 All Tenants in Database:")
        print("=" * 80)

        # The validator runs on assignment only, so a host stored before the rule refused it
        # still loads; this report is where an operator finds one.
        refused_hosts = []
        for tenant, adapter_config in tenants_with_adapters:
            print(f"\nTenant: {tenant.name}")
            print(f"  ID: {tenant.tenant_id}")
            print(f"  Subdomain: {tenant.subdomain or 'None'}")
            print(f"  Virtual Host: {tenant.virtual_host}")
            try:
                validate_virtual_host(tenant.virtual_host)
            except ValueError as exc:
                refused_hosts.append(tenant.tenant_id)
                print(f"  ⚠️  {exc}")
            print(f"  Ad Server: {tenant.ad_server or 'None configured'}")
            if adapter_config and adapter_config.adapter_type:
                print(f"  Adapter: {adapter_config.adapter_type}")
                if adapter_config.gam_network_code:
                    print(f"  GAM Network Code: {adapter_config.gam_network_code}")

        print("\n" + "=" * 80)
        print(f"Total tenants: {len(tenants_with_adapters)}")
        if refused_hosts:
            print(
                f"⚠️  {len(refused_hosts)} tenant(s) store a virtual_host the admin UI cannot serve: "
                f"{', '.join(refused_hosts)}"
            )

        # Check for sync status - get latest sync for each tenant
        latest_syncs = (
            session.query(SyncJob, Tenant)
            .join(Tenant, SyncJob.tenant_id == Tenant.tenant_id)
            .filter(
                SyncJob.sync_id.in_(
                    session.query(SyncJob.sync_id)
                    .filter_by(
                        sync_id=session.query(SyncJob.sync_id)
                        .filter(SyncJob.tenant_id == SyncJob.tenant_id)
                        .order_by(SyncJob.started_at.desc())
                        .limit(1)
                    )
                    .subquery()
                )
            )
            .order_by(SyncJob.started_at.desc())
            .all()
        )

        if latest_syncs:
            print("\n📊 Latest Sync Status:")
            print("=" * 80)
            for sync, tenant in latest_syncs:
                print(f"\n{tenant.name}:")
                print(f"  Status: {sync.status}")
                print(f"  Started: {sync.started_at}")
                if sync.completed_at:
                    print(f"  Completed: {sync.completed_at}")
                if sync.summary:
                    import json

                    try:
                        summary = json.loads(sync.summary) if isinstance(sync.summary, str) else sync.summary
                        print(
                            f"  Inventory: {summary.get('ad_units', 0)} ad units, "
                            f"{summary.get('custom_targeting_keys', 0)} targeting keys"
                        )
                    except:
                        print(f"  Summary: {sync.summary}")


if __name__ == "__main__":
    check_all_tenants()
