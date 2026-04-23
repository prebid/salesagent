"""Regression test: Xandr adapter must parse non-ISO date formats.

Xandr API historically returns dates as "YYYY-MM-DD HH:MM:SS" (space-separated).
Python's datetime.fromisoformat in Python < 3.11 rejects this format.
Python 3.11+ is more permissive but Xandr may also return other variants.

The fix uses dateutil.parser.parse() which handles any reasonable format.

GH #1078 follow-up — salesagent-7jan.
"""

import ast
from datetime import datetime

import pytest

pytestmark = pytest.mark.unit


class TestXandrDateFormats:
    """Xandr adapter must handle various date formats from the API."""

    def test_space_separated_datetime_parses(self):
        """Xandr's historical format 'YYYY-MM-DD HH:MM:SS' must parse."""
        from dateutil import parser as dateutil_parser

        result = dateutil_parser.parse("2026-04-15 00:00:00")
        assert result == datetime(2026, 4, 15, 0, 0, 0)

    def test_iso_format_still_parses(self):
        """Standard ISO format must still work."""
        from dateutil import parser as dateutil_parser

        result = dateutil_parser.parse("2026-04-15T00:00:00")
        assert result == datetime(2026, 4, 15, 0, 0, 0)

    def test_date_only_parses(self):
        """Date-only strings must parse."""
        from dateutil import parser as dateutil_parser

        result = dateutil_parser.parse("2026-04-15")
        assert result.date() == datetime(2026, 4, 15).date()

    def test_xandr_adapter_uses_dateutil_not_fromisoformat(self):
        """xandr.py must use dateutil.parser, not datetime.fromisoformat for API dates."""
        import ast
        from pathlib import Path

        source = Path("src/adapters/xandr.py").read_text()
        tree = ast.parse(source)

        # Find fromisoformat calls that operate on external API data
        # (io["start_date"], io["end_date"], li["start_date"], li["end_date"])
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "fromisoformat"
                and isinstance(node.value, ast.Name)
                and node.value.id == "datetime"
            ):
                # Check if the argument references external API data
                # (subscript on io or li variables)
                parent = _find_parent_call(tree, node)
                if parent and _is_external_api_date(parent):
                    pytest.fail(
                        f"xandr.py:{node.lineno} uses datetime.fromisoformat on external "
                        "Xandr API data — use dateutil.parser.parse() instead"
                    )


def _find_parent_call(tree: ast.AST, target: ast.AST) -> ast.Call | None:
    """Find the Call node that contains this attribute as its func."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.func is target:
            return node
    return None


def _is_external_api_date(call: ast.Call) -> bool:
    """Check if a fromisoformat call argument is an API response subscript."""
    if not call.args:
        return False
    arg = call.args[0]
    # Match patterns like io["start_date"], li["end_date"]
    if isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name):
        if arg.value.id in ("io", "li"):
            return True
    return False
