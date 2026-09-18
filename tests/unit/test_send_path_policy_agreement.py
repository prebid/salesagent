"""One URL, one verdict — now enforced by there being one POLICY, not two that agree.

HISTORY, and why this file changed shape (GH #1802).

It used to characterize a real divergence: ``src/core/webhook_delivery.py`` fired
through ``WebhookURLValidator.validate_webhook_url`` (the REGISTRATION gate) while the
three service senders fired through ``reject_unsafe_outbound_webhook_url`` ->
``validate_outbound_webhook_url`` (the SEND gate). Same act, two implementations,
selected by which module the caller happened to sit in. The old docstring recorded that
they returned the same verdict for every production input and diverged on exactly one
case — ``localhost`` under ``ADCP_TESTING`` — and it deferred the real fix:

    "Demanding they collapse would be a different (and larger) change."

    "...it fails loudly if a future change silently collapses them, which would make
     the guard vacuous and must be rethought."

That larger change landed upstream. #1802 deleted BOTH functions along with the other
copies of address policy and left a single owner, ``EgressPolicy``, with the two moments
as two entry points on it:

* ``EgressPolicy.check_registration`` — registration time, DNS-free, ``allow_loopback``
  for the capture receiver under ``ADCP_TESTING``.
* ``EgressPolicy.resolve_for_dial`` — dial time, resolves once and PINS the connection
  to the address it validated.

So the old assertion has no subject: there is no second gate to be interchangeable
with. Per its own instruction this file is rethought rather than deleted, because the
OBLIGATION — one URL, one verdict — outlived the mechanism. What can go wrong now is
not "a sender reached for the wrong gate" but "someone grows a second policy again", so
that is what is pinned here:

1. The two moments agree on every destination, with the loopback allowance as the ONE
   deliberate, named exception — so a future divergence reappears as a failure here.
2. The superseded gates are really gone. Without that, re-adding them would recreate
   the split while the test above kept passing, because it only ever asks EgressPolicy.

Asserted on BEHAVIOUR, never on an import line: a source-substring check cannot tell an
import from a call, which is how the first draft of this file died to mutation testing.
The routing property (that each sender actually CALLS the shared entry point) is pinned
by AST one file over, in ``tests/unit/test_architecture_counterparty_egress_gated.py``
and ``tests/unit/test_stored_url_egress_gate.py``, and is deliberately not restated here.
"""

from __future__ import annotations

import pytest

from src.core.security.egress.policy import EgressPolicy

#: One representative per class the policy exists to judge, plus the case the two
#: moments deliberately still differ on.
DESTINATIONS = [
    pytest.param("https://localhost:9999/webhook", id="localhost-the-divergent-case"),
    pytest.param("https://127.0.0.1:9999/webhook", id="loopback-literal"),
    pytest.param("https://169.254.169.254/latest/meta-data", id="cloud-metadata"),
    pytest.param("https://host.docker.internal:9999/webhook", id="blocked-hostname"),
    pytest.param("https://10.0.0.5/webhook", id="rfc1918-literal"),
    pytest.param("https://[::1]/webhook", id="ipv6-loopback-literal"),
]


def _registration_admits(url: str, *, allow_loopback: bool) -> bool:
    """The registration moment's verdict as a bool. It RAISES rather than returning one."""
    try:
        EgressPolicy.check_registration(url, allow_loopback=allow_loopback)
    except Exception:
        return False
    return True


def _dial_admits(url: str) -> bool:
    """The dial moment's verdict as a bool, DNS and all."""
    try:
        EgressPolicy.resolve_for_dial(url, field=None, allow_private=False)
    except Exception:
        return False
    return True


@pytest.mark.parametrize("url", DESTINATIONS)
def test_both_moments_come_from_the_one_policy(url: str) -> None:
    """Registration and dial agree, except where the loopback allowance deliberately differs.

    This is the post-collapse form of the old divergence test. It fails if someone
    reintroduces a second policy that disagrees with the first — the failure the original
    file existed to prevent, expressed against the mechanism that now holds it.
    """
    is_local_capture_host = "localhost" in url or "127.0.0.1" in url

    strict_registration = _registration_admits(url, allow_loopback=False)
    dial = _dial_admits(url)

    assert strict_registration == dial, (
        f"the one policy must give one verdict for {url!r} at both moments; got "
        f"registration={strict_registration} dial={dial}. A disagreement here means a "
        "second address policy has grown back."
    )

    if is_local_capture_host:
        assert _registration_admits(url, allow_loopback=True), (
            f"the capture receiver {url!r} must still be admissible at registration under the "
            "explicit loopback allowance — that allowance is the ONE sanctioned difference, and "
            "losing it silently breaks every webhook-capture e2e scenario."
        )


def test_the_superseded_gates_are_really_gone() -> None:
    """The collapse is what makes the test above meaningful — pin that it happened."""
    from src.core import webhook_validator

    for gone in ("validate_webhook_url", "validate_outbound_webhook_url"):
        assert not hasattr(webhook_validator.WebhookURLValidator, gone), (
            f"WebhookURLValidator.{gone} is back. Address policy has ONE owner "
            "(EgressPolicy); a second gate is the defect this file exists to catch."
        )
