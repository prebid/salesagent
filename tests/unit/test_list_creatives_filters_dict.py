"""Regression: list_creatives must accept ``filters`` as a dict.

The MCP delegate (`core/platforms/_delegate.py:_delegate_list_creatives`)
serialises the request via ``req.model_dump(exclude_unset=True)`` before
forwarding to ``_list_creatives_impl``. That means ``filters`` arrives as a
``dict``, not a ``CreativeFilters`` Pydantic model.

The impl was unconditionally calling ``filters.model_dump(...)``, raising
``AttributeError: 'dict' object has no attribute 'model_dump'`` and surfacing
to clients as ``INTERNAL_ERROR: Platform method 'list_creatives' raised
AttributeError``.

These tests pin both supported input shapes (model + dict) so the impl keeps
working for both transport paths.
"""

import pytest

from src.core.exceptions import AdCPAuthenticationError
from src.core.tools.creatives.listing import _list_creatives_impl


def test_filters_as_dict_does_not_raise_attribute_error():
    """``filters`` arriving as a dict must not raise AttributeError.

    With ``identity=None`` the impl should reach the auth gate and raise
    ``AdCPAuthenticationError`` — never the AttributeError that surfaced in
    production via the MCP delegate's ``model_dump(exclude_unset=True)`` path.
    """
    with pytest.raises(AdCPAuthenticationError):
        _list_creatives_impl(
            filters={"media_buy_ids": ["mb_test_nonexistent"]},
            identity=None,
        )


def test_filters_as_pydantic_model_still_works():
    """``filters`` arriving as a CreativeFilters model continues to work."""
    from adcp import CreativeFilters

    with pytest.raises(AdCPAuthenticationError):
        _list_creatives_impl(
            filters=CreativeFilters(media_buy_ids=["mb_test_nonexistent"]),
            identity=None,
        )


def test_no_filters_still_works():
    """The no-filters path is unaffected."""
    with pytest.raises(AdCPAuthenticationError):
        _list_creatives_impl(identity=None)
