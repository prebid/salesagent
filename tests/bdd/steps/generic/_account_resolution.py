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


def ensure_tenant_principal(ctx: dict, env: object) -> None:
    """Create tenant + principal if not already created by a Given step."""
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal


# ``seed_account_with_access`` and ``seed_natural_key_matches`` MOVED to
# ``tests/helpers/account_seeding.py``. Neither was BDD-specific -- both take a tenant and
# a principal and seed through the factories -- and integration tests need the same
# "Account row PLUS the AgentAccountAccess join" seeding, which is the part everything
# that rolls its own account setup gets wrong. Import them from tests.helpers now.
# ``ensure_tenant_principal`` stays: it takes the BDD ``ctx`` dict, so it is a step helper.
