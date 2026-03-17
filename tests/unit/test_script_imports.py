"""Regression test: scripts must not have broken imports.

Validates that standalone scripts in scripts/ and tests/scripts/ can be
imported without ImportError. Catches cases where refactoring removes
a function that scripts still depend on.

Fixes: salesagent-0q8
"""

import importlib
import sys
from pathlib import Path

import pytest


class TestScriptImports:
    """Verify standalone scripts don't have broken imports."""

    def test_initialize_tenant_mgmt_api_key_imports(self):
        """scripts/initialize_tenant_mgmt_api_key.py must import without error."""
        script_path = Path("scripts/initialize_tenant_mgmt_api_key.py")
        assert script_path.exists(), f"Script not found: {script_path}"

        spec = importlib.util.spec_from_file_location("init_api_key_script", script_path)
        module = importlib.util.module_from_spec(spec)

        # The script imports from src.admin.sync_api — this must not raise ImportError
        try:
            spec.loader.exec_module(module)
        except ImportError as e:
            pytest.fail(f"Script has broken import: {e}")
        except Exception:
            # Other errors (e.g., missing DB) are OK — we only care about ImportError
            pass

    def test_quick_sync_test_imports(self):
        """tests/scripts/quick_sync_test.py must import without error."""
        script_path = Path("tests/scripts/quick_sync_test.py")
        assert script_path.exists(), f"Script not found: {script_path}"

        # This script uses 'from sync_api import ...' (relative-style)
        # so we need to add src/admin to sys.path temporarily
        admin_path = str(Path("src/admin").resolve())
        original_path = sys.path[:]
        try:
            sys.path.insert(0, admin_path)
            spec = importlib.util.spec_from_file_location("quick_sync_script", script_path)
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except ImportError as e:
                pytest.fail(f"Script has broken import: {e}")
            except Exception:
                pass
        finally:
            sys.path[:] = original_path
