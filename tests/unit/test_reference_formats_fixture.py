"""Lock the reference-format fixture as the testing-mode source of truth (issue #1418).

The checked-in fixture (tests/fixtures/creative_formats/reference_formats.json) is what
ADCP_TESTING=true serves via creative_agent_registry._get_reference_formats(). These tests
assert the registry serves exactly the fixture, the fixture's structural invariants hold,
load_reference_formats() memoizes and fails loud, and the legacy_id_map still backs
upgrade_legacy_format_id.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.format_cache import (
    CACHE_FILE,
    DEFAULT_AGENT_URL,
    load_format_cache,
    load_reference_formats,
    upgrade_legacy_format_id,
)
from src.core.schemas import Format


@pytest.fixture
def fixture_data() -> dict:
    """Raw JSON contents of the checked-in reference fixture."""
    with open(CACHE_FILE) as f:
        return json.load(f)


def test_registry_serves_the_fixture(fixture_data: dict) -> None:
    """_get_reference_formats() returns exactly the fixture's formats (the drift lock)."""
    from src.core.creative_agent_registry import _get_reference_formats

    served = _get_reference_formats()
    served_ids = [f.format_id.id for f in served]
    fixture_ids = [entry["format_id"]["id"] for entry in fixture_data["formats"]]

    assert served_ids == fixture_ids, "registry format ids must equal the fixture format ids in order"

    # Per-format model equality after round-trip through Format.model_validate.
    for served_fmt, raw in zip(served, fixture_data["formats"], strict=True):
        assert served_fmt == Format.model_validate(raw)


def test_fixture_is_non_empty(fixture_data: dict) -> None:
    """The captured catalog must contain formats (never an empty catalog)."""
    assert fixture_data["formats"], "reference fixture must contain captured formats"
    assert len(load_reference_formats()) == len(fixture_data["formats"])


def test_fixture_format_ids_are_unique(fixture_data: dict) -> None:
    """No duplicate format_ids in the captured catalog."""
    ids = [entry["format_id"]["id"] for entry in fixture_data["formats"]]
    assert len(ids) == len(set(ids)), f"duplicate format_ids: {sorted({i for i in ids if ids.count(i) > 1})}"


def test_fixture_agent_url_is_canonical(fixture_data: dict) -> None:
    """Every captured format carries the canonical agent_url (normalized at capture)."""
    urls = {entry["format_id"]["agent_url"] for entry in fixture_data["formats"]}
    assert urls == {DEFAULT_AGENT_URL}, f"non-canonical agent_url(s) in fixture: {urls - {DEFAULT_AGENT_URL}}"


def test_every_fixture_entry_validates_as_format(fixture_data: dict) -> None:
    """Every fixture entry validates against src.core.schemas.Format and keeps is_standard."""
    for entry in fixture_data["formats"]:
        fmt = Format.model_validate(entry)
        assert fmt.is_standard is True, f"{entry['format_id']['id']} must carry is_standard=True"


def test_load_reference_formats_is_memoized() -> None:
    """load_reference_formats() returns the same memoized object across calls."""
    assert load_reference_formats() is load_reference_formats()


def test_load_reference_formats_fails_loud_on_missing_file(monkeypatch, tmp_path: Path) -> None:
    """A missing fixture raises (No Quiet Failures) — never returns an empty list."""
    from src.core import format_cache

    load_reference_formats.cache_clear()
    monkeypatch.setattr(format_cache, "CACHE_FILE", tmp_path / "does_not_exist.json")
    try:
        with pytest.raises(FileNotFoundError):
            load_reference_formats()
    finally:
        load_reference_formats.cache_clear()


def test_load_reference_formats_fails_loud_on_empty_formats(monkeypatch, tmp_path: Path) -> None:
    """A fixture with no formats raises rather than silently serving nothing."""
    from src.core import format_cache

    bad = tmp_path / "empty.json"
    bad.write_text(json.dumps({"schema_version": 2, "legacy_id_map": {}, "formats": []}))
    load_reference_formats.cache_clear()
    monkeypatch.setattr(format_cache, "CACHE_FILE", bad)
    try:
        with pytest.raises(ValueError, match="no 'formats' entries"):
            load_reference_formats()
    finally:
        load_reference_formats.cache_clear()


def test_legacy_id_map_backs_upgrade(fixture_data: dict) -> None:
    """legacy_id_map keys still upgrade to namespaced FormatId (test_format_cache regression)."""
    legacy_map = load_format_cache()
    assert legacy_map == fixture_data["legacy_id_map"]
    assert legacy_map, "legacy_id_map must be preserved for upgrade_legacy_format_id"

    for legacy_id in legacy_map:
        upgraded = upgrade_legacy_format_id(legacy_id)
        assert upgraded.id == legacy_id
        assert str(upgraded.agent_url).rstrip("/") == DEFAULT_AGENT_URL.rstrip("/")
