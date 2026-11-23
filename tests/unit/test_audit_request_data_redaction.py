"""Pure pieces of the audit-row and secret-field rules.

What a decorated route stores is graded through the Flask client in
``tests/integration/test_audit_request_data_secrets.py``; this file keeps the parsing edge
cases a route test would not enumerate: the one URL reducer and the keep / clear / set
decision for a write-only secret field.
"""

from __future__ import annotations

import pytest

from src.admin.utils.secret_fields import KEEP, submitted_secret
from src.core.webhook_validator import url_for_record


@pytest.mark.parametrize(
    ("url", "for_logs", "for_records"),
    [
        ("https://buyer.example/hook?token=s3cret", "https://buyer.example/hook", "https://buyer.example/..."),
        ("https://user:pa55@buyer.example:8443/hook", "https://buyer.example/hook", "https://buyer.example/..."),
        (
            "https://hooks.slack.com/services/T000/B000/XXXXSECRET",
            "https://hooks.slack.com/services/T000/B000/XXXXSECRET",
            "https://hooks.slack.com/...",
        ),
        ("HTTP://Buyer.Example/", "http://buyer.example/", "http://buyer.example"),
        ("https://buyer.example", "https://buyer.example", "https://buyer.example"),
        ("https://[::1]:9000/x#frag", "https://[::1]/x", "https://[::1]/..."),
        ("https://buyer.example:notaport/x", "https://buyer.example/x", "https://buyer.example/..."),
        ("https://[::1/x", None, None),  # unterminated IPv6 bracket: urlparse raises
        ("not-a-url", None, None),
        (None, None, None),
    ],
)
def test_one_reducer_decides_what_of_a_url_may_be_recorded(url, for_logs, for_records):
    assert url_for_record(url, keep_path=True) == for_logs
    assert url_for_record(url, keep_path=False) == for_records


@pytest.mark.parametrize(
    ("form", "expected"),
    [
        ({"secret": ""}, KEEP),
        ({"secret": "   "}, KEEP),
        ({}, KEEP),
        ({"secret": " new "}, "new"),
        ({"secret": "", "clear_secret": "on"}, None),
        ({"secret": "typed", "clear_secret": "on"}, None),  # clearing wins over a typed value
    ],
)
def test_a_secret_field_is_kept_cleared_or_set(form, expected):
    assert submitted_secret(form, "secret") == expected
