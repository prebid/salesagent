"""Shared assertions for the AdCP v3.1 format_id federation contract.

A serialized ``format_id`` MUST be an object carrying both ``agent_url`` and
``id`` — never a bare string. This is the **schema** contract
(``core/format-id.json``: ``required: [agent_url, id]``, never a plain string).
The storyboard (``creative/index.yaml`` discover_formats / list_formats) only
grades ``field_present`` on ``formats[0]``; this helper is intentionally
stricter — every entry, never a bare string.

Used by the UC-005 ``format-id-shape`` BDD steps and their falsifiability unit
test; reusable by the ``roundtrip-from-products`` / ``third-party-agent``
sibling scenarios.
"""

from __future__ import annotations

from typing import Any


def assert_wire_format_id_is_object(fid: Any) -> None:
    """Assert a single serialized ``format_id`` is an object with ``agent_url`` + ``id``.

    ``isinstance(fid, dict)`` is the falsifiable check: a regression that flattens
    the structured object to its ``id`` string on the wire serializes as ``str``
    and fails here. Asserts *presence* of ``agent_url`` and ``id``, not an exact
    key set — adcp 5.7.0 adds optional ``width``/``height``/``duration_ms``, and
    ``agent_url`` normalizes with a trailing slash so its value is not asserted.

    Args:
        fid: A single ``format_id`` value as it appears on the serialized wire.

    Raises:
        AssertionError: if ``fid`` is not an object, or is missing either key.
    """
    assert isinstance(fid, dict), f"format_id must serialize as an object, got {type(fid).__name__}: {fid!r}"
    assert "agent_url" in fid, f"format_id missing agent_url: {fid!r}"
    assert "id" in fid, f"format_id missing id: {fid!r}"
