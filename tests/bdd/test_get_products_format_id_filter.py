"""BDD scenario binding for the legacy format_id filter on get_products.

The scenario stores real ``products.format_ids`` documents under two different declaring
agents and reads which products a buyer receives: the (agent_url, id) pair matching
``core/format-id.json`` makes a MUST of anyone comparing two references, which this filter
carried no implementation of -- it compared bare ids.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-GET-PRODUCTS-format-id-filter.feature")
