import json
import logging
from unittest.mock import MagicMock, patch

from src.core.logging_config import JSONFormatter


def test_json_formatter_includes_trace_id_when_span_active():
    mock_ctx = MagicMock()
    mock_ctx.is_valid = True
    mock_ctx.trace_id = 0xABCD1234ABCD1234ABCD1234ABCD1234

    mock_span = MagicMock()
    mock_span.get_span_context.return_value = mock_ctx

    with patch("src.core.logging_config.trace.get_current_span", return_value=mock_span):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        output = json.loads(formatter.format(record))

    assert output["trace_id"] == "abcd1234abcd1234abcd1234abcd1234"


def test_json_formatter_omits_trace_id_when_no_span():
    mock_span = MagicMock()
    mock_span.get_span_context.return_value = MagicMock(is_valid=False)

    with patch("src.core.logging_config.trace.get_current_span", return_value=mock_span):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        output = json.loads(formatter.format(record))

    assert "trace_id" not in output


def test_json_formatter_omits_trace_id_when_otel_not_available():
    with patch("src.core.logging_config.trace", None):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        output = json.loads(formatter.format(record))

    assert "trace_id" not in output
