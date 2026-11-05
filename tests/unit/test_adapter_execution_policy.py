"""Pure nullable approval defaults shared by live and sandbox construction.

Current PostgreSQL columns are NOT NULL; the existing legacy None semantics
are retained as a pure calculation rather than manufacturing impossible rows.
"""

import pytest

from src.core.database.repositories.adapter_config import effective_manual_approval


@pytest.mark.parametrize(
    "adapter,gam,mock,kevel,expected",
    [
        ("google_ad_manager", None, False, False, None),
        ("mock", False, None, False, True),
        ("kevel", False, False, None, True),
        ("triton", True, True, True, False),
    ],
)
def test_nullable_approval_defaults(adapter, gam, mock, kevel, expected):
    assert effective_manual_approval(adapter, gam, mock, kevel) is expected
