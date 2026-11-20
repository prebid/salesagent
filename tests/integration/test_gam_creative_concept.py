"""The seller-side concept the GAM adapter puts on each creative it pushes (#1506).

AdCP exposes ``concept_id``/``concept_name`` read-only on list_creatives but carries no concept
on sync_creatives, so there is no protocol writer. GAM has no first-class creative group, so
the adapter falls back to the GAM Order as the closest native grouping. The value is namespaced
(``gam-order-<id>``) and tagged with ``concept_source`` so it stays distinguishable from a
future buyer-supplied concept.

The validation tests that lived in this file drove the adapter's legacy creative path, which
guessed a creative's type and size from loose asset keys; that path is gone, and every creative
reaches the adapter with the carrier core read for it.
"""

from datetime import datetime

import pytest

from tests.helpers.gam_client import build_gam_adapter, gam_tag_asset, stub_gam_client_manager
from tests.helpers.gam_client import gam_line_item as _line_item

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _build_adapter(*, line_items=()):
    """Build the GAM adapter over a stand-in GAM client serving the order's *line_items*."""
    return build_gam_adapter(stub_gam_client_manager(line_items=line_items))


def test_approved_creative_carries_seller_side_concept_from_order():
    """An approved creative carries the GAM-Order-derived seller-side concept."""
    adapter = _build_adapter(line_items=[_line_item("test_package", sizes=((970, 250),))])

    result = adapter.add_creative_assets("789", [gam_tag_asset("cr-concept", size=(970, 250))], datetime.now())

    assert len(result) == 1
    status = result[0]
    assert status.status == "approved"
    assert (status.concept_id, status.concept_name, status.concept_source) == (
        "gam-order-789",
        "GAM Order 789",
        "gam_order",
    )


def test_failed_creative_carries_no_concept_enrichment():
    """A creative GAM is not handed (its size fits no placeholder) is never pushed, so it gets no concept."""
    adapter = _build_adapter(line_items=[_line_item("test_package", sizes=((728, 90),))])

    result = adapter.add_creative_assets("789", [gam_tag_asset("cr-misfit", size=(970, 250))], datetime.now())

    assert len(result) == 1
    status = result[0]
    assert status.status == "failed"
    assert (status.concept_id, status.concept_name, status.concept_source) == (None, None, None)
