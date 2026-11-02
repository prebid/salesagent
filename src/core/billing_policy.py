"""Billing-party policy: the single source of truth for the billing value-set.

The SDK ``BillingParty`` enum (pinned to AdCP spec v3.1.1 by
``tests/unit/test_billing_party_parity.py``) is the only place the
billing-party value-set is defined. The ``accounts`` DB CHECK constraint,
the ``sync_accounts`` billing gate, and the capabilities
``account.supported_billing`` declaration all derive from it — no literal
copy of the value-set may appear anywhere else (#1521, #1592).
"""

from typing import TYPE_CHECKING

from adcp.types.generated_poc.enums.billing_party import (
    BillingParty,
)  # TODO: no stable alias in adcp.types

if TYPE_CHECKING:
    from src.core.tenant_context import TenantContext

    pass

BILLING_PARTY_VALUES: tuple[str, ...] = tuple(member.value for member in BillingParty)


def resolve_supported_billing(tenant: "TenantContext | None") -> list[str]:
    """Resolve the billing models this seller supports.

    Single source for BOTH the ``sync_accounts`` billing gate and the
    capabilities ``account.supported_billing`` emission (#1592 C2).

    An unconfigured tenant (missing/``None`` ``supported_billing``) supports
    the full spec enum. An explicitly configured empty list means
    "reject every billing model" and is preserved as-is — only ``None``
    means unconfigured.
    """
    supported = tenant.supported_billing if tenant else None
    if not isinstance(supported, list):
        return list(BILLING_PARTY_VALUES)
    return [str(v) for v in supported]


def resolve_account_sandbox(tenant: "TenantContext | None") -> bool:
    """Resolve whether this seller supports sandbox accounts.

    Single source for BOTH the capabilities ``account.sandbox`` emission and the
    ``sync_accounts`` sandbox provisioning gate — the same claim-and-enforcement
    pairing :func:`resolve_supported_billing` exists for. Four sites used to
    inline ``tenant.get("account_sandbox", True)``, so the declaration and the
    gate could disagree about a seller's posture, and each copy defaulted a
    missing tenant to *supported*.

    No tenant means no declared support: False. A seller advertises sandbox by
    configuring it, never by the absence of configuration.
    """
    if not tenant:
        return False
    return tenant.account_sandbox
