"""The (code, recovery) pair on ``sync_creatives``' per-item ``errors[]`` advisories.

``sync_creatives`` reports a per-creative failure as an ``action="failed"`` entry
carrying an ``errors[]`` advisory (``SyncCreativeResult.errors``, built by
``src/core/tools/creatives/_processing.py``'s ``_failed_sync_result``). Those
advisories never pass the boundary error translator — they are serialized
verbatim inside a 200/success response — so the pair *in the advisory* IS the
buyer-facing wire contract, and nothing downstream can correct it.

``adcp.types.Error`` types ``code`` as a bare ``str`` and leaves ``recovery``
free, so nothing in the type system stops a call site pairing a code with a
recovery the pin contradicts. ``_failed_sync_result`` now takes the TYPED
exception and nothing else, building the advisory through
``Error.from_exception`` (``src/core/schemas/_base.py``): the exception class
names the code, and the ``Error`` model resolves message, suggestion and
recovery from ``CODE_TABLE`` per read. No call site can author any of the
three, and none can forward a recovery its code contradicts. These tests grade
that derivation on the wire, per transport.

Spec grounding — AdCP 3.1.1 (the version this repo PINS, ``adcp==6.6.0``;
``dist/schemas/3.1.1/enums/error-code.json`` ``enumMetadata``, normative per its
own ``$comment``; the same table ``src/core/errors/codes.py`` machine-loads into
``CODE_TABLE``):

* ``SERVICE_UNAVAILABLE`` -> recovery ``transient`` ("retry with exponential
  backoff").
* ``CONFIGURATION_ERROR`` -> recovery ``terminal`` ("surface to a human at the
  seller — the buyer cannot resolve a seller-side deployment misconfiguration
  and MUST NOT auto-retry").
* ``VALIDATION_ERROR`` -> recovery ``correctable``.
* ``CREATIVE_REJECTED`` -> recovery ``correctable`` ("revise the creative per
  the seller's advertising_policies"). Its ``details`` shape is
  ``error-details/creative-rejected.json`` — ``reasons[]``, "Specific reasons
  the creative was rejected", ``additionalProperties: true``.

The pin names NO code for a per-item creative-sync failure:
``creative/sync-creatives-response.json`` types ``creatives[].errors[]`` as
``core/error.json`` and describes it only as "Validation or processing errors
(only present when action='failed')". Which code a per-item advisory carries is
therefore the seller's choice, bounded by two pinned rules — a published code
MUST carry its ``enumMetadata`` recovery, and receivers classify unknown codes
by ``recovery`` (``enums/error-code.json`` description). So the graded question
for each scenario below is whether the CHOSEN code's pinned recovery matches
what the buyer can actually do about that condition.

``adcp.types.Error.recovery``'s own field description (pinned SDK) adds:
"Senders SHOULD populate ``recovery`` on every error from 3.1 onward — it is the
normative carrier of recovery semantics across version skew. A receiver that
does not recognize ``error.code`` ... MUST still be able to classify the error
from ``recovery``." An advisory with a code and no recovery therefore under-fills
the wire contract on purpose-built receivers.

What each class here grades:

1. ``TestConfigurationErrorAdvisoryCarriesThePinnedPair`` — the two
   ``except AdCPConfigurationError`` branches in ``_processing.py`` (update path and
   create path). Both intend TERMINAL — their own comment says "Surface it
   honestly so the buyer does not retry a misconfiguration" — and they say so by
   CHOOSING ``CONFIGURATION_ERROR``, whose pinned recovery is ``terminal``.
   Before, they expressed it by hand-typing ``recovery="terminal"`` onto the
   default code ``SERVICE_UNAVAILABLE``, whose pinned recovery is ``transient``:
   the buyer read a self-contradicting pair, and a buyer classifying by code was
   told to retry a misconfiguration forever.
2. ``TestNoPreviewsAdvisoryIsACorrectableRejection`` — the "creative agent
   returned no previews and the creative carries no media_url" branch. This suite
   used to demand ``SERVICE_UNAVAILABLE``/``transient`` here, because
   ``_failed_sync_result`` had a ``code`` parameter defaulting to that value and
   this site passed neither code nor recovery. That expectation pinned a
   PARAMETER DEFAULT, not an obligation, and the value it pinned was wrong on
   the pin's own terms: ``SERVICE_UNAVAILABLE`` reads "Seller service is
   temporarily unavailable. Retry with exponential backoff", and this call did
   not fail — the agent answered, with zero previews. Retrying the identical
   creative yields the identical empty answer, so ``transient`` told the buyer
   to loop forever over a deterministic outcome. That is the precise disease
   ``tests/unit/test_guards_creative_input_raise.py`` was written to eliminate
   ("SERVICE_UNAVAILABLE / transient (retry-the-unretryable)" for a
   buyer-correctable creative-input problem). The site now raises
   ``AdCPCreativeRejectedError(field="media_url",
   details=CreativeRejectionDetails(reasons=["no_previews"]))``:
   ``correctable`` is what the buyer can act on — supply a ``media_url``, which
   is exactly what the sibling branch two lines above accepts.
3. ``TestTypedErrorForwardingKeepsTheTriple`` — the ``except AdCPSalesAgentError``
   branch in ``_sync.py``, which hands the EXCEPTION to ``_failed_sync_result`` so
   the advisory carries that error's own code and field.

   Nothing forwards a recovery any more — ``_sync.py`` says so at the call site
   ("Nothing here forwards a recovery: it follows from the code"), and the
   ``Error`` model refuses to be handed one. What this class pins is that the
   triple ARRIVES: the typed error's code, the pin's recovery for that code, and
   the ``field`` naming which of up to 100 submitted creatives' inputs to fix.

Conformance storyboard: UNGRADED — nothing in ``dist/compliance/3.1.1/``
exercises a per-item creative-sync advisory's recovery classification (same
finding recorded by the sibling ``test_creative_agent_dial_refusal_recovery.py``
and ``test_creative_agent_egress.py``).
"""

from __future__ import annotations

import json
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
from tests.integration._egress_ingest_helpers import _FORMAT_ID, _registered_format

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# The three wire transports, matching the sibling sync-advisory suites: the
# advisory is built inside ``_impl`` and must reach the buyer identically on
# every dispatch path. There is no IMPL member to add — #1721 deleted that
# transport, so every row here crosses a real wire and ``_advisory`` reads it.
_ALL_TRANSPORTS = [Transport.A2A, Transport.REST, Transport.MCP]

# A generative format id (``output_format_ids`` non-empty via
# ``CreativeSyncEnv.setup_generative_build``) — the branch whose
# GEMINI_API_KEY check is production's OWN ``AdCPConfigurationError`` raise.
_GENERATIVE_FORMAT_ID = "display_gen_banner"

# Transcribed from the PIN, ``enums/error-code.json`` ``enumMetadata`` (AdCP
# 3.1.1, ``adcp==6.6.0``). Literals, not ``CODE_TABLE`` reads: the table is
# production's own projection of this file, so grading against it would compare
# production with itself.
_PINNED_CONFIGURATION_ERROR_SUGGESTION = (
    "surface to a human at the seller — the buyer cannot resolve a "
    "seller-side deployment misconfiguration and MUST NOT auto-retry"
)
_PINNED_CREATIVE_REJECTED_SUGGESTION = "revise the creative per the seller's advertising_policies"


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


def _assert_pair(advisory: dict[str, Any], *, code: str, recovery: str) -> None:
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


def _assert_suggestion(advisory: dict[str, Any], *, suggestion: str) -> None:
    """Assert the advisory carries the PIN's own suggestion text for its code.

    Transcribed from ``enums/error-code.json`` ``enumMetadata`` rather than read
    from ``CODE_TABLE`` at runtime: reading the table would compare production
    against itself and pass for any value the table happened to hold. A literal
    is auditable against the pinned file by eye.
    """
    assert advisory.get("suggestion") == suggestion, (
        f"advisory.suggestion={advisory.get('suggestion')!r}, expected the pinned "
        f"enumMetadata suggestion {suggestion!r} for code {advisory.get('code')!r}"
    )


def _assert_configuration_advisory(advisory: dict[str, Any], *, creative_id: str, absent: str) -> None:
    """Grade a seller-misconfiguration advisory, identically on both branches.

    Shared rather than written twice because the obligation is that the two
    ``except AdCPConfigurationError`` branches are INDISTINGUISHABLE on the wire:
    the same fault on the same tool cannot classify differently depending on
    whether the creative already existed. Two copies could drift apart and
    still both be green, which is the thing being prevented.

    ``details`` is asserted whole because it is what proves this scenario drove
    a configuration branch and not a neighbouring one — those branches are the only
    sites that attach ``ConfigurationDetails(creative_id=...)``, and no other
    per-item failure in ``_processing.py`` can produce that exact block.

    ``absent`` names operator-diagnostic text the raise site put on
    ``internal_detail``. KNOWN DIVERGENCE from a pinned SHOULD:
    ``CONFIGURATION_ERROR``'s enumDescription says sellers "SHOULD populate
    error.message with operator-actionable detail (which metadata key is
    missing, which env var is unset)". This deployment declines that SHOULD
    deliberately — buyer-facing text is a function of the code alone
    (``src/core/errors/codes.py``), and provenance-bearing text goes to
    ``internal_detail``, which no serializer emits. The same enumDescription's
    MUST NOT ("credentials, connection strings, or stack traces ... the message
    is wire-visible to the buyer") is what that rule generalizes. Asserted so a
    later edit cannot quietly interpolate the diagnostic back onto the wire.
    """
    _assert_pair(advisory, code="CONFIGURATION_ERROR", recovery="terminal")
    _assert_suggestion(advisory, suggestion=_PINNED_CONFIGURATION_ERROR_SUGGESTION)
    assert advisory.get("details") == {"creative_id": creative_id}, (
        f"advisory.details={advisory.get('details')!r}, expected {{'creative_id': {creative_id!r}}} — "
        "the configuration branches attach ConfigurationDetails(creative_id=...) and nothing else does, "
        "so this is what proves the scenario drove the branch it claims to grade"
    )
    assert absent not in json.dumps(advisory), (
        f"operator diagnostic {absent!r} reached the buyer's wire in {advisory!r} — it belongs on "
        "internal_detail (server log only)"
    )


class TestConfigurationErrorAdvisoryCarriesThePinnedPair:
    """A seller misconfiguration must reach the buyer as CONFIGURATION_ERROR/terminal.

    Both branches used to emit ``SERVICE_UNAVAILABLE`` (the ``_failed_sync_result``
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
        class (``raise_mapped_outbound_error``'s operator branch,
        ``src/core/helpers/outbound_error_mapping.py`` :171-178), so this is the
        shape a real dial failure has when it reaches ``_create_new_creative``'s
        ``except AdCPConfigurationError`` branch (``_processing.py`` :941-955).

        The injected instance is built the way that branch builds it — the endpoint
        sentence on ``internal_detail``, keyword-only, no positional message.
        ``AdCPSalesAgentError`` has no ``message`` parameter at all: the sentence
        is a read-only property over ``CODE_TABLE``, so a raise site cannot
        author one. Injecting the sentence any other way would be testing a
        constructor production cannot call.
        """
        creative_id = f"c_cfg_create_{uuid.uuid4().hex[:8]}"
        endpoint_diagnostic = (
            "The configured endpoint for the creative agent is not reachable under this deployment's egress policy."
        )

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            env.set_run_async_result([_registered_format()])
            env.mock["registry"].return_value.preview_creative = AsyncMock(
                side_effect=AdCPConfigurationError(internal_detail=endpoint_diagnostic)
            )

            result = env.call_via(transport, creatives=[_creative(creative_id)])

            _assert_configuration_advisory(
                _advisory(result, creative_id),
                creative_id=creative_id,
                absent="egress policy",
            )

    @pytest.mark.parametrize("transport", _ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_update_path_missing_generative_key(self, integration_db, transport):
        """Production's OWN AdCPConfigurationError raise, on the update-path branch.

        No injection: a generative format with no GEMINI_API_KEY configured is
        the exact condition ``_update_existing_creative`` raises
        ``AdCPConfigurationError`` for (``_processing.py`` :348), and it is the
        deployment misconfiguration the ``except`` branch's comment names
        (:579-596). It must classify identically to the create-path branch
        (:943-957) — the same fault on the same tool cannot carry two different
        pairs depending on whether the creative already existed.

        That raise is also the ONLY ``AdCPConfigurationError`` reachable on the
        update path, so the CONFIGURATION_ERROR asserted below cannot have come
        from anywhere else. Nothing is mocked into raising it here.
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

            _assert_configuration_advisory(
                _advisory(result, creative_id),
                creative_id=creative_id,
                absent="GEMINI_API_KEY",
            )


class TestNoPreviewsAdvisoryIsACorrectableRejection:
    """An empty preview answer is the buyer's to correct, not the seller's to retry.

    ``_processing.py`` :928-941 (create) and :562-576 (update): the creative
    agent ANSWERED and returned zero previews, for a creative carrying no
    ``media_url`` to fall back on. Two lines above, the same branch accepts the
    identical empty answer when a ``media_url`` IS present — so ``media_url`` is
    both the discriminator and the buyer's remedy, and it is what the advisory
    names in ``field``.

    Graded against the pin, not against a parameter default. This suite
    previously demanded ``SERVICE_UNAVAILABLE``/``transient`` here on the sole
    ground that ``_failed_sync_result`` had a ``code`` parameter defaulting to
    that value and this site passed none. The parameter is gone (the builder
    takes the typed exception), and the value it defaulted to contradicted the
    pin: ``SERVICE_UNAVAILABLE`` is "Seller service is temporarily unavailable.
    Retry with exponential backoff", and nothing here was unavailable. Backoff
    cannot change a deterministic empty answer, so ``transient`` sent the buyer
    into an unbounded retry over a condition one request field would clear —
    "retry-the-unretryable", named as the disease in
    ``tests/unit/test_guards_creative_input_raise.py``, whose remedy is the
    ``CREATIVE_REJECTED``/``correctable`` family.

    ``CREATIVE_REJECTED``'s enumDescription reads "Creative failed content
    policy review", which is narrower than this condition, and the pin defines
    no code for "the agent produced nothing renderable". Recorded as a known
    imprecision rather than smoothed over: the pin makes ``recovery`` — not the
    code — the normative carrier a receiver MUST classify by
    (``enums/error-code.json`` description; ``Error.recovery`` field
    description), the vocabulary is explicitly open, and ``correctable`` is the
    true answer for a fault the buyer clears by supplying ``media_url``. The
    specifics the code's own prose does not carry ride
    ``creative-rejected.json``'s ``reasons[]``, asserted below.
    """

    @pytest.mark.parametrize("transport", _ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_no_previews_no_media_url_is_creative_rejected_correctable(self, integration_db, transport):
        """No previews and no media_url -> CREATIVE_REJECTED/correctable, field=media_url."""
        creative_id = f"c_no_previews_{uuid.uuid4().hex[:8]}"

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

            advisory = _advisory(result, creative_id)
            _assert_pair(advisory, code="CREATIVE_REJECTED", recovery="correctable")
            _assert_suggestion(advisory, suggestion=_PINNED_CREATIVE_REJECTED_SUGGESTION)
            assert advisory.get("field") == "media_url", (
                f"advisory.field={advisory.get('field')!r}, expected 'media_url' — it is the one "
                "request field that turns this failure into a success, and the buyer cannot infer "
                "it from a correctable classification alone"
            )
            # Whole-block equality, not a membership check: the prose the old
            # expectation asserted on ("no previews returned and no media_url
            # provided") is no longer on the buyer's wire — a code's sentence is
            # a function of the code alone — so ``reasons`` IS the machine-readable
            # successor that says WHY, and a partial assert would let the reason
            # be dropped or joined by an unpinned second one unnoticed.
            assert advisory.get("details") == {"creative_id": creative_id, "reasons": ["no_previews"]}, (
                f"advisory.details={advisory.get('details')!r}, expected "
                f"{{'creative_id': {creative_id!r}, 'reasons': ['no_previews']}} — 'reasons' is "
                "creative-rejected.json's own property ('Specific reasons the creative was rejected') "
                "and is the only place this advisory states what went wrong"
            )


class TestTypedErrorForwardingKeepsTheTriple:
    """A typed AdCPSalesAgentError's code, recovery AND field all reach the advisory.

    ``_sync.py``'s ``except AdCPSalesAgentError`` branch (:430-461) is the advisory
    path whose values come from the raised error rather than from the call
    site's literals — it hands the EXCEPTION to ``_failed_sync_result``. All
    three travel together: without ``field``, a request carrying up to 100
    creatives tells the buyer their input is correctable but not which input,
    and without the correctable pair it tells them the SELLER is unavailable for
    a fault in their own document.

    Nothing forwards a recovery on this path, and this docstring must not
    pretend otherwise: the branch's own comment says "Nothing here forwards a
    recovery: it follows from the code", and ``Error`` has no settable
    ``recovery`` for a caller to hand one to. What this class pins is that the
    triple ARRIVES on the wire — the code the exception class names, the pin's
    recovery for that code, and the ``field`` the typed error carried.
    """

    @pytest.mark.parametrize("transport", _ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_unknown_format_advisory_carries_code_recovery_and_field(self, integration_db, transport):
        """An unknown format_id -> REFERENCE_NOT_FOUND / correctable / field=format_id."""
        creative_id = f"c_typed_{uuid.uuid4().hex[:8]}"

        with CreativeSyncEnv() as env:
            env.setup_default_data()
            # The agent genuinely does not expose this format: fetch_format_spec
            # returns None and _validate_creative_input raises the typed
            # AdCPFormatNotFoundError(field="format_id") that _sync.py forwards.
            env.mock["registry"].return_value.get_format = AsyncMock(return_value=None)

            result = env.call_via(transport, creatives=[_creative(creative_id)])

            advisory = _advisory(result, creative_id)
            _assert_pair(advisory, code="REFERENCE_NOT_FOUND", recovery="correctable")
            assert advisory.get("field") == "format_id", (
                f"advisory.field={advisory.get('field')!r}, expected 'format_id' — the typed error's "
                "own field is what names the input to fix; dropping it strands the buyer"
            )
