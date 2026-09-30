"""An unreachable creative agent must not leak its raw failure text into the
SUCCESS payload's ``errors[]``.

The sibling obligation to ``tests/integration/test_typed_error_wire_safety.py``,
on the OTHER buyer-facing error carrier. ``list_creative_formats`` degrades
gracefully: one unreachable agent does not fail the request, it produces an
``AGENT_UNREACHABLE`` entry in ``errors[]`` on an otherwise successful response
(``src/core/creative_agent_registry.py`` ``list_all_formats_with_errors``).
Because the response is a success, ``result.is_error`` is False and
``wire_error_envelope`` is ``None`` — the two-layer envelope helpers cannot
grade this path at all, which is why it needs its own assertion surface
(``assert_no_marker_in_payload_errors``).

The carrier is in scope for the same MUST NOT list as the envelope:

* AdCP 3.1.1 ``v3.1.1:docs/building/operating/transport-errors.mdx``
  § Security Considerations / Seller Requirements (lines 659-670) opens with
  "Error responses flow through LLM context. Every field is client-facing" and
  forbids internal service names/hostnames/IP addresses and upstream API
  responses.
* ``dist/compliance/3.1.1/universal/error-compliance.yaml`` (:323) grades a
  typed error code "via either ``adcp_error`` (envelope) or ``errors[]``
  (payload)" — the two carriers have equal status. That step validates the
  CODE only, so the message-content obligation asserted here is ungraded by the
  storyboard and rests on the normative prose above.

The construction site fixed here interpolated BOTH the caught exception and the
seller-configured ``agent.agent_url``::

    message=f"Creative agent at {agent.agent_url} is unreachable: {e}"

Both are dropped. The raw cause is still captured server-side by the existing
``logger.error(..., exc_info=True)`` one line above it — ``adcp.types.Error`` is
an SDK model, so it has no ``internal_detail`` slot to route it through.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.creative_agent_registry import CreativeAgentRegistry
from src.core.errors.codes import CODE_TABLE
from tests.factories import PrincipalFactory, TenantFactory
from tests.harness.creative_formats import CreativeFormatsEnv
from tests.harness.transport import Transport
from tests.helpers.envelope_assertions import assert_no_marker_in_payload_errors

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Seller-internal infrastructure detail of the shape a real transport failure
# carries. Same fake-DSN marker technique as
# tests/bdd/steps/domain/security_wire_safety.py: if it shows up in errors[],
# the raw third-party text reached the buyer.
_SECRET_MARKER = "creative-agent.internal.svc.cluster.local:8443"
_RAW_FAILURE_TEXT = f"[Errno -2] Name or service not known: {_SECRET_MARKER}"

# The seller-configured agent endpoint. The buyer never sent it and this seller
# does not publish creative-agent URLs through its capability declarations, so
# transport-errors.mdx:666 ("MUST NOT include: internal service names,
# hostnames, or IP addresses") keeps it off the wire too.
_AGENT_URL = "https://creative-agent.internal.svc.cluster.local:8443/mcp"

# IMPL has no wire body; the three wire transports all serialize the same
# success payload, and the message is baked in before any of them runs.
_TRANSPORTS = [Transport.A2A, Transport.MCP, Transport.REST]


@contextlib.contextmanager
def _one_unreachable_agent(env):
    """Fault ONLY the per-agent dial, leaving the aggregation branch under test real.

    Both tests below need the identical injection, so it is spelled once
    (CLAUDE.md DRY invariant). Two things are patched and no more:

    * ``_get_tenant_agents`` — supplies the seller-configured agent, because
      there is no tenant row for one.
    * ``_fetch_formats_operator`` — the DEEPEST production method on the
      operator dial, so ``get_formats_for_agent`` (provenance branch, testing
      short-circuit, cache) and ``list_all_formats_with_errors`` (the
      ``except`` branch this module grades) both run for real.

    The fetch method used to be ``_fetch_formats_from_agent``, patched alongside
    a ``_build_adcp_client``. Neither name exists any more: the egress-seam
    migration renamed the operator dial to ``_fetch_formats_operator`` and
    deleted the SDK-client builder outright in favour of
    ``call_operator_mcp_tool``, whose httpx stack egress policy can reach
    (adcp 6.6.0 exposes no transport knob — adcp-client-python#1004). Patching a
    name that no longer exists is what the re-point fixes; the injected fault and
    everything asserted about it are unchanged.
    """
    real_registry = CreativeAgentRegistry()

    agent = MagicMock()
    agent.agent_url = _AGENT_URL
    agent.name = "Internal Creative Agent"

    with (
        patch.object(CreativeAgentRegistry, "_get_tenant_agents", return_value=[agent]),
        patch.object(
            CreativeAgentRegistry,
            "_fetch_formats_operator",
            new=AsyncMock(side_effect=ConnectionError(_RAW_FAILURE_TEXT)),
        ),
    ):
        # Run the REAL aggregation method (the construction site under test)
        # instead of the harness stub; only the per-agent fetch is faulted.
        env.mock["registry"].return_value.list_all_formats_with_errors = real_registry.list_all_formats_with_errors
        yield


@pytest.mark.parametrize("transport", _TRANSPORTS, ids=lambda t: t.value)
def test_unreachable_agent_yields_a_safe_payload_error(integration_db, transport, monkeypatch):
    """errors[0] names the failure in first-party terms and carries no raw text."""
    # ADCP_TESTING short-circuits list_all_formats_with_errors to the checked-in
    # reference catalog, which never reaches the failure branch under test.
    monkeypatch.setenv("ADCP_TESTING", "false")

    with CreativeFormatsEnv(tenant_id="fmt-wire-safety", principal_id="fmt-wire-principal") as env:
        tenant = TenantFactory(tenant_id="fmt-wire-safety", subdomain="fmt-wire-safety")
        PrincipalFactory(tenant=tenant, principal_id="fmt-wire-principal")

        with _one_unreachable_agent(env):
            result = env.call_via(transport)

        assert not result.is_error, f"an unreachable agent must degrade, not fail the request: {result.payload!r}"

        errors = (result.wire_response or {}).get("errors")
        assert errors, (
            f"a failed agent must be reported in the success payload's errors[], got {result.wire_response!r}"
        )

        # POSITIVE: the exact first-party sentence the seller publishes, at the
        # protocol position. Mandatory — a negative-only check passes vacuously.
        assert errors[0]["code"] == "AGENT_UNREACHABLE", f"errors[0].code={errors[0]['code']!r}"
        assert errors[0]["message"] == CODE_TABLE["AGENT_UNREACHABLE"].message, (
            f"errors[0].message={errors[0]['message']!r}, expected the first-party sentence "
            f"{CODE_TABLE['AGENT_UNREACHABLE'].message!r}"
        )

        # NEGATIVE: neither the third party's text nor the seller-configured
        # endpoint appears anywhere in errors[].
        assert_no_marker_in_payload_errors(result.wire_response, _SECRET_MARKER)
        assert_no_marker_in_payload_errors(result.wire_response, _RAW_FAILURE_TEXT)


@pytest.mark.parametrize("transport", _TRANSPORTS, ids=lambda t: t.value)
def test_requested_format_id_on_a_failed_agent_is_reference_not_found(integration_db, transport, monkeypatch):
    """A format_id REFERENCING the failed agent is a resolution failure, not an advisory.

    The sibling above covers the seller-aggregation branch: nobody asked for that
    agent, it merely failed while the seller was collecting its catalog, and the
    buyer gets the transient AGENT_UNREACHABLE advisory.

    This is the other branch, and the pinned spec is explicit about it --
    ``dist/docs/3.1.0/creative/task-reference/list_creative_formats.mdx:654``:

        REFERENCE_NOT_FOUND | Requested format_id doesn't exist, or referenced
        creative agent is unavailable / not accessible. error.field MUST
        identify which typed parameter failed to resolve.

    So when ``req.format_ids`` names this agent, the code changes AND
    ``error.field`` moves from "formats" (the response section the advisory
    degrades) to "format_ids" (the typed parameter that failed to resolve --
    the MUST).

    RECOVERY IS THE BUYER-VISIBLE CONSEQUENCE, and is why the two branches must
    not be collapsed: AGENT_UNREACHABLE is transient, so a buyer is told to
    retry; REFERENCE_NOT_FOUND is correctable, so it is told to fix the
    reference. Retrying the same format_ids against a down agent never succeeds,
    which is exactly the wrong advice the undifferentiated advisory gave.

    Covers: salesagent-3dawm.16 keep-condition (1).
    """
    monkeypatch.setenv("ADCP_TESTING", "false")

    with CreativeFormatsEnv(tenant_id="fmt-ref-nf", principal_id="fmt-ref-principal") as env:
        tenant = TenantFactory(tenant_id="fmt-ref-nf", subdomain="fmt-ref-nf")
        PrincipalFactory(tenant=tenant, principal_id="fmt-ref-principal")

        with _one_unreachable_agent(env):
            result = env.call_via(transport, format_ids=[{"agent_url": _AGENT_URL, "id": "display_300x250"}])

        assert not result.is_error, f"a failed agent must still degrade, not fail the request: {result.payload!r}"

        errors = (result.wire_response or {}).get("errors")
        assert errors, f"a referenced-but-failed agent must be reported in errors[], got {result.wire_response!r}"

        assert errors[0]["code"] == "REFERENCE_NOT_FOUND", (
            f"a format_id naming the failed agent must resolve-fail, not emit the aggregation advisory; "
            f"got code={errors[0]['code']!r}"
        )
        # The MUST from mdx:654 — name the typed parameter that failed to resolve.
        assert errors[0]["field"] == "format_ids", (
            f"error.field MUST identify the typed parameter that failed to resolve; got {errors[0].get('field')!r}"
        )
        # The buyer-visible flip. Derived from the code via CODE_TABLE, never authored.
        assert errors[0]["recovery"] == "correctable", (
            f"REFERENCE_NOT_FOUND is correctable -- retrying the same format_ids cannot succeed; "
            f"got {errors[0].get('recovery')!r}"
        )
        assert errors[0]["recovery"] != CODE_TABLE["AGENT_UNREACHABLE"].recovery.value, (
            "the two branches must not advise the buyer identically"
        )

        # The wire-safety obligation still holds on this branch.
        assert_no_marker_in_payload_errors(result.wire_response, _SECRET_MARKER)
        assert_no_marker_in_payload_errors(result.wire_response, _RAW_FAILURE_TEXT)
