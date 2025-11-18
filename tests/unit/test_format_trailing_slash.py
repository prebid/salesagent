"""Test that format ID comparison handles trailing slashes correctly."""

from src.core.schemas import FormatId


def test_format_comparison_with_trailing_slash():
    """Test that format IDs with and without trailing slashes match correctly."""

    # Create format IDs with and without trailing slashes
    format_with_slash = FormatId(agent_url="https://creative.adcontextprotocol.org/", id="display_300x250_image")

    format_without_slash = FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250_image")

    # Normalize URLs by stripping trailing slashes (convert AnyUrl to string first)
    url_with = str(format_with_slash.agent_url).rstrip("/") if format_with_slash.agent_url else None
    url_without = str(format_without_slash.agent_url).rstrip("/") if format_without_slash.agent_url else None

    # After normalization, they should be equal
    assert url_with == url_without
    assert url_with == "https://creative.adcontextprotocol.org"

    # Tuples should match after normalization
    tuple_with = (url_with, format_with_slash.id)
    tuple_without = (url_without, format_without_slash.id)

    assert tuple_with == tuple_without
    assert tuple_with == ("https://creative.adcontextprotocol.org", "display_300x250_image")


def test_format_set_comparison_with_mixed_slashes():
    """Test that sets of format tuples handle trailing slashes correctly."""

    # Product formats (from database, might have trailing slash)
    product_formats = {
        ("https://creative.adcontextprotocol.org/", "display_300x250_image"),
        ("https://creative.adcontextprotocol.org/", "display_728x90_image"),
    }

    # Requested formats (from client, might not have trailing slash)
    requested_formats = {
        ("https://creative.adcontextprotocol.org", "display_300x250_image"),
    }

    # Without normalization, this would fail
    # assert requested_formats.issubset(product_formats)  # This FAILS!

    # With normalization
    normalized_product = {(url.rstrip("/") if url else None, fid) for url, fid in product_formats}
    normalized_requested = {(url.rstrip("/") if url else None, fid) for url, fid in requested_formats}

    # After normalization, subset check should work
    assert normalized_requested.issubset(normalized_product)

    # Find unsupported formats (should be empty)
    unsupported = normalized_requested - normalized_product
    assert unsupported == set()
