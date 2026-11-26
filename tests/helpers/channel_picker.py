"""The admin channel picker, read out of rendered HTML the way a browser reads the form (GH #1532).

One reader for the template guard (``tests/unit/test_architecture_channel_selector_enum.py``)
and the admin page tests (``tests/admin/test_product_channel_picker.py``), so the two cannot
disagree about what an option is.
"""

from __future__ import annotations

from bs4 import BeautifulSoup


def picker_options(html: str) -> list[tuple[str, bool]]:
    """``(value, selected)`` per ``<option>`` of the ``select#channels`` in *html*, in document order.

    Parsed rather than pattern-matched, so attribute order, quoting and extra attributes
    cannot hide an option. Document order is the order a browser posts the selected ones in.
    """
    select = BeautifulSoup(html, "html.parser").select_one("select#channels")
    assert select is not None, "the page carries no channel picker"
    return [(option["value"], option.has_attr("selected")) for option in select.find_all("option")]
