"""merge mcp oauth and virtual host heads

Revision ID: d35284fd33cf
Revises: 7f31c0ab94d2, b7c9e4a2d6f1
Create Date: 2026-10-28 12:18:38.366577

Reconciles the two heads a branch merge left behind. Both descend from
``823974a5553e`` and share nothing above it, so the revisions each contributes
uniquely are:

* under ``7f31c0ab94d2``: the main-line work — ``tenants`` (``account_sandbox``,
  ``capability_declarations``, the mandatory ``virtual_host`` and its derived
  ``virtual_host_name`` unique index), ``principals`` (token hashing, the inbound
  signature ``agent_url`` key, the tenant cascade), ``accounts``, ``media_buys``,
  ``push_notification_configs``, ``pricing_options``, ``adapter_config``, and the
  new ``signing_keys`` and ``adcp_replay`` tables;
* under ``b7c9e4a2d6f1``: the MCP OAuth work — the new ``oauth_clients`` and
  ``oauth_authorization_codes`` tables, plus ``oauth_clients.redirect_uris``.

The two sets touch disjoint tables: the OAuth branch only CREATES tables, and no
main-line revision names either of them. Its foreign keys reference
``tenants.tenant_id`` and ``principals.(tenant_id, principal_id)``, which the
main-line revisions leave in place — they add and drop other columns and
constraints on those tables, never the keys referenced here. So ordering between
the two branches cannot matter and there is no conflict for this revision to
resolve. It therefore carries no operations: it exists to give the graph a single
head, which is exactly what a merge revision is for.
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "d35284fd33cf"
down_revision: str | Sequence[str] | None = ("7f31c0ab94d2", "b7c9e4a2d6f1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing to do — the merged chains are schema-disjoint (see module docstring)."""


def downgrade() -> None:
    """Nothing to do — this revision applied no schema change to reverse."""
