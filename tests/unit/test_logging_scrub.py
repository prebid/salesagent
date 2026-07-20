"""Unit tests for log-sanitization helpers in src.core.logging_config.

Buyer-controlled strings (callback URLs, SSRF rejection details) are
interpolated into log records; unscrubbed control characters would let one
request forge adjacent records in plain-text log mode (#1546 re-review item 4).
"""

from src.core.logging_config import _is_production_log_env, scrub_control_chars


class TestScrubControlChars:
    """Control characters are escaped; printable content passes verbatim."""

    def test_surviving_control_chars_are_escaped(self):
        """VT/FF/ESC survive urlparse and raw CR/LF live in stored URLs — all must escape."""
        raw = "https://cb.example/a\x0bb\x0cc\x1b[31md\re\nf\x7fg\x85h"
        scrubbed = scrub_control_chars(raw)

        assert not any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in scrubbed)
        for escape in ("\\x0b", "\\x0c", "\\x1b", "\\r", "\\n", "\\x7f", "\\x85"):
            assert escape in scrubbed
        # Printable content is preserved in order around the escapes.
        assert scrubbed.startswith("https://cb.example/a")
        assert scrubbed.endswith("h")

    def test_plain_url_is_unchanged(self):
        url = "https://cb.example/webhook?a=1&b=%20&c=café"
        assert scrub_control_chars(url) == url


class TestProductionLogGate:
    """JSON log escaping keys off every documented production signal.

    The gate previously honored only FLY_APP_NAME / PRODUCTION, so a
    self-hosted deploy signalling via the documented ENVIRONMENT=production
    got plain-text (forgeable) logs.
    """

    def _clear(self, monkeypatch):
        for var in ("FLY_APP_NAME", "PRODUCTION", "ENVIRONMENT"):
            monkeypatch.delenv(var, raising=False)

    def test_environment_production_enables_json_logs(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("ENVIRONMENT", "production")
        assert _is_production_log_env() is True

    def test_fly_app_name_enables_json_logs(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("FLY_APP_NAME", "salesagent")
        assert _is_production_log_env() is True

    def test_development_defaults_to_plain_logs(self, monkeypatch):
        self._clear(monkeypatch)
        assert _is_production_log_env() is False

    def test_non_production_environment_value_stays_plain(self, monkeypatch):
        self._clear(monkeypatch)
        monkeypatch.setenv("ENVIRONMENT", "staging")
        assert _is_production_log_env() is False
