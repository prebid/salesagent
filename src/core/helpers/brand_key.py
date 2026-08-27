"""The (domain, brand_id) half of the account natural key, read in ONE place.

The AdCP account natural key is brand.domain + brand.brand_id + operator +
sandbox (BR-RULE-056). Reading its brand half looks trivial and is not:
``BrandReference.brand_id`` is ``BrandId``, a ``RootModel[str]`` that does NOT
override ``__str__``, so ``str(brand.brand_id)`` yields the repr
``"root='brand_one'"`` rather than ``brand_one``.

That is not a cosmetic defect. The mangled text was persisted into
``accounts.brand->>'brand_id'`` and used as the lookup key, while the resolver
read the unmangled value — so the account could never be resolved by its own
key, and because ``JSONType`` validates on read but not on write, the row could
not even be loaded through the ORM afterwards (salesagent-myhs). The cause was
five hand-rolled copies of this extraction disagreeing with each other; this
module exists so there is exactly one.

Leaf by design: it imports nothing from the repository, tool or helper layers,
so the repository (which owns the row) and the request-side callers can both
depend on it without a cycle.
"""

from __future__ import annotations

from typing import Any, overload

from adcp.types import BrandReference


@overload
def brand_key_parts(brand: BrandReference) -> tuple[str, str | None]: ...


@overload
def brand_key_parts(brand: BrandReference | dict | None) -> tuple[str | None, str | None]: ...


def brand_key_parts(brand: BrandReference | dict | None) -> tuple[Any, str | None]:
    """Read the (domain, brand_id) half of the natural key out of a brand reference.

    Accepts both shapes and neither is a fallback: a freshly built row assigns a
    plain dict, while a request payload and a row loaded from the database both
    carry a ``BrandReference``.

    The overloads keep callers that already hold a ``BrandReference`` from having
    to re-narrow ``domain``: it is a required ``str`` on that model, and only the
    dict/None arms can widen it to ``None``.
    """
    if brand is None:
        return None, None
    if isinstance(brand, dict):
        raw_id = brand.get("brand_id")
        return brand.get("domain"), None if raw_id is None else str(raw_id)
    return brand.domain, None if brand.brand_id is None else brand.brand_id.root
