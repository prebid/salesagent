"""BDD scenario binding for canonical format options announced through get_products.

Scenarios store real ``products.format_options`` documents and read what a buyer receives:
the canonical half of core/product.json's "format_ids, format_options, or BOTH" MUST, which
this seller carried no implementation of at all.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-GET-PRODUCTS-format-options.feature")
