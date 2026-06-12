"""Applicability checks for GAM-derived sync streams.

These helpers define whether a derived GAM stream has data prerequisites for a
tenant. Schedulers use them before dispatching work; public status uses the
same checks so inapplicable streams do not surface as stale failures.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.core.database.repositories.product import ProductRepository
from src.core.database.repositories.tenant_signal import TenantSignalRepository

GAM_ADAPTER_TYPE = "google_ad_manager"


def tenant_has_custom_key_value_signals(session: Session, tenant_id: str) -> bool:
    """Return whether signal coverage can run for this GAM tenant."""
    repo = TenantSignalRepository(session, tenant_id)
    return any((signal.adapter_config or {}).get("kind") == "custom_key_value" for signal in repo.list_all())


def tenant_has_pricing_availability_targets(session: Session, tenant_id: str) -> bool:
    """Return whether pricing/availability can run for this GAM tenant."""
    products = ProductRepository(session, tenant_id).list_all_with_inventory()
    for product in products:
        config = product.effective_implementation_config
        if config.get("targeted_placement_ids") or config.get("targeted_ad_unit_ids"):
            return True
    return False


def gam_reporting_applicable(*, adapter_type: str) -> bool:
    """Return whether a separate reporting sync is applicable for this adapter.

    GAM bundles delivery reporting into its inventory sync — there is no
    dedicated reporting sync job for GAM tenants. Non-GAM adapters run
    reporting as a distinct scheduled sync and remain applicable.
    """
    return adapter_type != GAM_ADAPTER_TYPE


def gam_signal_coverage_applicable(session: Session, *, tenant_id: str, adapter_type: str) -> bool:
    if adapter_type != GAM_ADAPTER_TYPE:
        return True
    return tenant_has_custom_key_value_signals(session, tenant_id)


def gam_pricing_availability_applicable(session: Session, *, tenant_id: str, adapter_type: str) -> bool:
    if adapter_type != GAM_ADAPTER_TYPE:
        return True
    return tenant_has_pricing_availability_targets(session, tenant_id)
