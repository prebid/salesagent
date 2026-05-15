"""Structural guard: ``raise ValueError(...)`` in business logic must shrink toward zero.

``ValueError`` is transport-agnostic but does not carry an AdCP error code, a
recovery hint, or context. At the transport boundary it is mapped to a synthetic
``AdCPValidationError`` (VALIDATION_ERROR), which loses the semantic specificity
the caller intended. Per the error-emission architecture, business logic should
raise typed AdCPError subclasses (AdCPValidationError, AdCPBudgetTooLowError,
AdCPMediaBuyNotFoundError, etc.) with explicit codes.

This guard counts ``raise ValueError(...)`` sites in ``src/core/tools/`` and
``src/adapters/`` per file, with a per-file CAP frozen at substrate landing.
Caps only SHRINK over time as PR 2 cleanup migrates sites to typed raises.
"""

from __future__ import annotations

import ast
from pathlib import Path

VALUE_ERROR_PER_FILE_CAP: dict[str, int] = {
    "src/adapters/__init__.py": 2,
    "src/adapters/base.py": 1,
    "src/adapters/broadstreet/adapter.py": 3,
    "src/adapters/broadstreet/config_schema.py": 4,
    "src/adapters/gam/auth.py": 5,
    "src/adapters/gam/client.py": 1,
    "src/adapters/gam/managers/creatives.py": 3,
    "src/adapters/gam/managers/orders.py": 11,
    "src/adapters/gam/managers/targeting.py": 22,
    "src/adapters/gam/pricing_compatibility.py": 2,
    "src/adapters/gam_implementation_config_schema.py": 4,
    "src/adapters/google_ad_manager.py": 8,
    "src/adapters/kevel.py": 2,
    "src/adapters/mock_ad_server.py": 7,
    "src/adapters/triton_digital.py": 2,
    "src/adapters/xandr.py": 5,
    "src/core/tools/creatives/_processing.py": 2,
    "src/core/tools/creatives/_validation.py": 5,
    "src/core/tools/media_buy_create.py": 26,
    "src/core/tools/media_buy_update.py": 5,
    "src/core/tools/performance.py": 1,
    "src/core/tools/products.py": 1,
    "src/core/tools/task_management.py": 4,
}

_REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [_REPO_ROOT / "src/core/tools", _REPO_ROOT / "src/adapters"]


def _rel(path: Path) -> str:
    return str(path.relative_to(_REPO_ROOT))


def _count_value_error_raises(filepath: Path) -> list[int]:
    """Return line numbers of ``raise ValueError(...)`` in the file."""
    if not filepath.exists():
        return []
    try:
        tree = ast.parse(filepath.read_text(), filename=str(filepath))
    except SyntaxError:
        return []
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            if isinstance(exc, ast.Call):
                func = exc.func
                if isinstance(func, ast.Name) and func.id == "ValueError":
                    lines.append(node.lineno)
    return lines


class TestNoValueErrorInImpl:
    """``raise ValueError(...)`` sites must stay within their per-file cap."""

    def test_value_error_sites_within_caps(self):
        from tests.unit._per_file_cap_guard import assert_per_file_caps

        assert_per_file_caps(
            cap_dict=VALUE_ERROR_PER_FILE_CAP,
            count_sites=_count_value_error_raises,
            scan_dirs=SCAN_DIRS,
            site_label="raise ValueError",
            typed_raise_hint="convert to typed AdCPError raise (e.g., AdCPValidationError)",
            rel=_rel,
        )

    def test_capped_files_still_exist(self):
        """Stale-cap detection."""
        from tests.unit._per_file_cap_guard import assert_capped_files_still_exist

        assert_capped_files_still_exist(VALUE_ERROR_PER_FILE_CAP, "VALUE_ERROR_PER_FILE_CAP", repo_root=_REPO_ROOT)

    def test_caps_only_shrink(self):
        """If a file has fewer sites than its cap, lower the cap to match."""
        from tests.unit._per_file_cap_guard import assert_caps_only_shrink

        assert_caps_only_shrink(VALUE_ERROR_PER_FILE_CAP, _count_value_error_raises, repo_root=_REPO_ROOT)
