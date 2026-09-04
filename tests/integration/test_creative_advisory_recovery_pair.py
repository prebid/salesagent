"""The (code, recovery) pair on ``sync_creatives``' per-item ``errors[]`` advisories.

``sync_creatives`` reports a per-creative failure as an ``action="failed"`` entry
carrying an ``errors[]`` advisory (``SyncCreativeResult.errors``, built by
``src/core/tools/creatives/_processing.py``'s ``_failed_sync_result``). Those
advisories never pass the boundary error translator — they are serialized
verbatim inside a 200/success response — so the pair *in the advisory* IS the
buyer-facing wire contract, and nothing downstream can correct it.

``adcp.types.Error`` types ``code`` as a bare ``str`` and leaves ``recovery``
free, so nothing in the type system stops a call site pairing a code with a
recovery the pin contradicts. ``_failed_sync_result`` now builds every advisory
through ``src.core.exceptions.wire_advisory``, which DERIVES recovery from the
code; these tests grade that derivation on the wire, per transport.

Spec grounding — AdCP 3.1.1 (the version this repo PINS, ``adcp==6.6.0``;
``dist/schemas/3.1.1/enums/error-code.json`` ``enumMetadata``, normative per its
own ``$comment``; the same table ``src/core/exceptions.py`` machine-loads into
``RECOVERY_BY_WIRE_CODE``):

* ``SERVICE_UNAVAILABLE`` -> recovery ``transient`` ("retry with exponential
  backoff").
* ``CONFIGURATION_ERROR`` -> recovery ``terminal`` ("surface to a human at the
  seller — the buyer cannot resolve a seller-side deployment misconfiguration
  and MUST NOT auto-retry").
* ``VALIDATION_ERROR`` -> recovery ``correctable``.

``adcp.types.Error.recovery``'s own field description (pinned SDK) adds:
"Senders SHOULD populate ``recovery`` on every error from 3.1 onward — it is the
normative carrier of recovery semantics across version skew. A receiver that
does not recognize ``error.code`` ... MUST still be able to classify the error
from ``recovery``." An advisory with a code and no recovery therefore under-fills
the wire contract on purpose-built receivers.

What each class here grades:

1. ``TestConfigurationErrorAdvisoryCarriesThePinnedPair`` — the two
   ``except AdCPConfigurationError`` arms in ``_processing.py`` (update path and
   create path). Both intend TERMINAL — their own comment says "Surface it
   honestly so the buyer does not retry a misconfiguration" — and they now say so
   by CHOOSING ``CONFIGURATION_ERROR``, whose pinned recovery is ``terminal``.
   Before, they expressed it by hand-typing ``recovery="terminal"`` onto the
   default code ``SERVICE_UNAVAILABLE``, whose pinned recovery is ``transient``:
   the buyer read a self-contradicting pair, and a buyer classifying by code was
   told to retry a misconfiguration forever.
2. ``TestDefaultAdvisoryDerivesRecoveryFromItsCode`` — a call site that passes
   NEITHER code nor recovery. It emits ``SERVICE_UNAVAILABLE``, and ``recovery``
   is now the pin's ``transient`` rather than absent.
3. ``TestTypedErrorForwardingKeepsTheTriple`` — the ``except AdCPError`` arm in
   ``_sync.py``, which forwards a typed error's own code, recovery AND field onto
   the advisory. It is the ONE path whose recovery is still hand-forwarded rather
   than derived (deferred deliberately: two raise sites reachable from it still
   hand-type ``terminal``).

   It pins the VALUES that reach the buyer, not the forwarding mechanism: for a
   typed error whose instance recovery already equals its code's pinned recovery
   — which is every error this scenario can produce — deleting the forward would
   not change the answer, so this test would stay green. What it guards is that
   the triple arrives at all. The forward itself becomes observable only once a
   site pairs a code with a recovery the pin contradicts, which is exactly the
   condition the deferring lane removes.

Conformance storyboard: UNGRADED — nothing in ``dist/compliance/3.1.1/``
exercises a per-item creative-sync advisory's recovery classification (same
finding recorded by the sibling ``test_creative_agent_dial_refusal_recovery.py``
and ``test_creative_agent_egress.py``).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from adcp.types import CreativeAsset, FormatId

from src.core.exceptions import AdCPConfigurationError
from tests.factories.creative_asset import CreativeAssetFactory, build_assets, text_spec
from tests.factories.format import AGENT_URL
from tests.harness.creative_sync import CreativeSyncEnv
from tests.harness.transport import Transport, TransportResult

# The registered-format builder is IMPORTED, not restated: it encodes the
# Pydantic ``__eq__`` trap that decides whether ``_processing.py`` finds a
# matching ``format_obj`` at all (a pre-built ``src.core.schemas.FormatId``
# silently never matches), and that decision must have exactly one owner.
from tests.integration.test_creative_agent_dial_refusal_recovery import _FORMAT_ID, _registered_format

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# IMPL alongside the three wire transports, matching the sibling sync-advisory
# suites: the advisory is built inside ``_impl`` and must reach the buyer
# identically on every dispatch path.
_ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.REST, Transport.MCP]

# A generative format id (``output_format_ids`` non-empty via
# ``CreativeSyncEnv.setup_generative_build``) — the branch whose
# GEMINI_API_KEY check is production's OWN ``AdCPConfigurationError`` raise.
_GENERATIVE_FORMAT_ID = "display_gen_banner"


def _creative(creative_id: str, **overrides: Any) -> CreativeAsset:
    """A creative naming the tenant's registered format; ``overrides`` vary one field.

    ``format_id`` is built from ``adcp.types.FormatId`` for the reason the
    imported ``_registered_format`` documents — the format match in
    ``_processing.py`` is ``BaseModel.__eq__``, which is class-exact.
    """
    fields: dict[str, Any] = {
        "creative_id": creative_id,
        "format_id": FormatId(id=_FORMAT_ID, agent_url=AGENT_URL),
    }
    fields.update(overrides)
    return CreativeAssetFactory(**fields)


def _advisory(result: TransportResult, creative_id: str) -> dict[str, Any]:
    """Return the SERIALIZED ``errors[0]`` of *creative_id*'s failed per-item entry.

    A per-item advisory rides inside a SUCCESS response, so there is no error
    envelope to read: the buyer-visible artifact is the serialized
    ``creatives[].errors[]`` object. This reads the real wire body where the
    transport captured one (REST's HTTP body, MCP's ``structured_content``) and
    otherwise serializes the typed payload through production's own serializer
    (``model_dump(mode="json")``, per ``tests/CLAUDE.md`` for IMPL). Either way
    the assertion lands on a plain dict — never on a reconstructed exception,
    and never on an enum whose ``__eq__`` might paper over a wrong value.
    """
    payload = result.payload
    assert payload is not None, f"expected a sync response, got {result!r}"

    body = result.wire_response or payload.model_dump(mode="json")
    entries = [e for e in body.get("creatives", []) if e.get("creative_id") == creative_id]
    assert entries, f"no result for {creative_id!r} in {body.get('creatives')!r}"
    entry = entries[0]

    assert entry.get("action") == "failed", (
        f"expected a per-item failure for {creative_id!r}; action={entry.get('action')!r}, entry={entry!r}"
    )
    errors = entry.get("errors") or []
    assert errors, f"a failed creative must carry an errors[] advisory; got {entry!r}"
    return errors[0]


def _assert_pair(
    advisory: dict[str, Any],
    *,
    code: str,
    recovery: str,
    message_substr: str | None = None,
) -> None:
    """Assert the advisory's (code, recovery) pair EXACTLY, as one fact.

    Asserted as a tuple because the pair is the obligation: a right code beside
    a wrong recovery is precisely the defect being graded, and two independent
    asserts would let the first one hide the second.
    """
    actual = (advisory.get("code"), advisory.get("recovery"))
    assert actual == (code, recovery), (
        f"advisory (code, recovery) = {actual!r}, expected {(code, recovery)!r}. "
        f"The pinned enums/error-code.json enumMetadata classifies {code} as {recovery}; "
        f"the advisory serializes verbatim to the buyer, so this pair IS the wire contract. "
        f"Full advisory: {advisory!r}"
    )
    if message_substr is not None:
        assert message_substr in (advisory.get("message") or ""), (
            f"advisory.message={advisory.get('message')!r} does not contain {message_substr!r} — "
            "this test is not driving the call site it claims to grade"
        )


class TestConfigurationErrorAdvisoryCarriesThePinnedPair:
    """A seller misconfiguration must reach the buyer as CONFIGURATION_ERROR/terminal.

    Both arms used to emit ``SERVICE_UNAVAILABLE`` (the ``_failed_sync_result``
    default) with a hand-typed ``recovery="terminal"``. A buyer that classifies
    by code — which the pinned enum tells it to do for a code it recognizes —
    read "the seller is temporarily unavailable, retry with backoff" about a
    condition only a human at the seller can clear; a buyer that classified by
    recovery read terminal. The response said both. Asserting the pair as ONE
    tuple is what keeps them from drifting apart again.
    """

    @pytest.mark.parametrize("transport", _ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_create_path_agent_configuration_error(self, integration_db, transport):
        """The creative agent raises AdCPConfigurationError on the create-path dial.

        A registry that refuses its own configured endpoint raises exactly this
        class (``raise_mapped_outbound_error``'s operator arm,
        ``src/core/helpers/outbound_error_mapping.py``), so this is the shape a
        real dial failure has when it reaches ``_create_new_creative``'s
        ``except AdCPConfigurationError`` arm (``_processing.py`` :729-735).
        """
        creative_id = f"c_cfg_create_{uuid.uuid4().hex[:8]}"

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            env.set_run_async_result([_registered_format()])
            env.mock["registry"].return_value.preview_creative = AsyncMock(
                side_effect=AdCPConfigurationError(
                    "The configured endpoint for creative agent is not reachable under this deployment's egress policy."
                )
            )

            result = env.call_via(transport, creatives=[_creative(creative_id)])

            _assert_pair(
                _advisory(result, creative_id),
                code="CONFIGURATION_ERROR",
                recovery="terminal",
            )

    @pytest.mark.parametrize("transport", _ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_update_path_missing_generative_key(self, integration_db, transport):
        """Missing account GEMINI key on the update-path generative arm.

        No injection: a generative format with no GEMINI_API_KEY configured is
        the exact condition ``_check_gemini_key_or_advisory`` grades. Per #1831
        this seller-specific misconfiguration uses
        ``X_PREBID_CREATIVE_GEMINI_KEY_MISSING`` / ``terminal`` (AdCP 3.1.1
        seller-specific code shape), not ``CONFIGURATION_ERROR`` (which the
        enum pairs with flipped transport failure markers unsuitable for a
        success-path per-creative advisory).
        """
        from tests.factories import CreativeFactory

        creative_id = f"c_cfg_update_{uuid.uuid4().hex[:8]}"

        with CreativeSyncEnv() as env:
            tenant, principal = env.setup_default_data()
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id=creative_id,
                name="Original Name",
                format=_GENERATIVE_FORMAT_ID,
                agent_url=env.DEFAULT_AGENT_URL,
                status="approved",
                data={"assets": {}, "url": "https://example.com/original.png"},
            )
            generative_format = env.setup_generative_build(
                format_id=_GENERATIVE_FORMAT_ID,
                gemini_api_key="",  # unset key -> production raises AdCPConfigurationError
            )

            result = env.call_via(
                transport,
                creatives=[
                    CreativeAssetFactory(
                        creative_id=creative_id,
                        name="Attempted New Name",
                        format_id=generative_format,
                    )
                ],
            )

            _assert_pair(
                _advisory(result, creative_id),
                code="X_PREBID_CREATIVE_GEMINI_KEY_MISSING",
                recovery="terminal",
                message_substr="GEMINI_API_KEY not configured",
            )


class TestDefaultAdvisoryDerivesRecoveryFromItsCode:
    """An advisory built with no explicit code still owes the buyer a recovery.

    ``_processing.py`` :727 passes neither ``code`` nor ``recovery``: the
    creative agent returned no previews for a creative with no media_url. The
    default code is ``SERVICE_UNAVAILABLE``, whose pinned recovery is
    ``transient`` — but the field is simply absent on the wire today, so a
    receiver classifying by recovery (what the pinned ``Error.recovery``
    description tells it to do for an unrecognized code) gets nothing.
    """

    @pytest.mark.parametrize("transport", _ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_default_code_advisory_is_service_unavailable_transient(self, integration_db, transport):
        """No previews and no media_url -> SERVICE_UNAVAILABLE paired with transient."""
        creative_id = f"c_default_{uuid.uuid4().hex[:8]}"

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            env.set_run_async_result([_registered_format()])
            # The env's default preview_creative already returns {} — no
            # previews. The creative carries a text asset only, so there is no
            # media_url to fall back on.

            result = env.call_via(
                transport,
                creatives=[
                    _creative(
                        creative_id,
                        assets=build_assets(text_spec("headline", content="Nothing renderable here")),
                    )
                ],
            )

            _assert_pair(
                _advisory(result, creative_id),
                code="SERVICE_UNAVAILABLE",
                recovery="transient",
                message_substr="no previews returned and no media_url provided",
            )


class TestTypedErrorForwardingKeepsTheTriple:
    """A typed AdCPError's code, recovery AND field all reach the advisory.

    ``_sync.py``'s ``except AdCPError`` arm (:382-390) is the one advisory path
    whose values come from the raised error rather than from the call site's
    literals. All three travel together: without ``field``, a request carrying
    up to 100 creatives tells the buyer their input is correctable but not
    which input, and without the correctable pair it tells them the SELLER is
    unavailable for a fault in their own document.

    Pinned here because the advisory constructor re-derives the code half through
    ``to_wire_error_code`` — that half IS observable here, on the wire.

    The recovery half is not, and this docstring must not pretend otherwise: for
    every typed error this scenario can produce, the instance's recovery already
    equals its code's pinned recovery, so deleting the forward leaves the answer
    unchanged and this test green (measured, not assumed). What it guards is that
    the triple ARRIVES. The forward becomes observable only once a raise site
    pairs a code with a recovery the pin contradicts — the condition the lane
    that deletes the forward removes first.
    """

    @pytest.mark.parametrize("transport", _ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_unknown_format_advisory_carries_code_recovery_and_field(self, integration_db, transport):
        """An unknown format_id -> VALIDATION_ERROR / correctable / field=format_id."""
        creative_id = f"c_typed_{uuid.uuid4().hex[:8]}"

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            # The agent genuinely does not expose this format: fetch_format_spec
            # returns None and _validate_creative_input raises the typed
            # AdCPValidationError(field="format_id") that _sync.py forwards.
            env.mock["registry"].return_value.get_format = AsyncMock(return_value=None)

            result = env.call_via(transport, creatives=[_creative(creative_id)])

            advisory = _advisory(result, creative_id)
            _assert_pair(advisory, code="VALIDATION_ERROR", recovery="correctable")
            assert advisory.get("field") == "format_id", (
                f"advisory.field={advisory.get('field')!r}, expected 'format_id' — the typed error's "
                "own field is what names the input to fix; dropping it strands the buyer"
            )
