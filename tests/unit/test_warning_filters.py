"""Tests that pytest.ini filterwarnings correctly suppresses library ResourceWarnings.

Bug: salesagent-kmop — 29 ResourceWarnings from CPython asyncio internals clutter
test output. These should be suppressed in pytest.ini while keeping our own code's
ResourceWarnings visible.
"""

import configparser
import re
from pathlib import Path

PYTEST_INI = Path(__file__).resolve().parents[2] / "pytest.ini"


def _parse_filterwarnings() -> list[str]:
    """Parse the filterwarnings entries from pytest.ini."""
    config = configparser.ConfigParser()
    config.read(str(PYTEST_INI))
    raw = config.get("pytest", "filterwarnings", fallback="")
    return [line.strip() for line in raw.splitlines() if line.strip()]


class TestAsyncioResourceWarningFilters:
    """Verify that pytest.ini suppresses asyncio-originated ResourceWarnings."""

    def test_base_events_resource_warning_suppressed(self):
        """asyncio/base_events.py ResourceWarning (unclosed event loop) must be suppressed."""
        filters = _parse_filterwarnings()
        # Look for a filter that ignores ResourceWarning from asyncio.base_events
        has_filter = any(
            "ResourceWarning" in f and "asyncio" in f and "base_events" in f and f.startswith("ignore") for f in filters
        )
        assert has_filter, (
            "pytest.ini filterwarnings must contain an ignore entry for "
            "ResourceWarning from asyncio.base_events. Current entries:\n" + "\n".join(f"  {f}" for f in filters)
        )

    def test_selector_events_resource_warning_suppressed(self):
        """asyncio/selector_events.py ResourceWarning (unclosed transport) must be suppressed."""
        filters = _parse_filterwarnings()
        has_filter = any(
            "ResourceWarning" in f and "asyncio" in f and "selector_events" in f and f.startswith("ignore")
            for f in filters
        )
        assert has_filter, (
            "pytest.ini filterwarnings must contain an ignore entry for "
            "ResourceWarning from asyncio.selector_events. Current entries:\n" + "\n".join(f"  {f}" for f in filters)
        )

    def test_own_code_resource_warnings_not_suppressed(self):
        """ResourceWarnings from src/ and tests/ must NOT be suppressed.

        Verify no blanket 'ignore::ResourceWarning' exists (which would hide
        warnings from our own code).
        """
        filters = _parse_filterwarnings()
        blanket_ignores = [f for f in filters if re.match(r"^ignore::ResourceWarning\s*$", f)]
        assert not blanket_ignores, (
            "pytest.ini must NOT contain a blanket 'ignore::ResourceWarning' — "
            "this would hide ResourceWarnings from our own code. Found:\n"
            + "\n".join(f"  {f}" for f in blanket_ignores)
        )

    def test_filter_uses_module_scoping(self):
        """Each ResourceWarning filter must specify a module pattern (column 4).

        Format: ignore::ResourceWarning:module_regex
        A filter without module scope would suppress ALL ResourceWarnings.
        """
        filters = _parse_filterwarnings()
        resource_filters = [f for f in filters if "ResourceWarning" in f]
        for rf in resource_filters:
            parts = rf.split(":")
            # Format: action:message:category:module:lineno
            # With :: shorthand, parts will be ['ignore', '', 'ResourceWarning', 'module', ...]
            # We need at least 4 parts and the module part must not be empty
            assert len(parts) >= 4, f"ResourceWarning filter must include a module pattern: {rf}"
            module_part = parts[3].strip()
            assert module_part, f"ResourceWarning filter has empty module pattern (would suppress all): {rf}"
