"""The persistence mapping layer: one translation per aggregate between a domain model and its row.

See [docs/design/persistence-mapping-layer.md](../../../../docs/design/persistence-mapping-layer.md).
"""

from src.core.database.mapping._base import RowMapper, unmapped_columns

__all__ = ["RowMapper", "unmapped_columns"]
