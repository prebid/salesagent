"""The ingest refusal log line names the field and the URL's scheme and host, never the rest."""

import logging

from src.admin.utils.url_policy import _is_allowed


def test_rejection_log_omits_credentials_path_and_query(caplog):
    caplog.set_level(logging.WARNING, logger="src.admin.utils.url_policy")

    url = "https://user:hunter2@169.254.169.254/services/T0/B0/xoxb-secret?token=abc#frag"
    assert _is_allowed(url, "Slack webhook URL") is False

    messages = [r.getMessage() for r in caplog.records if r.name == "src.admin.utils.url_policy"]
    assert messages == ["[SECURITY] Rejected Slack webhook URL at ingest: https://169.254.169.254/..."]
    for secret in ("user", "hunter2", "xoxb-secret", "token", "abc", "frag"):
        assert secret not in messages[0]
