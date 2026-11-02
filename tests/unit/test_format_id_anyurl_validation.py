"""Format identity over FormatId objects whose ``agent_url`` is a pydantic ``AnyUrl``.

Regression origin: ``.rstrip()`` was called directly on ``AnyUrl`` during
create_media_buy's format validation and raised ``AttributeError``. These tests
used to guard that by RE-IMPLEMENTING the normalization inline and asserting the
re-implementation worked — which graded nothing in ``src/``. They now drive the
real entry point, ``format_resolver.format_identity_or_none``, which is the one
place that turns a reference of any shape into a comparison key.
"""

from src.core.format_resolver import format_display, format_identity_or_none
from src.core.schemas import FormatId

# The CANONICAL form: an empty path renders as "/" (algorithm step 5).
AGENT_INPUT = "https://creative.adcontextprotocol.org"
AGENT = "https://creative.adcontextprotocol.org/"


# REMOVED: test_anyurl_agent_url_does_not_raise_and_ignores_a_trailing_slash and
# test_case_port_and_fragment_are_canonicalized_away. Both asserted CANONICALIZATION
# rules -- step 5 (empty path and "/" are one agent), step 2 (host case), step 4 (default
# port dropped), step 8 (fragment stripped) -- and every one of those is graded directly
# against the spec's own equivalences in
# ``tests/unit/test_url_canonicalization_vectors.py`` (SAME_AGENT rows "step 5", "step 2:
# host case", "step 4: default port is dropped", "step 8: fragment is stripped"), on top of
# the 37 published conformance vectors run against the vendored implementation in
# ``src/vendor/adcp_canonical``. That module is deliberately MORE conformant than the
# pinned SDK, which fails 14 of the 37, so canonicalization is not a place where
# production is suspect by default.
#
# Duplicating those rules here re-asserted them through one extra caller, in a form that
# names an implementation value rather than a spec rule, and the two cases went red on a
# type change (FormatIdentity is a distinct type, not a tuple[str, str]) while the
# behavior they described was never wrong. The cases below are what this module still
# grades that the vector test cannot: that ``format_identity_or_none`` accepts a FormatId
# whose ``agent_url`` is an AnyUrl and a raw dict off the wire alike (the original
# regression was ``.rstrip()`` called on an AnyUrl), and that the display form spells the
# identity that was compared.


def test_product_and_package_references_compare_equal_across_url_spellings():
    """The two sides create_media_buy compares are built by different producers."""
    product_format = FormatId(agent_url=AGENT, id="display_300x250")
    package_format = {"agent_url": AGENT_INPUT, "id": "display_300x250"}  # off the wire, a dict

    assert format_identity_or_none(product_format) == format_identity_or_none(package_format)


def test_display_spells_the_identity_that_was_compared():
    """An error listing supported formats must not contradict the values compared."""
    identity = format_identity_or_none(FormatId(agent_url=AGENT, id="display_300x250"))

    assert identity is not None
    assert format_display(identity) == f"{AGENT}display_300x250"  # AGENT ends in "/"


def test_path_is_part_of_the_identity():
    """``/mcp`` is a different endpoint, not a spelling of the origin.

    Two normalizers used to ``removesuffix("/mcp")`` before comparing, which made
    one host's MCP endpoint and its bare origin the same agent. Nothing in the pin
    asks for that; ``canonicalize_target_uri`` preserves the path.
    """
    assert format_identity_or_none(FormatId(agent_url=f"{AGENT_INPUT}/mcp", id="d")) != format_identity_or_none(
        FormatId(agent_url=AGENT_INPUT, id="d")
    )
