"""One URL, one verdict: the send paths must not disagree about a destination.

``src/core/webhook_delivery.py`` fired through ``validate_webhook_url`` while the
three service senders (``protocol_webhook_service``, ``webhook_delivery_service``,
``order_approval_service``) fired through ``reject_unsafe_outbound_webhook_url`` ->
``validate_outbound_webhook_url``. Same act, two policies, chosen by which module
the caller happens to be in.

Measured scope of the disagreement, so the severity is not overstated: the two
gates return the SAME verdict for every input in production. They diverge on
exactly one case — ``localhost`` while ``ADCP_TESTING`` is set, where the outbound
gate allows a capture receiver and the strict gate refuses it. So this is a
test-visible inconsistency, not a reachable production hole. It still matters:
the suite grades two different policies depending on which sender a scenario
happens to exercise, and a reader at a call site cannot tell which one applies.

What is NOT asserted, deliberately: that the two functions return the same
verdict. They are legitimately different moments — ``validate_webhook_url`` is
the REGISTRATION-time gate, ``validate_outbound_webhook_url`` the SEND-time one,
and the send-time allowance for a capture receiver is why they differ at all.
Demanding they collapse would be a different (and larger) change, and it belongs
to the ADCP_TESTING ticket, not here. The defect is that a SEND path reached for
the REGISTRATION gate.

So: one test characterizes the divergence (proving that picking the wrong one is
a real behavioural difference, not a stylistic one), and one pins that no send
path picks the wrong one.
"""

from __future__ import annotations

import pytest

from src.core.webhook_validator import WebhookURLValidator

#: One representative per class the gate exists to judge, plus the case the two
#: gates actually disagreed on.
DESTINATIONS = [
    pytest.param("http://localhost:9999/webhook", id="localhost-the-divergent-case"),
    pytest.param("http://127.0.0.1:9999/webhook", id="loopback-literal"),
    pytest.param("http://169.254.169.254/latest/meta-data", id="cloud-metadata"),
    pytest.param("http://host.docker.internal:9999/webhook", id="blocked-hostname"),
    pytest.param("http://10.0.0.5/webhook", id="rfc1918-literal"),
    pytest.param("http://[::1]/webhook", id="ipv6-loopback-literal"),
]


@pytest.mark.parametrize("url", DESTINATIONS)
def test_the_registration_and_send_gates_are_not_interchangeable(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Characterize WHY the send path must use the send gate.

    If the two gates always agreed, using the wrong one would be a naming nit.
    They do not: with ``ADCP_TESTING`` set, the send-time gate admits a local
    capture receiver that the registration gate refuses. This records that
    difference so the guard below is understood as protecting behaviour rather
    than tidiness — and it fails loudly if a future change silently collapses
    them, which would make the guard vacuous.
    """
    monkeypatch.setenv("ADCP_TESTING", "true")

    registration, _ = WebhookURLValidator.validate_webhook_url(url)
    send_time, _ = WebhookURLValidator.validate_outbound_webhook_url(url)

    is_local_capture_host = "localhost" in url or "127.0.0.1" in url
    if is_local_capture_host:
        assert send_time and not registration, (
            f"under ADCP_TESTING the send-time gate must admit the capture host {url!r} that the "
            f"registration gate refuses; got send={send_time} registration={registration}. If this "
            "changed deliberately, the guard below is now vacuous and must be rethought."
        )
    else:
        assert registration == send_time, (
            f"the gates must differ ONLY on the local capture host; they disagree about {url!r} "
            f"(registration={registration}, send={send_time})"
        )


# NO wiring guard lives here, deliberately.
#
# The first draft asserted "reject_unsafe_outbound_webhook_url appears in each
# sender's source". Mutation-testing killed it: reverting webhook_delivery to the
# registration-time gate left the IMPORT line in place, so the substring check
# stayed green while the behaviour regressed. A source-substring test cannot tell
# an import from a call.
#
# The property is already pinned properly, by AST, one file over:
# tests/unit/test_architecture_counterparty_egress_gated.py asserts
# deliver_webhook_with_retry actually CALLS the shared entry point — and that
# guard DOES redden under the same mutation (verified). Restating it weakly here
# would be duplication that reads as extra safety while providing less.
