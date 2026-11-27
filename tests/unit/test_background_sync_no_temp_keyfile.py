"""Regression test for hmcd: service account credentials must not persist on disk.

background_sync_service.py wrote GAM service account JSON to a temp file, then
read it with from_service_account_file. If os.unlink failed, credentials
remained on disk. The fix uses from_service_account_info (dict, no file).

Every GAM client is now built by ``GAMClientManager.for_adapter_config``, whose
credentials come from ``GAMAuthManager``, so that is the file this test reads.

GH #1078 follow-up — security.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CREDENTIALS_FILE = Path("src/adapters/gam/auth.py")


class TestNoTempKeyfileForServiceAccount:
    """GAM service account auth must not write credentials to temp files."""

    def test_no_named_temporary_file_usage(self):
        """The GAM credentials builder must not use NamedTemporaryFile.

        Service account JSON should be passed as a dict via
        from_service_account_info, not written to a temp file via
        from_service_account_file. Temp files risk credential leakage
        if cleanup fails.
        """
        source = _CREDENTIALS_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "NamedTemporaryFile":
                pytest.fail(
                    f"{_CREDENTIALS_FILE}:{node.lineno} uses NamedTemporaryFile — "
                    "service account credentials must use from_service_account_info "
                    "to avoid writing secrets to disk"
                )

    def test_uses_from_service_account_info(self):
        """The GAM credentials builder must build stored service account JSON with from_service_account_info."""
        source = _CREDENTIALS_FILE.read_text(encoding="utf-8")

        assert "from_service_account_info" in source, (
            f"{_CREDENTIALS_FILE} should use from_service_account_info "
            "(dict-based, no temp file) for stored service account JSON"
        )
