"""Grading for the JSON the two "test connection" admin routes put on the wire.

``ProbeResult`` is one typed value for both registries; each blueprint projects
it onto the key names its own template already contracts on. That projection --
``ok/message/count/samples`` -> ``success/message/format_count|signal_count/
sample_formats`` -- is the whole of what these routes do with a probe outcome,
and it is read by ``templates/creative_agents.html`` and
``templates/signals_agents.html`` at those exact key names. A wrong attribute on
either side would ``AttributeError`` into the blanket ``except`` and reach the
operator as a generic 500 with nothing red, so the projection is asserted here as
an EXACT body, key for key.

The dial is NOT under test: ``probe_agent`` is patched at the registry, which is
the seam ``tests/integration/test_operator_probe_agent.py`` grades from the other
side (stored row -> dial config, 17 cases). These cases start where that one
stops -- with a known ``ProbeResult`` -- and grade only the HTTP status and the
JSON body.

The signals route re-asks egress policy about the stored URL before probing, so
the rows here carry ``UNDIALLED_PUBLIC_HTTPS_ORIGIN``: a public-unicast IP
literal that passes policy under every hatch posture without a DNS answer, and
that nothing in this module ever connects to.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.admin.app import create_app
from src.core.utils.operator_mcp import ProbeResult
from tests.factories import CreativeAgentFactory, SignalsAgentFactory, TenantFactory
from tests.helpers.egress_hatches import UNDIALLED_PUBLIC_HTTPS_ORIGIN

app = create_app()

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

AGENT_URL = f"{UNDIALLED_PUBLIC_HTTPS_ORIGIN}/mcp"

# Sentences and counts unlike any default on the path, so a body that carried a
# default instead of the projected ProbeResult field cannot pass by coincidence.
SUCCESS_MESSAGE = "Connected to Optable, 42 formats available"
FAILURE_MESSAGE = "Connection failed: handshake rejected by the endpoint"
FORMAT_COUNT = 42
SIGNAL_COUNT = 37
SAMPLE_FORMATS = ("display_300x250_image", "video_16x9_15s", "audio_30s")

_CREATIVE_PROBE = "src.core.creative_agent_registry.CreativeAgentRegistry.probe_agent"
_SIGNALS_PROBE = "src.core.signals_agent_registry.SignalsAgentRegistry.probe_agent"


@pytest.fixture
def client():
    """Flask test client with CSRF disabled for POST testing."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    with app.test_client() as client:
        yield client


def _auth_session(client, tenant_id: str) -> None:
    """Populate a super-admin test-mode session."""
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {"email": "test@example.com", "is_super_admin": True}
        sess["email"] = "test@example.com"
        sess["tenant_id"] = tenant_id
        sess["test_user"] = "test@example.com"
        sess["test_user_role"] = "super_admin"
        sess["test_user_name"] = "Test User"
        sess["test_tenant_id"] = tenant_id


@pytest.fixture
def creative_agent(factory_session):
    """A committed creative-agent row, with the client already authenticated for it."""
    return CreativeAgentFactory(tenant=TenantFactory(), agent_url=AGENT_URL)


@pytest.fixture
def signals_agent(factory_session):
    """A committed signals-agent row, with the client already authenticated for it."""
    return SignalsAgentFactory(tenant=TenantFactory(), agent_url=AGENT_URL)


def _post_creative_test(client, agent) -> tuple[int, dict]:
    _auth_session(client, agent.tenant_id)
    response = client.post(f"/tenant/{agent.tenant_id}/creative-agents/{agent.id}/test")
    return response.status_code, response.get_json()


def _post_signals_test(client, agent) -> tuple[int, dict]:
    _auth_session(client, agent.tenant_id)
    response = client.post(f"/tenant/{agent.tenant_id}/signals-agents/{agent.id}/test")
    return response.status_code, response.get_json()


class TestCreativeAgentProbeProjection:
    """POST /tenant/<id>/creative-agents/<agent_id>/test — ProbeResult -> JSON."""

    def test_success_projects_every_field_onto_the_template_key_names(self, client, creative_agent):
        """ok/message/count/samples arrive as success/message/format_count/sample_formats."""
        result = ProbeResult(ok=True, message=SUCCESS_MESSAGE, count=FORMAT_COUNT, samples=SAMPLE_FORMATS)
        with patch(_CREATIVE_PROBE, new=AsyncMock(return_value=result)):
            status, body = _post_creative_test(client, creative_agent)

        assert status == 200
        assert body == {
            "success": True,
            "message": SUCCESS_MESSAGE,
            "format_count": FORMAT_COUNT,
            "sample_formats": list(SAMPLE_FORMATS),
        }

    def test_failure_puts_the_sentence_under_error_not_message(self, client, creative_agent):
        """A refused probe is a 400 whose sentence rides under ``error``."""
        result = ProbeResult(ok=False, message=FAILURE_MESSAGE)
        with patch(_CREATIVE_PROBE, new=AsyncMock(return_value=result)):
            status, body = _post_creative_test(client, creative_agent)

        assert status == 400
        assert body == {"success": False, "error": FAILURE_MESSAGE}

    def test_success_without_a_count_stays_null_on_the_wire(self, client, creative_agent):
        """``count=None`` is an absence and must not be projected as a genuine 0."""
        result = ProbeResult(ok=True, message=SUCCESS_MESSAGE, count=None)
        with patch(_CREATIVE_PROBE, new=AsyncMock(return_value=result)):
            status, body = _post_creative_test(client, creative_agent)

        assert status == 200
        assert body == {
            "success": True,
            "message": SUCCESS_MESSAGE,
            "format_count": None,
            "sample_formats": [],
        }

    def test_missing_agent_row_is_404(self, client, creative_agent):
        """The projection cases cannot be passing against a missing-row path."""
        _auth_session(client, creative_agent.tenant_id)
        response = client.post(f"/tenant/{creative_agent.tenant_id}/creative-agents/{creative_agent.id + 9999}/test")

        assert response.status_code == 404
        assert response.get_json() == {"error": "Creative agent not found"}


class TestSignalsAgentProbeProjection:
    """POST /tenant/<id>/signals-agents/<agent_id>/test — ProbeResult -> JSON."""

    def test_success_projects_count_onto_signal_count(self, client, signals_agent):
        """The signals template reads ``signal_count`` and has no sample list."""
        result = ProbeResult(ok=True, message=SUCCESS_MESSAGE, count=SIGNAL_COUNT)
        with patch(_SIGNALS_PROBE, new=AsyncMock(return_value=result)):
            status, body = _post_signals_test(client, signals_agent)

        assert status == 200
        assert body == {"success": True, "message": SUCCESS_MESSAGE, "signal_count": SIGNAL_COUNT}

    def test_failure_puts_the_sentence_under_error_not_message(self, client, signals_agent):
        """A refused probe is a 400 whose sentence rides under ``error``."""
        result = ProbeResult(ok=False, message=FAILURE_MESSAGE)
        with patch(_SIGNALS_PROBE, new=AsyncMock(return_value=result)):
            status, body = _post_signals_test(client, signals_agent)

        assert status == 400
        assert body == {"success": False, "error": FAILURE_MESSAGE}

    def test_success_without_a_count_stays_null_on_the_wire(self, client, signals_agent):
        """The state the old dict route could not express: absent count, not 0."""
        result = ProbeResult(ok=True, message=SUCCESS_MESSAGE, count=None)
        with patch(_SIGNALS_PROBE, new=AsyncMock(return_value=result)):
            status, body = _post_signals_test(client, signals_agent)

        assert status == 200
        assert body == {"success": True, "message": SUCCESS_MESSAGE, "signal_count": None}

    def test_missing_agent_row_is_404(self, client, signals_agent):
        """The projection cases cannot be passing against a missing-row path."""
        _auth_session(client, signals_agent.tenant_id)
        response = client.post(f"/tenant/{signals_agent.tenant_id}/signals-agents/{signals_agent.id + 9999}/test")

        assert response.status_code == 404
        assert response.get_json() == {"error": "Signals agent not found"}
