"""Unit coverage for session_operator_email (Chris #1718 Aug-28 D9)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.admin.utils.helpers import session_operator_email


@pytest.mark.parametrize(
    "user_info,expected",
    [
        ("op@example.com", "op@example.com"),
        ({"email": "dict@example.com"}, "dict@example.com"),
        ({}, "system"),
        ({"email": ""}, "system"),
        ({"email": None}, "system"),
        ("", "system"),
        (None, "system"),
    ],
    ids=["bare-string", "dict-email", "empty-dict", "empty-email", "none-email", "empty-string", "missing"],
)
def test_session_operator_email_shapes(user_info, expected, monkeypatch):
    fake_session = MagicMock()
    fake_session.get = MagicMock(side_effect=lambda key, default=None: user_info if key == "user" else default)
    monkeypatch.setattr("src.admin.utils.helpers.session", fake_session)
    assert session_operator_email() == expected
