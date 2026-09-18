"""A stored URL is re-judged at SEND time, not trusted because it was judged at WRITE time.

Two senders dial an operator-supplied URL read back out of config or a DB row:

* ``src/admin/blueprints/tenants.py`` — the "send test message" route POSTs to
  ``tenant.slack_webhook_url``. The SAME blueprint gates on WRITE, which makes this
  the sharpest case: a row that was safe when written, or written before the gate
  existed, or edited directly in the database, must not be dialled on nobody's policy.
* ``src/adapters/base_workflow.py`` — POSTs to ``tenant_config["slack"]["webhook_url"]``.

RETARGETED (GH #1802). The obligation is unchanged; the mechanism that satisfies it
moved and got stronger. This branch answered it with one local helper,
``webhook_validator.deliver_json_to_allowed_destination``, which judged the URL just
before handing it to ``requests.post``. #1802 deleted that helper along with the other
three copies of address policy and put the judgement inside the egress seam:
``SlackNotifier.send_message`` -> ``webhook_delivery.deliver_webhook_with_retry`` ->
``webhook_egress.deliver_webhook`` -> ``outbound_http.send``, whose FIRST act is
``EgressPolicy.resolve_for_dial`` — it re-resolves DNS and raises
``OutboundRequestBlocked`` before a transport object exists at all.

That is strictly better than what this guard used to pin: the old helper resolved once
and then let ``requests`` resolve again, leaving a TOCTOU window between the two; the
seam resolves once and PINS the connection to the address it validated. So the shared
sender these two sites must route through is now ``send_message``.

The discriminator is unchanged and is the point of the file: **no HTTP call is made**.
Asserting only "an exception was raised" would pass against a connection failure to the
blocked host — which is what a refused destination looks like anyway — so it would
grade nothing. Here it is asserted at the socket boundary BELOW the policy, which a
refusal never reaches.

Every refusal is paired with an ACCEPT case. Without one, deleting the send entirely
would satisfy every refusal here.
"""

from __future__ import annotations

import ast
from unittest.mock import MagicMock, patch

import pytest

from tests.unit._architecture_helpers import called_function_names, parse_module, repo_root

#: Destinations production's gate refuses, one per class it exists to catch.
BLOCKED_URLS = [
    pytest.param("https://169.254.169.254/hook", id="cloud-metadata"),
    pytest.param("https://host.docker.internal:9999/hook", id="blocked-hostname"),
    pytest.param("https://10.0.0.5/hook", id="rfc1918-literal"),
    pytest.param("https://[::1]/hook", id="ipv6-loopback-literal"),
]

_PUBLIC_URL = "https://hooks.slack.com/services/T000/B000/xxxx"

#: The call sites that must route through the shared sender, as (file, function).
SENDER_CALL_SITES = [
    ("src/admin/blueprints/tenants.py", "test_slack"),
    ("src/adapters/base_workflow.py", "_send_workflow_notification"),
]

#: The one sender both sites delegate to. Everything below it — retry ladder, auth
#: selection, address policy — belongs to the seam, not to the call sites.
_SHARED_SENDER = "send_message"

#: The socket boundary, BELOW the policy. Deliberately not the seam's own
#: ``_sync_transport``: that function IS where ``resolve_for_dial`` runs, so patching it
#: would remove the gate being graded and every refusal test would pass vacuously.
#: ``httpx.HTTPTransport.handle_request`` is the first thing a refusal never reaches.
_SOCKET_BOUNDARY = "httpx.HTTPTransport.handle_request"


def _notifier(url: str):
    from src.services.slack_notifier import SlackNotifier

    return SlackNotifier(webhook_url=url)


class TestSharedSenderRefusesBlockedDestinations:
    @pytest.fixture(autouse=True)
    def _hatch_closed(self, monkeypatch):
        """Pin ``ADCP_OUTBOUND_ALLOW_PRIVATE`` CLOSED for every test in this class.

        These tests grade the POLICY's refusal of private/reserved destinations, so the
        hatch that suspends that refusal is a precondition and must be stated, not
        inherited. It is not hypothetical: the in-network CI stack opens the hatch so its
        containers can reach each other, and under it ``10.0.0.5`` and ``[::1]`` are
        legitimately DIALLED — three tests here passed on a laptop and failed on the CI
        box for that reason alone, grading the ambient environment rather than the gate.

        Written through ``egress_hatch_env``, the one place in the test tree that spells
        the variable, and written EXPLICITLY in the off case as the literal ``"false"``
        the repo's ``== "true"`` convention treats as off.
        """
        from tests.helpers.egress_hatches import egress_hatch_env

        for name, value in egress_hatch_env(private=False).items():
            monkeypatch.setenv(name, value)

    @pytest.mark.parametrize("blocked_url", BLOCKED_URLS)
    def test_blocked_destination_is_not_dialled(self, blocked_url):
        with patch(_SOCKET_BOUNDARY) as socket:
            delivered = _notifier(blocked_url).send_message("hi", max_retries=1)

        assert socket.call_count == 0, (
            f"reached the socket for {blocked_url!r} — a stored URL is being trusted because it was "
            "judged at write time, not because it is safe now"
        )
        assert delivered is False

    def test_refusal_does_not_echo_the_blocked_range_to_the_caller(self):
        """AdCP 3.1.1 L1/security.mdx step 6 — detailed causes are a topology side channel."""
        with patch(_SOCKET_BOUNDARY):
            delivered = _notifier("https://10.0.0.5/hook").send_message("hi", max_retries=1)

        assert delivered is False  # a bool, carrying no cause back to the caller

    def test_public_destination_is_still_delivered(self):
        """The gate is a policy, not a kill switch."""
        ok = MagicMock()
        ok.status_code = 200
        ok.text = "ok"

        with (
            patch("src.core.security.webhook_egress.deliver_webhook") as deliver,
        ):
            deliver.return_value = MagicMock(delivered=True, attempts=1, kind="delivered", detail="", log_level=20)
            with patch("src.core.webhook_delivery.deliver_webhook", deliver):
                delivered = _notifier(_PUBLIC_URL).send_message("hi", max_retries=1)

        assert deliver.call_count == 1, "a public destination must still reach the seam"
        assert deliver.call_args.args[0] == _PUBLIC_URL
        assert delivered is True


@pytest.mark.arch_guard
@pytest.mark.parametrize(("path", "func_name"), SENDER_CALL_SITES)
def test_each_stored_url_sender_routes_through_the_shared_sender(path: str, func_name: str) -> None:
    """Each site delegates rather than keeping its own HTTP client.

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
