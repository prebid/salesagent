"""cascade principals to their tenant

Revision ID: 390461e816ea
Revises: c7e2a5b40d18
Create Date: 2026-09-14 20:43:06.431286

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "390461e816ea"
down_revision: str | Sequence[str] | None = "c7e2a5b40d18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: PostgreSQL's default name for the unnamed constraint ``initial_schema`` declared as
#: ``sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"])``.
_CONSTRAINT = "principals_tenant_id_fkey"


def upgrade() -> None:
    """Make principals.tenant_id ON DELETE CASCADE in the database.

    A principal cannot exist without a tenant, so the tenant's deletion is the
    principal's deletion. That is a property of the data, and until this revision it was
    enforced nowhere in the schema: ``initial_schema`` created the foreign key with no
    ``ondelete``, which PostgreSQL records as NO ACTION, and no later revision changed
    it. The ORM model has declared ``ondelete="CASCADE"`` all along, but a declaration on
    a mapped column does not alter an already-created constraint, so the two disagreed.

    What actually removed principals with their tenant was the ORM relationship
    ``Tenant.principals`` and its ``cascade="all, delete-orphan"`` — a Python-side
    cascade that runs only when a Tenant object is loaded and deleted through a session.
    salesagent-3cs7o.26 deleted that relationship (a traversal of it reaches Principal
    rows without importing the class, which is what the TID251 ban exists to prevent),
    and deleting a tenant then began failing with a foreign-key violation.

    Moving the rule into the constraint is what the rule deserves: the database now
    refuses an orphaned principal and removes principals with their tenant whether or not
    any object graph is loaded, so no ORM relationship is load-bearing for it.
    """
    op.drop_constraint(_CONSTRAINT, "principals", type_="foreignkey")
    op.create_foreign_key(
        _CONSTRAINT,
        "principals",
        "tenants",
        ["tenant_id"],
        ["tenant_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Restore the NO ACTION delete rule this revision replaced.

    Recreating the constraint without ``ondelete`` is what PostgreSQL records as NO
    ACTION, which is the rule every database migrated before this revision carried.
    Deleting a tenant that still has principals fails again afterwards.
    """
    op.drop_constraint(_CONSTRAINT, "principals", type_="foreignkey")
    op.create_foreign_key(
        _CONSTRAINT,
        "principals",
        "tenants",
        ["tenant_id"],
        ["tenant_id"],
    )
