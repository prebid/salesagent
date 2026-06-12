"""Account status wire-projection — single source of truth.

Internal ORM account statuses that the AdCP wire spec doesn't model are mapped
to spec-valid ``AccountStatus`` enum values at every wire-emit boundary, so
``adcp.types.Account`` / ``SyncResponseAccount`` validation accepts them.
Internal logic (routing in media_buy_create, provisioning in
account_provisioning) still reads the raw DB column to distinguish the states.

#332: ``pending_provision`` collapses to ``pending_approval`` — both mean
"buyer can't use this account yet, operator-side work pending"; the AdCP
``Account.status`` enum doesn't distinguish "waiting for human approval" from
"waiting for technical provisioning" at the buyer-visible level.

Shared by ``accounts.py`` (get_accounts / sync_accounts) and
``media_buy_create.py`` (buyer-safe account projection on the create response)
so both surfaces emit the identical wire status for a given ORM row.
"""

INTERNAL_TO_WIRE_STATUS = {
    "pending_provision": "pending_approval",
}


def wire_status(status: str | None) -> str | None:
    """Translate an internal lifecycle status to a spec-valid AccountStatus.

    Spec enum (``adcp.types.AccountStatus``):
        active, pending_approval, rejected, payment_required, suspended, closed

    Anything not in the translation map passes through unchanged — only
    internal-only states need rewriting.
    """
    if status is None:
        return None
    return INTERNAL_TO_WIRE_STATUS.get(status, status)
