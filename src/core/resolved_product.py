"""ResolvedProduct: wire-shape Product + server-side internal fields.

The library ``adcp.types.Product`` is the wire-shape projection — exactly
what buyers see. Server-side code needs additional fields (adapter
implementation config, principal-access ACLs, derived device-type
projections, country filters) that must NOT reach the wire.

Historically these lived on a ``Product(LibraryProduct)`` schema extension
with ``exclude=True`` markers. That conflated wire-shape with internal
data — schemas got mutated as working buffers, the model_dump override
grew warts, and #71 happened because schema-layer shims masked an actual
DB-shape divergence.

``ResolvedProduct`` keeps the two concerns separate:

* ``wire`` — the spec-clean :class:`adcp.types.Product` for serialization
* ``implementation_config`` / ``countries`` / ``device_types`` /
  ``allowed_principal_ids`` — internal fields that travel alongside the
  wire shape through the server-side filter pipeline

Filters operate on ``ResolvedProduct``. At the wire boundary, projects
``r.wire`` to get the spec-compliant payload.

``__getattr__`` delegates unknown attribute lookups to ``wire`` so
common reads like ``resolved.product_id`` or ``resolved.name`` work
without callers having to write ``resolved.wire.product_id``. Internal
fields are explicit dataclass attributes and shadow any accidental
collision with library fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adcp.types import Product as LibraryProduct


@dataclass(frozen=True)
class ResolvedProduct:
    """Wire-shape Product + server-side internal fields.

    Read ``resolved.<wire-field>`` for any AdCP spec field
    (delegates to ``wire``). Read internal fields directly:
    ``resolved.implementation_config``, ``resolved.countries``, etc.
    """

    wire: LibraryProduct
    implementation_config: dict[str, Any] | None = None
    countries: list[str] | None = None
    device_types: list[str] | None = None
    allowed_principal_ids: list[str] | None = None

    def __getattr__(self, name: str) -> Any:
        # ``__getattr__`` only fires when the attribute isn't found via
        # normal lookup, so dataclass fields and methods take precedence
        # over wire-side delegation.
        return getattr(object.__getattribute__(self, "wire"), name)
