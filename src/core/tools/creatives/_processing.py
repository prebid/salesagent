"""Creative create/update logic: DB persistence, agent validation, preview extraction.

SDK 5.7 type:ignore tracking (adcontextprotocol/adcp-client-python#913):
- [attr-defined] on line ~769: CreativeAsset is a RootModel proxy; .creative_id
  assignment exists at runtime but mypy cannot see through __setattr__. Fixable
  when the SDK ships typed accessors or a shared unwrapper helper.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from adcp.types.domains.core.representation_selection import (
    RepresentationSelection as LibraryRepresentationSelection,
)
from adcp.types.legacy import LegacyCreativeAsset as CreativeAsset

from src.core.errors.details import AdapterFailureDetails, ConfigurationDetails, CreativeRejectionDetails
from src.core.exceptions import (
    AdCPConfigurationError,
    AdCPCreativeRejectedError,
    AdCPSalesAgentError,
    AdCPServiceUnavailableError,
)
from src.core.format_resolver import (
    creative_identity_of,
    find_format,
    find_operator_format_of_kind,
    is_dialled_agent_format,
    is_generative,
)
from src.core.helpers import _validate_creative_assets
from src.core.helpers.outbound_error_mapping import raise_mapped_outbound_error
from src.core.schemas import CreativeFormatIdentity, CreativeStatusEnum, SyncCreativeResult
from src.core.schemas import Error as AdCPErrorDetail
from src.core.security.outbound_http import OperatorEndpoint, OutboundError
from src.core.tenant_context import TenantContext
from src.core.validation_helpers import run_async_in_sync_context
from src.services.ai.config import tenant_google_api_key

from ._assets import (
    ASSET_MAP,
    _build_creative_data,
    _extract_message_from_assets,
    _extract_url_from_assets,
    _generative_assets,
)

if TYPE_CHECKING:
    from src.core.database.repositories.creative import CreativeRepository
    from src.core.schemas import Format

logger = logging.getLogger(__name__)

#: Fields a full upsert rewrites on every update. They used to be reported as changed
#: unconditionally, which made ``action: unchanged`` unreachable: a resync of identical
#: data always listed all five. Now only the ones the BUYER submitted are compared, each
#: against the row's prior value -- see :class:`SubmittedUpsertFields`, which also says why
#: the agent's back-fill of the ones the buyer left empty is not compared at all.
_UPSERT_FIELDS = ("url", "click_url", "width", "height", "duration")

#: The members of ``changes`` that are metadata, not revision content. A change to one of
#: these is reported (``action: updated``) and keeps the creative's review state.
#: docs/creative/specification.mdx (v3.2.7) § Creative revision identity excludes the
#: "explicit operational/metadata fields `name`, `tags`, `status`, `weight`,
#: `placement_refs`, and `placement_ids`" from revision content and says "a metadata-only
#: mutation may still return `updated`" while preserving the current revision. Of those,
#: ``name`` is the one change detection reports; everything else it reports (format
#: identity, assets, the upsert fields) is revision-bearing.
_METADATA_CHANGES = frozenset({"name"})


def _execution(selection: LibraryRepresentationSelection | None) -> tuple[Any, ...] | None:
    """The execution review fingerprint a selection pins, or ``None`` for an unselected creative.

    core/representation-selection.json (v3.2.7) ``review_identity``: "the receiving seller
    tracks (selected_representation_id, selected_output_digest,
    execution_vast_version-or-null, execution_daast_version-or-null) separately from the
    source revision; changing any member is an updated execution that follows ordinary
    re-review without minting a buyer revision". The other lineage members (the source
    revision, the strategy, who resolved it) are not in it, so a change confined to them is
    reported and keeps the review state.
    """
    if selection is None:
        return None
    return (
        selection.selected_representation_id,
        selection.selected_output_digest,
        selection.execution_vast_version,
        selection.execution_daast_version,
    )


@dataclass(frozen=True)
class SubmittedUpsertFields:
    """The BUYER's own values for the upsert fields -- the one input to change detection.

    ``changes`` is "Field names that were modified"
    (creative/sync-creatives-response.json, 3.2.3), and what a sync modifies is what the
    buyer sent. A value the seller DERIVES for itself -- the creative agent's preview
    answer, and the ``url``/``width``/``height``/``duration`` it back-fills into keys the
    buyer left empty -- is not an axis of "did this creative change", for two reasons that
    both bite:

    * It is re-derived from unchanged inputs on every sync, so counting it makes
      ``unchanged`` -- a pinned member of ``enums/creative-action.json`` -- unreachable for
      a creative nobody edited, and leaves a buyer no way to tell a no-op from a real edit.
    * ``dry_run`` "returns what would be created/updated/deleted"
      (sync-creatives-request.json#/properties/dry_run), and a preview MUST NOT fire the
      agent request (an outbound call is not undoable by the transaction's rollback, which
      is why ``CreativeRepository.outbound`` suppresses it). So the preview branch
      structurally cannot know the agent's answer, and the only way its ``changes`` can
      equal the live run's is if the agent's answer is not an input to ``changes`` at all.
      That makes this a forced reading of the pin, not a preference.

    The pinned response agrees on where a preview belongs: it carries the render on its own
    ``creatives[].preview_url`` field, "Preview URL for generative creatives (only present
    for generative formats)" -- a separate channel from ``changes``.

    WHY A TYPE AND NOT THE ``data`` DICT. ``data`` is the PERSISTENCE document and the
    agent block mutates it in place, so handing ``data`` to change detection is exactly how
    a re-rendered preview came to be reported as a buyer's edit (salesagent-8s2p0: an
    identical re-sync answered ``updated`` on 4 rows, and a dry run could not match the
    live run on 6 more). :meth:`submitted_by` COPIES the buyer's values out before the
    agent can reach them, and the builder takes this class rather than a mapping -- so
    there is no spelling of "compare the enriched document" left to write, and no list for
    an agent-derived key to be appended to.
    """

    #: Only the keys the buyer gave a VALUE. ``_build_creative_data`` writes all five
    #: unconditionally, ``None`` where the buyer sent nothing, and the agent back-fills
    #: exactly those ``None``\ s (every write is guarded on ``not data.get(key)``) -- so
    #: "the buyer supplied it" is "not None here", and it reads the same on the preview
    #: branch, where the agent never ran.
    values: dict[str, Any]

    @classmethod
    def submitted_by(cls, data: Mapping[str, Any]) -> SubmittedUpsertFields:
        """Snapshot the buyer's upsert values. Call BEFORE any agent enrichment of ``data``."""
        return cls(values={key: data[key] for key in _UPSERT_FIELDS if data.get(key) is not None})

    def changed_against(self, prior: PriorCreativeState) -> list[str]:
        """The submitted fields whose value differs from the row's.

        Iterates the SUBMITTED keys, not all five: a field the buyer omitted was not
        modified by this sync, and comparing its absence against the row would report a
        change on every resync of a creative whose url the agent supplies.
        """
        return [
            key for key in _UPSERT_FIELDS if key in self.values and self.values[key] != prior.upsert_fields.get(key)
        ]


@dataclass(frozen=True)
class PriorCreativeState:
    """The values an update is compared AGAINST: the persisted row, pre-update.

    Snapshotted before the row is mutated, so ``comparison_changes`` can report
    what an update actually changed. There is one source and one branch --
    ``dry_run`` no longer builds a parallel "previewed" state (#1721: it is a
    transaction-disposal decision at the UoW boundary), so the second
    constructor this class used to carry is gone with it.
    """

    name: str | None
    #: The stored format identity, read through the ONE row reader. The three partial
    #: columns this used to snapshot were one value decomposed, and comparing them took
    #: three comparisons plus a private agent_url normalization; the identity carries its
    #: own comparison form, so "did the format change?" is one ``!=``.
    identity: CreativeFormatIdentity
    #: The upsert-rewritten data fields as the row held them, so an update can report
    #: which of them it actually changed instead of listing all five every time.
    upsert_fields: dict[str, Any]
    #: The asset map as the row held it -- the JSON the column wrote, so it is compared
    #: through ``ASSET_MAP`` rather than against the submitted models directly.
    assets: Any
    #: Whether that map is the buyer's submission rather than the agent's output. Absent on
    #: a row written before it was recorded, which reads as the agent's -- the conservative
    #: side, since it only declines to report a change.
    assets_are_buyer_submitted: bool
    #: The selection lineage the row holds, read back into the pinned model so it compares
    #: with the incoming one by value. ``None`` for a creative not resolved from a set.
    representation_selection: LibraryRepresentationSelection | None

    @classmethod
    def from_row(cls, existing_creative) -> PriorCreativeState:
        row_data = existing_creative.data or {}
        selection = row_data.get("representation_selection")
        return cls(
            name=existing_creative.name,
            identity=CreativeFormatIdentity.from_row(existing_creative),
            upsert_fields={key: row_data.get(key) for key in _UPSERT_FIELDS},
            assets=row_data.get("assets"),
            assets_are_buyer_submitted=bool(row_data.get("assets_are_buyer_submitted")),
            representation_selection=None
            if selection is None
            else LibraryRepresentationSelection.model_validate(selection),
        )

    def enters_review(self, creative: CreativeAsset, changes: list[str]) -> bool:
        """Whether an update with *changes* sends the creative back to review.

        Revision content does (every change outside ``_METADATA_CHANGES``), and so does a
        different execution (:func:`_execution`). A lineage change that pins the same
        execution is reported as ``representation_selection`` and keeps the review state.
        """
        content = set(changes) - _METADATA_CHANGES - {"representation_selection"}
        return bool(content) or _execution(creative.representation_selection) != _execution(
            self.representation_selection
        )


def comparison_changes(
    creative: CreativeAsset, prior: PriorCreativeState, identity: CreativeFormatIdentity
) -> list[str]:
    """The part of ``changes`` derived purely by comparison -- no mutation, no agent.

    Deliberately decoupled from the field assignments it used to be interleaved
    with, which is what makes an update's reported ``changes`` derivable from a
    before/after pair rather than from the mutation code. The ``name`` quirk is preserved exactly as the live path had it: a
    ``None`` incoming name that differs from the prior one still reports ``name``
    as changed even though the live branch assigns nothing (the assignment keeps its
    ``is not None`` guard at the call site).

    ``identity`` is the caller's ALREADY-PROJECTED incoming identity, and it is a parameter
    rather than a second ``creative_identity_of`` call here for two reasons. It used to be
    ``format_value``, documented as unused, while this function recomputed the identity from
    the creative alone -- so the projection happened twice and the copies could disagree the
    moment one of them gained an input. That is no longer hypothetical: the projection needs
    the registry's ``Format`` to reach past the glob, this function has no catalog to find
    one with, and a spec-less recomputation compared against a spec-projected stored identity
    would report ``format`` as changed on every update of the 56 formats that need it. Taking
    the value means the reported ``changes`` and the ``set_identity`` decision in
    ``_update_existing_creative`` derive from one projection by construction.
    """
    changes: list[str] = []
    if creative.name != prior.name:
        changes.append("name")

    # ONE comparison, and it is the identity's own. ``CreativeFormatIdentity`` compares its
    # canonical members and deliberately excludes the verbatim assertion: the same agent
    # respelled (host case, a default port, a trailing slash ``AnyUrl`` used to append) is the
    # same agent, and comparing raw spellings reported a format change on every respelling.
    if identity != prior.identity:
        changes.append("format")

    # ``assets`` is required on every creative (core/creative-asset.json, 3.2.3) and is the
    # buyer's whole submission for a generative format, where the prompt IS an asset. It was
    # absent from change detection, so an update that rewrote the message answered
    # ``unchanged`` -- the four generative-update cases in
    # tests/integration/test_creative_sync_processing.py.
    #
    # Compared only when the buyer SUBMITTED assets, which is the same rule the upsert
    # fields follow and here it is what keeps the agent out of the comparison: the
    # generative branches overwrite ``data["assets"]`` ONLY when the buyer provided none
    # (see the ``not user_provided_assets`` guard), so a submitted map is the buyer's on
    # both sides and an omitted one is never read back.
    #
    # Through ``ASSET_MAP`` because the two sides arrive in different forms -- the
    # submitted map holds asset MODELS, the row's holds the JSON the column wrote -- and a
    # raw ``!=`` between them differs always, which would make ``unchanged`` unreachable
    # again. ``ASSET_MAP`` is the pinned annotation itself and this is validation, not
    # serialization, so it carries no edge concern.
    # Compared when the buyer submits a map OR the row's map is one they submitted before,
    # so EMPTYING the assets reports a change too. Both sides false is the generative case
    # where the agent supplies the map: there the row holds a value the buyer never sent,
    # and comparing their empty submission against it would report a change on every
    # resync -- the regression this whole class exists to prevent.
    if creative.assets or prior.assets_are_buyer_submitted:
        if ASSET_MAP.validate_python(creative.assets or {}) != ASSET_MAP.validate_python(prior.assets or {}):
            changes.append("assets")
    # The lineage the buyer sent against the lineage stored, whole: it is what list_creatives
    # reads back, so any member that moved is a modified field. Whether the move re-enters
    # review is the execution fingerprint's question (``PriorCreativeState.enters_review``).
    if creative.representation_selection != prior.representation_selection:
        changes.append("representation_selection")
    return changes


def update_changes(
    creative: CreativeAsset,
    prior: PriorCreativeState,
    identity: CreativeFormatIdentity,
    submitted: SubmittedUpsertFields,
) -> list[str]:
    """What an update CHANGED: the one answer the result reports and review is decided on.

    Computed once per update, before the creative agent is asked anything, so the reported
    ``changes`` and the review decision in ``_update_existing_creative`` read one value:
    the result reports all of it, and review reads the part outside ``_METADATA_CHANGES``.
    """
    # dict.fromkeys: each field once, first occurrence's position kept.
    return list(dict.fromkeys([*comparison_changes(creative, prior, identity), *submitted.changed_against(prior)]))


def build_update_sync_result(
    creative_id: str,
    *,
    changes: list[str],
    internal_status: str | None = None,
) -> SyncCreativeResult:
    """The ONE place an ``updated`` or ``unchanged`` sync result is built, for both branches.

    ``changes`` (:func:`update_changes`) is ``[name?, format?] + the SUBMITTED upsert fields whose value differs
    from the row's``, each field once. That group used to be all five unconditionally,
    which made ``unchanged`` -- a member of the pinned enums/creative-action.json --
    unreachable: a resync of identical data always reported five changed fields.
    Comparing against the row's prior values is what lets identical data answer
    ``unchanged`` and a real change name the field.

    There is no agent-derived group, and the ``agent_derived_changes`` parameter that
    carried one is gone: a preview render and a generative build are the SELLER's
    derivations, they are re-made on every sync, and a preview transaction cannot make
    them at all -- see :class:`SubmittedUpsertFields` for the two pinned sentences that
    force this and for what used to go wrong. The agent still writes its answer to
    ``data``; ``data`` is simply not what change detection reads.

    ``internal_status`` is absent for an in-request duplicate, which has no row to
    read a status from. The field is ``exclude=True``, so it never reaches the
    wire and its absence cannot make a preview diverge from a live run.
    """
    action: Literal["updated", "unchanged"] = "updated" if changes else "unchanged"

    return SyncCreativeResult(
        creative_id=creative_id,
        action=action,
        internal_status=internal_status,
        changes=changes,
    )


def _failed_sync_result(creative_id: str, source: AdCPSalesAgentError) -> SyncCreativeResult:
    """Build a SyncCreativeResult for a failed creative sync operation.

    Takes the TYPED error and nothing else. The advisory carries the exception's CODE
    plus its structured specifics; the sentence, recovery and suggestion are resolved
    from ``CODE_TABLE`` by ``Error.of`` — the same single derivation the transport
    envelope uses — so a per-creative advisory and the request-level envelope cannot
    disagree about the same failure.

    Provenance-bearing text belongs on ``internal_detail`` at the RAISE site (server log
    only), never in ``details``: a diagnostic in ``details`` is on the buyer's wire, which
    is the forward ``adcp_error_for`` exists to prevent.
    """
    return SyncCreativeResult(
        creative_id=creative_id,
        action="failed",
        # structural-guard: advisory per-creative result in SyncCreativeResult.errors[]
        # from_exception, NOT a hand-rolled decomposition: reading error_code/field/details
        # off the exception separately re-implemented the conversion and skipped the one
        # place that renders a details CLASS to its wire dict, so a typed detail shape
        # reached Error(details=...) as a model and failed validation.
        errors=[AdCPErrorDetail.from_exception(source)],
        assigned_to=None,
        assignment_errors=None,
    )


def _no_previews_refusal(
    creative_id: str,
    *,
    creative: CreativeAsset,
    data: Mapping[str, Any],
    is_preview: bool,
    preview_requested: bool = True,
) -> SyncCreativeResult | None:
    """The ONE policy for "the agent-backed branch came back with no previews".

    Returns the refusal, or ``None`` when the sync may proceed. Called from the ``else``
    of ``if preview_result and preview_result.get("previews")`` on BOTH the create and the
    update path, which is the whole point of it existing: the two paths carried the same
    refusal and it had drifted one nesting level apart (salesagent-2je8o). The update's
    copy sat on the ``else`` of ``if format_obj and is_dialled_agent_format(...)``, i.e. on
    "the format did not resolve", so

    * an update whose format DID resolve and whose agent returned no previews was accepted
      as ``updated`` with no media_url at all, while the identical create was refused
      ``CREATIVE_REJECTED``/``no_previews`` -- one tool, two policies for one state; and
    * the refusal it did have fired on a branch where no preview was ever requested, so a
      buyer whose format is simply not in the catalogue was told the agent failed to render.

    One function reached from one branch position is what makes a second divergence
    unspellable; there is no longer a copy to drift.
    """
    if not preview_requested:
        # THE GENERATIVE BRANCH NEVER ASKS. It sets ``preview_result = None`` on purpose --
        # "skip preview_creative since we already have the output" -- because
        # ``build_creative`` produced the asset. So a falsy result here is the same "we did
        # not ask" state the ``is_preview`` arm below describes, arriving by a different
        # route, and refusing it told the buyer the agent failed to render a creative no
        # agent was asked to render.
        #
        # This is the third state the docstring's warning anticipated: it says testing
        # ``preview_result is None`` would conflate "we did not ask" with "the agent had
        # none", and the generative branch produced exactly that conflation one level up.
        # A generative creative also has no ``url`` before its build, so the media_url
        # escape below cannot save it -- every generative update was refused
        # CREATIVE_REJECTED/no_previews (salesagent-hkq9p: 3 BDD identifiers on a2a, mcp
        # and rest, plus 5 integration tests).
        return None

    if is_preview:
        # The boundary SUPPRESSED the call (an outbound request is not undoable by the
        # transaction's rollback), so a falsy result here means "we did not ask", NOT "the
        # agent had none". Asked of the TRANSACTION, deliberately: testing
        # ``preview_result is None`` would conflate those two, which is the confusion this
        # branch exists to prevent. Failing the entry would report a fault that does not
        # exist and would break the pinned dry_run contract: a preview "returns what would
        # be created/updated/deleted" (sync-creatives-request.json#/properties/dry_run),
        # not a refusal blaming the creative agent.
        logger.info(
            "[sync_creatives] dry_run: skipped preview generation for %s; the preview reports what it would do",
            creative_id,
        )
        return None

    # No previews is only acceptable if the creative carries a media_url of its own (a
    # direct URL to the asset); such a static creative does not need a render.
    if bool(getattr(creative, "url", None) or data.get("url")):
        logger.warning(
            "[sync_creatives] Preview generation returned no previews for %s (static creative with media_url)",
            creative_id,
        )
        return None

    logger.error(
        "[sync_creatives] Preview generation returned nothing for %s and no media_url",
        creative_id,
    )
    return _failed_sync_result(
        creative_id,
        AdCPCreativeRejectedError(
            field="media_url",
            details=CreativeRejectionDetails(creative_id=creative_id, reasons=["no_previews"]),
        ),
    )


def _defer_ai_review(
    creative_repo: CreativeRepository,
    *,
    creative_id: str,
    tenant: TenantContext,
    webhook_url: str | None,
    principal_id: str,
) -> None:
    """Hand the creative to the background AI reviewer, AFTER this transaction commits.

    The job opens its OWN AdminCreativeUoW, commits a review verdict, and then
    sends Slack and the push webhook -- none of it inside this transaction, so a
    rollback cannot reach any of it. Registering it on the unit of work instead
    of calling it here means the preview branch needs no gate: a preview rolls back,
    the queue is discarded, and nothing was submitted.

    It also fixes an ordering bug that had nothing to do with previews. This used
    to flush and submit inline, but flush() is not commit() and the job reads
    through its own session -- so on the create branch the row might not exist yet,
    and on the update branch the job read PRE-update state and committed a verdict
    over it. Deferring past the commit removes the race by construction.

    Captures scalars only. Holding the ORM row here would hand a detached
    instance to a thread after this session is gone.
    """
    from src.admin.blueprints.creatives import _ai_review_executor, _ai_review_lock, _ai_review_tasks

    def _submit() -> None:
        from src.admin.blueprints.creatives import _ai_review_creative

        task_id = f"ai_review_{creative_id}_{uuid.uuid4().hex[:8]}"
        future = _ai_review_executor.submit(
            _ai_review_creative,
            creative_id=creative_id,
            tenant_id=tenant.tenant_id,
            webhook_url=webhook_url,
            slack_webhook_url=tenant.slack_webhook_url,
            principal_name=principal_id,
        )
        with _ai_review_lock:
            _ai_review_tasks[task_id] = {
                "future": future,
                "creative_id": creative_id,
                "created_at": time.time(),
            }
        logger.info(f"[sync_creatives] Submitted AI review for {creative_id} (task: {task_id})")

    creative_repo.after_commit(_submit, label=f"ai_review:{creative_id}")


def incoming_identity_of(creative: CreativeAsset, all_formats: list[Any]) -> CreativeFormatIdentity:
    """The identity a sync item names, with its canonical kind projected. Read ONCE per item.

    The create path, the update path and the review record all need it, and each used to
    compute it (or, the review record, to read the deprecated ``format_id`` instead).

    The SPEC is passed, and that is the whole of why format_kind used to be NULL. Passing it
    is what makes the registry's steps 2 (the agent's own ``canonical``) and 4 (the
    structural match) reachable; without it only the glob (step 3) answers. Measured
    against the pinned capture of 76 formats -- 23 project without the spec, all 76 with it
    -- so a bare call refused most of the formats the agent actually serves.

    ``find_format`` is pure (no HTTP, no DB) and ``all_formats`` is pre-fetched outside the
    transaction, so resolving it costs nothing and adds no call inside the savepoint. A
    reference that projects to no canonical kind is refused here (UNSUPPORTED_FEATURE, see
    ``creative_identity_of``), before any creative agent is dialled for it.
    """
    spec = find_format(all_formats, creative.format_id) if creative.format_id else None
    return creative_identity_of(creative, spec=spec)


def _dialled_format_for(
    creative: CreativeAsset, identity: CreativeFormatIdentity, all_formats: list[Any]
) -> Format | None:
    """The served format whose creative agent this item is previewed (or built) by, or None.

    A LEGACY item names its format, and with it the agent: ``find_format`` over the
    catalogue pre-fetched outside the transaction (pure, so nothing is dialled inside the
    savepoint; and not a ``==`` loop, because pydantic v2 equality is class-sensitive and a
    raw comparison matched nothing on A2A -- salesagent-kyc89). A CANONICAL item names
    neither, so it is previewed by the operator's own agent when that agent declares the
    kind (:func:`find_operator_format_of_kind`). Both used to be gated on
    ``creative.format_id``, so a creative identified by ``format_kind`` was never previewed
    at all (salesagent-ol033.20.16).

    ``is_dialled_agent_format``, not ``is_agent_backed``: an adapter-served format declares
    a pseudo-URL with no host, and dialling it sends preview_creative at a url the egress
    seam refuses (salesagent-zufx8). A format this seller serves but will not dial
    (unresolved, or an adapter pseudo-URL) is processed with no external validation and no
    preview fetched -- BR-RULE-035 INV-2, as ``is_dialled_agent_format`` states.
    """
    fmt = (
        find_format(all_formats, creative.format_id)
        if creative.format_id is not None
        else find_operator_format_of_kind(all_formats, identity.kind)
    )
    return fmt if fmt is not None and is_dialled_agent_format(fmt) else None


def _preview_manifest(
    creative: CreativeAsset,
    data: Mapping[str, Any],
    *,
    identity: CreativeFormatIdentity,
    creative_id: str,
    name: Any,
) -> dict[str, Any]:
    """The manifest this seller sends a creative agent's ``preview_creative``.

    A legacy creative's ``format_id`` is deliberately NOT written here: the registry
    composes it from the same ``{agent_url, id}`` object it sends as the call's own
    ``format_id`` (``CreativeAgentRegistry.preview_creative``). Both builders used to write
    the bare id string, which satisfies neither arm of core/creative-manifest.json (v3.2.7)
    -- the named arm's ``format_id`` is a core/format-id.json OBJECT -- so the seller
    authored an invalid manifest on every static preview.

    A CANONICAL creative's identity IS written here, because the manifest is where the pin
    puts it: creative/preview-creative-request.json (v3.2.7) marks ``format_id`` deprecated
    and says new requests "carry portable format identity in creative_manifest.format_kind
    plus optional creative_manifest.format_option_ref". ``wire_format_kind`` chooses the arm,
    the same oneOf core/creative-manifest.json declares; ``option.document`` is the
    core/format-option-ref.json document, ``scope`` included, exactly as stored.
    """
    manifest: dict[str, Any] = {"creative_id": creative_id, "name": name}
    if identity.wire_format_kind is not None:
        manifest["format_kind"] = identity.wire_format_kind
        if identity.option is not None:
            manifest["format_option_ref"] = identity.option.document
    # Validate assets are in dict format (AdCP v2.4+)
    if creative.assets:
        validated_assets = _validate_creative_assets(creative.assets)
        if validated_assets:
            manifest["assets"] = validated_assets
    if data.get("url"):
        manifest["url"] = data.get("url")
    return manifest


def _request_static_preview(
    creative_repo: CreativeRepository,
    registry: Any,
    creative: CreativeAsset,
    data: Mapping[str, Any],
    *,
    identity: CreativeFormatIdentity,
    format_obj: Format,
    creative_id: str,
    name: Any,
) -> dict[str, Any] | None:
    """Ask *format_obj*'s agent to preview this static creative. ONE request, both paths.

    A legacy creative routes by the ``format_id`` it named; a canonical one sends no
    ``format_id`` at all and carries its identity in the manifest (:func:`_preview_manifest`).
    """
    manifest = _preview_manifest(creative, data, identity=identity, creative_id=creative_id, name=name)
    legacy_id = creative.format_id.id if creative.format_id is not None else None
    logger.info(
        "[sync_creatives] Calling preview_creative for validation: %s (%s) from agent %s, has_assets=%s, has_url=%s",
        creative_id,
        identity.label,
        format_obj.agent_url,
        bool(creative.assets),
        bool(data.get("url")),
    )
    # OUT-OF-TRANSACTION EFFECT: a preview must not fire a request at a creative agent's
    # endpoint (same rule accounts.py states for activation proofs). None here is already
    # the no-result case the consumer guards for, so no second result path appears.
    return creative_repo.outbound(
        lambda: run_async_in_sync_context(
            registry.preview_creative(
                agent_url=format_obj.agent_url,
                format_id=legacy_id,
                creative_manifest=manifest,
            )
        )
    )


def _store_previews(data: dict[str, Any], preview_result: Mapping[str, Any] | None) -> bool:
    """Write the agent's previews into *data*. Returns whether it answered any.

    The full answer is kept for the admin UI (AdCP PR #119), and the first render
    back-fills ``url`` and the dimensions only where the buyer supplied none, so a buyer's
    own values are never overwritten by a render. ``False`` is the no-previews state
    :func:`_no_previews_refusal` owns.
    """
    if not (preview_result and preview_result.get("previews")):
        return False
    data["preview_response"] = preview_result
    renders = preview_result["previews"][0].get("renders", [])
    if renders:
        first_render = renders[0]
        if first_render.get("preview_url") and not data.get("url"):
            data["url"] = first_render["preview_url"]
        dimensions = first_render.get("dimensions", {})
        for key in ("width", "height", "duration"):
            if dimensions.get(key) and not data.get(key):
                data[key] = dimensions[key]
    logger.info(
        "[sync_creatives] Preview data populated: url=%s, width=%s, height=%s, variants=%s",
        bool(data.get("url")),
        data.get("width"),
        data.get("height"),
        len(preview_result["previews"]),
    )
    return True


def _generative_build_key(tenant: TenantContext, creative_format: Any) -> str:
    """The Gemini API key a generative build sends the creative agent: the TENANT's own.

    The creative agent bills the caller's key, not its own
    (``CreativeAgentRegistry.build_creative``), and the caller is this tenant. The key is
    read from the tenant's AI configuration (``tenant.ai_config``, which the admin AI
    settings write and the creative AI review reads) through
    :func:`src.services.ai.config.tenant_google_api_key`. A tenant with no Google key
    there cannot build, which is this seller's configuration and nothing the buyer can
    correct: CONFIGURATION_ERROR, on the create and the update path alike.
    """
    api_key = tenant_google_api_key(tenant.ai_config)
    if not api_key:
        logger.error(
            "[sync_creatives] Cannot build generative creative %s: tenant %s has no Gemini API key",
            creative_format,
            tenant.tenant_id,
        )
        raise AdCPConfigurationError()
    return api_key


def _apply_approval_mode(
    creative_repo: CreativeRepository,
    row: Any,
    *,
    tenant: TenantContext,
    webhook_url: str | None,
    principal_id: str,
) -> bool:
    """Set *row*'s review status from the tenant's approval mode. Returns whether it needs approval.

    The mode is read off *tenant* and nowhere else, through the same
    ``TenantContext.auto_approves_creatives`` that ``get_adcp_capabilities`` advertises as
    ``media_buy.creative_approval_mode``, so the advertised posture and this routing cannot
    disagree.

    ONE policy for a created and an updated creative, whichever identity arm it carries. The
    update path used to run this only ``if creative.format_id``, so re-syncing a creative
    identified by ``format_kind`` kept its old status under every mode and never reached AI
    or human review: business logic keyed on the deprecated arm, which
    docs/creative/creative-manifests.mdx (v3.2.7) § Basic structure makes the legacy branch
    ("New manifests use the canonical branch"). Every sync item carries exactly one
    identity (``CreativeAssetRequest`` refuses neither and both), so there is no item this
    policy has nothing to say about.
    """
    if tenant.auto_approves_creatives:
        row.status = CreativeStatusEnum.approved.value
        return False
    row.status = CreativeStatusEnum.pending_review.value
    if tenant.approval_mode == "ai-powered":
        _defer_ai_review(
            creative_repo,
            creative_id=row.creative_id,
            tenant=tenant,
            webhook_url=webhook_url,
            principal_id=principal_id,
        )
    return True


def _update_existing_creative(
    creative: CreativeAsset,
    existing_creative: Any,
    creative_repo: CreativeRepository,
    format_value: Any,
    tenant: TenantContext,
    webhook_url: str | None,
    all_formats: list[Any],
    registry: Any,
    principal_id: str,
    incoming_identity: CreativeFormatIdentity,
) -> tuple[SyncCreativeResult, bool]:
    """Update an existing creative with upsert semantics .

    Handles the full update path: field updates, approval mode logic,
    creative agent validation (generative and static), preview extraction,
    and data persistence.

    Args:
        creative: CreativeAsset model from the sync payload.
        existing_creative: Existing DBCreative model to update (mutated in-place).
        creative_repo: CreativeRepository for DB operations (flush, update_data).
        format_value: Validated FormatId from Creative schema.
        tenant: The tenant; its approval_mode routes review (``_apply_approval_mode``).
        webhook_url: Push notification webhook URL for AI review callbacks.
        all_formats: Pre-fetched creative formats from registry.
        registry: CreativeAgentRegistry instance.
        principal_id: Authenticated principal ID for AI review callbacks.
        incoming_identity: The identity this sync item names (``incoming_identity_of``).

    Returns:
        Tuple of (SyncCreativeResult, needs_approval).
    """
    # Update updated_at timestamp
    now = datetime.now(UTC)
    existing_creative.updated_at = now

    # Snapshot what this update is compared against BEFORE mutating the row, and
    # derive the comparison-based part of `changes` from it. The dry_run branch runs
    # the identical comparison against the state an earlier entry previewed, which
    # is the only way the two branches can agree — see build_update_sync_result.
    prior = PriorCreativeState.from_row(existing_creative)

    # Upsert mode: update provided fields. Assignment stays guarded on `is not
    # None`; the comparison that decides whether `name` counts as changed lives in
    # comparison_changes and deliberately does NOT share that guard.
    if creative.name != existing_creative.name:
        name_value = creative.name
        if name_value is not None:
            existing_creative.name = str(name_value)
    # The identity the buyer sent this time (``incoming_identity``, read once by the caller).
    # Written only when it differs from the stored one, and the difference is the identity's
    # own equality -- which canonicalizes agent_url, the comparison ``core/format-id.json``
    # makes a MUST, and ignores the verbatim spelling it retains. Four columns move together
    # or not at all, through the one repository mapping that ``create`` also uses.
    if incoming_identity != prior.identity:
        creative_repo.set_identity(existing_creative, incoming_identity)

    creative_format = creative.format_id

    # Store creative properties in data field
    # Full upsert semantics (replace all data, not merge)
    url = _extract_url_from_assets(creative)
    data = _build_creative_data(creative, url)

    # The buyer's own upsert values, COPIED out before the creative agent below can
    # back-fill any of them. From here on `data` is the persistence document only: it is
    # what gets written to the row, and `submitted` is what change detection reads. Taking
    # the snapshot here -- above the agent block, on the one line that builds the buyer's
    # document -- is what makes the preview branch and the live branch report the same
    # `changes` by construction instead of by a residual-divergence note.
    submitted = SubmittedUpsertFields.submitted_by(data)
    changes = update_changes(creative, prior, incoming_identity, submitted)

    # CHANGED CONTENT ENTERS REVIEW; AN UNCHANGED OR METADATA-ONLY RESYNC KEEPS ITS REVIEW
    # STATE. Decided on the content part of the one change list the result reports.
    # docs/creative/specification.mdx (v3.2.7) § Creative revision identity: "Prior approval
    # does not transfer: an accepted content-bearing unversioned update enters the ordinary
    # review lifecycle", and the same section excludes ``name`` from revision content, so a
    # rename answers ``updated`` and keeps its approval. § Creative Status Lifecycle allows
    # ``approved -> pending_review`` only for a buyer-initiated revision update or for a
    # seller-initiated re-review that MUST fire ``creative.status_changed``; re-reviewing an
    # unchanged or renamed creative is neither. Either identity arm reaches this the same
    # way: a canonical creative whose format_option_ref or assets change re-enters review
    # exactly as a legacy one does. A different selected execution re-enters review too,
    # even with the source revision unchanged (representation-sets.mdx § Sync, review, and
    # readback): "approval never transfers silently to different execution bytes".
    needs_approval = (
        _apply_approval_mode(
            creative_repo,
            existing_creative,
            tenant=tenant,
            webhook_url=webhook_url,
            principal_id=principal_id,
        )
        if prior.enters_review(creative, changes)
        else False
    )

    # ALWAYS validate updates with creative agent
    format_obj = _dialled_format_for(creative, incoming_identity, all_formats)
    if format_obj is not None:
        try:
            preview_requested = True
            if creative_format is not None and is_generative(format_obj):
                # Generative creative update - rebuild using AI
                logger.info(
                    f"[sync_creatives] Detected generative format update: {creative_format}, "
                    f"checking for Gemini API key"
                )

                gemini_api_key = _generative_build_key(tenant, creative_format)

                # Extract message/brief from assets or inputs
                message = _extract_message_from_assets(creative)

                # Extract promoted_offerings from assets if available
                promoted_offerings = None
                if creative.assets:
                    for role, asset in creative.assets.items():
                        if role == "promoted_offerings":
                            promoted_offerings = asset
                            break

                # Get existing context_id for refinement
                existing_context_id = None
                if existing_creative.data:
                    existing_context_id = existing_creative.data.get("generative_context_id")

                # Use provided context_id or existing one
                context_id = getattr(creative, "context_id", None) or existing_context_id

                # Only call build_creative if we have a message (refinement)
                if message:
                    logger.info(
                        f"[sync_creatives] Calling build_creative for update: "
                        f"{existing_creative.creative_id} format {creative_format} "
                        f"from agent {format_obj.agent_url}, "
                        f"message_length={len(message) if message else 0}, "
                        f"context_id={context_id}"
                    )

                    # OUT-OF-TRANSACTION EFFECT: a preview must not fire a request at a
                    # creative agent's endpoint (same rule accounts.py states for
                    # activation proofs). None here is already the no-result case the
                    # consumer below guards for, so no second result path appears.
                    build_result = creative_repo.outbound(
                        lambda: run_async_in_sync_context(
                            registry.build_creative(
                                agent_url=format_obj.agent_url,
                                format_id=creative_format,
                                message=message,
                                gemini_api_key=gemini_api_key,
                                promoted_offerings=promoted_offerings,
                                context_id=context_id,
                                finalize=getattr(creative, "approved", False),
                            )
                        )
                    )

                    # Store build result in data
                    if build_result:
                        data["generative_build_result"] = build_result
                        data["generative_status"] = build_result.get("status", "draft")
                        data["generative_context_id"] = build_result.get("context_id")

                        # Extract creative output if available
                        if build_result.get("creative_output"):
                            creative_output = build_result["creative_output"]

                            # Only use generative assets if user didn't provide their own
                            user_provided_assets = creative.assets
                            if creative_output.get("assets") and not user_provided_assets:
                                data["assets"] = _generative_assets(creative_output["assets"])
                                logger.info("[sync_creatives] Using assets from generative output (update)")
                            elif user_provided_assets:
                                logger.info(
                                    "[sync_creatives] Preserving user-provided assets in update, "
                                    "not overwriting with generative output"
                                )

                            if creative_output.get("output_format"):
                                output_format = creative_output["output_format"]
                                data["output_format"] = output_format

                                # Only use generative URL if user didn't provide one
                                if isinstance(output_format, dict) and output_format.get("url"):
                                    if not data.get("url"):
                                        data["url"] = output_format["url"]
                                        logger.info(
                                            f"[sync_creatives] Got URL from generative output (update): {data['url']}"
                                        )
                                    else:
                                        logger.info(
                                            "[sync_creatives] Preserving user-provided URL in update, "
                                            "not overwriting with generative output"
                                        )

                        logger.info(
                            f"[sync_creatives] Generative creative updated: "
                            f"status={data.get('generative_status')}, "
                            f"context_id={data.get('generative_context_id')}"
                        )
                else:
                    # No prompt → skip build, but preserve generative fields
                    # from existing data (data was rebuilt from scratch above)
                    if existing_creative.data:
                        for key in (
                            "generative_build_result",
                            "generative_status",
                            "generative_context_id",
                            "output_format",
                        ):
                            if key in existing_creative.data:
                                data[key] = existing_creative.data[key]
                    logger.info("[sync_creatives] No message for generative update, keeping existing creative data")

                # Skip preview_creative call since we already have the output
                preview_result = None
                # ... and say so, rather than leaving the refusal below to read a falsy
                # result as "the agent returned nothing" (salesagent-hkq9p).
                preview_requested = False
            else:
                preview_result = _request_static_preview(
                    creative_repo,
                    registry,
                    creative,
                    data,
                    identity=incoming_identity,
                    format_obj=format_obj,
                    creative_id=existing_creative.creative_id,
                    name=creative.name or existing_creative.name,
                )

            if not _store_previews(data, preview_result):
                # No previews came back from an agent-backed format. ONE policy, shared
                # with the create path -- see _no_previews_refusal for why this sits
                # here and not on the `else` of the agent-backed test above.
                refusal = _no_previews_refusal(
                    existing_creative.creative_id,
                    creative=creative,
                    data=data,
                    is_preview=creative_repo.is_preview,
                    preview_requested=preview_requested,
                )
                if refusal is not None:
                    return (refusal, False)

        except AdCPConfigurationError as config_error:
            # Server-side misconfiguration (e.g. GEMINI_API_KEY missing) is terminal
            # and admin-fixable — not a transient creative-agent outage. Surface it
            # honestly so the buyer does not retry a misconfiguration.
            logger.error(
                "[sync_creatives] configuration error for update of %s",
                existing_creative.creative_id,
                exc_info=True,
            )
            return (
                _failed_sync_result(
                    existing_creative.creative_id,
                    AdCPConfigurationError(
                        details=ConfigurationDetails(creative_id=existing_creative.creative_id),
                        internal_detail=config_error,
                    ),
                ),
                False,
            )
        except OutboundError as outbound_error:
            # A refused/undeliverable egress request is already correctly classified
            # by the seam — delegate rather than laundering it into the generic
            # transient branch below. This branch MUST precede the typed branch: both
            # OutboundError subclasses are also AdCPSalesAgentError subclasses
            # (OutboundRequestBlocked/AdCPBlockedUrlError,
            # OutboundDeliveryFailed/AdCPServiceUnavailableError), so a typed catch
            # first would swallow the provenance classification.
            # raise_mapped_outbound_error always raises; the mapped error propagates
            # out of this function to _sync.py's per-creative typed handler, which
            # builds the per-item result from the typed error itself.
            raise_mapped_outbound_error(
                outbound_error,
                provenance=OperatorEndpoint("the creative agent"),
                logger=logger,
            )
        except AdCPSalesAgentError as typed_error:
            # GENERALIZES the AdCPConfigurationError branch above. That branch was added so a
            # missing GEMINI_API_KEY would not read as a transient creative-agent outage;
            # the same is true of every other typed error the registry raises. A
            # rate limit degraded to SERVICE_UNAVAILABLE tells the buyer to retry
            # immediately, which is the one thing a 429 says not to do.
            logger.error(
                "[sync_creatives] typed %s for update of %s",
                typed_error.error_code,
                existing_creative.creative_id,
                exc_info=True,
            )
            return (_failed_sync_result(existing_creative.creative_id, typed_error), False)
        except Exception as validation_error:
            # Creative agent validation failed for update (network error, agent down, etc.)
            # Do NOT update the creative - it needs validation before acceptance
            logger.error(
                "[sync_creatives] creative agent unreachable or validation error for update of %s",
                existing_creative.creative_id,
                exc_info=True,
            )
            return (
                _failed_sync_result(
                    existing_creative.creative_id,
                    AdCPServiceUnavailableError(
                        details=AdapterFailureDetails(creative_id=existing_creative.creative_id),
                        internal_detail=validation_error,
                    ),
                ),
                False,
            )

    # `data` carries the agent's enrichment and is what gets PERSISTED. It is deliberately
    # not what the result below reports on.
    creative_repo.update_data(existing_creative, data)

    # Record result for updated creative, from the buyer's submission and the row's prior
    # state -- the two things a preview transaction can read exactly as well as a live one.
    return (
        build_update_sync_result(
            existing_creative.creative_id,
            changes=changes,
            internal_status=existing_creative.status,
        ),
        needs_approval,
    )


def _create_new_creative(
    creative: CreativeAsset,
    creative_repo: CreativeRepository,
    format_value: Any,
    tenant: TenantContext,
    webhook_url: str | None,
    all_formats: list[Any],
    registry: Any,
    principal_id: str,
    incoming_identity: CreativeFormatIdentity,
) -> tuple[SyncCreativeResult, bool]:
    """Create a new creative and persist it to the database .

    Handles the full create path: URL extraction, data dict construction,
    creative agent validation (generative build or static preview),
    DB insertion, approval mode logic, and AI review submission.

    Mutates ``creative.creative_id`` in-place when the ID is server-generated.

    Returns:
        Tuple of (SyncCreativeResult, needs_approval).
    """

    # Extract creative_id for error reporting (must be defined before any validation)
    creative_id = creative.creative_id or "unknown"

    # Prepare data field with all creative properties
    url = _extract_url_from_assets(creative)
    data = _build_creative_data(creative, url)

    # Store user-provided assets for preservation check
    user_provided_assets = creative.assets

    # ALWAYS validate creatives with the creative agent (validation + preview generation)
    creative_format = creative.format_id
    format_obj = _dialled_format_for(creative, incoming_identity, all_formats)
    if format_obj is not None:
        try:
            preview_requested = True
            if creative_format is not None and is_generative(format_obj):
                # Generative creative - call build_creative
                logger.info(
                    f"[sync_creatives] Detected generative format: {creative_format}, checking for Gemini API key"
                )

                gemini_api_key = _generative_build_key(tenant, creative_format)

                # Extract message/brief from assets or inputs
                message = _extract_message_from_assets(creative)

                if not message:
                    message = f"Create a creative for: {creative.name}"
                    logger.warning(
                        "[sync_creatives] No message found in assets/inputs, using creative name as fallback"
                    )

                # Extract promoted_offerings from assets if available
                promoted_offerings = None
                if creative.assets:
                    for role, asset in creative.assets.items():
                        if role == "promoted_offerings":
                            promoted_offerings = asset
                            break

                # Call build_creative
                # Extract string ID from FormatId object if needed
                format_id_str = creative_format.id
                logger.info(
                    f"[sync_creatives] Calling build_creative for generative format: "
                    f"{format_id_str} from agent {format_obj.agent_url}, "
                    f"message_length={len(message) if message else 0}"
                )

                # OUT-OF-TRANSACTION EFFECT: a preview must not fire a request at a
                # creative agent's endpoint (same rule accounts.py states for
                # activation proofs). None here is already the no-result case the
                # consumer below guards for, so no second result path appears.
                build_result = creative_repo.outbound(
                    lambda: run_async_in_sync_context(
                        registry.build_creative(
                            agent_url=format_obj.agent_url,
                            format_id=format_id_str,
                            message=message,
                            gemini_api_key=gemini_api_key,
                            promoted_offerings=promoted_offerings,
                            context_id=getattr(creative, "context_id", None),
                            finalize=getattr(creative, "approved", False),
                        )
                    )
                )

                # Store build result
                if build_result:
                    data["generative_build_result"] = build_result
                    data["generative_status"] = build_result.get("status", "draft")
                    data["generative_context_id"] = build_result.get("context_id")

                    # Extract creative output
                    if build_result.get("creative_output"):
                        creative_output = build_result["creative_output"]

                        # Only use generative assets if user didn't provide their own
                        if creative_output.get("assets") and not user_provided_assets:
                            data["assets"] = _generative_assets(creative_output["assets"])
                            logger.info("[sync_creatives] Using assets from generative output")
                        elif user_provided_assets:
                            logger.info(
                                "[sync_creatives] Preserving user-provided assets, "
                                "not overwriting with generative output"
                            )

                        if creative_output.get("output_format"):
                            output_format = creative_output["output_format"]
                            data["output_format"] = output_format

                            # Only use generative URL if user didn't provide one
                            if isinstance(output_format, dict) and output_format.get("url"):
                                if not data.get("url"):
                                    data["url"] = output_format["url"]
                                    logger.info(f"[sync_creatives] Got URL from generative output: {data['url']}")
                                else:
                                    logger.info(
                                        "[sync_creatives] Preserving user-provided URL, "
                                        "not overwriting with generative output"
                                    )

                    logger.info(
                        f"[sync_creatives] Generative creative built: "
                        f"status={data.get('generative_status')}, "
                        f"context_id={data.get('generative_context_id')}"
                    )

                # Skip preview_creative call since we already have the output
                preview_result = None
                # ... and say so, rather than leaving the refusal below to read a falsy
                # result as "the agent returned nothing" (salesagent-hkq9p).
                preview_requested = False
            else:
                preview_result = _request_static_preview(
                    creative_repo,
                    registry,
                    creative,
                    data,
                    identity=incoming_identity,
                    format_obj=format_obj,
                    creative_id=creative.creative_id or str(uuid.uuid4()),
                    name=creative.name,
                )

            if not _store_previews(data, preview_result):
                # No previews came back from an agent-backed format. ONE policy, shared
                # with the update path -- see _no_previews_refusal, which also owns the
                # "a preview transaction did not ask" arm that used to be an `elif`
                # here.
                refusal = _no_previews_refusal(
                    creative_id,
                    creative=creative,
                    data=data,
                    is_preview=creative_repo.is_preview,
                    preview_requested=preview_requested,
                )
                if refusal is not None:
                    return (refusal, False)

        except AdCPConfigurationError as config_error:
            # Server-side misconfiguration (e.g. GEMINI_API_KEY missing) is terminal
            # and admin-fixable — not a transient creative-agent outage. Surface it
            # honestly so the buyer does not retry a misconfiguration.
            logger.error("[sync_creatives] configuration error - rejecting creative %s", creative_id, exc_info=True)
            return (
                _failed_sync_result(
                    creative_id,
                    AdCPConfigurationError(
                        details=ConfigurationDetails(creative_id=creative_id),
                        internal_detail=config_error,
                    ),
                ),
                False,
            )
        except OutboundError as outbound_error:
            # A refused/undeliverable egress request is already correctly classified
            # by the seam — delegate rather than laundering it into the generic
            # transient branch below. This branch MUST precede the typed branch: both
            # OutboundError subclasses are also AdCPSalesAgentError subclasses
            # (OutboundRequestBlocked/AdCPBlockedUrlError,
            # OutboundDeliveryFailed/AdCPServiceUnavailableError), so a typed catch
            # first would swallow the provenance classification.
            # raise_mapped_outbound_error always raises; the mapped error propagates
            # out of this function to _sync.py's per-creative typed handler, which
            # builds the per-item result from the typed error itself.
            raise_mapped_outbound_error(
                outbound_error,
                provenance=OperatorEndpoint("the creative agent"),
                logger=logger,
            )
        except AdCPSalesAgentError as typed_error:
            # Same generalization as the update path above.
            logger.error(
                "[sync_creatives] typed %s - rejecting creative %s",
                typed_error.error_code,
                creative_id,
                exc_info=True,
            )
            return (_failed_sync_result(creative_id, typed_error), False)
        except Exception as validation_error:
            # Creative agent validation failed (network error, agent down, etc.)
            # Do NOT store the creative - it needs validation before acceptance
            logger.error(
                "[sync_creatives] creative agent unreachable or validation error - rejecting creative %s",
                creative_id,
                exc_info=True,
            )
            return (
                _failed_sync_result(
                    creative_id,
                    AdCPServiceUnavailableError(
                        details=AdapterFailureDetails(creative_id=creative_id),
                        internal_detail=validation_error,
                    ),
                ),
                False,
            )

    # Create initial creative with pending_review status (will be updated based on approval mode)
    creative_status = CreativeStatusEnum.pending_review.value

    db_creative = creative_repo.create(
        creative_id=creative.creative_id or None,
        name=creative.name,
        # The identity the buyer gave, with a legacy reference projected to its canonical
        # kind. ONE value for all four columns: the three legacy strings this used to pass
        # could only ever persist the deprecated arm, and a parameter the hand-written
        # extraction did not name was dropped on the way in.
        identity=incoming_identity,
        principal_id=principal_id,
        status=creative_status,
        data=data,
    )

    # Update creative_id if it was generated (i6k: model attribute assignment).
    # The attr-defined ignore is GONE: the pinned SDK made CreativeAsset a RootModel whose
    # __getattr__ proxied to .root, which is what hid the attribute from mypy. the pinned SDK's
    # CreativeAsset is a plain model again and declares creative_id itself.
    if not creative.creative_id:
        creative.creative_id = db_creative.creative_id

    needs_approval = _apply_approval_mode(
        creative_repo,
        db_creative,
        tenant=tenant,
        webhook_url=webhook_url,
        principal_id=principal_id,
    )

    return (
        SyncCreativeResult(
            creative_id=db_creative.creative_id,
            action="created",
            internal_status=db_creative.status,
        ),
        needs_approval,
    )
