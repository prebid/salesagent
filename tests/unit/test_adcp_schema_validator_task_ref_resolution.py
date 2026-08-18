"""``_find_schema_ref_for_task`` resolves a doubly-defined task by index key order.

``list-creative-formats`` is defined in TWO sections of the pinned index:
``media-buy`` (the sales-agent-facing ``format_ids`` + ``creative_agents`` list)
and ``creative`` (the authoritative full format definitions). They are different
schemas under the same task name, so which one a payload is graded against is a
real contract choice — and ``_find_schema_ref_for_task`` makes it implicitly, by
iterating ``index["schemas"]`` in the index's own key order and returning the
first hit. media-buy precedes creative there, so media-buy wins.

That was documented in the method's docstring ("re-sorting the sections would
silently flip it") and graded by nothing — a `sorted()` added to the iteration,
or an upstream index reordering, would move every ``list-creative-formats``
payload onto a different schema with no test turning red. This module grades it:
the winner today, the flip under a re-sorted index, and the double definition
that makes the choice exist at all.

GH #1868
"""

from __future__ import annotations

import pytest

from tests.helpers.adcp_schema_validator import AdCPSchemaValidator

_TASK = "list-creative-formats"


@pytest.fixture
def validator() -> AdCPSchemaValidator:
    return AdCPSchemaValidator()


async def test_task_is_defined_in_both_sections(validator):
    """Meta-guard: the double definition still exists in the pinned index.

    Without this, the winner assertion below would pass vacuously if upstream
    ever dropped the creative-side definition — there would be no competition
    left to resolve, and the order dependence would be untested silently.
    """
    index = await validator.get_schema_index()
    sections = index["schemas"]

    defining = [name for name, section in sections.items() if _TASK in section.get("tasks", {})]

    assert defining == ["media-buy", "creative"], (
        f"{_TASK!r} is expected to be defined in exactly the media-buy and creative sections, "
        f"in that index order; found {defining}. If upstream changed this, the resolution "
        "choice below changed with it — re-derive it, do not just update the list."
    )


@pytest.mark.parametrize("kind", ["request", "response"])
async def test_media_buy_section_wins_for_doubly_defined_task(validator, kind):
    """The resolved ref is the media-buy section's, because it comes first in the index."""
    ref = await validator._find_schema_ref_for_task(_TASK, kind)

    assert ref == f"media-buy/{_TASK}-{kind}.json", (
        f"{_TASK} {kind} resolved to {ref!r}. The media-buy section precedes creative in the "
        "pinned index's key order, so its (sales-agent-facing) schema is the one payloads are "
        "graded against — if this flipped to creative/, every list-creative-formats payload is "
        "now being validated against a different contract."
    )


@pytest.mark.parametrize("kind", ["request", "response"])
async def test_resolution_flips_when_sections_are_reordered(validator, kind):
    """The choice really is order-dependent — it is not media-buy for some other reason.

    Sorting the sections alphabetically puts creative before media-buy. If a
    future refactor adds ``sorted()`` to the iteration in
    ``_find_schema_ref_for_task``, this is the behavior it would ship.
    """
    index = await validator.get_schema_index()
    validator._index_cache = {**index, "schemas": dict(sorted(index["schemas"].items()))}

    ref = await validator._find_schema_ref_for_task(_TASK, kind)

    assert ref == f"creative/{_TASK}-{kind}.json", (
        f"Re-sorted the index sections (creative now precedes media-buy) and {_TASK} {kind} "
        f"still resolved to {ref!r} — the resolution is not actually following index key "
        "order, so the docstring's stated mechanism is wrong."
    )
