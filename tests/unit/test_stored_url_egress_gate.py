"""A stored URL is re-judged at SEND time, not trusted because it was judged at WRITE time.

Two senders dial an operator-supplied URL read back out of config or a DB row,
with no destination policy at the moment of the call:

* ``src/admin/blueprints/tenants.py`` — the "send test message" route POSTs to
  ``tenant.slack_webhook_url``. The SAME blueprint gates on WRITE
  (``WebhookURLValidator.validate_webhook_url`` in ``update_slack``), which makes
  this the sharpest case: a row that was safe when written, or written before the
  gate existed, or edited directly in the database, is dialled on nobody's policy.
* ``src/adapters/base_workflow.py`` — POSTs to ``tenant_config["slack"]["webhook_url"]``.

All three are the same act, so they get ONE implementation rather than three
gates that can drift — ``deliver_json_to_allowed_destination``. That is the same
direction salesagent-og9k.8 argues for on the four existing send paths.

The discriminator throughout is that **no HTTP call is made**, asserted on
``requests.post``. Asserting only "an exception was raised" would pass against a
connection failure to the blocked host — which is what a refused destination
looks like anyway — so it would grade nothing.

Every refusal is paired with an ACCEPT case. Without one, deleting the send
entirely would satisfy every refusal here.
"""

from __future__ import annotations

import ast
from unittest.mock import MagicMock, patch

import pytest

from tests.unit._architecture_helpers import called_function_names, parse_module, repo_root

#: Destinations production's gate refuses, one per class it exists to catch.
BLOCKED_URLS = [
    pytest.param("http://169.254.169.254/hook", id="cloud-metadata"),
    pytest.param("http://host.docker.internal:9999/hook", id="blocked-hostname"),
    pytest.param("http://10.0.0.5/hook", id="rfc1918-literal"),
    pytest.param("http://[::1]/hook", id="ipv6-loopback-literal"),
]

_PUBLIC_URL = "https://hooks.slack.com/services/T000/B000/xxxx"

#: The call sites that must route through the shared sender, as (file, function).
SENDER_CALL_SITES = [
    ("src/admin/blueprints/tenants.py", "test_slack"),
    ("src/adapters/base_workflow.py", "_send_workflow_notification"),
]

_SHARED_SENDER = "deliver_json_to_allowed_destination"


def _ok_response() -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.text = "ok"
    response.raise_for_status = MagicMock()
    return response


class TestSharedSenderRefusesBlockedDestinations:
    @pytest.mark.parametrize("blocked_url", BLOCKED_URLS)
    def test_blocked_destination_is_not_dialled(self, blocked_url):
        from src.core.webhook_validator import deliver_json_to_allowed_destination

        with patch("requests.post") as post:
            delivered = deliver_json_to_allowed_destination(blocked_url, {"text": "hi"}, kind="Test")

        assert post.call_count == 0, (
            f"POSTed to {blocked_url!r} — a stored URL is being trusted because it was judged at write "
            "time, not because it is safe now"
        )
        assert delivered is False

    def test_refusal_does_not_echo_the_blocked_range_to_the_caller(self):
        """AdCP 3.1.1 L1/security.mdx:104-119 step 6 — detailed causes are a topology side channel."""
        from src.core.webhook_validator import deliver_json_to_allowed_destination

        with patch("requests.post"):
            delivered = deliver_json_to_allowed_destination("http://10.0.0.5/hook", {}, kind="Test")

        assert delivered is False  # a bool, carrying no cause back to the caller

    def test_public_destination_is_still_delivered(self):
        """The gate is a policy, not a kill switch."""
        from src.core.webhook_validator import deliver_json_to_allowed_destination

        with (
            patch("socket.gethostbyname", return_value="93.184.216.34"),
            patch("requests.post", return_value=_ok_response()) as post,
        ):
            delivered = deliver_json_to_allowed_destination(_PUBLIC_URL, {"text": "hi"}, kind="Test")

        assert delivered is True
        assert post.call_count == 1


@pytest.mark.arch_guard
@pytest.mark.parametrize(("path", "func_name"), SENDER_CALL_SITES)
def test_each_stored_url_sender_routes_through_the_shared_sender(path: str, func_name: str) -> None:
    """Each site delegates rather than keeping its own ``requests.post``.

    Without this the shared sender could exist, be fully tested, and be used by
    nobody — which is exactly the state the TLS capture receiver was found in.
    """
    tree = parse_module(repo_root() / path)
    assert tree is not None, f"could not parse {path}"
    func = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == func_name),
        None,
    )
    assert func is not None, f"{path}::{func_name} not found — update SENDER_CALL_SITES if it was renamed"

    called = called_function_names(func)
    assert _SHARED_SENDER in called, (
        f"{path}::{func_name} does not call {_SHARED_SENDER}; a stored URL reaches the network on nobody's policy"
    )
    assert "post" not in called, (
        f"{path}::{func_name} still holds its own requests.post — the gate and the send must not be "
        "separable at this site"
    )
