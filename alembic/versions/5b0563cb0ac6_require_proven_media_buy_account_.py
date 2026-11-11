"""Require proven media-buy account ownership.

Revision ID: 5b0563cb0ac6
Revises: f7c2e8a13d64

Only the original create request, its scoped creation workflow, or a cached
create response naming this exact buy can identify its account. Current grants
and natural-key lookups cannot prove historical ownership. Account mode was
mutable before #1721, so an ID also needs independent persisted mode evidence:
an explicit boolean in a natural-key create reference, or sandbox:true in a
same-buy create response. An absent response marker never proves live mode.

AdCP 3.1.1 and 3.2.7 core/account-ref.json both permit boolean sandbox on the
natural-key arm only. Legacy null/omitted mode is deliberately unresolved.
This local upgrade policy is ungraded by conformance storyboards.
No rows are deleted or accounts/grants invented. Refusal rolls back everything.
"""

from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa

from alembic import op
from src.core.database.migration_guards import abort_if_rows

revision = "5b0563cb0ac6"
down_revision = "f7c2e8a13d64"
branch_labels = None
depends_on = None

_REMEDY = (
    "No buys were changed or deleted. Verify the listed buys against their original "
    "creation records, including sandbox/live mode, and explicitly repair account_id "
    "only where ownership is established before retrying. Do not infer ownership from "
    "current account grants or convert a sandbox buy to a live account."
)


def _document(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _response_matches(response: dict[str, Any], buy_id: str, steps: set[str], status: Any) -> bool:
    # Successful create/submitted arms may carry nonfatal errors advisories.
    # Their identifying field and the stored protocol status select the arm.
    if status == "completed":
        return (
            response.get("media_buy_id") == buy_id
            and response.get("task_id") is None
            and (response.get("sandbox") is None or response.get("sandbox") is True)
        )
    if status == "submitted":
        task_id = response.get("task_id")
        return (
            isinstance(task_id, str)
            and task_id in steps
            and response.get("media_buy_id") is None
            and response.get("sandbox") is None
        )
    return False


def _recover(bind: sa.Connection, buy: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Return one proven account, or a reason an operator must resolve this buy."""
    params = {"buy": buy["media_buy_id"], "tenant": buy["tenant_id"], "principal": buy["principal_id"]}
    workflows = (
        bind.execute(
            sa.text("""
        SELECT DISTINCT w.step_id, w.request_data
          FROM object_workflow_mapping m
          JOIN workflow_steps w ON w.step_id = m.step_id
          JOIN contexts c ON c.context_id = w.context_id
         WHERE m.object_type = 'media_buy' AND m.object_id = :buy AND m.action = 'create'
           AND w.tool_name = 'create_media_buy' AND w.step_type = 'media_buy_creation'
           AND c.tenant_id = :tenant AND c.principal_id = :principal
           AND NOT EXISTS (
               SELECT 1 FROM object_workflow_mapping other
                WHERE other.step_id = w.step_id AND other.object_type = 'media_buy'
                  AND other.action = 'create' AND other.object_id <> :buy)
    """),
            params,
        )
        .mappings()
        .all()
    )
    steps = {row["step_id"] for row in workflows}
    documents = [_document(buy["raw_request"]), *(_document(row["request_data"]) for row in workflows)]
    # Successful create workflows do not persist a mode-stamped response. Only
    # the idempotency cache has a producer for that evidence.
    responses: list[dict[str, Any]] = []
    ids: set[str] = set()
    modes: set[bool] = set()
    natural_refs: list[dict[str, Any]] = []
    for document in documents:
        ref = document.get("account")
        if ref is None:
            continue
        if not isinstance(ref, dict):
            return None, "malformed_account_reference"
        if "account_id" in ref:
            # A sandbox flag on the ID arm is invalid; it cannot prove mode.
            if set(ref) != {"account_id"} or not isinstance(ref["account_id"], str) or not ref["account_id"]:
                return None, "malformed_account_reference"
            ids.add(ref["account_id"])
        else:
            brand = _document(ref.get("brand"))
            if not isinstance(brand.get("domain"), str) or not isinstance(ref.get("operator"), str):
                return None, "malformed_natural_key"
            if type(ref.get("sandbox")) is not bool:
                return None, "historical_mode_unproven"
            # The account table has no identity columns for these newer selectors.
            if any(ref.get(key) is not None for key in ("operator_unit", "currency", "timezone")):
                return None, "unsupported_natural_key_identity"
            modes.add(ref["sandbox"])
            natural_refs.append(ref)

    attempts = bind.execute(
        sa.text("""
        SELECT account_id, idempotency_key, payload_hash, response_envelope
          FROM idempotency_attempts
         WHERE tenant_id = :tenant AND principal_id = :principal AND tool_name = 'create_media_buy'
           AND (response_envelope->'response'->>'media_buy_id' = :buy
                OR response_envelope->'response'->>'task_id' = ANY(CAST(:steps AS text[])))
    """),
        {**params, "steps": sorted(steps)},
    ).mappings()
    for attempt in attempts:
        envelope = _document(attempt["response_envelope"])
        response = _document(envelope.get("response"))
        if not _response_matches(response, params["buy"], steps, envelope.get("status")):
            return None, "conflicting_create_response"
        if buy["idempotency_key"] is not None and buy["idempotency_key"] != attempt["idempotency_key"]:
            return None, "conflicting_idempotency_key"
        if buy["payload_hash"] and attempt["payload_hash"] and buy["payload_hash"] != attempt["payload_hash"]:
            return None, "conflicting_payload_hash"
        if attempt["account_id"]:
            ids.add(attempt["account_id"])
        if envelope["status"] == "completed":
            responses.append(response)

    for response in responses:
        if response.get("sandbox") is True:
            modes.add(True)
    if len(ids) != 1:
        return None, "account_evidence_conflict" if ids else "account_identity_unproven"
    if len(modes) != 1:
        return None, "mode_evidence_conflict" if modes else "historical_mode_unproven"
    account_id = next(iter(ids))
    account = (
        bind.execute(
            sa.text("""
        SELECT sandbox, operator, brand, created_at FROM accounts
         WHERE tenant_id = :tenant AND account_id = :account
    """),
            {"tenant": params["tenant"], "account": account_id},
        )
        .mappings()
        .one_or_none()
    )
    if account is None:
        return None, f"account_missing:{account_id}"
    if buy["created_at"] is None:
        return None, "missing_historical_timestamp"
    if account["created_at"] > buy["created_at"]:
        return None, f"account_newer_than_buy:{account_id}"
    if bool(account["sandbox"]) != next(iter(modes)):
        return None, f"account_mode_changed:{account_id}"
    brand = _document(account["brand"])
    for ref in natural_refs:
        if ref["operator"] != account["operator"] or any(
            ref["brand"].get(key) != brand.get(key) for key in ("domain", "brand_id", "countries")
        ):
            return None, f"natural_key_mismatch:{account_id}"
    return account_id, None


def _account_fk(ondelete: str) -> None:
    op.drop_constraint("fk_media_buys_account", "media_buys", type_="foreignkey")
    op.create_foreign_key(
        "fk_media_buys_account",
        "media_buys",
        "accounts",
        ["tenant_id", "account_id"],
        ["tenant_id", "account_id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    bind = op.get_bind()
    # No writer may change the evidence, introduce a new NULL or win a projected
    # idempotency key between the survey and the constraint installation.
    bind.execute(sa.text("LOCK TABLE media_buys IN ACCESS EXCLUSIVE MODE"))
    bind.execute(
        sa.text(
            "LOCK TABLE accounts, contexts, workflow_steps, object_workflow_mapping, idempotency_attempts IN SHARE MODE"
        )
    )
    bind.execute(
        sa.text("""
        CREATE TEMPORARY TABLE media_buy_account_recovery (
            tenant_id text NOT NULL, principal_id text NOT NULL, media_buy_id text PRIMARY KEY,
            account_id text, reason text
        ) ON COMMIT DROP
    """)
    )
    buys = (
        bind.execute(
            sa.text("""
        SELECT tenant_id, principal_id, media_buy_id, raw_request, created_at, idempotency_key, payload_hash
          FROM media_buys WHERE account_id IS NULL ORDER BY tenant_id, principal_id, media_buy_id
    """)
        )
        .mappings()
        .all()
    )
    for buy in buys:
        account_id, reason = _recover(bind, buy)
        bind.execute(
            sa.text("""
            INSERT INTO media_buy_account_recovery VALUES (:tenant, :principal, :buy, :account, :reason)
        """),
            {
                "tenant": buy["tenant_id"],
                "principal": buy["principal_id"],
                "buy": buy["media_buy_id"],
                "account": account_id,
                "reason": reason,
            },
        )
    abort_if_rows(
        bind,
        "SELECT * FROM media_buy_account_recovery WHERE reason IS NOT NULL "
        "ORDER BY tenant_id, principal_id, media_buy_id",
        describe=lambda row: (
            f"tenant={row.tenant_id!r} principal={row.principal_id!r} media_buy={row.media_buy_id!r}: {row.reason}"
        ),
        headline="Cannot require account ownership: {count} unresolved media buy(s).",
        remedy=_REMEDY,
    )
    abort_if_rows(
        bind,
        """
        SELECT b.tenant_id, b.principal_id, coalesce(r.account_id, b.account_id) AS account_id,
               array_agg(b.media_buy_id ORDER BY b.media_buy_id) AS buys
          FROM media_buys b LEFT JOIN media_buy_account_recovery r USING (media_buy_id)
         WHERE b.idempotency_key IS NOT NULL
         GROUP BY b.tenant_id, b.principal_id, coalesce(r.account_id, b.account_id), b.idempotency_key
        HAVING count(*) > 1 ORDER BY b.tenant_id, b.principal_id, account_id
    """,
        describe=lambda row: (
            f"tenant={row.tenant_id!r} principal={row.principal_id!r} "
            f"account={row.account_id!r} media_buys={row.buys!r}"
        ),
        headline="Account recovery would collide with idx_media_buys_idempotency_key: {count} group(s).",
        remedy=_REMEDY,
    )
    bind.execute(
        sa.text("""
        UPDATE media_buys b SET account_id = r.account_id
          FROM media_buy_account_recovery r WHERE b.media_buy_id = r.media_buy_id
    """)
    )
    _account_fk("RESTRICT")
    op.alter_column("media_buys", "account_id", existing_type=sa.String(100), nullable=False)


def downgrade() -> None:
    op.alter_column("media_buys", "account_id", existing_type=sa.String(100), nullable=True)
    _account_fk("SET NULL")
