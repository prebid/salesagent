"""Egress backoff/flag helpers shared by the outbound-HTTP integration tests.

Extracted from ``tests/integration/test_outbound_http.py`` (GH #1802 merge). That module
has 67 real tests of its own, so it was simultaneously a test suite and a helper library
for fifteen other modules — the shape ``test_architecture_no_cross_test_module_imports``
forbids, because importing it drags its whole suite (and its fixtures) into an unrelated
module's collection.

MERGE NOTE (RFC 9421 semantic merge). The extraction is the incoming branch's; the BODIES
are #1721's. The two sides changed these helpers for unrelated reasons — one moved their
home, the other replaced ``monkeypatch.setenv`` with typed settings injection because the
seam has no env surface left to drive (``ruff-environment.toml``; see :func:`set_flags`).
Taking either side alone loses the other's work, and keeping the incoming bodies here would
have been worse than a lost refactor: six modules would have written environment variables
that nothing on the merged tree reads, disarming their refusal cases silently.
"""

from __future__ import annotations

from tests.helpers.settings_injection import inject_limits

#: The settings FIELD the seam reads for its retry backoff base
#: (``src/core/security/outbound_http.py`` -> ``egress.attempts._backoff_seconds``).
#:
#: It is a field name, not the ``ADCP_OUTBOUND_BACKOFF_BASE_SECONDS`` environment variable
#: these tests used to write. Writing the variable drove the settings loader's string
#: parsing and reached the seam only when nothing had already built the settings, which is
#: an ordering condition no call site could see.
BACKOFF_BASE_FIELD = "adcp_outbound_backoff_base_seconds"

#: A rate-limited answer, as the origin sends it. The body is asserted against in the
#: opacity cases, so it carries a marker rather than a plausible payload. It lives HERE
#: rather than in ``test_outbound_http.py`` because ``rate_limited`` below writes it: a
#: helper reaching back into the test module for it is the cross-test-module import this
#: extraction removed, and leaving it behind is how the extraction shipped three
#: NameErrors that only the integration suite could see — ~357 failures from three
#: missing lines.
_RATE_LIMITED_BODY = b'{"error": "slow down"}'


def _attempts_module():
    """Import ``egress.attempts`` (the MODULE, not the class) lazily.

    :func:`pin_jitter` needs the module object itself, to patch ``random.uniform``
    where ``_backoff_seconds`` reads it. Moved here with its only caller.
    """
    from src.core.security.egress import attempts

    return attempts


def set_flags(monkeypatch, *, private: bool = False) -> None:
    """State the private-range escape hatch explicitly, as the fact the seam reads.

    ``outbound_http._allow_private`` reads
    ``get_settings().limits.adcp_outbound_allow_private``, so the hatch is INJECTED
    (:func:`tests.helpers.settings_injection.inject_limits`) rather than written into the
    environ. Both postures are always stated — a hatch the test leaves unsaid is a hatch
    decided by whatever exported it into the shell, which is how a refusal case gets
    silently disarmed.

    It used to ``monkeypatch.setenv(ADCP_OUTBOUND_ALLOW_PRIVATE, ...)``. That only reached
    the seam while nothing had yet built the settings, because the settings object is built
    once and cached: every caller whose FIXTURES read settings first (a
    ``CreativeAgentRegistry()`` reads ``integrations.creative_agent_url`` in ``__init__``)
    got the default posture instead of the one it asked for — an opened hatch stayed shut,
    and a refusal case was enforced by accident rather than by ``enforce_egress_policy``.
    Injection has no such ordering condition. ``egress_hatch_env`` still owns the ENV
    spelling for the two harness sites that hand the variable to another process.

    There is no ``insecure`` parameter anymore (GH #1757): the scheme
    gate is unconditional in production, so there is nothing left to relax —
    a caller that used to pass ``insecure=True`` needed a real https origin
    (see the ``local_origin_tls`` fixture) instead.
    """
    inject_limits(monkeypatch, adcp_outbound_allow_private=private)


def pin_jitter(monkeypatch, value: float) -> list[tuple]:
    """Freeze the seam's jitter draw and record how it was called.

    BR-RULE-029's jitter is a real ``random.uniform(0, 1)`` draw, so any test
    that asserts a delay's magnitude has to pin it — otherwise the assertion is
    graded against a number the test does not know.

    The patch target is the module attribute ``egress.attempts.random``, which
    is also the string target the UC-004 circuit-breaker harness patches
    (``tests/harness/delivery_circuit_breaker.py``) — the schedule moved there
    with ``_backoff_seconds`` (GH #1802). Pinning it here for the same
    obligation keeps the seam suite and the BDD suite grading one implementation:
    a ``from random import uniform`` in ``egress.attempts`` would break both at
    once, which is the point.

    Returns the list of ``(args)`` tuples the seam passed to ``uniform``, so a
    caller can grade the draw itself — one draw per sleep, with the literal
    ``(0, 1)`` bounds the rule names.
    """
    calls: list[tuple] = []

    def _pinned(*args):
        calls.append(args)
        return value

    monkeypatch.setattr(_attempts_module().random, "uniform", _pinned)
    return calls


def fast_backoff(monkeypatch) -> None:
    """Make a retry test's real sleeps negligible without weakening what it grades.

    For the retry tests that grade attempt COUNTS: they have to sleep between
    attempts, but what they sleep is not their obligation — BR-RULE-029's
    magnitudes are graded once, by the schedule section of
    ``tests/integration/test_outbound_http.py``.

    BOTH halves are required. The base override alone does not make these tests
    fast, because the jitter is an additive ``uniform(0, 1)`` draw independent of
    the base: at a 1ms base each sleep would still average half a second.

    The base is stated EXPLICITLY, exactly as ``set_flags`` states the hatch, so an
    ambient value cannot change what these tests wait.
    """
    set_backoff_base(monkeypatch, 0.001)
    pin_jitter(monkeypatch, 0.0)


def set_backoff_base(monkeypatch, seconds: float | None = None) -> float:
    """State the retry backoff base the seam will read. ``None`` = the shipped default.

    A typed float onto ``limits.adcp_outbound_backoff_base_seconds``, which is the fact
    ``egress.attempts._backoff_seconds`` reads. Writing
    ``ADCP_OUTBOUND_BACKOFF_BASE_SECONDS`` instead — what every case here used to do —
    graded the settings loader parsing a string on the way to the value, and only landed
    at all while nothing had built the settings yet (see :func:`set_flags`).

    ``None`` replaces the ``monkeypatch.delenv`` the default-schedule cases used: it
    injects the SHIPPED default read off the field rather than trusting the environment to
    be unset, so those cases cannot be knocked off BR-RULE-029's 1/2/4 by an ambient value.

    Returns the base in effect, so a case that needs the number can assert against what it
    injected without restating it.
    """
    from src.core.config import LimitSettings

    base = LimitSettings.model_fields[BACKOFF_BASE_FIELD].default if seconds is None else seconds
    inject_limits(monkeypatch, **{BACKOFF_BASE_FIELD: base})
    return base


def rate_limited(local_origin, retry_after: str | None = None) -> None:
    """Program the origin to answer 429, optionally with a ``Retry-After`` header.

    The header is sent by a server that really wrote it, not injected into a
    mocked response object: "the seam honoured Retry-After" is a claim about
    what it does with bytes off the wire, and a stubbed ``response.headers``
    could only restate the number the test already chose.
    """
    headers = {"Retry-After": retry_after} if retry_after is not None else None
    local_origin.respond_with(429, body=_RATE_LIMITED_BODY, headers=headers)
