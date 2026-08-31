"""Shared account-setup helper for BDD step definitions.

Provides the tenant/principal bootstrap used by UC-002 and UC-006 When steps.

Account *resolution* itself is no longer driven here: account-resolution
scenarios now dispatch a full ``create_media_buy`` through the wire transport
(#1417), so production resolves the account at the transport boundary
and emits the outcome (success or ACCOUNT_NOT_FOUND/AMBIGUOUS/SETUP_REQUIRED/
PAYMENT_REQUIRED/SUSPENDED/VALIDATION_ERROR) on the wire. The former test-side
``AdCPValidationError`` construction and the IMPL-only resolve_account call were
removed: they bypassed the wire and reconstructed errors the harness never saw.

"""

from __future__ import annotations

# Canonical seeder now lives in tests/helpers/ so the integration suite can import it
# without reaching into bdd/steps/generic; re-exported here for the BDD step files that
# reference it via this module (uc004_delivery, uc006_sync_creatives) (#1329).
from tests.helpers.accounts import seed_account_with_access

__all__ = ["ensure_tenant_principal", "seed_account_with_access"]


def ensure_tenant_principal(ctx: dict, env: object) -> None:
    """Create tenant + principal if not already created by a Given step."""
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
