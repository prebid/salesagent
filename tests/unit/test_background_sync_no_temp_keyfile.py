"""Regression test for hmcd: service account credentials must not persist on disk.

background_sync_service.py wrote GAM service account JSON to a temp file, then
read it with from_service_account_file. If os.unlink failed, credentials
remained on disk. The fix uses from_service_account_info (dict, no file).

GH #1078 follow-up — security.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SYNC_FILE = Path("src/services/background_sync_service.py")


class TestNoTempKeyfileForServiceAccount:
    """GAM service account auth must not write credentials to temp files."""

    def test_no_named_temporary_file_usage(self):
        """background_sync_service must not use NamedTemporaryFile.

        Service account JSON should be passed as a dict via
        from_service_account_info, not written to a temp file via
        from_service_account_file. Temp files risk credential leakage
        if cleanup fails.
        """
        source = _SYNC_FILE.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "NamedTemporaryFile":
                pytest.fail(
                    f"background_sync_service.py:{node.lineno} uses NamedTemporaryFile — "
                    "service account credentials must use from_service_account_info "
                    "to avoid writing secrets to disk"
                )

    def test_uses_from_service_account_info(self):
        """background_sync_service must use from_service_account_info (not _file)."""
        source = _SYNC_FILE.read_text()

        assert "from_service_account_info" in source, (
            "background_sync_service.py should use from_service_account_info "
            "(dict-based, no temp file) instead of from_service_account_file"
        )
