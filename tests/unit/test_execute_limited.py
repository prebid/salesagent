"""Unit tests for execute_limited() query helper."""

from unittest.mock import MagicMock

from sqlalchemy import select

from src.admin.utils.helpers import LimitedResult, execute_limited


class TestExecuteLimited:
    """execute_limited applies .limit() and computes the truncated flag."""

    def _make_session(self, rows: list) -> MagicMock:
        session = MagicMock()
        session.scalars.return_value.all.return_value = rows
        return session

    def test_under_limit_not_truncated(self):
        session = self._make_session(["a", "b", "c"])
        stmt = select()

        result = execute_limited(session, stmt, limit=10)

        assert result == LimitedResult(rows=["a", "b", "c"], truncated=False)

    def test_at_limit_is_truncated(self):
        session = self._make_session(["a", "b", "c"])

        result = execute_limited(session, stmt=select(), limit=3)

        assert result.truncated is True
        assert len(result.rows) == 3

    def test_empty_result_not_truncated(self):
        session = self._make_session([])

        result = execute_limited(session, stmt=select(), limit=100)

        assert result == LimitedResult(rows=[], truncated=False)

    def test_limit_of_one_with_one_row_is_truncated(self):
        session = self._make_session(["only"])

        result = execute_limited(session, stmt=select(), limit=1)

        assert result.truncated is True
        assert result.rows == ["only"]

    def test_applies_limit_to_statement(self):
        """Verify .limit() is chained onto the statement before execution."""
        session = self._make_session([])
        stmt = MagicMock()
        limited_stmt = MagicMock()
        stmt.limit.return_value = limited_stmt

        execute_limited(session, stmt, limit=42)

        stmt.limit.assert_called_once_with(42)
        session.scalars.assert_called_once_with(limited_stmt)

    def test_returns_namedtuple_unpacking(self):
        """LimitedResult supports tuple unpacking (rows, truncated = ...)."""
        session = self._make_session(["x"])

        rows, truncated = execute_limited(session, stmt=select(), limit=5)

        assert rows == ["x"]
        assert truncated is False
