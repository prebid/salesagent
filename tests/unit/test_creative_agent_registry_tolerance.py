"""Regression test for per-format resilient ingestion (salesagent-az8d, gates o94v).

The owner-flagged invariant: a single non-conforming format in a creative
agent's list_creative_formats response must NOT invalidate the whole batch.
``src/core/creative_agent_registry.py::_validate_formats_tolerant`` today
tolerates ONLY the *additive asset_type* path (line ~135-137 ``continue``);
any other ``ValidationError`` hits line ~138 ``raise`` and nukes the batch.
No existing test demonstrates this gap — this file fills it.

Pattern: the test is the failing-test gate for the production fix
(`salesagent-az8d`). It is wrapped in ``xfail(strict=True)`` so:
- ``--runxfail`` shows the test fail today (proving the gap)
- normal run xfails clean (no suite redness)
- when ``az8d`` lands and the helper salvages the conforming formats, the
  test xpasses -> strict-fail -> forces marker removal.
"""

import logging

import pytest

from src.core.creative_agent_registry import _validate_formats_tolerant


def _good(format_id: str, name: str) -> dict:
    """Minimal Format dict that the adcp library validates cleanly."""
    return {"format_id": {"id": format_id, "agent_url": "https://example.com"}, "name": name}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "salesagent-az8d: per-format resilience for non-asset-type ValidationError "
        "is not implemented — _validate_formats_tolerant raises (L138) and nukes the "
        "whole batch when any format fails for a non-additive-asset_type reason. "
        "Lands green when az8d implements drop+log per-format for ANY ValidationError."
    ),
)
def test_non_asset_type_malformed_format_must_not_nuke_batch():
    """One malformed format (missing required ``name``) must be dropped+logged.

    The remaining well-formed formats MUST be returned. Today the helper raises
    a Pydantic ``ValidationError`` and discards every format, including the
    conforming ones.
    """
    good_a = _good("good_a", "Good A")
    bad_missing_name = {"format_id": {"id": "bad", "agent_url": "https://example.com"}}  # required `name` missing
    good_b = _good("good_b", "Good B")

    logger = logging.getLogger("salesagent.tests.az8d")
    result = _validate_formats_tolerant([good_a, bad_missing_name, good_b], logger)

    returned_ids = {fmt.format_id.id for fmt in result}
    assert returned_ids == {"good_a", "good_b"}, (
        f"az8d invariant: only the two conforming formats must survive, got {returned_ids}"
    )
    assert len(result) == 2, f"expected exactly 2 conforming formats, got {len(result)}"
