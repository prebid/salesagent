"""stored request-signing digest postures become required

``CapabilityDeclarations`` refuses a stored ``covers_content_digest`` of ``either`` or
``forbidden``, which the previous pin accepted. A tenant holding one then answers
``get_adcp_capabilities`` with ``CONFIGURATION_ERROR``, and ``posture_for_tenant`` falls back
to the unsupported posture, so nothing is verified for it.

A DATA MIGRATION, by the owner's ruling: the seller verifies signatures with the digest, so a
stored posture with ``supported: true`` becomes ``required``. ``get-adcp-capabilities-response.json``
(v3.2.7) pins the value to the const ``required`` whenever ``supported`` is true and says
``either`` and ``forbidden`` "MUST NOT be advertised as a 3.2 signing posture". The same
description names no exception for ``supported: false``, where nothing is verified and there
is no policy to state, so such a posture loses the field instead.

Graded by the ``request-signing-migrated-digest`` scenarios, which store a legacy posture and
run :func:`rewrite_stored_digest_postures` against the seller's real database.

``downgrade`` changes nothing: ``required`` is valid on the previous pin, and which legacy value
a tenant held is not kept.

Revision ID: e3a1c7d95b20
Revises: 7a74ba340f11
Create Date: 2026-11-04 12:00:00.000000

"""

import logging
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

# revision identifiers, used by Alembic.
revision: str = "e3a1c7d95b20"
down_revision: str | Sequence[str] | None = "7a74ba340f11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY = "capability_declarations -> 'request_signing' ->> 'covers_content_digest' IN ('either', 'forbidden')"
_SUPPORTED = "capability_declarations -> 'request_signing' ->> 'supported' = 'true'"


def rewrite_stored_digest_postures(bind: Any) -> None:
    """Make each stored legacy digest posture one the pin admits."""
    bind.execute(
        sa.text(
            "UPDATE tenants SET capability_declarations = jsonb_set(capability_declarations, "
            "'{request_signing,covers_content_digest}', '\"required\"') "
            f"WHERE {_LEGACY} AND {_SUPPORTED}"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE tenants SET capability_declarations = capability_declarations "
            "#- '{request_signing,covers_content_digest}' "
            f"WHERE {_LEGACY} AND NOT COALESCE({_SUPPORTED}, false)"
        )
    )


def upgrade() -> None:
    """Rewrite every stored legacy digest posture."""
    rewrite_stored_digest_postures(op.get_bind())


def downgrade() -> None:
    """Leave the rewritten postures in place; ``required`` is valid on the previous pin."""
    logger.info("stored covers_content_digest values stay as upgraded: the legacy value is not kept")
