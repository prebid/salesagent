"""Recovery classification of an egress refusal on the operator creative-agent dial.

``sync_creatives`` validates a creative's ``format_id`` against the tenant's
registered creative-agent formats (``registry.list_all_formats``,
``src/core/tools/creatives/_sync.py:154``) and, once a matching ``format_obj``
is found, DIALS that agent — ``preview_creative`` for a static format,
``build_creative`` for a generative one — to validate the creative and fetch a
preview (``src/core/tools/creatives/_processing.py``). The dialled
``agent_url`` is ``format_obj.agent_url``: an entry out of the TENANT's own
registered formats, not a buyer-supplied address. Provenance = operator.

Both dials go through ``src.core.utils.operator_mcp.call_operator_mcp_tool``,
which reaches ``call_mcp_tool`` (``src/core/utils/mcp_client.py``); that
function runs the seam's ``validate_url`` before ever opening a connection and
lets a refusal propagate as ``OutboundRequestBlocked`` "unretried and correctly
classified" (its own comment). ``src/core/helpers/outbound_error_mapping.py`` names the operator
classification for that seam failure explicitly:
``CONFIGURATION_ERROR``/terminal, "surface to a human at the seller — the
buyer cannot resolve a seller-side deployment misconfiguration and MUST NOT
auto-retry" (pinned ``dist/schemas/3.1.1/enums/error-code.json``
enumMetadata, same grounding as ``tests/integration/test_creative_agent_egress.py``).

The bug: ``_processing.py``'s ``_create_new_creative``/``_update_existing_creative``
wrap the dial in a bare ``try`` whose only typed branch is
``except AdCPConfigurationError`` (the GEMINI_API_KEY-missing case) — a
refused seam call is not that class, falls through to the generic
``except Exception``, and is laundered into::

    "Creative agent unreachable or validation error: ...
     Retry recommended - creative agent may be temporarily unavailable."
    recovery="transient"

which tells the buyer to retry a refusal that will recur identically forever.
``src/core/tools/creatives/_sync.py``'s own outer ``except AdCPSalesAgentError as e:``
handler (around line 359) ALREADY carries a typed error's own
``recovery``/``error_code`` onto the per-item result correctly — the defect is
that the seam failure never reaches it, because the inner generic
``except Exception`` in ``_processing.py`` intercepts it first.

Conformance storyboard: ungraded (same finding as
``test_creative_agent_egress.py`` for the registry-level format fetch; nothing
in ``dist/compliance/3.1.1/`` exercises a creative-agent egress failure on the
sync_creatives dial).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from src.core.schemas import CreativeFormatIdentity
from src.core.security.outbound_http import OutboundRequestBlocked
from tests.factories.creative_asset import CreativeAssetFactory
from tests.harness.creative_sync import CreativeSyncEnv
from tests.harness.transport import Transport
from tests.integration._egress_ingest_helpers import _registered_format

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_ALL_TRANSPORTS = [Transport.A2A, Transport.REST, Transport.MCP]


def _seed_registered_format(env: CreativeSyncEnv):
    """Serve the registered format from the tenant's own creative agent.

    Returns the identity the agent publishes it under and the declaration it serves. The
    agent is a real peer the tenant configured, so the dial ``_processing.py`` makes for a
    creative in this format is an OPERATOR dial to a deployment-configured endpoint -- the
    provenance this module grades. The buyer only chooses WHICH format, by naming this
    identity.
    """
    from adcp.types.domains.core.format_id import FormatReferenceStructuredObject as FormatId

    declaration = _registered_format()
    env.set_served_catalogue([declaration])
    (seeded,) = env.seeded_format_ids()
    return FormatId(id=seeded.id, agent_url=seeded.agent_url), declaration


class TestOperatorDialRefusalIsTerminalNotTransient:
    """A refused OPERATOR creative-agent dial must be reported terminal, not transient.

    Today it surfaces as ``SERVICE_UNAVAILABLE``/transient with "Retry
    recommended" (the generic ``except Exception`` branch's defaults) instead of
    the seam's own ``CONFIGURATION_ERROR``/terminal classification.
    """

    @pytest.mark.parametrize("transport", _ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_refused_preview_dial_is_terminal(self, integration_db, transport):
        """``preview_creative`` (static format path) raises OutboundRequestBlocked -> terminal."""
        creative_id = f"c_operator_refused_{uuid.uuid4().hex[:8]}"

        with CreativeSyncEnv() as env:
            env.setup_default_data()

            registry = env.mock["registry"].return_value
            format_id, _declaration = _seed_registered_format(env)
            registry.preview_creative = AsyncMock(side_effect=OutboundRequestBlocked())

            result = env.call_via(
                transport,
                creatives=[CreativeAssetFactory(creative_id=creative_id, format_id=format_id)],
            )

            payload = result.payload
            assert payload is not None, f"expected a sync response, got {result!r}"
            entries = [c for c in payload.creatives if c.creative_id == creative_id]
            assert entries, f"no result for {creative_id!r} in {payload.creatives!r}"
            entry = entries[0]

            assert str(getattr(entry.action, "value", entry.action)) == "failed", (
                f"a creative whose operator agent egress refused must not be synced; action={entry.action!r}"
            )
            assert entry.errors, f"a failed creative must carry an error; got {entry!r}"
            error = entry.errors[0]

            assert error.recovery == "terminal", (
                f"errors[0].recovery={error.recovery!r} — a refused operator agent is refused "
                "identically forever; telling the buyer to retry ('transient') is false"
            )
            assert error.code == "CONFIGURATION_ERROR", (
                f"errors[0].code={error.code!r} — this is the seam's OWN classification "
                "(raise_mapped_outbound_error's operator branch), not the generic SERVICE_UNAVAILABLE "
                "default a laundered Exception falls back to"
            )

    @pytest.mark.parametrize("transport", _ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_refused_update_dial_is_terminal_and_leaves_creative_unmutated(self, integration_db, transport):
        """``_update_existing_creative``'s dial (the sibling function) gets the same fix.

        Also pins the architect-review regression-risk finding: ``_update_existing_creative``
        stages field mutations (name/status/agent_url/format) on ``existing_creative``
        BEFORE the dial. ``_sync.py`` wraps per-creative processing in
        ``creative_repo.begin_nested()`` (a SAVEPOINT) — an exception propagating out of
        the dial (this fix's ``raise_mapped_outbound_error`` call) must roll that
        savepoint back, so the stored creative must be BYTE-FOR-BYTE unchanged after a
        refused update, not silently mutated despite the sync reporting failure.
        """
        from src.core.database.repositories.uow import CreativeUoW
        from tests.factories import CreativeFactory

        creative_id = f"c_operator_refused_update_{uuid.uuid4().hex[:8]}"

        with CreativeSyncEnv() as env:
            tenant, principal = env.setup_default_data()
            format_id, declaration = _seed_registered_format(env)
            original = CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id=creative_id,
                name="Original Name",
                format=format_id.id,
                agent_url=format_id.agent_url,
                # The capture does not carry the peer's identity, so the seed resolves
                # against the declaration the peer serves -- the one the sync resolves.
                format_spec=declaration,
                status="approved",
                data={"assets": {}, "url": "https://example.com/original.png"},
            )
            # The identity is read into a frozen value BEFORE the dial: ``stored`` below can be
            # the same ORM instance, so a snapshot is what makes the comparison mean anything.
            original_name, original_status, original_identity = (
                original.name,
                original.status,
                CreativeFormatIdentity.from_row(original),
            )

            registry = env.mock["registry"].return_value
            registry.preview_creative = AsyncMock(side_effect=OutboundRequestBlocked())

            update_creative = CreativeAssetFactory(
                creative_id=creative_id,
                name="Attempted New Name",
                format_id=format_id,
            )

            result = env.call_via(transport, creatives=[update_creative])

            payload = result.payload
            assert payload is not None, f"expected a sync response, got {result!r}"
            entries = [c for c in payload.creatives if c.creative_id == creative_id]
            assert entries, f"no result for {creative_id!r} in {payload.creatives!r}"
            entry = entries[0]

            assert str(getattr(entry.action, "value", entry.action)) == "failed", (
                f"a creative whose operator agent egress refused must not be updated; action={entry.action!r}"
            )
            assert entry.errors, f"a failed creative must carry an error; got {entry!r}"
            error = entry.errors[0]
            assert error.recovery == "terminal", (
                f"errors[0].recovery={error.recovery!r} — the update-path dial must classify "
                "identically to the create-path dial"
            )
            assert error.code == "CONFIGURATION_ERROR", f"errors[0].code={error.code!r}"

            with CreativeUoW(env._tenant_id) as uow:
                assert uow.creatives is not None
                persisted = uow.creatives.get_by_ids([creative_id], env._principal_id)
                assert persisted, f"creative {creative_id!r} must still exist"
                stored = persisted[0]
                assert stored.name == original_name, (
                    f"a refused update must not persist the attempted name change: "
                    f"stored={stored.name!r}, original={original_name!r}"
                )
                assert stored.status == original_status, (
                    f"a refused update must not persist a status change: "
                    f"stored={stored.status!r}, original={original_status!r}"
                )
                stored_identity = CreativeFormatIdentity.from_row(stored)
                assert stored_identity == original_identity, (
                    f"a refused update must not persist a format-identity change: "
                    f"stored={stored_identity!r}, original={original_identity!r}"
                )
                # ``assertion`` is ``compare=False``, so the equality above does not see the
                # verbatim reference -- the half the buyer is echoed back gets its own check.
                assert stored_identity.assertion == original_identity.assertion, (
                    f"a refused update must not persist a change to the asserted format_id: "
                    f"stored={stored_identity.assertion!r}, original={original_identity.assertion!r}"
                )
