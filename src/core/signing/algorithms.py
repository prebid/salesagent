"""FROZEN FORWARDING SHIM — exists for one committed migration, and for nothing else.

The signing value-sets moved OUT of this package to :mod:`src.core.signing_contract`
(salesagent-n78j0.3). They had to leave: Python runs a package's ``__init__`` before any
of its submodules, so ``from src.core.signing.algorithms import ...`` in
``src/core/database/models.py`` executed ``src/core/signing/__init__.py``, which imports
``keys`` -> ``database.models`` -> back into a half-initialised module. That cycle is what
``_LAZY_EXPORTS`` used to defer; moving the leaf out of the package removes it instead.

``alembic/versions/e7a2c40b91d5_add_signing_keys_table.py`` :37 imports this path, and
CLAUDE.md says "Never modify existing migrations after commit!" — so the migration stays
BYTE-IDENTICAL and this module keeps its import resolving.

**This file holds NO LOGIC, and must not grow any.** It re-exports names; it computes
nothing. A shim that computes is a second definition site, which is the exact fault
(one rule, two places) that salesagent-n78j0.3 removed — at which point the CHECK body
would again have two homes that can drift. Imports and ``__all__``, nothing else.

Do not import this from new code. New callers take a CLAUSE from
:func:`~src.core.signing_contract.signing_alg_check_clause`, or the value-set from
:mod:`src.core.signing_contract` directly.

RELATED DEFECT, filed not fixed: this migration interpolates these LIVE constants into
the DDL it emits, so two databases at the same alembic revision can carry different CHECK
constraints with nothing recording the divergence — ``salesagent-89p27``. Freezing it
needs a NEW migration pinning the values as literals, which is its own atom.
"""

from src.core.signing_contract.algorithms import (
    MINTABLE_PURPOSES,
    REQUEST_SIGNING,
    SIGNING_ALG_VALUES,
    sql_value_list,
)

__all__ = [
    "MINTABLE_PURPOSES",
    "REQUEST_SIGNING",
    "SIGNING_ALG_VALUES",
    "sql_value_list",
]
