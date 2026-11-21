"""BDD scenario binding for the canonical format filters on get_products.

Two filters the request DECLARED and nothing read: ``filters.format_kinds``, which had
zero production references, and ``filters.standard_formats_only``, which was keyed on a
prefix heuristic over the legacy slug and therefore selected a canonical-only product
unconditionally once migration 08cd415b3d91 made one representable.

core/product-filters.json's root is what makes both a correctness defect rather than a
missing capability: filters "decide which products MAY BE RETURNED", so ignoring one hands
the buyer products it asked to exclude and nothing in the response says so.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-GET-PRODUCTS-canonical-format-filters.feature")
