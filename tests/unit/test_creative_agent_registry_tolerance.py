"""Per-format resilient ingestion (#1333).

The invariant: a single non-conforming format in a creative agent's
``list_creative_formats`` response must NOT invalidate the whole batch.
``_validate_formats_tolerant`` drops what the pinned adcp schema cannot validate,
logs one aggregated warning, and returns the conforming remainder.

Seen live on 2026-09-15: the public reference agent added ``pixel_ratio`` to
``display_image.accepts_parameters``; adcp 6.6.0 knows only ``dimensions`` and
``duration``; the old ``raise`` turned that one entry into "0 formats" for every
buyer (``list_creative_formats``) and every admin format picker.
"""

import logging

from src.core.creative_agent_registry import _validate_formats_tolerant

AGENT = "https://example.com"


def _good(format_id: str, name: str) -> dict:
    """Minimal Format dict that the adcp library validates cleanly."""
    return {"format_id": {"id": format_id, "agent_url": AGENT}, "name": name}


def test_non_asset_type_malformed_format_must_not_nuke_batch(caplog):
    """One malformed format (missing required ``name``) is dropped and logged."""
    good_a = _good("good_a", "Good A")
    bad_missing_name = {"format_id": {"id": "bad", "agent_url": AGENT}}  # required `name` missing
    good_b = _good("good_b", "Good B")

    logger = logging.getLogger("salesagent.tests.tolerance")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        result = _validate_formats_tolerant([good_a, bad_missing_name, good_b], logger)

    assert {fmt.format_id.id for fmt in result} == {"good_a", "good_b"}
    assert len(result) == 2
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "one aggregated warning, not one per format"
    assert "bad" in warnings[0].getMessage()
    assert "name" in warnings[0].getMessage()


def test_live_reference_agent_pixel_ratio_entry_is_kept_minus_the_parameter(caplog):
    """The exact shape the public reference agent served on 2026-09-15.

    ``display_image`` is the format most products name; it must survive, with the
    parameter values the pinned schema does model.
    """
    display_image = {
        "format_id": {"id": "display_image", "agent_url": AGENT},
        "name": "Display Image",
        "accepts_parameters": ["dimensions", "pixel_ratio"],  # pixel_ratio unknown to adcp 6.6.0
    }
    display_html = _good("display_html", "Display HTML")

    logger = logging.getLogger("salesagent.tests.tolerance")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        result = _validate_formats_tolerant([display_image, display_html], logger)

    by_id = {fmt.format_id.id: fmt for fmt in result}
    assert set(by_id) == {"display_image", "display_html"}
    assert [p.value for p in by_id["display_image"].accepts_parameters] == ["dimensions"]
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert "display_image" in caplog.records[0].getMessage()
    assert "pixel_ratio" in caplog.records[0].getMessage()


def test_unknown_parameter_plus_another_defect_is_dropped(caplog):
    """Stripping the parameter only rescues a format that is otherwise valid."""
    bad = {
        "format_id": {"id": "bad", "agent_url": AGENT},
        "accepts_parameters": ["pixel_ratio"],  # and `name` is missing
    }
    logger = logging.getLogger("salesagent.tests.tolerance")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        result = _validate_formats_tolerant([bad, _good("ok", "OK")], logger)
    assert [fmt.format_id.id for fmt in result] == ["ok"]
    assert "name" in caplog.records[-1].getMessage()


def test_batch_where_nothing_survives_is_logged_as_error(caplog):
    """An empty remainder is contract breakage, so it is loud (ERROR), but still not an exception."""
    logger = logging.getLogger("salesagent.tests.tolerance")
    bad = {"format_id": {"id": "bad", "agent_url": AGENT}}
    with caplog.at_level(logging.WARNING, logger=logger.name):
        result = _validate_formats_tolerant([bad], logger)
    assert result == []
    assert caplog.records[-1].levelno == logging.ERROR


def test_all_conforming_formats_pass_through_silently(caplog):
    logger = logging.getLogger("salesagent.tests.tolerance")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        result = _validate_formats_tolerant([_good("a", "A"), _good("b", "B")], logger)
    assert len(result) == 2
    assert not caplog.records
