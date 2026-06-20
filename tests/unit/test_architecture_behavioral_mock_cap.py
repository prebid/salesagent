# pylint: disable=duplicate-code
"""Structural guard: hand-rolled mock constructions in behavioral tests must shrink toward the harness.

Behavioral test suites (``test_*_behavioral.py``) should drive production through
the test harness environments (``tests/harness/``), which patch external
dependencies via ``EXTERNAL_PATCHES`` and expose fluent setup helpers
(``env.set_media_buy(...)``, ``env.setup_media_buy_data()``, factory-boy rows).
Hand-rolled ``MagicMock()`` / ``Mock()`` / ``AsyncMock()`` / ``patch(...)``
constructions are the anti-pattern the harness replaces — the kind of bespoke
mock scaffolding (e.g. the former ``standard_mocks`` fixture and ``_PatchContext``
helper) that the media-buy behavioral migration removed.

This guard pins each behavioral file at its current count of mock constructions
via a per-file cap dict that can only shrink (same ratcheting convention as
``test_architecture_resolved_identity_inline_cap`` and ``.duplication-baseline``).
New behavioral files with mock constructions fail immediately; existing files
that drop below their cap force it down (no silent regression). The intent is to
keep the harness migration's gains and steer remaining mocks toward env fluent
helpers — e.g. ``MediaBuyUpdateEnv.set_media_buy`` / ``set_currency_limit``
instead of locally built mock MediaBuy / CurrencyLimit objects.

Only ``test_*_behavioral.py`` files are scanned; other test files legitimately
construct mocks and are out of scope here.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Per-file cap on hand-rolled mock constructions in behavioral test files.
# Frozen at the current count; can only shrink. New behavioral files with mock
# constructions fail immediately (force a deliberate cap entry or harness use).
BEHAVIORAL_MOCK_CONSTRUCTION_CAP: dict[str, int] = {
    "tests/integration/test_create_media_buy_behavioral.py": 38,
    "tests/integration/test_creative_sync_behavioral.py": 2,
    "tests/integration/test_delivery_poll_behavioral.py": 2,
    "tests/integration/test_delivery_webhook_behavioral.py": 5,
    "tests/integration/test_get_products_behavioral.py": 44,
    "tests/unit/test_authorized_properties_behavioral.py": 23,
    "tests/unit/test_creative_formats_behavioral.py": 17,
    "tests/unit/test_delivery_poll_behavioral.py": 14,
    "tests/unit/test_delivery_service_behavioral.py": 6,
    "tests/unit/test_performance_index_behavioral.py": 33,
    "tests/unit/test_sync_creatives_behavioral.py": 31,
    "tests/unit/test_update_media_buy_behavioral.py": 113,
}

# unittest.mock construction callables counted as hand-rolled mocking.
_MOCK_CALLABLES = frozenset({"MagicMock", "Mock", "AsyncMock", "NonCallableMock", "PropertyMock", "patch"})


def _is_behavioral_test_file(path: Path) -> bool:
    name = path.name
    return name.startswith("test_") and name.endswith("_behavioral.py")


def _count_mock_constructions(filepath: Path) -> list[int]:
    """Return line numbers of hand-rolled mock constructions in a behavioral test file.

    Counts direct (``MagicMock(...)``) and attribute (``mock.patch(...)``) call
    shapes for the unittest.mock construction callables. Non-behavioral files
    return ``[]`` (out of scope). ``env.mock[...]`` accesses are not calls to a
    mock constructor and are not counted.
    """
    if not _is_behavioral_test_file(filepath):
        return []
    try:
        tree = ast.parse(filepath.read_text(), filename=str(filepath))
    except (OSError, SyntaxError):
        return []
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _MOCK_CALLABLES:
                lines.append(node.lineno)
            elif isinstance(func, ast.Attribute) and func.attr in _MOCK_CALLABLES:
                lines.append(node.lineno)
    return lines


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_DIRS = [_REPO_ROOT / "tests"]


def _rel(path: Path) -> str:
    return str(path.relative_to(_REPO_ROOT)).replace("\\", "/")


from tests.unit._per_file_cap_guard import (
    assert_capped_files_still_exist,
    assert_caps_only_shrink,
    assert_per_file_caps,
)


def test_behavioral_mock_constructions_within_caps() -> None:
    """Behavioral test files must not exceed their hand-rolled mock cap; new files fail."""
    assert_per_file_caps(
        cap_dict=BEHAVIORAL_MOCK_CONSTRUCTION_CAP,
        count_sites=_count_mock_constructions,
        scan_dirs=_SCAN_DIRS,
        site_label="hand-rolled mock construction",
        typed_raise_hint="drive production through a tests/harness/ env (EXTERNAL_PATCHES + fluent setup helpers) instead",
        rel=_rel,
    )


def test_behavioral_mock_capped_files_still_exist() -> None:
    """Stale-cap detection — every capped file path must still exist on disk."""
    assert_capped_files_still_exist(
        BEHAVIORAL_MOCK_CONSTRUCTION_CAP,
        "BEHAVIORAL_MOCK_CONSTRUCTION_CAP",
        repo_root=_REPO_ROOT,
    )


def test_behavioral_mock_caps_only_shrink() -> None:
    """If a behavioral file has fewer mock constructions than its cap, lower the cap to match."""
    assert_caps_only_shrink(
        BEHAVIORAL_MOCK_CONSTRUCTION_CAP,
        _count_mock_constructions,
        repo_root=_REPO_ROOT,
    )
