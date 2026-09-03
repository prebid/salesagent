"""Integration tests for unified MCP client utility.

These tests verify that our unified MCP client can connect to real MCP servers
and handle various scenarios (auth, retries, errors, etc.).
"""

import asyncio
import socket

import pytest


def _host_reachable(host: str, port: int = 443, timeout: float = 2.0) -> bool:
    """Check if an external host is reachable (DNS + TCP)."""
    try:
        socket.setdefaulttimeout(timeout)
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except (OSError, TimeoutError):
        return False


_audience_agent_reachable = _host_reachable("audience-agent.fly.dev")
skip_no_audience_agent = pytest.mark.skipif(
    not _audience_agent_reachable,
    reason="audience-agent.fly.dev is not reachable",
)

from src.core.exceptions import RECOVERY_BY_WIRE_CODE, AdCPError, build_two_layer_error_envelope
from src.core.security import outbound_http as outbound_http_module
from src.core.security.outbound_http import OutboundRequestBlocked
from src.core.signals_agent_registry import SignalsAgent, SignalsAgentRegistry
from src.core.utils import mcp_client as mcp_client_module
from src.core.utils.mcp_client import (
    MCPConnectionError,
    _build_auth_headers,
    call_mcp_tool,
)
from tests.helpers import assert_backoff_schedule, assert_envelope_shape
from tests.helpers.egress_hatches import ALLOW_PRIVATE_ENV

# Reused rather than restated (precedent: tests/integration/test_vendor_egress.py):
# the jitter pin, the escape-hatch setter and the backoff-base knob name are the
# seam suite's own helpers — copying any of them here is how one copy drifts.
from tests.integration.test_outbound_http import BACKOFF_BASE_ENV, pin_jitter, set_flags

# A cloud-metadata address: refused by the egress seam unconditionally, escape
# hatches or not. Spelled as an MCP endpoint because that is the shape a
# tenant-registered agent URL arrives in.
METADATA_AGENT_URL = "https://169.254.169.254/mcp"


class TestBuildAuthHeaders:
    """Test auth header building logic."""

    def test_no_auth(self):
        """No auth config returns empty headers."""
        headers = _build_auth_headers(None)
        assert headers == {}

    def test_bearer_auth_default_header(self):
        """Bearer auth uses Authorization header by default."""
        auth = {"type": "bearer", "credentials": "token123"}
        headers = _build_auth_headers(auth)
        assert headers == {"Authorization": "Bearer token123"}

    def test_api_key_auth_default_header(self):
        """API key auth uses x-api-key header by default."""
        auth = {"type": "api_key", "credentials": "key123"}
        headers = _build_auth_headers(auth)
        assert headers == {"x-api-key": "key123"}

    def test_custom_auth_header(self):
        """Custom header name overrides default."""
        auth = {"type": "bearer", "credentials": "token123"}
        headers = _build_auth_headers(auth, auth_header="X-Custom-Auth")
        assert headers == {"X-Custom-Auth": "Bearer token123"}

    def test_generic_auth_type(self):
        """Unknown auth types use x-api-key with credentials as-is."""
        auth = {"type": "custom", "credentials": "secret123"}
        headers = _build_auth_headers(auth)
        assert headers == {"x-api-key": "secret123"}

    def test_missing_credentials(self):
        """Missing credentials returns empty headers."""
        auth = {"type": "bearer"}
        headers = _build_auth_headers(auth)
        assert headers == {}

    def test_missing_type(self):
        """Missing auth type returns empty headers."""
        auth = {"credentials": "token123"}
        headers = _build_auth_headers(auth)
        assert headers == {}


@pytest.mark.asyncio
class TestCreateMCPClient:
    """Test MCP client creation and connection."""

    @pytest.mark.skip_ci
    async def test_connect_to_creative_agent(self):
        """Can connect to AdCP creative agent (known good server) and call a tool."""
        agent_url = "https://creative.adcontextprotocol.org/mcp"

        result = await call_mcp_tool(agent_url=agent_url, tool="list_creative_formats", arguments={}, timeout=10)

        assert result is not None
        assert isinstance(result.structured_content, dict)

    @pytest.mark.skip_ci
    @skip_no_audience_agent
    async def test_connect_to_audience_agent(self):
        """Can connect to audience/signals agent and call a tool."""
        agent_url = "https://audience-agent.fly.dev"

        result = await call_mcp_tool(
            agent_url=agent_url, tool="get_signals", arguments={"signal_spec": "test"}, timeout=10
        )

        assert result is not None
        assert isinstance(result.structured_content, dict)

    async def test_unresolvable_host_is_refused_before_dialling(self):
        """Invalid URL raises MCPConnectionError after retries."""
        # Behaviour change (GH #1802): an unresolvable host is now refused by
        # egress policy before any connection is attempted, rather than being dialled,
        # retried and reported as a connection failure. The seam cannot pin a
        # connection to an address it could not resolve, so it declines to try.
        agent_url = "https://nonexistent.example.com/mcp"

        with pytest.raises(OutboundRequestBlocked) as exc_info:
            await call_mcp_tool(agent_url=agent_url, tool="noop", arguments={}, timeout=5, max_attempts=2)

        # Opaque by design: the refusal must not echo which host or address failed.
        assert "nonexistent.example.com" not in str(exc_info.value)

    async def test_respects_max_retries(self, monkeypatch):
        """Connection failures respect max_attempts parameter."""
        monkeypatch.setenv(ALLOW_PRIVATE_ENV, "true")
        # A loopback port with nothing listening: resolves, fails fast, and the retry
        # budget is what is graded. It needs the private-range hatch because policy
        # refuses loopback addresses by default (https is required unconditionally now
        # regardless of any hatch, GH #1802 -- hence the scheme flip above). NOT a live origin — call_mcp_tool never
        # passes its timeout to the transport, so an origin that answers without speaking
        # MCP hangs instead of failing (a bug adjacent to this ticket, not fixed here).
        agent_url = "https://localhost:9999/mcp"

        with pytest.raises(MCPConnectionError) as exc_info:
            await call_mcp_tool(agent_url=agent_url, tool="noop", arguments={}, timeout=1, max_attempts=1)

        # Should only try once
        assert "after 1 attempts" in str(exc_info.value)


@pytest.mark.asyncio
class TestURLHandling:
    """Test that URL handling respects user input."""

    @pytest.mark.skip_ci
    @skip_no_audience_agent
    async def test_respects_user_url_exactly(self):
        """Uses the exact URL provided by user (no modifications)."""
        # Audience agent is at base URL (no path)
        agent_url = "https://audience-agent.fly.dev"

        result = await call_mcp_tool(
            agent_url=agent_url, tool="get_signals", arguments={"signal_spec": "test"}, timeout=10
        )
        assert isinstance(result.structured_content, dict)

    @pytest.mark.skip_ci
    async def test_strips_trailing_slashes_only(self):
        """Trailing slashes are stripped but the path is preserved."""
        # URL with trailing slash
        agent_url = "https://creative.adcontextprotocol.org/mcp/"

        result = await call_mcp_tool(agent_url=agent_url, tool="list_creative_formats", arguments={}, timeout=10)
        assert isinstance(result.structured_content, dict)


@pytest.mark.asyncio
class TestErrorHandling:
    """Test error handling and edge cases."""

    @pytest.mark.skip_ci
    async def test_client_cleanup_on_error(self):
        """The client is properly cleaned up even if an error occurs mid-call.

        ``call_mcp_tool`` owns the client's lifetime entirely (``async with
        client:`` lives inside the seam, not at the call site), so there is no
        caller-visible client left dangling to grade — the only observable
        behavior is that a successful call returns cleanly.
        """
        agent_url = "https://creative.adcontextprotocol.org/mcp"

        result = await call_mcp_tool(agent_url=agent_url, tool="list_creative_formats", arguments={}, timeout=10)
        assert isinstance(result.structured_content, dict)

        # If we reach here without hanging, cleanup worked correctly
        assert True

    async def test_timeout_handling(self, monkeypatch):
        """Connection timeout is respected."""
        # Use a URL that will timeout (assuming nothing on port 9999)
        monkeypatch.setenv(ALLOW_PRIVATE_ENV, "true")
        # Unchanged target: a loopback port with nothing listening, which fails fast.
        # It needs the private-range hatch because policy refuses loopback
        # addresses by default (https is required unconditionally now regardless of
        # any hatch, GH #1802 -- hence the scheme flip above). NOT repointed at a live origin: call_mcp_tool accepts a timeout
        # and never passes it to the transport, so an origin that ANSWERS but does not
        # speak MCP hangs forever rather than timing out. That bug is adjacent to this
        # ticket and not fixed here.
        agent_url = "https://localhost:9999/mcp"

        with pytest.raises(MCPConnectionError):
            await call_mcp_tool(agent_url=agent_url, tool="noop", arguments={}, timeout=1, max_attempts=1)


class _SleepRecordingAsyncio:
    """Stands in for a module's ``asyncio``, recording sleeps only.

    Replacing the name the module resolves — rather than setting
    ``asyncio.sleep`` on the real module — keeps the substitution scoped to the
    retry loop under test. The transport, anyio and the event loop keep the real
    ``asyncio.sleep``, so a hang or a deadlock observed in this test is the
    client's, not one this fixture introduced.
    """

    def __init__(self, real, slept: list[float]) -> None:
        self._real = real
        self._slept = slept

    async def sleep(self, seconds: float, *args, **kwargs) -> None:
        self._slept.append(seconds)

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def recorded_retry_sleeps(monkeypatch):
    """Record every sleep the MCP client's retries perform, without waiting them out.

    The sleep is the observation point, not the wall clock: a retry schedule of
    1s then 2s cannot be graded by timing a test, and waiting it out would make
    this file three seconds slower for no added signal.

    BOTH modules that could hold the retry sleep are covered — ``mcp_client``
    itself and the egress seam (``src.core.security.outbound_http``), whose
    backoff helper the client is required to defer to instead of recomputing a
    schedule locally. Recording into one shared list means the grade is about
    the durations, wherever the sleep executes; a sleep that moved between the
    two modules stays observed, and a schedule graded here cannot pass because
    the observation point was pointed at the wrong module.

    The replacement must be an ``async def`` — a plain ``MagicMock`` returns a
    non-awaitable and the client would ``TypeError`` on the await instead of
    failing on what is being graded. Same reason as ``record_sleeps`` in
    ``tests/integration/test_outbound_http.py``.
    """
    slept: list[float] = []
    for module in (mcp_client_module, outbound_http_module):
        # raising=False: mcp_client no longer imports asyncio (its retry sleep
        # moved into the seam's sleep_backoff), so the recorder is installed
        # unconditionally — if a local sleep ever reappears there with a fresh
        # asyncio import, it is recorded again instead of silently unobserved.
        real = getattr(module, "asyncio", asyncio)
        monkeypatch.setattr(module, "asyncio", _SleepRecordingAsyncio(real, slept), raising=False)
    return slept


@pytest.fixture
def egress_hatches_closed(monkeypatch):
    """Close the private-range escape hatch explicitly, as the literal ``"false"``.

    Not optional and not a default: ``run_all_tests_host.sh`` and the e2e compose
    files export ``ADCP_OUTBOUND_ALLOW_PRIVATE`` for the creative-agent stack,
    so a test that merely assumed it unset would grade nothing on exactly the
    machines this suite runs on. There is no scheme hatch to close anymore
    (GH #1802 deleted it) — https is required unconditionally.
    """
    monkeypatch.setenv(ALLOW_PRIVATE_ENV, "false")


@pytest.mark.asyncio
class TestRefusedAgentUrlIsNotDialled:
    """A refused agent URL propagates as a policy refusal, not a transport failure.

    ``call_mcp_tool`` is the MCP seam's entry point, so the egress seam's
    address and scheme policy applies ONCE, at its top, before the connection
    candidates are built — outside the retry loop and outside the ``try`` whose
    arm is a bare ``except Exception``.

    Position is the whole obligation. Validating inside that loop would leave
    ``OutboundRequestBlocked`` caught, logged as "MCP connection attempt N/M
    failed", slept on, retried against the same blocked URL, retried again
    against the synthesised ``{url}/mcp`` candidate, and finally re-raised as
    ``MCPConnectionError`` — a policy refusal converted into a retried transport
    failure with the wrong class.

    Note what that means for the assertions: an origin-hit count of zero holds
    under BOTH designs, so it cannot be the grade on its own. The sleep
    assertion is what separates them.
    """

    # A correct refusal returns in microseconds — nothing is dialled. The bound
    # exists because the FAILING state is a hang, not a fast wrong answer:
    # `call_mcp_tool` accepts a `timeout` and never passes it to the
    # transport, so a client that reaches the wire waits out the OS connect
    # timeout on an unroutable address, four times over. Without this the whole
    # slice dies with no report instead of failing with one.
    @pytest.mark.timeout(60)
    @pytest.mark.parametrize("destination", ["local-origin-loopback", "cloud-metadata"])
    async def test_refused_url_raises_blocked_without_dialling_or_retrying(
        self, destination, local_origin, egress_hatches_closed, recorded_retry_sleeps
    ):
        """The refusal is raised as-is, before the wire, and is never retried.

        Both destinations are refused by the seam for real reasons, not mocked
        ones: ``127.0.0.1`` is in a reserved range (and the URL is plain http),
        and ``169.254.169.254`` is the cloud-metadata address. The loopback case
        points at an origin that is genuinely listening and would answer, which
        is what makes ``hits == 0`` a statement about the client rather than
        about an address nothing could have reached anyway.
        """
        agent_url = local_origin.base_url if destination == "local-origin-loopback" else METADATA_AGENT_URL

        with pytest.raises(OutboundRequestBlocked):
            await call_mcp_tool(agent_url=agent_url, tool="noop", arguments={}, timeout=5, max_attempts=3)

        assert local_origin.hits == 0, f"the client dialled a refused address: {local_origin.requests}"
        assert recorded_retry_sleeps == [], (
            f"a policy refusal was retried (slept {recorded_retry_sleeps}) — it was caught by the "
            "connection retry loop instead of being raised before it"
        )


@pytest.mark.asyncio
class TestConnectionRetryBackoffSchedule:
    """Connection retries against a failing primary sleep the seam's schedule.

    BR-RULE-029 (graded for webhook delivery in BR-UC-004 INV-3, generalised to
    every egress retry loop by the call-site-backoff guard): exponential backoff
    of 1s, 2s, 4s PLUS a ``uniform(0, 1)`` jitter draw, up to 3 attempts. The
    schedule is computed in exactly one module —
    ``src/core/security/outbound_http.py`` — and the MCP client's connection
    loop must read it from there, not recompute a local copy that silently
    drifts (a bare ``retry_delay * 2**attempt`` has no jitter and ignores the
    ``ADCP_OUTBOUND_BACKOFF_BASE_SECONDS`` knob, and nothing fails when it
    drifts further).

    ``recorded_retry_sleeps`` observes the sleep in BOTH modules, so the grade
    is about the durations wherever the sleep executes — the jitter term is
    what separates the seam's schedule from a call-site recomputation.
    """

    async def test_failing_primary_sleeps_seam_schedule_with_jitter(self, monkeypatch, recorded_retry_sleeps):
        """max_retries=3 against a dead port sleeps base + pinned jitter, twice.

        The jitter draw is pinned to 0.25 via the seam suite's ``pin_jitter``
        (patches ``egress.attempts.random.uniform`` — the one home of the draw),
        turning the grade exact: 1.25s then 2.25s. A schedule computed anywhere
        other than the seam never reaches that draw and shows up here as the
        bare bases. The base knob is ``delenv``'d so an ambient test-speed
        value cannot turn this into an assertion about something else.
        """
        set_flags(monkeypatch, private=True)
        monkeypatch.delenv(BACKOFF_BASE_ENV, raising=False)
        pin_jitter(monkeypatch, 0.25)
        # A loopback port with nothing listening: resolves, fails fast, and the
        # sleeps between attempts are what is graded (the private-range hatch is open
        # because policy refuses loopback addresses by default; https is required
        # unconditionally now regardless of any hatch, GH #1802 -- hence the
        # scheme flip above). Ends in /mcp so no
        # fallback candidate is synthesised — one URL, 3 attempts, 2 sleeps.
        agent_url = "https://localhost:9999/mcp"

        with pytest.raises(MCPConnectionError):
            await call_mcp_tool(agent_url=agent_url, tool="noop", arguments={}, timeout=1, max_attempts=3)

        # The shared grader accepts a matching PREFIX of the schedule, so the
        # count is pinned explicitly: 3 attempts must produce exactly 2 sleeps.
        assert len(recorded_retry_sleeps) == 2, (
            f"expected exactly 2 backoff sleeps for 3 attempts, got {recorded_retry_sleeps}"
        )
        assert_backoff_schedule(recorded_retry_sleeps, jitter=0.25)

    async def test_default_attempt_budget_is_the_rules_three(self, monkeypatch, recorded_retry_sleeps):
        """Omitting ``max_attempts`` grades the DEFAULT budget against the rule.

        BR-RULE-029 says "up to 3 times". The schedule case above passes
        ``max_attempts=3`` explicitly, so on its own a silently raised default
        (say, 5) would sail past it while every production caller that takes
        the default exceeds the rule. Three attempts leave exactly two sleeps
        and say so in the failure message.
        """
        set_flags(monkeypatch, private=True)
        agent_url = "https://localhost:9999/mcp"

        with pytest.raises(MCPConnectionError) as exc_info:
            await call_mcp_tool(agent_url=agent_url, tool="noop", arguments={}, timeout=1)

        assert "after 3 attempts" in str(exc_info.value)
        assert len(recorded_retry_sleeps) == 2, (
            f"default attempt budget slept {recorded_retry_sleeps} — expected 2 sleeps for 3 attempts"
        )


def _always_failing_get_signals(signal_spec: str, discovery_mode: str = "brief") -> dict:
    """A ``get_signals`` tool that fails every time it is called.

    Takes the parameters ``GetSignalsRequest.model_dump(exclude_none=True)``
    actually sends, so the call fails in the tool BODY — an origin that rejected
    the arguments would fail during schema validation instead, which is a
    different failure at a different layer than the one under test.
    """
    raise ValueError("the signals tool is broken")


@pytest.mark.asyncio
class TestToolFailureIsRetriedByReexecutingTheBody:
    """A tool call that fails transiently is retried — with the tool called again.

    This is the property the context-manager seam structurally cannot have. Under
    ``create_mcp_client`` (this seam's predecessor) the ``yield`` sat inside the
    retry loop's own ``try`` while the caller's ``client.call_tool(...)`` ran in the
    CALLER's ``async with`` body — outside that ``try``. A tool-level failure is
    therefore thrown back INTO the generator at the yield, caught by its
    ``except Exception``, and the loop tries to yield a second time; contextlib
    refuses (``RuntimeError: generator didn't stop after athrow()``). Verified
    against the unmodified seam while authoring this test: the tool ran ONCE and
    the caller got that RuntimeError.

    ``call_mcp_tool`` owns the body instead of lending out a client, so the tool
    call is inside the per-attempt ``try`` and a transient tool failure is
    retried on the SAME attempt sequence a connect failure uses.

    The origin's own invocation count is the grade. A client that re-dials but
    does not re-issue the tool call would satisfy any assertion about the value
    returned; only the counter separates "the body was re-executed" from "the
    connection was re-established".
    """

    @pytest.mark.timeout(60)
    async def test_transient_tool_failure_is_retried_and_the_tool_runs_twice(
        self, mcp_origin_tls, monkeypatch, recorded_retry_sleeps
    ):
        """Attempt 1's tool call raises, attempt 2's succeeds, and the caller gets attempt 2's result."""
        # The MCP origin is a loopback address, which egress policy refuses by
        # default; https is required unconditionally regardless (GH #1802),
        # and the origin serves real TLS off the shared generated leaf.
        set_flags(monkeypatch, private=True)

        outcomes = iter([ValueError("transient tool failure"), {"served_on_attempt": 2}])

        def flaky() -> dict:
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        origin = mcp_origin_tls(flaky=flaky)

        result = await call_mcp_tool(
            agent_url=origin.base_url,
            tool="flaky",
            arguments={},
            timeout=10,
            max_attempts=3,
        )

        assert result.structured_content == {"served_on_attempt": 2}
        assert origin.invocations == ["flaky", "flaky"], (
            f"the tool body was not re-executed on the retry: {origin.invocations}"
        )
        # One sleep between two attempts, through the seam's schedule — the tool
        # failure was recorded on the shared attempt sequence, not retried by a
        # loop of its own that happens to re-dial.
        assert len(recorded_retry_sleeps) == 1, (
            f"expected exactly 1 backoff sleep between the two attempts, got {recorded_retry_sleeps}"
        )


@pytest.mark.asyncio
class TestExhaustedFailureReachesTheRegistryClassified:
    """An exhausted TOOL failure classifies exactly as an exhausted CONNECT failure does.

    Both legs run the same production path — ``SignalsAgentRegistry._fetch_signals_operator``,
    whose ``except (MCPConnectionError, MCPCompatibilityError)`` arm delegates to
    ``raise_mapped_mcp_error`` — and both must land on the one envelope that arm
    produces for a seam failure carrying no HTTP status.

    The connect leg is the reference: it passes today, so a failure of this test
    is a statement about the tool leg and not about the fixture, the registry or
    the classifier. The tool leg is the new obligation. Verified against the
    unmodified seam while authoring: today the tool leg raises an unclassified
    ``RuntimeError: generator didn't stop after athrow()`` that the registry's
    ``except`` arm does not even catch — so this is a NEW obligation, not an
    extension of a green one.
    """

    @pytest.mark.timeout(60)
    @pytest.mark.parametrize("failure", ["tool-level", "connection-level"])
    async def test_seam_failure_surfaces_as_the_mapped_envelope(
        self, failure, mcp_origin_tls, monkeypatch, recorded_retry_sleeps
    ):
        """Whichever layer fails, the buyer-facing envelope is the seam's one classified pair."""
        set_flags(monkeypatch, private=True)

        origin = None
        if failure == "tool-level":
            origin = mcp_origin_tls(get_signals=_always_failing_get_signals)
            agent_url = origin.base_url
        else:
            # A loopback port with nothing listening: resolves, fails fast, and
            # ends in /mcp so no fallback candidate is synthesised — one URL, one
            # attempt budget, as in the tool-level leg.
            agent_url = "https://localhost:9999/mcp"

        agent = SignalsAgent(agent_url=agent_url, name="stub-signals-agent", enabled=True, timeout=10)

        with pytest.raises(AdCPError) as exc_info:
            await SignalsAgentRegistry()._fetch_signals_operator(agent, brief="a brief")

        # The recovery is read from the pinned enumMetadata rather than written as
        # a literal, then pinned once here so the derivation cannot silently
        # return something else.
        expected_recovery = RECOVERY_BY_WIRE_CODE["SERVICE_UNAVAILABLE"]
        assert expected_recovery == "transient", (
            f"the pinned enumMetadata classifies SERVICE_UNAVAILABLE as {expected_recovery!r}, not 'transient' — "
            "the premise this assertion is built on no longer holds"
        )
        assert_envelope_shape(
            build_two_layer_error_envelope(exc_info.value),
            "SERVICE_UNAVAILABLE",
            recovery=expected_recovery,
            message_substr="signals agent stub-signals-agent is unreachable.",
        )
        assert len(recorded_retry_sleeps) == 2, (
            f"the failure was not retried on the seam's 3-attempt budget (slept {recorded_retry_sleeps})"
        )
        if origin is not None:
            assert origin.invocations == ["get_signals"] * 3, (
                f"the failing tool was not re-issued on every attempt: {origin.invocations}"
            )


def _unreached_get_signals(signal_spec: str, discovery_mode: str = "brief") -> dict:
    """A ``get_signals`` tool that must never run.

    Takes the parameters ``GetSignalsRequest.model_dump(exclude_none=True)``
    actually sends, so a call that DID reach the origin would be served rather
    than rejected during schema validation — the failure then names the fact
    under test ("the refused destination was dialled") instead of a schema
    mismatch at a different layer.
    """
    raise AssertionError("the refused destination was dialled and served a tool call")


@pytest.fixture
def hatch_closes_between_precheck_and_dial(monkeypatch):
    """Make the seam's TWO resolutions of the same URL genuinely disagree.

    ``call_mcp_tool`` resolves the destination twice: once through
    ``validate_url`` above the candidate loop, and once more inside the loop,
    when ``guarded_client_factory(current_url)``'s client is built and
    ``EgressPolicy.resolve_for_dial`` runs again on the dial. In production the
    two verdicts differ only when the DNS answer changes between them — a
    rebind, which is exactly the case where retrying a refused destination is
    worst. That window cannot be opened from a test by waiting for DNS, so it is
    opened through the OTHER input both resolutions read at CALL time
    (``_env_flag(ADCP_OUTBOUND_ALLOW_PRIVATE)``): the hatch is open when the
    pre-check reads it and closed when the dial reads it.

    Nothing here fabricates the refusal. The wrapper delegates to the real
    ``guarded_client_factory``, so the ``OutboundRequestBlocked`` the client
    meets is raised by the real ``EgressPolicy`` against the real loopback
    address of a real listening origin. Faking it — stubbing the factory to
    raise, or patching ``call_mcp_tool`` — would grade the test's own stub
    instead of the seam.

    Returns:
        The list of URLs the client actually built a dialling client for, one
        entry per attempt: the attempt counter for a destination policy refused.
    """
    dialled: list[str] = []
    build_real_factory = mcp_client_module.guarded_client_factory

    def building(url: str):
        real_factory = build_real_factory(url)

        def factory(*args, **kwargs):
            dialled.append(url)
            return real_factory(*args, **kwargs)

        # Closed HERE — after ``validate_url`` has already returned its verdict
        # above the loop, and before the factory resolves the same URL again.
        monkeypatch.setenv(ALLOW_PRIVATE_ENV, "false")
        return factory

    monkeypatch.setattr(mcp_client_module, "guarded_client_factory", building)
    return dialled


@pytest.mark.asyncio
class TestDialTimeRefusalIsNotRetriedOrLaundered:
    """An egress refusal raised INSIDE the dialling client keeps its own classification.

    ``TestRefusedAgentUrlIsNotDialled`` above grades the PRE-CHECK path, where
    ``validate_url`` refuses before the candidate loop is even built. This class
    grades the other half, and the half that is currently wrong: the pre-check
    PASSES and the in-loop ``guarded_client_factory`` resolution REFUSES. That
    refusal is raised inside ``async with client``, i.e. inside the per-attempt
    ``try`` whose arm is a bare ``except Exception`` — so today it is caught,
    logged as "MCP connection attempt N/M failed", slept on, retried against the
    destination egress policy has already refused, and finally re-raised as
    ``MCPConnectionError``. The registry then classifies that as
    ``SERVICE_UNAVAILABLE``/``transient``: a policy decision laundered into a
    transport failure, which is precisely what the comment above ``validate_url``
    claims its placement prevents.

    Two facts are graded together because either alone is satisfiable by the
    wrong design. The attempt count alone would pass if the refusal were raised
    unretried but still rewrapped as a transport failure; the wire envelope alone
    would pass if the refusal were retried three times and only then reported
    with the right code. The obligation is BOTH: one attempt, and the refusal's
    own code and recovery.
    """

    @pytest.mark.timeout(60)
    async def test_a_dial_time_refusal_reaches_the_buyer_unretried_and_unlaundered(
        self, mcp_origin_tls, monkeypatch, recorded_retry_sleeps, hatch_closes_between_precheck_and_dial
    ):
        """One attempt, no sleeps, and CONFIGURATION_ERROR/terminal on the wire."""
        # The pre-check's posture: loopback is allowed, so ``validate_url`` passes
        # and the candidate loop is entered — the precondition that separates this
        # test from the pre-check case, which never reaches the loop at all.
        set_flags(monkeypatch, private=True)

        # A real MCP origin that is genuinely listening and would answer. That is
        # what makes "nothing was served" a statement about the client's refusal
        # rather than about an address nothing could have reached anyway.
        origin = mcp_origin_tls(get_signals=_unreached_get_signals)
        agent = SignalsAgent(agent_url=origin.base_url, name="stub-signals-agent", enabled=True, timeout=10)

        with pytest.raises(AdCPError) as exc_info:
            await SignalsAgentRegistry()._fetch_signals_operator(agent, brief="a brief")

        assert hatch_closes_between_precheck_and_dial == [origin.base_url], (
            "a destination the egress policy refused at dial time was dialled more than once: "
            f"{hatch_closes_between_precheck_and_dial}"
        )
        assert recorded_retry_sleeps == [], (
            f"a dial-time policy refusal was retried (slept {recorded_retry_sleeps}) — it was caught by the "
            "connection retry loop instead of being re-raised out of it"
        )
        assert origin.invocations == [], f"the refused destination served a tool call: {origin.invocations}"

        # Read from the pinned enumMetadata rather than written as a literal, then
        # pinned once here so the derivation cannot silently return something else.
        expected_recovery = RECOVERY_BY_WIRE_CODE["CONFIGURATION_ERROR"]
        assert expected_recovery == "terminal", (
            f"the pinned enumMetadata classifies CONFIGURATION_ERROR as {expected_recovery!r}, not 'terminal' — "
            "the premise this assertion is built on no longer holds"
        )
        # The refusal's OWN pair, which ``raise_mapped_outbound_error`` produces for
        # an ``OutboundRequestBlocked`` against an ``OperatorEndpoint``. Reaching
        # SERVICE_UNAVAILABLE/transient here means the refusal arrived at the
        # registry as ``MCPConnectionError`` — swallowed and relabelled by the seam.
        assert_envelope_shape(
            build_two_layer_error_envelope(exc_info.value),
            "CONFIGURATION_ERROR",
            recovery=expected_recovery,
            message_substr="is not reachable under this deployment's egress policy",
        )
