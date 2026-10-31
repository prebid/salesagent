"""stamp the scope discriminator onto stored creative format_option_ref documents

Revision ID: c3f71a0d58e4
Revises: b41aded6b531
Create Date: 2026-10-09 12:40:00.000000

``core/format-option-ref.json`` at the 3.2.1 pin is a oneOf with
``discriminator: {propertyName: "scope"}``, and BOTH arms list ``scope`` in ``required``.
The writer that produced ``creatives.format_option_ref`` omitted it and wrote
``publisher_domain: null`` on the product arm, which that arm forbids outright
(``"publisher_domain": false``). A document in that shape names no arm of the union, so
every read of such a creative failed with ``union_tag_not_found`` -- the whole listing,
not the one field.

This revision normalizes the documents already stored to the shape the writer now
produces. It invents nothing: the pin makes the two members interdependent -- the
publisher arm REQUIRES ``publisher_domain`` and the product arm FORBIDS it -- so the
domain's presence IS the discriminator, and deriving ``scope`` from it is the same
derivation ``FormatOptionIdentity.scope`` performs in the model.

``downgrade()`` reverts the normalization exactly: it strips ``scope`` back out and
restores the explicit ``publisher_domain: null`` on product-scope rows. No table is
copied and nothing is kept aside to be restored -- both directions are a pure, derivable
rewrite of the one column.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c3f71a0d58e4"
down_revision: str | Sequence[str] | None = "b41aded6b531"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Stamp ``scope`` on, and drop the null ``publisher_domain`` the product arm forbids.

    Scoped to rows that have a document and no ``scope`` yet, so re-running is a no-op and
    a document already carrying its discriminator is left exactly as stored.
    """
    op.execute(
        """
        UPDATE creatives
           SET format_option_ref = CASE
                   WHEN format_option_ref ->> 'publisher_domain' IS NULL
                       THEN (format_option_ref - 'publisher_domain')
                            || jsonb_build_object('scope', 'product')
                   ELSE format_option_ref || jsonb_build_object('scope', 'publisher')
               END
         WHERE format_option_ref IS NOT NULL
           AND NOT format_option_ref ? 'scope'
        """
    )


def downgrade() -> None:
    """Remove the discriminator again, back to the shape this revision found."""
    op.execute(
        """
        UPDATE creatives
           SET format_option_ref = CASE
                   WHEN format_option_ref ->> 'scope' = 'product'
                       THEN (format_option_ref - 'scope')
                            || jsonb_build_object('publisher_domain', NULL)
                   ELSE format_option_ref - 'scope'
               END
         WHERE format_option_ref IS NOT NULL
           AND format_option_ref ? 'scope'
        """
    )
