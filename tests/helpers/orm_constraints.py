"""Read CHECK-constraint value-sets off an ORM model, for parity guards.

Several DB CHECK constraints in this schema are DERIVED copies of a single
source-of-truth value tuple (``ck_accounts_billing`` from
``src.core.billing_policy.BILLING_PARTY_VALUES``; ``ck_signing_keys_alg`` from
``src.core.signing.algorithms.SIGNING_ALG_VALUES``). Each one is pinned by a
parity guard that asserts the CHECK's literals equal the constant — the #1521
bug class, where a hand-written CHECK froze while the spec enum grew and a
spec-valid value IntegrityError'd on INSERT.

Every such guard needs the same two steps: locate the named CHECK on the model's
table, then pull its quoted literals out of the rendered SQL. That is one logical
operation with the model/constraint substituted, so it lives here once.
"""

from __future__ import annotations

import re
from typing import Any


def quoted_values(sql: str) -> tuple[str, ...]:
    """Extract the single-quoted value literals from a CHECK constraint's SQL text."""
    return tuple(re.findall(r"'([^']*)'", sql))


def check_constraint_sql(model: Any, constraint_name: str) -> str:
    """Return the SQL text of the named CHECK constraint on *model*'s table."""
    for constraint in model.__table__.constraints:
        if constraint.name == constraint_name:
            return str(constraint.sqltext)
    raise AssertionError(f"{model.__name__} has no CHECK constraint named {constraint_name}")


def check_constraint_values(model: Any, constraint_name: str) -> tuple[str, ...]:
    """Return the quoted literals of the named CHECK constraint on *model*'s table."""
    return quoted_values(check_constraint_sql(model, constraint_name))
