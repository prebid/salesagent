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

    def test_no_from_service_account_file(self):
        """background_sync_service must not call from_service_account_file.

        Service account credentials must be passed as a dict — either directly
        via google.oauth2.service_account.Credentials.from_service_account_info,
        or indirectly via GAMClientManager / GAMAuthManager which use the same
        dict-based call. from_service_account_file requires writing JSON to
        disk and risks credential leakage on cleanup failure.
        """
        source = _SYNC_FILE.read_text()

        assert "from_service_account_file" not in source, (
            "background_sync_service.py must not use from_service_account_file — "
            "use GAMClientManager + build_gam_config_from_adapter, which routes "
            "service account JSON through from_service_account_info (dict-based)"
        )
