"""Unit tests for src.core.schemas._base.strip_none_deep (#1868 review).

strip_none_deep's docstring says it "recursively drop[s] None values from
nested dicts/lists". The dict branch does (``if v is not None``); the list
branch didn't -- it recursed into each element but never filtered a bare
``None`` list element itself, so a payload like
``{"a": [None, {"x": None, "y": 2}]}`` kept the top-level list ``None``.

No test previously called this function directly -- the only prior match
under tests/ was an AST name matcher in a structural guard, not a behavioral
test (per the #1868 review finding).
"""

from __future__ import annotations

from src.core.schemas._base import strip_none_deep


class TestStripNoneDeepDict:
    """Dict branch: already correct, pinned here as a regression guard."""

    def test_drops_none_values_from_dict(self):
        assert strip_none_deep({"a": 1, "b": None, "c": 2}) == {"a": 1, "c": 2}

    def test_recurses_into_nested_dict(self):
        assert strip_none_deep({"a": {"x": None, "y": 2}}) == {"a": {"y": 2}}


class TestStripNoneDeepList:
    """List branch: bare None list elements must be dropped too (#1868 review)."""

    def test_drops_bare_none_list_elements(self):
        assert strip_none_deep([1, None, 2]) == [1, 2]

    def test_drops_none_elements_in_list_of_dicts(self):
        """Exact reproduction from the #1868 review finding."""
        result = strip_none_deep({"a": [None, {"x": None, "y": 2}]})
        assert result == {"a": [{"y": 2}]}


class TestStripNoneDeepTopLevelNone:
    """A bare top-level None must survive unstripped.

    product.py's core_fields comment relies on this: a core field
    force-included as null by a parent model_dump() override must stay
    null through this pass, not be stripped by it (strip_none_deep is only
    ever called on a field's *value*, e.g. ``strip_none_deep(value)`` for
    ``key, value in data.items()`` -- it never sees the enclosing dict, so
    it cannot itself decide whether a top-level None should be dropped).
    """

    def test_bare_none_survives(self):
        assert strip_none_deep(None) is None
