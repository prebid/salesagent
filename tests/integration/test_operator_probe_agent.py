"""Grading for the operator's "test connection" probe on both agent registries.

The regression this module exists to make impossible: the two admin
test-connection routes used to rebuild the stored-row -> dial-config mapping by
hand, and in doing so dropped ``auth_header`` and ``timeout`` (the signals half
additionally hard-coded ``timeout=30``, discarding the stored column). The
operator's button therefore dialled with different auth and a different timeout
than production did, so a probe that passed proved nothing about the path that
actually runs. ``CreativeAgentRegistry.config_for`` /
``SignalsAgentRegistry.config_for`` are now the single mapping, and
``probe_agent`` is the single entry point -- but neither was graded, so deleting
``auth_header=db_agent.auth_header`` from ``config_for`` reintroduced the whole
bug with every test still green.

Complements ``tests/integration/test_auth_header_propagation.py``: that module
starts from a hand-built ``CreativeAgent``/``SignalsAgent`` dataclass and proves
the FETCH forwards it to the seam. This one starts from a stored ORM row -- the
thing an operator actually configures -- and proves the row's own columns reach
the same seam through ``probe_agent``. Both patch
``src.core.utils.operator_mcp.call_mcp_tool``, one frame below
``call_operator_mcp_tool``, so the whole forwarding chain stays under test and a
parameter dropped at any hop still fails these assertions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.creative_agent_registry import CreativeAgent, CreativeAgentRegistry
from src.core.database.models import CreativeAgent as DBCreativeAgent
from src.core.database.models import SignalsAgent as DBSignalsAgent
from src.core.exceptions import AdCPConfigurationError, AdCPServiceUnavailableError
from src.core.signals_agent_registry import SignalsAgent, SignalsAgentRegistry
from src.core.utils.mcp_client import _build_auth_headers
from tests.factories import CreativeAgentFactory, SignalsAgentFactory, TenantFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_SEAM_DIAL = "src.core.utils.operator_mcp.call_mcp_tool"

# Deliberately unlike every default on the path: a header name no default would
# produce, and a timeout that is neither the dataclass default (30) nor the
# literal the signals route used to hard-code (also 30). A config that carried
# defaults instead of the row would fail on both.
STORED_AUTH_HEADER = "X-Optable-Key"
STORED_TIMEOUT = 17
STORED_AUTH_TYPE = "bearer"
STORED_CREDENTIALS = "stored-credential-abc123"


TENANT_ID = "operator_probe"


@pytest.fixture
def env(integration_db):
    """The factories bound to a real session for the duration of the test."""
    with IntegrationEnv() as env:
        yield env


@pytest.fixture
def tenant(env):
    """A committed tenant, so production's own session sees the agent rows."""
    return TenantFactory(tenant_id=TENANT_ID, name="Operator Probe", subdomain="operatorprobe")


@pytest.fixture
def creative_row(tenant) -> DBCreativeAgent:
    return CreativeAgentFactory(
        tenant=tenant,
        agent_url="https://creative.optable.example/mcp",
        name="Optable Creative",
        enabled=True,
        priority=7,
        auth_type=STORED_AUTH_TYPE,
        auth_credentials=STORED_CREDENTIALS,
        auth_header=STORED_AUTH_HEADER,
        timeout=STORED_TIMEOUT,
    )


@pytest.fixture
def signals_row(tenant) -> DBSignalsAgent:
    return SignalsAgentFactory(
        tenant=tenant,
        agent_url="https://signals.optable.example/mcp",
        name="Optable Signals",
        enabled=True,
        auth_type=STORED_AUTH_TYPE,
        auth_credentials=STORED_CREDENTIALS,
        auth_header=STORED_AUTH_HEADER,
        timeout=STORED_TIMEOUT,
    )


def _payload_dial(payload: dict) -> AsyncMock:
    """A ``call_mcp_tool`` stand-in answering with *payload* as structured content."""
    return AsyncMock(return_value=MagicMock(structured_content=payload, content=[]))


def _raising_dial(exc: Exception) -> AsyncMock:
    return AsyncMock(side_effect=exc)


def _signal_payload(count: int) -> dict:
    """*count* signals in the shape the pinned ``GetSignalsResponse`` accepts.

    Fully-formed rather than minimal: ``_fetch_signals_operator`` validates the
    payload and turns a ValidationError into a FAILED probe, so an under-filled
    signal would silently grade the failure path while claiming to grade success.
    """
    return {
        "signals": [
            {
                "signal_agent_segment_id": f"seg_{index}",
                "name": f"Signal {index}",
                "description": f"Signal {index} description",
                "signal_type": "marketplace",
                "deployments": [
                    {
                        "type": "platform",
                        "platform": "gam",
                        "is_live": True,
                        "scope": "platform-wide",
                        "decisioning_platform_segment_id": f"seg_{index}",
                    }
                ],
            }
            for index in range(count)
        ]
    }


def _format_payload(count: int) -> dict:
    return {
        "formats": [
            {
                "format_id": {"agent_url": "https://creative.optable.example", "id": f"display_{index}"},
                "name": f"Format {index}",
                "type": "display",
            }
            for index in range(count)
        ]
    }


# ---------------------------------------------------------------------------
# 1. config_for carries EVERY dialled field
# ---------------------------------------------------------------------------


class TestConfigForCarriesTheWholeRow:
    """The one row -> config mapping loses nothing between the DB and the dial."""

    def test_creative_config_for_carries_every_dialled_field(self, creative_row):
        """Exact equality on the whole dataclass -- an omitted field falls back to
        its default, which is precisely the bug, so field-presence is not enough."""
        assert CreativeAgentRegistry.config_for(creative_row) == CreativeAgent(
            agent_url="https://creative.optable.example/mcp",
            name="Optable Creative",
            enabled=True,
            priority=7,
            auth={"type": STORED_AUTH_TYPE, "credentials": STORED_CREDENTIALS},
            auth_header=STORED_AUTH_HEADER,
            timeout=STORED_TIMEOUT,
        )

    def test_signals_config_for_carries_every_dialled_field(self, signals_row):
        assert SignalsAgentRegistry.config_for(signals_row) == SignalsAgent(
            agent_url="https://signals.optable.example/mcp",
            name="Optable Signals",
            enabled=True,
            auth={"type": STORED_AUTH_TYPE, "credentials": STORED_CREDENTIALS},
            auth_header=STORED_AUTH_HEADER,
            timeout=STORED_TIMEOUT,
            # NAMED, not left to its ``None`` default, and this is the field the
            # exact-equality style above exists for: ``config_for`` reads tenant_id
            # because it SELECTS THE SIGNING KEY, so a probe config built without it
            # dials UNSIGNED while production's config for the same row dials signed
            # (``signals_agent_registry.config_for`` states exactly this). Defaulting
            # it here would assert the divergence away.
            tenant_id=TENANT_ID,
        )

    def test_creative_config_for_leaves_auth_absent_when_the_row_stores_none(self, tenant):
        """No stored credentials means no auth dict -- not an empty one, which
        ``_build_auth_headers`` would treat identically but which would hide a
        half-configured row from anything reading ``auth is None``."""
        row = CreativeAgentFactory(tenant=tenant, auth_type=None, auth_credentials=None)
        assert CreativeAgentRegistry.config_for(row).auth is None

    def test_creative_probe_config_dials_like_the_production_discovery_path(self, creative_row):
        """The probe's config and the config production discovery builds for the
        SAME row must agree on everything the seam reads. Two hand-written
        mappings that drift is the original defect; this is the drift alarm."""
        production = next(
            agent
            for agent in CreativeAgentRegistry()._get_tenant_agents(TENANT_ID)
            if agent.agent_url == creative_row.agent_url
        )
        probe = CreativeAgentRegistry.config_for(creative_row)

        assert probe.auth_header == production.auth_header == STORED_AUTH_HEADER
        assert probe.timeout == production.timeout == STORED_TIMEOUT
        assert (
            _build_auth_headers(probe.auth, probe.auth_header)
            == _build_auth_headers(production.auth, production.auth_header)
            == {STORED_AUTH_HEADER: f"Bearer {STORED_CREDENTIALS}"}
        )

    def test_signals_probe_config_dials_like_the_production_discovery_path(self, signals_row):
        production = next(
            agent
            for agent in SignalsAgentRegistry()._get_tenant_agents(TENANT_ID)
            if agent.agent_url == signals_row.agent_url
        )
        probe = SignalsAgentRegistry.config_for(signals_row)

        assert probe.auth_header == production.auth_header == STORED_AUTH_HEADER
        assert probe.timeout == production.timeout == STORED_TIMEOUT
        assert (
            _build_auth_headers(probe.auth, probe.auth_header)
            == _build_auth_headers(production.auth, production.auth_header)
            == {STORED_AUTH_HEADER: f"Bearer {STORED_CREDENTIALS}"}
        )


# ---------------------------------------------------------------------------
# 2. probe_agent dials with production's auth_header and timeout
# ---------------------------------------------------------------------------


class TestProbeDialsWithTheStoredConfig:
    """From an ORM row all the way to the kwargs the guarded seam receives."""

    @pytest.mark.asyncio
    async def test_creative_probe_dials_with_the_rows_auth_header_and_timeout(self, creative_row):
        with patch(_SEAM_DIAL, _payload_dial(_format_payload(1))) as dial:
            result = await CreativeAgentRegistry().probe_agent(creative_row)

        assert result.ok is True, result.message
        assert dial.call_args.kwargs["auth_header"] == STORED_AUTH_HEADER
        assert dial.call_args.kwargs["timeout"] == STORED_TIMEOUT
        assert dial.call_args.kwargs["auth"] == {"type": STORED_AUTH_TYPE, "credentials": STORED_CREDENTIALS}
        assert dial.call_args.kwargs["agent_url"] == "https://creative.optable.example/mcp"

    @pytest.mark.asyncio
    async def test_signals_probe_dials_with_the_rows_auth_header_and_timeout(self, signals_row):
        with patch(_SEAM_DIAL, _payload_dial({"signals": []})) as dial:
            result = await SignalsAgentRegistry().probe_agent(signals_row)

        assert result.ok is True, result.message
        assert dial.call_args.kwargs["auth_header"] == STORED_AUTH_HEADER
        assert dial.call_args.kwargs["timeout"] == STORED_TIMEOUT
        assert dial.call_args.kwargs["auth"] == {"type": STORED_AUTH_TYPE, "credentials": STORED_CREDENTIALS}
        assert dial.call_args.kwargs["agent_url"] == "https://signals.optable.example/mcp"


# ---------------------------------------------------------------------------
# 3. ProbeResult success shape
# ---------------------------------------------------------------------------


class TestProbeResultSuccessShape:
    """A count is the number the agent answered with, never a route-side default."""

    @pytest.mark.asyncio
    async def test_creative_success_reports_the_real_count_and_first_five_names(self, creative_row):
        with patch(_SEAM_DIAL, _payload_dial(_format_payload(6))):
            result = await CreativeAgentRegistry().probe_agent(creative_row)

        assert result.ok is True
        assert result.message == "Successfully connected to 'Optable Creative'"
        assert result.count == 6
        assert result.samples == ("Format 0", "Format 1", "Format 2", "Format 3", "Format 4")

    @pytest.mark.asyncio
    async def test_signals_success_reports_the_real_signal_count(self, signals_row):
        with patch(_SEAM_DIAL, _payload_dial(_signal_payload(3))):
            result = await SignalsAgentRegistry().probe_agent(signals_row)

        assert result.ok is True
        assert result.message == "Successfully connected to signals agent"
        assert result.count == 3
        assert result.samples == ()

    @pytest.mark.asyncio
    async def test_signals_success_with_zero_signals_is_a_zero_not_an_absence(self, signals_row):
        """A reachable agent holding no signals reports ``count == 0``; only a
        FAILED probe reports ``None``. The dict this replaced could not tell the
        two apart, because the route defaulted a missing count to 0."""
        with patch(_SEAM_DIAL, _payload_dial({"signals": []})):
            result = await SignalsAgentRegistry().probe_agent(signals_row)

        assert result.ok is True
        assert result.count == 0


# ---------------------------------------------------------------------------
# 4. ProbeResult failure shape -- probe_failure's configuration and
#    non-configuration branches.
#
#    ``probe_failure`` grew a THIRD branch with outbound request signing: an
#    ``AdCPConfigurationError`` whose ``__cause__`` is an ``MCPSigningError``
#    gets its own sentence, naming the key-ACTIVE and private-half levers
#    instead of the URL/credentials/egress ones below. Nothing in this file
#    grades it yet -- the signing branch reaches this seam only through
#    ``call_operator_mcp_tool``'s ``except MCPSigningError`` chaining, which
#    needs a tenant with an ACTIVE request_signing key whose material fails to
#    load, and therefore a signing-key fixture.
# ---------------------------------------------------------------------------

_OPERATOR_LEVERS = (
    "Check the agent URL, its credentials and auth header, and whether this "
    "deployment's egress policy allows the address."
)

# The cause half of the sentence is the text of the raise site's ``internal_detail``
# (the exception it caught), NOT ``AdCPConfigurationError.message``. Per ADR-010 ``message`` is a read-only
# property over ``CODE_TABLE`` -- a function of the code -- so it reads
# "Configuration error" for a handshake refusal, an egress refusal and an
# unparseable answer alike, naming no lever. ``internal_detail`` is barred from
# the BUYER-facing wire envelope (AdCP 3.1.1 transport-errors.mdx Security
# Considerations) and this is not that surface: it is the admin "test connection"
# dialog, read by the tenant operator who configured the agent. Pinned as one
# literal rather than recomputed from ``CODE_TABLE`` or from the raised error's
# own ``internal_detail``: an expectation derived from what production reads
# would pass for any text at all, including none.
_CONFIG_FAILURE_SENTENCE = f"Connection failed: Endpoint refused the handshake. {_OPERATOR_LEVERS}"

# The non-configuration branch has the SAME obligation and had the same blindness:
# ``str()`` of a typed error is its ``CODE_TABLE`` message, which reads "Service
# temporarily unavailable" for an unreachable endpoint, a rate-limited one and an
# undelivered request alike. ``raise_mapped_mcp_error`` carries the exception naming
# WHICH one in ``internal_detail``. No advice is appended here, unlike the
# configuration branch: none of the operator's levers is known to be the cause.
# Written out in full rather than composed from the detail the dial raises, for
# the same reason as above -- an expectation derived from the input would hold
# for any output.
_UNREACHABLE_FAILURE_SENTENCE = "Connection failed: creative agent Optable Creative is unreachable."


class TestProbeResultFailureShape:
    """A failed probe carries a sentence and an ABSENT count."""

    @pytest.mark.asyncio
    async def test_creative_configuration_failure_names_every_operator_lever(self, creative_row):
        dial = _raising_dial(
            AdCPConfigurationError(internal_detail=ConnectionRefusedError("Endpoint refused the handshake."))
        )
        with patch(_SEAM_DIAL, dial):
            result = await CreativeAgentRegistry().probe_agent(creative_row)

        assert result.ok is False
        assert result.count is None
        assert result.samples == ()
        assert result.message == _CONFIG_FAILURE_SENTENCE

    @pytest.mark.asyncio
    async def test_signals_configuration_failure_names_every_operator_lever(self, signals_row):
        dial = _raising_dial(
            AdCPConfigurationError(internal_detail=ConnectionRefusedError("Endpoint refused the handshake."))
        )
        with patch(_SEAM_DIAL, dial):
            result = await SignalsAgentRegistry().probe_agent(signals_row)

        assert result.ok is False
        assert result.count is None
        assert result.message == _CONFIG_FAILURE_SENTENCE

    @pytest.mark.asyncio
    async def test_unreachable_endpoint_names_the_endpoint_not_the_code_table_sentence(self, creative_row):
        """A SERVICE_UNAVAILABLE failure reports which endpoint state it was.

        The probe's second branch admits every failure the configuration branch does
        not -- unreachable, rate-limited, undelivered -- and all three share one
        ``CODE_TABLE`` sentence, so reading ``str(exc)`` would tell the operator
        nothing at all. Graded on the creative registry only because the branch is
        one shared function; the parity case below covers both.
        """
        dial = _raising_dial(
            AdCPServiceUnavailableError(
                internal_detail=ConnectionError("creative agent Optable Creative is unreachable.")
            )
        )
        with patch(_SEAM_DIAL, dial):
            result = await CreativeAgentRegistry().probe_agent(creative_row)

        assert result.ok is False
        assert result.count is None
        assert result.samples == ()
        assert result.message == _UNREACHABLE_FAILURE_SENTENCE

    @pytest.mark.asyncio
    async def test_creative_unexpected_failure_reports_the_short_form(self, creative_row):
        """The non-configuration branch: no advice is offered, because none of the
        operator's levers is known to be the cause."""
        with patch(_SEAM_DIAL, _raising_dial(RuntimeError("socket exploded"))):
            result = await CreativeAgentRegistry().probe_agent(creative_row)

        assert result.ok is False
        assert result.count is None
        assert result.message == "Connection failed: socket exploded"

    @pytest.mark.asyncio
    async def test_signals_unexpected_failure_reports_the_short_form(self, signals_row):
        with patch(_SEAM_DIAL, _raising_dial(RuntimeError("socket exploded"))):
            result = await SignalsAgentRegistry().probe_agent(signals_row)

        assert result.ok is False
        assert result.count is None
        assert result.message == "Connection failed: socket exploded"

    @pytest.mark.asyncio
    async def test_creative_agent_answering_with_no_formats_is_a_failure_without_a_count(self, creative_row):
        """A reachable agent serving an empty catalog is not a working creative
        agent. ``count`` stays absent rather than becoming 0, so the operator is
        not shown a number for a probe that did not succeed."""
        with patch(_SEAM_DIAL, _payload_dial({"formats": []})):
            result = await CreativeAgentRegistry().probe_agent(creative_row)

        assert result.ok is False
        assert result.count is None
        assert result.message == "Agent returned no formats"


# ---------------------------------------------------------------------------
# 5. The two registries report failure identically
# ---------------------------------------------------------------------------


class TestBothRegistriesReportFailureIdentically:
    """One operator, one dialog, one set of levers -- so one sentence.

    The two ladders were copy-pasted before they were shared. A wording
    difference between the creative button and the signals button would be a
    difference with no cause behind it, so it is asserted rather than left to
    reviewers to notice.
    """

    @pytest.mark.parametrize(
        "exc",
        [
            AdCPConfigurationError(internal_detail=ConnectionRefusedError("Endpoint refused the handshake.")),
            RuntimeError("socket exploded"),
        ],
        ids=["configuration", "unexpected"],
    )
    @pytest.mark.asyncio
    async def test_same_exception_yields_the_same_sentence_from_both_registries(self, exc, creative_row, signals_row):
        with patch(_SEAM_DIAL, _raising_dial(exc)):
            creative = await CreativeAgentRegistry().probe_agent(creative_row)
        with patch(_SEAM_DIAL, _raising_dial(exc)):
            signals = await SignalsAgentRegistry().probe_agent(signals_row)

        assert creative.ok is signals.ok is False
        assert creative.count is signals.count is None
        assert creative.message == signals.message
