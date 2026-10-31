"""CreativeSyncEnv — integration test environment for _sync_creatives_impl.

Patches: the creative agent registry INSTANCE (with production's own
``ReferenceFormatsRegistry``, so the catalogue is the committed capture the e2e server
reads), notifications, audit; pins the Gemini key in the environment.
Real: get_db_session, CreativeRepository, the format catalogue and every format
resolution, all validation/processing (all hit real DB).

A scenario whose subject is the catalogue itself calls ``set_served_catalogue``: the
formats are served by a real peer creative agent the tenant configures (in process and on
the live server alike), and the creative names them by ``env.seeded_format_ids()``.

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with CreativeSyncEnv() as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")

            response = env.call_impl(creatives=[{
                "creative_id": "c1",
                "name": "Test Creative",
                "format_id": {"id": "display_300x250", "agent_url": "..."},
                "media_url": "https://example.com/img.png",
            }])
            assert len(response.results) == 1

Generative creative usage::

    with CreativeSyncEnv() as env:
        env.setup_default_data()
        fmt = env.setup_generative_build(
            format_id="display_300x250_generative",   # an id the CAPTURE carries
            build_result={"status": "draft", "context_id": "ctx-1", "creative_output": {}},
        )
        result = env.call_via(transport, creatives=[{
            "creative_id": "c1",
            "name": "Gen Creative",
            "format_id": fmt,
            "assets": build_assets(
                text_spec("message", content="Build me a banner")
            ),
        }])

Available mocks via env.mock:
    "registry"            -- get_creative_agent_registry (lazy import in _sync.py). Its
                             ``.return_value`` is a REAL ``HarnessCreativeAgentRegistry``
                             (a ``ReferenceFormatsRegistry``), not a stand-in: the
                             catalogue and every format resolution are production's,
                             answered from the committed capture. Only
                             ``preview_creative`` / ``build_creative`` are recorders,
                             because neither has a capture (salesagent-1bc7l)
    "send_notifications"  -- _send_creative_notifications (from _workflow); runs the REAL
                             function by default, so its webhook/approval-mode guard is graded
    "slack_notifier"      -- get_slack_notifier (src.services.slack_notifier), the Slack sender
                             that function reaches. Read it through
                             ``env.slack_notified_creatives``, not directly: the accessor is
                             what answers "was Slack sent, and about what" over e2e_rest too,
                             where this mock is in the wrong process
    "audit_log"           -- _audit_log_sync (from _workflow)
    "ai_review_executor"  -- _ai_review_executor (lazy import in _processing.py, ai-powered
                             branch). Read it through ``env.ai_reviews_taken_up(awaiting=...)``,
                             for the same reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from adcp.types import AccountReferenceById

from src.core.creative_agent_registry import (
    PUBLIC_DEFAULT_AGENT_URL,
    FormatFetchResult,
    ReferenceFormatsRegistry,
    _get_reference_formats,
)
from src.core.schemas import Format, FormatId, SyncCreativesRequest, SyncCreativesResponse
from tests.e2e.webhook_capture_service import PEER_BUILD_TOOL, PEER_PREVIEW_TOOL
from tests.factories.mint import mint
from tests.harness._base import IntegrationEnv
from tests.harness._realize import e2e_unsupported, realize_e2e
from tests.harness.media_buy_create import OMIT_ACCOUNT, OMIT_IDEMPOTENCY_KEY
from tests.harness.peer_agent import PeerCreativeAgentMixin
from tests.harness.transport import DeliverResult
from tests.helpers.creative_test_helpers import creative_payload
from tests.helpers.peer_creative_agent import single_preview_document

# Kwargs ``IntegrationEnv._run_a2a_handler`` consumes itself (identity mock, protocol-level
# push config, request model) rather than forwarding as skill parameters — see
# tests/harness/_base.py. They must reach it untouched.
_A2A_RESERVED_KWARGS = frozenset({"identity", "a2a_push_notification_config", "req"})


@dataclass(frozen=True)
class CommittedSyncEffects:
    """What a SEPARATE database connection can see for one tenant right now.

    Every field is read over a connection the sync's transaction does not own,
    which is the only way to tell "written and committed" apart from "written
    and about to be rolled back" -- the question a dry_run preview asks, and the
    question an escaping effect (Slack) asks about the rows it names.
    """

    #: workflow_steps rows, tenant-scoped through their Context. Sorted.
    workflow_step_ids: list[str]
    #: object_workflow_mapping.object_id for those steps. Sorted.
    workflow_object_ids: list[str]
    #: contexts rows for the tenant. Sorted.
    context_ids: list[str]
    #: creative_assignments rows for the tenant.
    assignment_count: int
    #: creatives sitting at ``pending_review`` for the tenant+principal. Sorted.
    creatives_awaiting_approval: list[str]


@dataclass(frozen=True)
class CommittedAIReview:
    """The AI-review VERDICT a SEPARATE connection can see for one creative.

    The sibling oracle to :class:`CommittedSyncEffects`, for the one effect the
    sync itself never writes: the background reviewer opens its own unit of
    work, commits a decision onto the creative, and only then fires Slack and
    the push webhook. Grading that a verdict was SUBMITTED says nothing about
    whether one was ever REACHED, so this reads the row the reviewer is
    supposed to have written -- ``verdict is None`` means it never did.
    """

    #: ``creatives.status`` for the row, or None when there is no row at all.
    status: str | None
    #: ``creatives.data["ai_review"]``, or None when no verdict was committed.
    verdict: dict[str, Any] | None
    #: ``creatives.data["ai_review_error"]``, or None when the reviewer did not
    #: fail. Present here because the reviewer's own ``except Exception`` handler
    #: REVERTS a committed verdict's status to ``pending_review`` and writes this
    #: key, WITHOUT clearing ``ai_review``. A test that checks only ``verdict``
    #: therefore reads "approved" from a review that actually blew up
    #: -- so the absence of this key is part of the grade.
    error: dict[str, Any] | None = None


#: ``event_type`` src/services/slack_notifier.py stamps on every Slack send
#: (``send_message`` -> ``WebhookDelivery(event_type=...)``).
_SLACK_EVENT_TYPE = "slack.notification"

#: The operator-visible Creative ID field ``notify_creative_pending`` renders into
#: the Block Kit payload. Reading the rendered field rather than a substring search
#: over the whole document keeps "Slack named THIS creative" from being satisfied by
#: a creative id that happens to appear in a review URL for a different one.
_SLACK_CREATIVE_ID_FIELD = re.compile(r"\*Creative ID:\*\n`([^`]+)`")

#: How long the e2e AI-review read-back waits for the background reviewer's verdict.
#: Matches ``await_ai_review``'s bound, which joins the same job in process.
_AI_REVIEW_WAIT_SECONDS = 30.0
_AI_REVIEW_POLL_SECONDS = 0.25

#: How often the e2e notification observer asks the capture service whether the held
#: Slack delivery has arrived. Short: the server is blocked on it for as long as this
#: takes, up to the service's ``HOLD_MAX_SECONDS``.
_HELD_DELIVERY_POLL_SECONDS = 0.05


def _observe_at_the_held_slack_delivery(self: CreativeSyncEnv) -> None:
    """The e2e leg of :meth:`CreativeSyncEnv.observe_effects_at_notification`.

    In process the observer runs inside ``_send_creative_notifications``. Over e2e_rest
    that function runs in the server, so the observation is taken where its effect
    lands instead: the tenant's Slack webhook is pointed at a fresh capture-service key
    whose next delivery is HELD, and a thread in this process waits for that delivery
    to arrive, reads what an independent connection to the server's database sees
    while the server is still blocked on the send, then releases it. The read is the
    same one the in-process observer takes (:meth:`_record_visibility_at_notification`),
    taken while the notification is in flight rather than at its entry -- nothing the
    sync writes runs between the two.

    No delivery means no observation, and the Thens refuse a missing one; the thread
    stops at teardown either way.
    """
    import threading

    from tests.e2e import _webhook_capture

    key, received = _webhook_capture.register_capture_key()
    self.configure_tenant_field("slack_webhook_url", _webhook_capture.delivery_url_for(key))
    _webhook_capture.hold_next_delivery(key)
    stop = threading.Event()

    def _observe() -> None:
        try:
            while not stop.is_set():
                if received:
                    self._record_visibility_at_notification()
                    return
                stop.wait(_HELD_DELIVERY_POLL_SECONDS)
        finally:
            _webhook_capture.release_held_delivery(key)

    observer = threading.Thread(target=_observe, name=f"slack-hold-{key}", daemon=True)
    observer.start()

    def _stop_observing() -> None:
        stop.set()
        observer.join()

    self._guard("slack_hold_observer", _stop_observing)


def _slack_creative_ids(payload: Any) -> list[str]:
    """Creative ids named in one Slack delivery payload's Block Kit fields."""
    if not isinstance(payload, dict):
        return []
    return [
        match.group(1)
        for block in payload.get("blocks", [])
        if isinstance(block, dict)
        for field in block.get("fields", [])
        if isinstance(field, dict)
        for match in [_SLACK_CREATIVE_ID_FIELD.search(str(field.get("text", "")))]
        if match is not None
    ]


def _e2e_slack_notified_creatives(self: CreativeSyncEnv) -> list[str]:
    """Slack sends the SERVER performed, read off ``webhook_deliveries``.

    The mock the in-process branch counts lives in THIS process; the sync runs in
    the Docker server, so counting it over e2e_rest would report zero on every
    branch — a negative invariant (INV-2, INV-6) would pass vacuously and a
    positive one (INV-3) would fail for the wrong reason.

    What the server leaves behind is a row. ``deliver_webhook_with_retry``
    (src/core/webhook_delivery.py) writes one ``webhook_deliveries`` record per
    send, BEFORE it dials, whenever the caller supplies a tenant and an event type
    — and the Slack sender supplies both. So the row exists whether or not
    ``hooks.slack.test`` was reachable, which is what makes this an observation of
    the SELLER's behaviour rather than of the test rig's network.

    ``expire_all`` first: the server committed through its own session, so rows it
    wrote after this session last read are otherwise served from the identity map.
    """
    from sqlalchemy import select

    from src.core.database.models import WebhookDeliveryRecord

    session = self.get_session()
    session.expire_all()
    rows = session.scalars(
        select(WebhookDeliveryRecord)
        .filter_by(tenant_id=self._tenant_id, event_type=_SLACK_EVENT_TYPE)
        .order_by(WebhookDeliveryRecord.created_at)
    ).all()
    return [creative_id for row in rows for creative_id in _slack_creative_ids(row.payload)]


def _e2e_ai_reviews_taken_up(self: CreativeSyncEnv, *, awaiting: list[str]) -> list[str]:
    """Creatives the SERVER's background reviewer took up, read off the verdict.

    Over e2e_rest the submit happens in the server process, where no mock records
    it. What the submitted job leaves behind is observable instead: it opens its
    own unit of work and commits EITHER ``data["ai_review"]`` (a verdict) OR
    ``data["ai_review_error"]`` (the review raised) onto the creative — both
    branches of ``_ai_review_creative``, src/admin/blueprints/creatives.py — so
    exactly one of them appears for every submission, whether or not the server
    holds a usable Gemini key.

    ``awaiting`` is the WAIT target, not the answer: the loop ends as soon as
    those ids all carry an outcome, and the answer is then every creative of this
    tenant and principal that carries one. A submission the scenario did not ask
    for therefore still shows up, which is what keeps "exactly these creatives
    were reviewed" a real comparison rather than a membership check.

    Absence after the bound means no job was ever submitted — which is the INV
    that fails, correctly, rather than a flake.
    """
    import time

    deadline = time.monotonic() + _AI_REVIEW_WAIT_SECONDS
    while True:
        taken_up = self._committed_ai_review_outcomes()
        if set(awaiting) <= set(taken_up) or time.monotonic() >= deadline:
            return taken_up
        time.sleep(_AI_REVIEW_POLL_SECONDS)


#: The ``data`` keys the background AI reviewer writes: its verdict, or the error it failed
#: with -- the two branches of ``_ai_review_creative`` (src/admin/blueprints/creatives.py).
AI_REVIEWER_DATA_KEYS = ("ai_review", "ai_review_error")


def _has_ai_review_outcome(row: Any) -> bool:
    """True when the background reviewer committed either outcome onto *row*."""
    data = getattr(row, "data", None)
    if not isinstance(data, dict):
        return False
    return any(data.get(key) is not None for key in AI_REVIEWER_DATA_KEYS)


#: Where the in-process stand-in for the operator agent says its canonical render lives.
_CANONICAL_PREVIEW_URL = "https://preview.example.com/canonical.html"


def _the_pinned_agent_answers_for_itself(self: CreativeSyncEnv, *args: Any, **kwargs: Any) -> None:
    """The e2e leg of a preview answer: nothing to arrange.

    The e2e server dials the pinned reference agent at CREATIVE_AGENT_URL
    (docker-compose.e2e.yml), and that agent answers a preview request for itself. The
    scenario's precondition -- that the agent can resolve the request at all -- is checked
    by the caller against the capture both legs serve, before either leg runs.
    """


def _the_tenants_hosted_agent_publishes_it(self: CreativeSyncEnv, format_id: str) -> str:
    """The e2e leg of :meth:`CreativeSyncEnv.catalog_agent_url`: the tenant's hosted agent.

    The e2e server answers the standard creative agent's CATALOG from the capture, but it
    PREVIEWS each static creative in one of its formats by dialling that agent at
    CREATIVE_AGENT_URL -- the pinned reference agent, which allows each client 60 MCP POSTs
    a minute (adcontextprotocol/adcp#8574) and refused UC-006 syncs with a 429 in box run
    innet_291026_0008. So on e2e the format is named at the creative agent the stack hosts
    for this tenant (:meth:`PeerCreativeAgentMixin.host_reference_format`): the capture's
    own entry under that agent's URL, which the server lists from that agent, looks up
    from it and previews against it, as it does for any agent a seller configured.
    """
    return self.host_reference_format(format_id)


def _the_hosted_agent_answers(self: CreativeSyncEnv, tool: str, document: dict[str, Any]) -> None:
    """The e2e leg of :meth:`CreativeSyncEnv.answer_creative_agent`: the hosted agent serves it.

    The server dials the agent a format's identity names, and on e2e that is the tenant's
    hosted agent (:func:`_the_tenants_hosted_agent_publishes_it`), so its answer to *tool*
    is the one production reads -- through its own MCP client and payload extraction.
    """
    self.serve_hosted_tool(tool, document)


def _the_hosted_agent_received(self: CreativeSyncEnv, tool: str) -> list[dict[str, Any]]:
    """The e2e leg of :meth:`CreativeSyncEnv.creative_agent_requests`: what the hosted agent recorded.

    The registry's recorders are in THIS process and the sync runs in the server, so they
    would report no request on every branch -- a "no agent request" invariant would pass
    vacuously. The agent's side of the wire is where the server's request lands, and the
    hosted agent records each tool call it answers.
    """
    return self.hosted_agent_calls(tool)


class HarnessCreativeAgentRegistry(ReferenceFormatsRegistry):
    """PRODUCTION's testing-mode registry, used as itself rather than stood in for.

    ``ReferenceFormatsRegistry`` is what ``get_creative_agent_registry()`` selects
    whenever ``reference_formats_only`` is set, which is every test deployment and the
    e2e server (docker-compose.e2e.yml sets ``ADCP_TESTING``). Its
    ``_operator_formats_override`` answers an OPERATOR reference from the committed
    capture (``tests/fixtures/creative_formats/reference_formats.json``) and
    never dials. So the in-process catalogue and the e2e server's catalogue are THE SAME
    BYTES, which is the property a ``MagicMock`` registry destroyed: in-process
    transports were served a stand-in while e2e_rest was served the capture, and the same
    scenario graded two different sellers.

    What this subclass adds is two recorders, two counters and one fault, and no
    catalogue of its own -- a scenario that needs a different catalogue gets one from a
    real peer agent (:meth:`CreativeSyncEnv.set_served_catalogue`), not from here:

    ``preview_creative`` / ``build_creative`` are OUTBOUND AdCP operations with NO
    capture. ``_get_reference_formats`` covers ``list_creative_formats`` only;
    ``scripts/refresh-reference-formats.py`` captures the catalogue and nothing else, so
    production has no no-dial answer for these two and the un-overridden inherited methods
    reach ``call_operator_mcp_tool`` -- measured, this worktree, ADCP_TESTING=true:
    ``preview_creative`` and ``build_creative`` both dial
    ``https://creative.adcontextprotocol.org``. That is **salesagent-1bc7l**, which owns
    the gap and the remedy. Until a capture exists these two are recorded here, which is
    also what the ~40 call sites asserting ``registry.build_creative.call_args`` read.

    ``catalogue_reads`` / ``format_fetches`` count the two reads -- the whole catalogue,
    and one format -- and ``fail_format_fetch_with`` makes ``get_format`` raise. Neither
    is a stand-in: production's method still runs (the counter increments and delegates
    to ``super()``), the fault answers nothing and records nothing, and no Format can be
    invented by either. They exist because an obligation about WHETHER a read happened
    (once per sync, never for an adapter format) and one about a TRANSIENT agent failure
    both need an observation point, and the only one a ``MagicMock`` offered came
    attached to a mock's answers.

    Everything else is production: ``list_all_formats``, ``list_all_formats_with_errors``,
    ``get_format``, ``get_formats_for_agent`` and ``search_formats`` all consult production's
    one override hook for the operator agent and dial every agent the tenant configured, so
    a format resolves here exactly when one of the seller's agents serves it and is
    ``None`` exactly when none does. An UNCONFIGURED attribute raises
    ``AttributeError`` instead of answering a truthy child mock -- which is the second
    defect a ``MagicMock`` caused: ``canonical_kind_for``'s first step read
    ``spec.canonical.kind`` off a mock and got the string
    ``"<MagicMock name='mock.canonical.kind' id=...>"`` through production code.
    """

    def __init__(self) -> None:
        super().__init__()
        # The two with no capture. Defaults are the previous stand-in's defaults
        # verbatim, so this change moves the CATALOGUE and nothing else: an empty
        # preview answer is the no-previews case _processing.py already guards for.
        self.preview_creative = AsyncMock(return_value={})
        self.build_creative = AsyncMock(return_value={})
        #: Reads of the WHOLE catalogue, through production's own
        #: ``list_all_formats``/``list_all_formats_with_errors``. ``_sync.py:151``
        #: performs exactly one per request, and "once per sync, not once per
        #: creative" is the obligation UC-006-MAIN-MCP-09 states, so a count is the
        #: whole observation. It is an INTEGER on a real registry rather than a
        #: ``call_count`` on a stand-in: a Mock's counter came with a Mock's answers.
        self.catalogue_reads = 0
        #: Reads of ONE format, through production's own ``get_format`` -- which
        #: ``_validation.py`` performs once per creative and SKIPS entirely for an
        #: adapter (non-HTTP) agent_url. Counted separately because that is a
        #: different obligation from the catalogue fetch above.
        self.format_fetches = 0
        #: What :meth:`get_format` raises instead of answering. The registry types
        #: every network failure (429 -> AdCPRateLimitError, 5xx/timeout/connect ->
        #: AdCPServiceUnavailableError) and a scenario whose subject is a transient
        #: agent failure needs one raised from the real method, so that
        #: ``fetch_format_spec`` -> ``_validation.py`` -> the per-item handler see
        #: exactly the exception production hands them. A FAULT, not a stand-in: it
        #: answers nothing, records nothing, and cannot invent a Format.
        self._format_fetch_error: BaseException | None = None

    async def list_all_formats_with_errors(self, *args: Any, **kwargs: Any) -> FormatFetchResult:
        """Production's catalogue read, counted. ``list_all_formats`` delegates here."""
        self.catalogue_reads += 1
        return await super().list_all_formats_with_errors(*args, **kwargs)

    async def get_format(self, *args: Any, **kwargs: Any) -> Format | None:
        """Production's single-format read, counted -- or the configured fault."""
        self.format_fetches += 1
        if self._format_fetch_error is not None:
            raise self._format_fetch_error
        return await super().get_format(*args, **kwargs)

    def fail_format_fetch_with(self, exc: BaseException) -> None:
        """Make every subsequent :meth:`get_format` raise *exc*."""
        self._format_fetch_error = exc


def creative_fingerprint(creative: Any) -> tuple[str, str]:
    """The per-row state comparison every creative-effect oracle uses.

    ONE definition, because two callers compare the same thing for two different
    reasons: the dry_run oracle compares the tenant's library before and after a
    preview, and the post-commit oracle compares what an escaping effect could
    SEE against what the sync actually committed. ``status`` alone is too weak
    for either -- the update branch re-writes ``data`` while leaving ``status`` at
    ``pending_review``, so a status-only fingerprint reads a stale row and a
    freshly-updated one as identical.
    """
    return (creative.status, repr(creative.data))


class CreativeSyncEnv(PeerCreativeAgentMixin, IntegrationEnv):
    """Integration test environment for _sync_creatives_impl.

    Only mocks external services (creative agent registry, async runner,
    notifications, audit logging). Everything else is real:
    - Real get_db_session -> real DB queries
    - Real CreativeRepository -> real DB writes
    - Real validation/processing -> real business logic
    """

    EXTERNAL_PATCHES = {
        # The registry OBJECT is production's own ReferenceFormatsRegistry (see
        # HarnessCreativeAgentRegistry); what this patch replaces is only the
        # process-wide memoized instance, so each env gets a fresh format cache and
        # fresh recorders rather than sharing one across scenarios.
        #
        # ``run_async`` USED TO BE PATCHED HERE and was the second half of the parity
        # break. It patched the sync bridge _sync.py calls the registry THROUGH, with
        # ``side_effect = lambda coro: []`` -- so ``list_all_formats`` was discarded and
        # the in-process catalogue was EMPTY no matter which registry was installed
        # (``_sync.py:151`` read ``len=0``), while the e2e server fetched 71. With it
        # gone the real bridge runs, which is what _processing.py already used. Its
        # blast radius was exactly one call: ``run_async_in_sync_context`` has a single
        # call site in _sync.py.
        "registry": "src.core.creative_agent_registry.get_creative_agent_registry",
        "send_notifications": "src.core.tools.creatives._sync._send_creative_notifications",
        # The Slack sender _send_creative_notifications reaches (a call-time import from
        # src.services.slack_notifier). Patching it lets the real notification function
        # run -- its "only require-human, only with a webhook" guard is what BR-RULE-037
        # INV-2/INV-6 grade -- while nothing is posted anywhere.
        "slack_notifier": "src.services.slack_notifier.get_slack_notifier",
        "audit_log": "src.core.tools.creatives._sync._audit_log_sync",
        # The ai-powered branch of _processing.py hands a job to a real
        # ThreadPoolExecutor that opens its OWN AdminCreativeUoW, COMMITS a review
        # verdict, and then fires Slack + the push webhook
        # (src/admin/blueprints/creatives.py). That is an effect which escapes the
        # sync transaction entirely, so it belongs in this env's "only mocks
        # external services" set alongside send_notifications — and mocking it is
        # what makes "no AI-review side effect" an observable rather than a race
        # against a background thread. Per-test patches of the same target (e.g.
        # tests/integration/test_creative_sync_transport.py TestAIReviewTrigger)
        # nest inside this one and still win.
        "ai_review_executor": "src.admin.blueprints.creatives._ai_review_executor",
    }
    #: The EXTERNAL_PATCHES key above, bound to a name so the mock lookup and the base's
    #: ``patch:<key>`` cleanup label cannot drift from it: a rename that reached only one
    #: of them would turn "stop the executor patch" into a silent no-op.
    _AI_REVIEW_PATCH_NAME = "ai_review_executor"
    #: The agent the capture was taken FROM, which is the only url a format reference can
    #: name and resolve on all four transports. Two reasons, both measured in this
    #: worktree at ADCP_TESTING=true:
    #:
    #: 1. ``is_operator_agent`` is True for it, so ``_validation.py``'s
    #:    ``get_format(..., provenance=CounterpartyUrl(...))`` reaches the capture
    #:    override instead of the egress seam. With the previous value
    #:    (``https://creative.test.example.com``) the same call DIALLED
    #:    ``https://creative.test.example.com/mcp`` -- which is why the registry had to
    #:    be mocked at all.
    #: 2. ``find_format`` matches on the PAIR ``(canonical agent_url, id)``, and every
    #:    captured format carries ``https://creative.adcontextprotocol.org``. A creative
    #:    naming any other url resolves to None there, so ``identity_spec`` is None and
    #:    step 1 of the projection order (the format's own ``canonical`` declaration) is
    #:    never reached. ``tests/factories/creative.py`` already states this rule for
    #:    seeded rows ("a fixture names THIS url and lets production decide the
    #:    transport"); the harness was the half that had not followed.
    DEFAULT_AGENT_URL = PUBLIC_DEFAULT_AGENT_URL
    REST_ENDPOINT = "/api/v1/creatives/sync"

    #: The identities :meth:`set_served_catalogue` seeded, re-homed at the peer, or None.
    _seeded_format_ids: list[FormatId] | None = None

    #: {creative_id: fingerprint-or-None} as seen by an INDEPENDENT connection at
    #: the instant the sync handed that creative to the AI-review executor. The
    #: job the sync submits opens its own session, so this is exactly what that
    #: job would read -- and ``None`` means it would find no row at all.
    ai_review_commit_observations: dict[str, tuple[str, str] | None]

    #: ``(step_ids, mapped object_ids)`` an INDEPENDENT connection can see at the
    #: instant the sync fired its Slack notification, or ``None`` while no
    #: observer is installed. Slack is an escaping effect: it names workflow
    #: steps a human is expected to open, so what a SEPARATE connection can see
    #: at that instant is exactly what the person following the link will find.
    workflow_rows_at_notification: tuple[list[str], list[str]] | None

    #: ``creative_assignments`` rows committed for the tenant at that same
    #: instant. Ordering between the notification and the assignment stage is
    #: only observable as a count taken AT the notification, never after it.
    assignment_count_at_notification: int | None

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults for external mocks."""
        #: {format_id: declaration} for every format this env has been told to serve, so a
        #: step that seeds a row in one can reach the declaration the sync path resolves
        #: against (``published_format_spec``). Reset per env, with the mocks.
        self._served_formats: dict[str, Format] = {}
        # The registry is PRODUCTION's testing-mode registry, serving the committed
        # capture through its own override hook -- the same bytes the e2e server reads.
        # See HarnessCreativeAgentRegistry for what it adds (two recorders) and why.
        self.mock["registry"].return_value = HarnessCreativeAgentRegistry()

        # Notifications: the REAL _send_creative_notifications runs behind this mock, so
        # the mock still records that the notification step was entered (INV-3 asserts
        # its arguments) while the function's own guard decides whether Slack is
        # reached -- readable off mock["slack_notifier"]. A bare no-op here made
        # "no Slack sent without a webhook" (INV-6) ungradeable: the guard lives inside
        # the function the no-op replaced. The ordering observer is NOT installed here --
        # it is a side_effect, and installing it by default would make every existing
        # "was Slack called" scenario pay for two extra pooled-connection reads.
        # observe_effects_at_notification() opts a scenario in.
        from src.core.tools.creatives._workflow import _send_creative_notifications

        self.mock["send_notifications"].side_effect = _send_creative_notifications
        self.workflow_rows_at_notification = None
        self.assignment_count_at_notification = None

        # Audit log: no-op
        self.mock["audit_log"].return_value = None

        # AI review executor: accept the submit, RECORD what a separate database
        # connection can see at that instant, and hand back an inert future.
        # _processing.py stores the returned future in _ai_review_tasks; nothing
        # in the sync path reads it back.
        self.ai_review_commit_observations = {}
        self.mock[self._AI_REVIEW_PATCH_NAME].submit.side_effect = self._observe_at_ai_review_submit

        # No Gemini key is configured here: the generative build reads the TENANT's AI
        # configuration, and a tenant row carries none until a scenario sets one
        # (set_gemini_api_key).

    def _observe_at_ai_review_submit(self, *args: Any, **kwargs: Any) -> MagicMock:
        """Stand in for ``_ai_review_executor.submit`` and record DB visibility.

        The submitted job opens its OWN session (``AdminCreativeUoW`` in
        src/admin/blueprints/creatives.py), so the only state it can ever read is
        COMMITTED state. Reading that here, from a connection the sync's
        transaction does not own, is what turns "the effect was deferred until
        its transaction committed" into an observable rather than an inference:
        a flush leaves the row invisible to this read, a commit does not.
        """
        self.ai_review_commit_observations[kwargs["creative_id"]] = self._committed_creative_fingerprint(
            creative_id=kwargs["creative_id"],
            tenant_id=kwargs["tenant_id"],
            principal_id=kwargs["principal_name"],
        )
        return MagicMock()

    @realize_e2e(
        e2e_unsupported(
            "each observation is taken INSIDE the submit, by this env's stand-in for the AI-review "
            "executor, at the instant production hands the job over. Under e2e_rest the submit "
            "happens in the server process, where no stand-in runs. The job's write-back does keep "
            "what it read (it writes the whole data document back with its verdict added), but that "
            "read comes only after the review itself runs, so a job submitted from inside the open "
            "transaction almost always reads after the sync has committed anyway: the e2e answer "
            "would catch an early hand-off only when the job's read wins that race, and would pass "
            "the regression this scenario names otherwise. Grading it e2e needs a read taken at the "
            "submit inside the server, which is its own build"
        )
    )
    def ai_review_observations(self) -> dict[str, tuple[str, str] | None]:
        """{creative_id: fingerprint-or-None} an independent connection saw at each AI-review submit.

        See :attr:`ai_review_commit_observations`. An accessor rather than the attribute,
        so the one Then that reads it declares where it cannot be graded instead of reading
        an empty dict over e2e_rest.
        """
        return dict(self.ai_review_commit_observations)

    def _committed_creative_fingerprint(
        self, *, creative_id: str, tenant_id: str, principal_id: str
    ) -> tuple[str, str] | None:
        """The creative as a SEPARATE connection sees it, or None if not committed.

        Deliberately not ``get_db_session()``: that returns the thread's SCOPED
        session, which inside a request IS the transaction under test -- it would
        see the caller's own uncommitted writes and grade nothing. A second pooled
        connection (:meth:`_independent_session`) is the same isolation the
        background job gets.
        """
        from src.core.database.repositories.creative import CreativeRepository

        with self._independent_session() as independent_session:
            row = CreativeRepository(independent_session, tenant_id).get_by_id(creative_id, principal_id)
            return None if row is None else creative_fingerprint(row)

    def committed_creative_fingerprints(self) -> dict[str, tuple[str, str]]:
        """{creative_id: fingerprint} for every creative of this env's principal, as committed now.

        The library-wide twin of :meth:`_committed_creative_fingerprint`, on the same
        independent connection, so a step comparing the library before and after a
        request reads both sides from the database the request wrote, on every transport.
        """
        from src.core.database.repositories.creative import CreativeRepository

        with self._independent_session() as session:
            return {
                row.creative_id: creative_fingerprint(row)
                for row in CreativeRepository(session, self._tenant_id).list_by_principal(self._principal_id)
            }

    # --- the workflow-step / assignment oracle (GH #2002) ---
    #
    # Same independent-connection SHAPE as _committed_creative_fingerprint above,
    # over the rows _create_sync_workflow_steps writes. It exists because nothing
    # in this env could observe those rows: get_workflow_steps() reads the SCOPED
    # session, which inside a request IS the transaction under test -- it sees the
    # request's own un-committed writes, so it cannot tell "written and committed"
    # from "written and about to be rolled back", which is the entire question a
    # preview asks.

    def _independent_session(self) -> Any:
        """A Session on its OWN pooled connection -- never the scoped one.

        Bound to the engine THIS env's session writes through, which is the database the
        request under test commits to on every transport: production's engine in process,
        and the live server's database over e2e_rest (``IntegrationEnv`` binds the env's
        session there, so the factories' rows are the server's rows). Binding to
        ``get_engine()`` instead made the over-e2e answer depend on whether this process's
        cached settings had followed the BDD route's repoint of ``DATABASE_URL``.
        """
        from sqlalchemy.orm import Session as SQLAlchemySession

        return SQLAlchemySession(bind=self.get_session().get_bind())

    def _committed_workflow_rows(self, *, tenant_id: str) -> tuple[list[str], list[str]]:
        """``(step_ids, mapped object_ids)`` COMMITTED for *tenant_id*, both sorted.

        WorkflowStep carries no tenant_id column, so tenant scoping goes through
        its Context relationship -- the same join BaseTestEnv.get_workflow_steps
        uses, moved onto an independent connection.
        """
        from sqlalchemy import select

        from src.core.database.models import Context, ObjectWorkflowMapping, WorkflowStep

        with self._independent_session() as session:
            step_ids = sorted(
                session.scalars(
                    select(WorkflowStep.step_id).join(WorkflowStep.context).where(Context.tenant_id == tenant_id)
                ).all()
            )
            object_ids = (
                sorted(
                    session.scalars(
                        select(ObjectWorkflowMapping.object_id).where(ObjectWorkflowMapping.step_id.in_(step_ids))
                    ).all()
                )
                if step_ids
                else []
            )
            return step_ids, object_ids

    def _committed_context_ids(self, *, tenant_id: str) -> list[str]:
        """Context rows COMMITTED for *tenant_id*, sorted.

        The third row the write path creates. Graded separately from the steps
        because a rollback that reached the steps but left the context behind
        would still be a preview that persisted something.
        """
        from sqlalchemy import select

        from src.core.database.models import Context

        with self._independent_session() as session:
            return sorted(session.scalars(select(Context.context_id).where(Context.tenant_id == tenant_id)).all())

    def _committed_assignment_count(self, *, tenant_id: str) -> int:
        """creative_assignments rows COMMITTED for *tenant_id*."""
        from sqlalchemy import func, select

        from src.core.database.models import CreativeAssignment

        with self._independent_session() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(CreativeAssignment)
                    .where(CreativeAssignment.tenant_id == tenant_id)
                )
                or 0
            )

    def _committed_creatives_awaiting_approval(self, *, tenant_id: str, principal_id: str) -> list[str]:
        """creative_ids COMMITTED at ``pending_review`` for *tenant_id*, sorted."""
        from sqlalchemy import select

        from src.core.database.models import Creative

        with self._independent_session() as session:
            return sorted(
                session.scalars(
                    select(Creative.creative_id).where(
                        Creative.tenant_id == tenant_id,
                        Creative.principal_id == principal_id,
                        Creative.status == "pending_review",
                    )
                ).all()
            )

    # --- the AI-review EFFECT oracle (GH #1972) ---
    #
    # Everything above observes what the SYNC committed. The three members below
    # observe what the background REVIEWER committed, which is a different
    # question and the one no scenario has ever asked: with the executor mocked
    # (EXTERNAL_PATCHES, above) the only observable is "submit() was called",
    # which is true whether or not the submitted job ever ran a line of its body.

    def run_ai_review_for_real(self) -> None:
        """Drop the ``_ai_review_executor`` mock so the submitted job really RUNS.

        The mock is what makes "no AI-review side effect" observable for every
        other scenario, and it is exactly what makes the review's own effects
        UNobservable: nothing behind ``submit`` executes. Stopping the patch puts
        the production executor back in the path, so the job is dispatched the way
        production dispatches it — the precondition for grading a verdict instead
        of a submit. Pair with :meth:`await_ai_review`, which is what makes the
        background completion a wait rather than a race.

        Stopping goes through ``BaseTestEnv``'s cleanup registry rather than a list
        of live patcher objects: since #1858 the base no longer keeps one — it
        registers ``patcher.stop`` under ``patch:<EXTERNAL_PATCHES key>`` and
        releases them LIFO. The entry is REMOVED as well as invoked, because a
        second ``stop()`` at teardown raises on an already-stopped patcher.
        """
        label = f"patch:{self._AI_REVIEW_PATCH_NAME}"
        for entry in list(self._enter_cleanups):
            if entry[0] == label:
                _label, stop = entry
                stop()
                self._enter_cleanups.remove(entry)
                self.mock.pop(self._AI_REVIEW_PATCH_NAME, None)
                return
        raise AssertionError(f"{label} is not registered — the executor is not patched, nothing to restore")

    @staticmethod
    def await_ai_review(creative_id: str, timeout: float = 30.0) -> None:
        """Block until every AI-review job submitted for *creative_id* finished.

        ``_defer_ai_review`` registers each job's Future in the module-level
        ``_ai_review_tasks``; joining on those Futures is what turns a background
        effect into something an assertion can read without sleeping on a timer.
        Returning does NOT imply the job did anything — a worker that finished
        without running the body finishes just as fast, which is the distinction
        :meth:`committed_ai_review` exists to make.
        """
        from concurrent.futures import wait

        from src.admin.blueprints.creatives import _ai_review_lock, _ai_review_tasks

        with _ai_review_lock:
            futures = [task["future"] for task in _ai_review_tasks.values() if task["creative_id"] == creative_id]
        assert futures, f"no AI review job was ever submitted for {creative_id}"
        done, pending = wait(futures, timeout=timeout)
        assert not pending, f"AI review job for {creative_id} did not finish within {timeout}s"
        for future in done:
            future.result()  # re-raise anything the worker thread swallowed

    @property
    @realize_e2e(_e2e_slack_notified_creatives)
    def slack_notified_creatives(self) -> list[str]:
        """The creatives this sync dialled Slack about, oldest first.

        The observable BR-RULE-037 INV-3/INV-6 grade is the SENDER, not the
        notification step: production enters ``_send_creative_notifications`` for
        every creative that needs approval and decides inside it — require-human
        only, webhook configured only — whether Slack is reached. In process the
        sender is the patched ``get_slack_notifier``; over e2e it runs in the
        Docker server, where :func:`_e2e_slack_notified_creatives` reads the row
        the sender leaves behind instead.

        One accessor so the four steps that ask "was Slack sent, and about what"
        cannot answer the question three different ways — two of them used to read
        the step's call count and two the sender's, and only the sender's is the
        invariant.
        """
        calls = self.mock["slack_notifier"].return_value.notify_creative_pending.call_args_list
        return [call.kwargs["creative_id"] for call in calls]

    @realize_e2e(_e2e_ai_reviews_taken_up)
    def ai_reviews_taken_up(self, *, awaiting: list[str]) -> list[str]:
        """Every creative the background AI reviewer took up, sorted.

        In process the executor itself is mocked (EXTERNAL_PATCHES
        ``ai_review_executor``), so the submit IS the observable and "no AI review
        happened" is a value comparison rather than a race with a thread —
        ``awaiting`` is unused because a mocked submit has already happened by the
        time the response is back. Over e2e the real executor runs inside the
        server, where the submit is invisible and the verdict it commits is what
        remains; there ``awaiting`` bounds the wait
        (:func:`_e2e_ai_reviews_taken_up`).

        Sorted on both branches so the two are comparable against one expectation.
        """
        return sorted(
            {call.kwargs["creative_id"] for call in self.mock[self._AI_REVIEW_PATCH_NAME].submit.call_args_list}
        )

    def _committed_ai_review_outcomes(self) -> list[str]:
        """Creatives of this tenant+principal carrying either AI-review outcome.

        Read over an INDEPENDENT connection for the same reason
        :meth:`committed_ai_review` is: the reviewer writes through its own unit of
        work, so only a connection this test's session does not own can tell a
        committed verdict from one that was never written.
        """
        from src.core.database.repositories.creative import CreativeRepository

        with self._independent_session() as session:
            rows = CreativeRepository(session, self._tenant_id).list_by_principal(self._principal_id)
            return sorted(row.creative_id for row in rows if _has_ai_review_outcome(row))

    def committed_ai_review(self, creative_id: str) -> CommittedAIReview:
        """The verdict an INDEPENDENT connection can see for *creative_id*.

        Same isolation as :meth:`_committed_creative_fingerprint` and for the same
        reason: the reviewer writes through its OWN unit of work, so only a
        connection this test's session does not own can tell a committed verdict
        from one that was never written.
        """
        from src.core.database.repositories.creative import CreativeRepository

        with self._independent_session() as session:
            row = CreativeRepository(session, self._tenant_id).get_by_id(creative_id, self._principal_id)
            if row is None:
                return CommittedAIReview(status=None, verdict=None)
            data = row.data if isinstance(row.data, dict) else {}
            return CommittedAIReview(
                status=row.status,
                verdict=data.get("ai_review"),
                error=data.get("ai_review_error"),
            )

    @realize_e2e(_observe_at_the_held_slack_delivery)
    def observe_effects_at_notification(self) -> None:
        """Install the Slack-notification observer for this scenario.

        Ordering between an escaping effect and the writes it refers to is only
        observable AT the effect: after the request both are done and every
        ordering looks alike. The observer records what a separate connection
        could see the moment ``_send_creative_notifications`` was entered; over
        e2e_rest, while the server's Slack delivery is held in flight
        (:func:`_observe_at_the_held_slack_delivery`).
        """
        self.mock["send_notifications"].side_effect = self._observe_at_notification

    def _record_visibility_at_notification(self) -> None:
        """What an independent connection sees committed now: the workflow rows and the assignments."""
        self.workflow_rows_at_notification = self._committed_workflow_rows(tenant_id=self._tenant_id)
        self.assignment_count_at_notification = self._committed_assignment_count(tenant_id=self._tenant_id)

    def _observe_at_notification(self, *args: Any, **kwargs: Any) -> None:
        """Record DB visibility at the notification, then let the notification run.

        The two reads happen BEFORE the delegation, which is the whole point: the
        ordering between an escaping effect and the writes it refers to is only
        observable AT the effect.

        Then it calls the REAL ``_send_creative_notifications``, which is what
        ``_configure_mocks`` installs by default. It used to fall off the end
        instead, which silently disabled the Slack sender for exactly the
        scenarios that install this observer — so their own stated non-vacuity
        control ("a Slack notification should be sent immediately") could only
        ever be graded on the notification STEP having been entered, never on a
        send. Delegating keeps the recording and puts the sender back.
        """
        from src.core.tools.creatives._workflow import _send_creative_notifications

        self._record_visibility_at_notification()
        _send_creative_notifications(*args, **kwargs)

    def committed_sync_effects(self) -> CommittedSyncEffects:
        """Everything this tenant has COMMITTED right now, in ONE snapshot.

        One accessor rather than four: the scenarios compare these fields
        against each other (mappings against persisted creatives, the
        notification-time read against the final one), and four independently
        timed reads could not be compared.

        The same read on every transport. The second pooled connection is on the
        engine this env's session writes through (:meth:`_independent_session`),
        which over e2e_rest is the live server's database -- the one the server
        committed to. Read after the request has returned, so what it sees is
        exactly what the server left committed.
        """
        step_ids, object_ids = self._committed_workflow_rows(tenant_id=self._tenant_id)
        return CommittedSyncEffects(
            workflow_step_ids=step_ids,
            workflow_object_ids=object_ids,
            context_ids=self._committed_context_ids(tenant_id=self._tenant_id),
            assignment_count=self._committed_assignment_count(tenant_id=self._tenant_id),
            creatives_awaiting_approval=self._committed_creatives_awaiting_approval(
                tenant_id=self._tenant_id, principal_id=self._principal_id
            ),
        )

    def configure_agent_served_creative(self, *, generative: bool, format_id: str) -> dict[str, str]:
        """Configure a format the creative agent actually serves, generative or not.

        ONE seam for both cells of the dry_run out-of-transaction outline. The format is
        named at :meth:`catalog_agent_url`, so on e2e_rest it is the tenant's hosted agent
        that lists it, answers its preview or build, and records what the seller asked of
        it (:meth:`creative_agent_requests`); in process it is the standard agent this
        env's registry answers from the capture, with the two outbound calls recorded.

        ``generative`` is the scenario's DECLARATION and is checked against the capture
        rather than imposed on it: ``served_format`` refuses when the two disagree. The
        catalogue is not narrowed to this one format -- the capture carries every format and so
        does the server, so narrowing it would be the divergence this method exists to
        avoid.
        """
        if generative:
            return self.setup_generative_build(format_id=format_id)

        self.served_format(format_id, generative=False)
        agent_url = self.catalog_agent_url(format_id)
        self.answer_creative_agent(PEER_PREVIEW_TOOL, single_preview_document("agent-served"))
        return {"agent_url": agent_url, "id": format_id}

    def configure_agent_no_preview(self, *, format_id: str) -> dict[str, str]:
        """A served static format whose preview request the agent answers with nothing.

        With no image or video asset on the creative there is then no media_url to fall
        back on, which production classifies as CREATIVE_REJECTED with ``reasons``
        (src/core/tools/creatives/_processing.py). Over e2e_rest the agent answering is
        the tenant's hosted agent, which serves whatever answer it is given -- the same
        empty document the in-process registry hands back.
        """
        fmt = self.configure_agent_served_creative(generative=False, format_id=format_id)
        self.answer_creative_agent(PEER_PREVIEW_TOOL, {})
        return fmt

    @realize_e2e(_the_hosted_agent_answers)
    def answer_creative_agent(self, tool: str, document: dict[str, Any]) -> None:
        """The creative agent answers every *tool* request with *document* from now on.

        *tool* is ``preview_creative`` or ``build_creative``: the two outbound operations
        with no capture (see :class:`HarnessCreativeAgentRegistry`). In process the
        registry's recorder for that operation returns *document*; over e2e_rest the
        tenant's hosted agent serves it (:func:`_the_hosted_agent_answers`). One document,
        both legs, so a scenario's agent says the same thing on every transport.
        """
        setattr(self.mock["registry"].return_value, tool, AsyncMock(return_value=document))

    @realize_e2e(_the_hosted_agent_received)
    def creative_agent_requests(self, tool: str) -> list[dict[str, Any]]:
        """The arguments of every *tool* request the seller made of the creative agent, oldest first.

        *tool* is ``preview_creative`` or ``build_creative``. In process these are the
        keyword arguments production passed the registry's recorder, minus ``agent_url``
        (where the request went, which the agent does not receive as an argument); over
        e2e_rest they are the tool-call arguments the tenant's hosted agent recorded
        (:func:`_the_hosted_agent_received`). ``registry.build_creative`` forwards
        ``message`` to the agent verbatim, so a build's ``message`` reads the same on both
        legs; the other keys are each leg's own spelling and no step compares them.
        """
        recorder = getattr(self.mock["registry"].return_value, tool)
        return [{k: v for k, v in call.kwargs.items() if k != "agent_url"} for call in recorder.call_args_list]

    def operator_agent_previews_canonical_identity(self, kind: str, format_option_ref: dict[str, Any] | None) -> None:
        """The operator's creative agent renders a creative identified by *kind*, on every transport.

        THE PRECONDITION IS SHARED and checked here, against the capture both legs serve:
        the operator's agent declares exactly ONE format of *kind*.
        creative/preview-creative-request.json (v3.2.7) lets a preview omit
        ``target_capability_id`` and says the agent "MAY resolve the renderer only if
        exactly one advertised preview capability satisfies the manifest's canonical
        declaration", so that is the condition under which the pinned agent on e2e can
        answer at all -- and the condition under which production asks.

        The in-process leg then answers the way that contract reads (see
        :meth:`_answer_canonical_previews`); the e2e leg sends the request to the pinned
        reference agent, which answers for itself.
        """
        declaring = [fmt for fmt in _get_reference_formats() if fmt.canonical and str(fmt.canonical.kind) == kind]
        assert len(declaring) == 1, (
            f"the pinned capture declares {len(declaring)} formats of canonical kind {kind!r} "
            f"({sorted(fmt.format_id.id for fmt in declaring)}); an untargeted canonical preview resolves "
            "only when exactly one does, so this scenario would grade the agent's refusal instead"
        )
        self._answer_canonical_previews(kind, format_option_ref)

    @realize_e2e(_the_pinned_agent_answers_for_itself)
    def _answer_canonical_previews(self, kind: str, format_option_ref: dict[str, Any] | None) -> None:
        """Answer a preview request with a render ONLY when it routes the canonical identity.

        Not a blanket answer, because a recorder that renders anything grades nothing: the
        request has to carry no ``format_id`` at either level (deprecated in 3.2, and a
        canonical identity names none) and the manifest has to name *kind* and the
        creative's ``format_option_ref`` (``None`` when it has none) -- the identity
        creative/preview-creative-request.json (v3.2.7) puts in
        ``creative_manifest.format_kind`` and ``creative_manifest.format_option_ref``.
        Anything else is answered the way the pinned agent answers an unresolvable route:
        an ``errors`` payload with no previews.
        """

        async def _preview(*, agent_url: str, format_id: str | None, creative_manifest: dict[str, Any]) -> dict:
            routes_canonically = (
                format_id is None
                and "format_id" not in creative_manifest
                and creative_manifest.get("format_kind") == kind
                and creative_manifest.get("format_option_ref") == format_option_ref
            )
            if not routes_canonically:
                return {"errors": [{"code": "FORMAT_NOT_SUPPORTED"}]}
            return {
                "previews": [
                    {
                        "preview_id": "prev_canonical",
                        "renders": [
                            {"render_id": "r_canonical", "role": "primary", "preview_url": _CANONICAL_PREVIEW_URL}
                        ],
                        "input": {"name": "Default preview"},
                    }
                ]
            }

        self.mock["registry"].return_value.preview_creative = AsyncMock(side_effect=_preview)

    @realize_e2e(_the_tenants_hosted_agent_publishes_it)
    def catalog_agent_url(self, format_id: str) -> str:
        """The creative agent a scenario names the reference capture's *format_id* at.

        Either way the format is the capture's own declaration, published by a creative
        agent this seller federates, and its preview is answered by something other than
        the rate-limited reference agent.

        In process it is the standard creative agent the capture was taken from
        (:attr:`DEFAULT_AGENT_URL`): this env's registry answers it from the capture and
        records its previews, so nothing is dialled. The in-process peer agent is not used
        here because it is addressed by IP (``127.0.0.1``), and a host with no letter case
        cannot grade BR-RULE-039 INV-1 ("agent_url host case does not split format
        identity"), which re-spells this URL's host. On e2e it is the tenant's hosted agent;
        see :func:`_the_tenants_hosted_agent_publishes_it`.
        """
        return self.DEFAULT_AGENT_URL

    def served_format(self, format_id: str, *, generative: bool | None = None) -> Format:
        """The CAPTURE's own declaration for *format_id*. A FILTER, never a fabrication.

        THREE STAND-INS HAVE STOOD HERE AND EACH ONE FABRICATED SOMETHING THE SELLER
        THEN READ. First a ``MagicMock``, whose ``spec.canonical.kind`` answered a fresh
        child mock: ``canonical_kind_for``'s first normative step read it and got the
        string ``"<MagicMock name='mock.canonical.kind' id=...>"``, which the pinned
        vocabulary refused, so every scenario configuring a served format recorded
        ``action='failed'``. Then a hand-built ``Format`` with the kind and the
        output-format list as PARAMETERS, which fixed the shape and kept the invention:
        a declaration no agent serves, at an agent url no capture carries, so
        ``find_format`` matched nothing anyway.

        Now it is a lookup. The capture IS the catalogue -- read through
        ``_get_reference_formats``, the identical function
        ``ReferenceFormatsRegistry._operator_formats_override`` serves and the e2e server
        reads -- so a format is served here exactly when the reference agent serves it,
        with the agent's own ``canonical.kind`` and the agent's own
        ``output_format_ids``. There is nothing left for a harness to get wrong.

        ``generative`` is the scenario's declaration, CHECKED rather than imposed: the
        capture decides (``format_resolver.is_generative``) and a disagreement is a
        refusal, because a scenario that asked for a generative format and silently got a
        static one grades the wrong branch. Pass ``None`` not to assert either way.

        Raises ``KeyError`` for an id the capture does not carry, naming the near misses.
        That refusal is PRODUCTION'S refusal brought forward: ``get_format`` answers
        ``None`` for the same id and ``_validation.py`` raises
        ``AdCPFormatNotFoundError``, which is exactly what e2e_rest already did while
        in-process transports were handed a stand-in. An invented id is a scenario to
        fix (salesagent-jrr9y owns that population), not a format to manufacture.
        """
        capture = _get_reference_formats()
        served = next((fmt for fmt in capture if fmt.format_id.id == format_id), None)
        if served is None:
            near = sorted(fmt.format_id.id for fmt in capture if format_id.split("_")[0] in fmt.format_id.id)
            raise KeyError(
                f"{format_id!r} is not in the pinned reference capture "
                f"(tests/fixtures/creative_formats/reference_formats.json, {len(capture)} formats), so no "
                f"creative agent serves it and production refuses it with REFERENCE_NOT_FOUND on every "
                f"transport. Name a captured id instead; near misses: {near or 'none'}"
            )
        if generative is not None:
            from src.core.format_resolver import is_generative

            actual = is_generative(served)
            if actual is not generative:
                raise KeyError(
                    f"the capture's {format_id!r} is {'generative' if actual else 'static'}, but the "
                    f"scenario declared {'generative' if generative else 'static'}. The capture decides; "
                    f"pick an id that matches (generative ids end in '_generative')"
                )
        self._served_formats[format_id] = served
        return served

    def serves_format(self, format_id: str) -> bool:
        """Whether this env has been told to serve *format_id* itself.

        For a GENERIC seeding helper, which takes whatever format the scenario's payload
        carries: an id this env serves needs its declaration passed to the factory, and an
        id it does not is left to the factory's own capture lookup. Asking rather than
        reaching into the registry keeps the step out of the env's internals.
        """
        return format_id in self._served_formats

    def published_format_spec(self, agent_url: str, format_id: str) -> Format | None:
        """The declaration a sync naming ``(agent_url, format_id)`` resolves against, or None.

        For a step that SEEDS a library row through ``CreativeFactory``, which projects the
        row's legacy reference to its canonical kind exactly as the sync path does
        (``creative_identity_of``) and needs the same declaration to do it. In order:

        1. a format a Given configured this env to serve (:meth:`served_format`);
        2. an entry the tenant's hosted agent publishes (:meth:`host_reference_format`) --
           on e2e the agent :meth:`catalog_agent_url` names;
        3. the reference capture the standard agent answers from, which is the factory's
           own lookup (``served_format_spec``).

        Step 2 is why this exists. The factory reads only the capture, whose entries all name
        ``https://creative.adcontextprotocol.org``, so on e2e it found nothing at the hosted
        agent's URL and fell back to the bare id. The SDK maps ``display_300x250_image`` to a
        kind only at the reference agent's origin, so every seeded row in the catalog format
        was refused in the TEST process while the server, which reads the hosted agent's
        declaration and its ``canonical.kind``, resolved the identical sync. None leaves the
        factory to its own lookup, whose refusal is production's.
        """
        from src.core.format_resolver import find_format
        from tests.factories.creative import served_format_spec

        if format_id in self._served_formats:
            return self._served_formats[format_id]
        hosted = [Format.model_validate(entry) for entry in (self._hosted_formats or {}).values()]
        return find_format(hosted, FormatId(agent_url=agent_url, id=format_id)) or served_format_spec(
            agent_url, format_id
        )

    #: The capture's generative format a scenario gets when it does not name one. A
    #: CAPTURED id, not an invented one: the seven generative formats the reference agent
    #: serves all end in ``_generative`` and carry a real ``output_format_ids``, which is
    #: what ``format_resolver.is_generative`` reads.
    DEFAULT_GENERATIVE_FORMAT_ID = "display_300x250_generative"

    def setup_generative_build(
        self,
        format_id: str = DEFAULT_GENERATIVE_FORMAT_ID,
        build_result: dict[str, Any] | None = None,
        gemini_api_key: str | None = "test-gemini-key",
    ) -> dict[str, str]:
        """Configure harness for generative creative testing.

        Sets up:
        - the CAPTURE's declaration for *format_id*, refused unless it is generative there
        - the ``build_creative`` recorder's return value
        - the Gemini key

        Returns a format_id dict for use in creative payloads::

            fmt = env.setup_generative_build()
            creative = {"creative_id": "c1", "name": "Test", "format_id": fmt, ...}

        Neither the catalogue nor ``get_format`` is configured here any more. Both are
        answered by production from the capture, which already carries this format --
        narrowing the catalogue to one entry is what made the in-process seller differ
        from the server. The format is named at :meth:`catalog_agent_url`: the standard
        agent in process, and on e2e_rest the tenant's hosted agent, which publishes the
        capture's own entry and answers the build -- so the build request the server makes
        lands where :meth:`creative_agent_requests` reads it, and never on the reference
        agent, which would spend a real Gemini call on a test key.
        """
        self.served_format(format_id, generative=True)
        agent_url = self.catalog_agent_url(format_id)

        # The build answer. The generated assets are spelled the way the pinned asset
        # union spells them (core/assets/text-asset.json: asset_type + content), because
        # production types the agent's output before storing it.
        default_build = {
            "status": "draft",
            "context_id": "ctx-test-123",
            "creative_output": {
                "assets": {"headline": {"asset_type": "text", "content": "Generated headline"}},
                "output_format": {"url": "https://generated.example.com/creative.html"},
            },
        }
        self.answer_creative_agent(PEER_BUILD_TOOL, build_result or default_build)

        self.set_gemini_api_key(gemini_api_key)

        return {"agent_url": agent_url, "id": format_id}

    def set_gemini_api_key(self, value: str | None) -> None:
        """Configure (or clear, with ``None``) the TENANT's Gemini API key, on every transport.

        The generative build reads the key from the tenant's AI configuration
        (``tenant.ai_config`` through ``src.services.ai.config.tenant_google_api_key``),
        so "is the key configured" is that column, seeded in the shape the admin AI
        settings form writes it (``settings.update_ai``: a provider and its key). The
        write goes through the env-owned writer both caller paths read
        (:meth:`configure_tenant_field`): the identity a direct ``call_impl`` hands
        over, and the row the wire legs' resolver -- the e2e server's included -- reads.
        It used to be the server's environment, which a test cannot set for another
        process, and this method was declared unrealizable over e2e_rest for exactly
        that reason (CLAUDE.md pattern 11: a real input a deployment can set, seeded
        like any other state).

        ``None`` is a real state, not a missing argument -- it is the
        "GEMINI_API_KEY not configured" precondition BR-RULE-036 grades -- which is
        why the type admits it and there is no default. An empty string is the same
        state: the tenant has no AI configuration at all.
        """
        self.ensure_tenant()
        self.configure_tenant_field("ai_config", {"provider": "gemini", "api_key": value} if value else None)

    def set_served_catalogue(self, formats: list[Format]) -> None:
        """The seller's catalog is exactly *formats*, on every transport.

        A RESTRICTION, and the default is no restriction: the standard creative agent
        answers from the committed capture, which is what the e2e server serves. Call this
        only for a scenario whose subject IS the catalogue's contents -- an empty one, or a
        format the reference agent does not carry.

        The formats are served by a real peer creative agent and the tenant stops
        federating the standard one (``seed_own_creative_agent``), so ``list_all_formats``
        and ``get_format`` both read the peer's catalog through production's own registry,
        in process and on the live server alike. Each entry is RE-HOMED at the peer's URL:
        a single-format lookup routes by ``format_id.agent_url``, and a URL naming the
        standard agent is answered from the capture, never from the peer. So a creative
        has to name the identity the peer publishes -- :meth:`seeded_format_ids` hands it
        over, and no step spells an agent URL for a seeded format.
        """
        url = self.seed_own_creative_agent(formats, rehome=True)
        self._seeded_format_ids = [fmt.format_id.model_copy(update={"agent_url": url}) for fmt in formats]

    def seeded_format_ids(self) -> list[FormatId]:
        """The ``format_id`` of each format :meth:`set_served_catalogue` seeded, in order, as the peer publishes it.

        Raises rather than answering an empty list when nothing was seeded: a When step that
        reads these with no seeding Given would otherwise send a creative naming nothing.
        """
        assert self._seeded_format_ids is not None, (
            "no catalogue was seeded on this env; call set_served_catalogue() first"
        )
        return list(self._seeded_format_ids)

    @property
    def catalogue_reads(self) -> int:
        """How many times this env's registry has been asked for the WHOLE catalogue.

        ``_sync.py:151`` asks once per request, so this is how "the registry is queried
        once per sync, not once per creative" is observed. Named on the env rather than
        read off ``mock["registry"]`` for the reason
        :meth:`set_gemini_api_key` states: a step that has to know the registry's shape
        to express an observation breaks when the harness reorganises, and the reach is
        invisible to the e2e-escape-hatch guard.
        """
        return int(self.mock["registry"].return_value.catalogue_reads)

    @property
    def format_fetches(self) -> int:
        """How many times this env's registry has been asked for ONE format.

        ``_validation.py`` asks once per creative with a dialled agent_url and not at
        all for an adapter (non-HTTP) one, so zero is the observation "external format
        validation was skipped".
        """
        return int(self.mock["registry"].return_value.format_fetches)

    def fail_format_fetch(self, exc: BaseException) -> None:
        """Make the registry's single-format read RAISE *exc* from here on.

        In-process only, and not decorated ``realize_e2e``: the three scenarios that
        need it run on impl/mcp/a2a/rest, so the decorator would have no caller to
        dispatch -- and 35f6a5a83's ruling is that ``e2e_unsupported`` is not a thing to
        add. If a wire scenario ever needs a transient agent failure, the answer is a
        server whose registry can be put into that state, not a declaration here.

        For a scenario whose subject is a typed TRANSIENT agent failure: the registry
        types every network error (429 -> ``AdCPRateLimitError``, 5xx/timeout/connect ->
        ``AdCPServiceUnavailableError``) and those must keep their recovery semantics all
        the way to the wire rather than degrade to a terminal per-item ``failed``. The
        raise comes out of production's own ``get_format``, so ``fetch_format_spec``,
        ``_validation.py`` and ``_sync.py``'s per-item handler see exactly what they see
        in production; nothing stands in for the method and nothing answers in its place.

        The catalogue read (``list_all_formats``) is deliberately left alone: it happens
        once, before the loop, and faulting it would raise out of ``_sync.py`` ahead of
        the per-item handler this grades -- a pass for the wrong reason.
        """
        self.mock["registry"].return_value.fail_format_fetch_with(exc)

    #: AdCP 3.1.1 makes idempotency_key REQUIRED on sync-creatives-request. Supplied here so
    #: every scenario gets a spec-conformant request without each of ~200 call sites naming a
    #: key it does not care about. A scenario that IS about the key overrides it, and its
    #: value wins because setdefault only fills an absent one.
    #: Shape-valid per the pin: ^[A-Za-z0-9_.:-]{16,255}$.
    #:
    #: FRESH PER CALL, because sync_creatives now HONOURS the key. A fixed default meant two
    #: successive calls in one scenario were the same request: the second replayed the
    #: first's response instead of executing, so a create-then-upsert test saw "created"
    #: twice, and a create-then-modify test hit IDEMPOTENCY_CONFLICT. The pin's own guidance
    #: is "use a fresh UUID v4 for each request", which is what a real buyer does per call.
    @property
    def DEFAULT_IDEMPOTENCY_KEY(self) -> str:  # noqa: N802 - kept as the documented name
        return mint(f"harness-idem-{uuid4().hex}")

    def _with_required_request_fields(self, kwargs: dict, *, with_account: bool = True) -> dict:
        """Fill the spec-required fields a scenario has not set itself.

        `account` needs care: steps pass it EXPLICITLY as None for scenarios that are not
        about accounts (uc006_sync_creatives.py builds {"account": ctx.get("account_ref")}),
        and an explicit None fails validation now that the field is required -- setdefault
        would not see it. So a None is replaced, not merely defaulted.

        The value comes from the caller's own identity rather than a literal: the account a
        request names should be the account it is authenticated for, and a fabricated id
        would resolve to nothing. A scenario that IS about accounts sets account_ref and
        keeps it, because only a None is replaced.
        """
        # A MINIMAL VALID creative, not []. sync-creatives-request.json declares
        # ``creatives: {minItems: 1}``, so an empty array is a rejected request -- a test
        # that never mentions creatives (auth, isolation, notification) would fail on the
        # array rather than reaching what it grades. A test that explicitly passes [] still
        # gets [], because setdefault does not override an explicit value: that is how the
        # minItems rejection itself stays testable.
        kwargs.setdefault("creatives", [creative_payload()])
        # A scenario that means to send NO key cannot say so by leaving the kwarg out --
        # that is what every scenario that does not care about keys looks like, and those
        # get the default. The sentinel is the create harness's own, so both tools spell
        # "absent" the same way; the pin lists idempotency_key in /required, so what it
        # buys is a request the schema rejects, graded as such.
        if kwargs.get("idempotency_key") is OMIT_IDEMPOTENCY_KEY:
            kwargs.pop("idempotency_key")
        else:
            kwargs.setdefault("idempotency_key", self.DEFAULT_IDEMPOTENCY_KEY)
        # ``with_account=False`` for the IMPL path. account is a field of the REQUEST, and
        # requests are built by transport wrappers -- _sync_creatives_impl does not take one.
        # Injecting it there makes every direct-impl test resolve an account before reaching
        # its subject, so a scenario about an unknown tenant answers AdCPAuthorizationError
        # from account resolution instead of the auth rejection it grades.
        # Same sentinel discipline as the key: a scenario about the field's ABSENCE says so
        # with OMIT_ACCOUNT (the create harness's own), and the pin lists account in
        # /required, so what it buys is a request the schema rejects, graded as such.
        if kwargs.get("account") is OMIT_ACCOUNT:
            kwargs.pop("account")
        elif with_account and kwargs.get("account") is None:
            identity = kwargs.get("identity") or self.identity
            account_id = getattr(identity, "account_id", None)
            if not account_id:
                # No account on the identity: seed one and use it. Dropping the key was
                # viable only while the field was optional -- sync-creatives-request.json
                # lists account in /required, so an absent account is now a request the
                # schema rejects, and every scenario not ABOUT accounts would fail on a
                # missing field before reaching what it grades.
                if self._session is None:
                    # No DB bound (a contract test building a body outside ``with env:``).
                    # There is nothing to seed against and nothing that will resolve the
                    # reference, so a literal id gives the body its required SHAPE, which is
                    # all such a caller is asking for.
                    account_id = "acct_unbound"
                else:
                    account_id = self.setup_default_account(
                        principal_id=getattr(identity, "principal_id", None)
                    ).account_id
            # The TYPED reference, not a bare dict: the resolver hands this straight to
            # account_lookup.find_account, which reads AccountReference.root. A dict gets
            # as far as "'dict' object has no attribute 'root'". build_rest_body serialises
            # it for the wire itself.
            kwargs["account"] = AccountReferenceById(account_id=account_id)
        return kwargs

    def call_impl(self, **kwargs: Any) -> SyncCreativesResponse:
        """Call _sync_creatives_impl with real DB.

        Takes the same per-field kwargs a scenario always wrote and BUILDS the request from
        them, through ``SyncCreativesRequest`` -- the one seam the three transports construct
        through. ``_sync_creatives_impl`` takes ``(req, identity)``, so a harness that
        forwarded loose fields would be the only caller in the tree still spreading a request
        across a call signature, and would grade a shape production no longer has.

        The 'identity' kwarg defaults to self.identity. If 'account' is present it is
        resolved through the resolver's own account read (``resolved_identity._load_account``)
        for the identity's principal and the identity is rebuilt as an ``AccountIdentity``
        with the account inside; an absent one still reaches the REQUEST, because the
        schema requires the field, but is never resolved -- so a scenario about an unknown
        tenant keeps reaching the auth rejection it grades rather than an
        account-resolution error.

        This is the IN-PROCESS path, and it is the one production itself takes when
        ``create_media_buy`` uploads a package's inline creatives. It performs no idempotency
        probe, because the probe lives at the boundary; the transports below cross it.
        """
        from src.core.tools.creatives._sync import _sync_creatives_impl

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        kwargs = self._with_required_request_fields(kwargs, with_account=False)

        identity = kwargs.pop("identity")

        # Handle account kwarg -- the resolver's own account read, so this path cannot drift
        # from what a wire leg gets: one lookup, one conversion, the factory builds the type.
        account = kwargs.pop("account", None)
        if account is not None:
            from src.core.resolved_identity import _load_account
            from tests.factories.principal import PrincipalFactory

            identity = PrincipalFactory.make_account_identity(
                identity, _load_account(account, identity.tenant_id, identity.principal)
            )

        req = SyncCreativesRequest(
            # The schema REQUIRES account, and this path deliberately does not resolve one
            # (see the docstring), so an unstated account gets a syntactically valid
            # reference that no lookup will ever consult. _sync_creatives_impl reads
            # identity, never req.account.
            account=account if account is not None else AccountReferenceById(account_id="acct_unresolved"),
            **kwargs,
        )
        return _sync_creatives_impl(req=req, identity=identity)

    def deliver_a2a(self, **kwargs: Any) -> DeliverResult:
        """Dispatch sync_creatives through the REAL A2A ``on_message_send`` pipeline.

        This used to call ``sync_creatives_raw`` directly, routing AROUND
        ``on_message_send``. The consequence (per tests/CLAUDE.md's own table:
        A2A ``wire_response`` is populated ONLY when the env routes through
        ``_run_a2a_handler``) was that the A2A leg produced no wire at all — so
        every storyboard Then on this transport had nothing transport-observable
        to assert and fell back to reading an in-memory object. Delegating to the
        base ``_run_a2a_handler`` (message parse → skill routing →
        ``serve`` → ``to_wire`` →
        Task/Artifact DataPart) makes the a2a seat grade the real handler,
        per-item failures included, instead of standing in for it.

        Outbound kwargs are JSON-normalized through :meth:`_wire_value` — the
        SAME normalizer the REST leg's ``build_rest_body`` uses, not a second
        hand-rolled one — because they now travel via
        ``create_a2a_message_with_skill`` -> ``_dict_to_value`` (protobuf),
        which cannot carry the Pydantic models the raw wrapper accepted as live
        Python objects (account, push_notification_config) and would otherwise
        turn them into repr strings instead of a document a buyer could send.
        Normalization is applied per-kwarg rather than by calling
        ``build_rest_body``, because that helper SELECTS the REST body's keys:
        routing through it would silently drop the three kwargs
        ``_run_a2a_handler`` consumes itself (see ``_A2A_RESERVED_KWARGS``),
        which must reach it untouched.

        The spec-required fields (creatives, idempotency_key, account) are filled by
        :meth:`_with_required_request_fields` BEFORE normalization, not by a local
        ``creatives=[]`` default: the pin makes all three required on
        sync-creatives-request, and the account it injects is a typed
        ``AccountReference`` that only survives the protobuf hop once ``_wire_value``
        has turned it into a dict.
        """
        kwargs = self._with_required_request_fields(kwargs)
        return self._run_a2a_handler(
            "sync_creatives",
            SyncCreativesResponse,
            **{k: v if k in _A2A_RESERVED_KWARGS else self._wire_value(v) for k, v in kwargs.items()},
        )

    def deliver_mcp(self, **kwargs: Any) -> DeliverResult:
        """Call sync_creatives via Client(mcp) — full pipeline dispatch.

        No enum coercion needed — FastMCP's TypeAdapter handles it automatically.
        """
        kwargs = self._with_required_request_fields(kwargs)
        return self._run_mcp_client("sync_creatives", SyncCreativesResponse, **kwargs)

    @classmethod
    def _wire_value(cls, value: Any) -> Any:
        """Convert a Pydantic model (or a list of them) to its wire dict form.

        Anything else passes through unchanged, so a dict a scenario authored -- including
        one that sends an explicit null on purpose -- reaches the wire exactly as written.

        ``exclude_none`` for a MODEL, because an unset optional field is ABSENT on a buyer's
        wire, not null. The two are different documents at the pin: core/creative-asset.json's
        root oneOf counts a present key whatever its value, so dumping a canonical creative
        model with ``format_id: null`` beside ``format_kind`` would name both identifiers and
        be refused -- a request no buyer sent.
        """
        if isinstance(value, list):
            return [cls._wire_value(item) for item in value]
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json", exclude_none=True)
        return value

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to SyncCreativesBody shape for REST POST."""
        # The REST body expects 'creatives' as list[dict], matching SyncCreativesBody.
        # 'push_notification_config' and 'account' are declared by SyncCreativesBody and
        # forwarded by the route through ``serve`` — dropping either here would make
        # any REST test of that behavior silently vacuous.
        #
        # 'idempotency_key' is schema-REQUIRED on sync_creatives (pinned_request_schema_fields
        # reports it in the required set). It is carried by the acceptance seam rather than
        # declared on SyncCreativesBody, so it must ride the REST body for the REST leg to
        # grade idempotency at all — omitting it here would make every REST idempotency
        # assertion vacuous. _with_required_request_fields is what SUPPLIES it (plus
        # creatives and account) when the caller did not, so the loop below finds a value
        # to carry rather than skipping the key.
        kwargs = self._with_required_request_fields(dict(kwargs))
        body: dict[str, Any] = {}
        if "creatives" in kwargs:
            body["creatives"] = self._wire_value(kwargs["creatives"])
        for key in ("assignments", "creative_ids", "push_notification_config", "account", "idempotency_key"):
            if kwargs.get(key) is not None:
                body[key] = self._wire_value(kwargs[key])
        for key in ("delete_missing", "dry_run", "validation_mode"):
            if key in kwargs:
                body[key] = self._wire_value(kwargs[key])
        return body

    def parse_rest_response(self, data: dict[str, Any]) -> SyncCreativesResponse:
        """Parse a REST body into SyncCreativesResponse, through the reader-side door.

        Named here rather than left to the base because ``sync_creatives`` has no single
        pinned response model for ``RESPONSE_MODEL`` to hold. ``revive`` for the same
        reason the base uses it: a served document carries the context the boundary
        stamped, and the constructor refuses that field.
        """
        return cast("SyncCreativesResponse", SyncCreativesResponse.revive(data))


class RealRegistryCreativeSyncEnv(CreativeSyncEnv):
    """``CreativeSyncEnv`` with the creative-agent registry left UNPATCHED.

    ``CreativeSyncEnv`` mocks ``get_creative_agent_registry`` so ordinary sync
    tests never reach the network. This variant drops exactly that one patch and
    changes nothing else, so a buyer-supplied ``format_id.agent_url`` travels the
    real registry and is judged by the real egress seam — which is the point: a
    refusal produced by a mock proves nothing about production.

    Hermetic for the causes worth grading. A refused destination (cloud metadata,
    a reserved literal, a non-https scheme) is refused BEFORE a connection is
    opened, so no packet leaves and no DNS answer is needed.

    A FORMAT RESOLUTION IS HERMETIC HERE. THE PROCESS IS NOT. Both halves matter, and the
    first half alone is what two earlier versions of this paragraph said -- one claiming the
    reference agent "would be dialled for real" (which sent salesagent-3oo5h's diagnosis to
    the live public host twice), then one headed "hermetic for a successful fetch too", which
    is accurate about a fetch and invites the wider reading about the env.

    Resolution is hermetic: under ``ADCP_TESTING`` the registry this env leaves unpatched is
    ``ReferenceFormatsRegistry``, whose ``_operator_formats_override`` answers an OPERATOR
    reference from the checked-in capture before any fetch is attempted. The parent now
    serves that SAME capture (it installs ``ReferenceFormatsRegistry`` itself rather than a
    stand-in), so what this env buys is no longer the catalogue -- it is the real
    ``get_creative_agent_registry`` and therefore the real egress seam for a
    COUNTERPARTY-supplied agent_url, which is what its scenarios grade.

    The process is not: that class overrides ONE method and inherits NINE public ones, so
    hermeticity is a property of what each one happens to call rather than of the class.
    ``preview_creative``, ``build_creative`` and the agent probe perform a real MCP handshake,
    and a dial on some paths is swallowed by the call site rather than failing the test.
    **salesagent-1bc7l** is the mechanism, the measurement and the remedy; it is not restated
    here, because a docstring that duplicates a bead drifts from it. Read it before using this
    env to argue that something did not reach the network.

    TRAP inherited from the same pattern in ``RealResolverProductEnv``:
    ``self.mock["registry"]`` does not exist after ``__enter__``, so any helper
    that programs the registry (``setup_generative_build``) will ``KeyError`` on this
    env. ``set_served_catalogue`` does not program the registry -- it seeds a real peer
    agent and the tenant's row -- so it works here too.
    """

    EXTERNAL_PATCHES = {name: target for name, target in CreativeSyncEnv.EXTERNAL_PATCHES.items() if name != "registry"}

    def _configure_mocks(self) -> None:
        """Configure everything except the registry, which is real here.

        Same shape as ``RealResolverProductEnv`` (tests/harness/product.py): a throwaway
        sink absorbs the parent's registry wiring and is deleted afterwards, so the
        REST of the parent's wiring still runs. Forking the body instead — which is what
        this method used to do — silently dropped whatever the parent gained later, and it
        HAS gained: the ai_review_executor stand-in and the
        ``ai_review_commit_observations`` / ``*_at_notification`` attributes the effect
        oracles read. A fork would leave those unset and every oracle would AttributeError.

        A ``SimpleNamespace`` and not a ``MagicMock``: nothing reads this object, so all it
        needs is a settable ``return_value``, and a sink that answers every attribute is how
        a stand-in stops being visibly inert.
        """
        self.mock["registry"] = cast("Any", SimpleNamespace(return_value=None))
        try:
            super()._configure_mocks()
        finally:
            del self.mock["registry"]


class LibraryUpsertCreativeSyncEnv(RealRegistryCreativeSyncEnv):
    """Historical name for ``RealRegistryCreativeSyncEnv``; both its mechanisms moved up.

    It existed for two patches that are now gone from the whole family:

    1. ``CreativeSyncEnv`` used to blank the sync bridge ``_sync.py`` calls the registry
       THROUGH (``self.mock["run_async"].side_effect = lambda coro: []``), so
       ``list_all_formats`` was discarded and ``_sync.py:151`` read ``len=0`` no matter
       which registry was installed. That patch is gone from the base.
    2. It patched ``CreativeAgentRegistry.get_format`` to answer from
       ``_get_reference_formats()``, on the stated ground that ``get_format`` "consults
       ``_operator_formats_override``: False DIALS" for ANY id. THAT GROUND WAS WRONG, and
       re-measured here at ADCP_TESTING=true, instrumenting ``asend``:

           get_format(capture_url, id,        provenance=None)          dials=[]
           get_format(capture_url, id,        provenance=Counterparty)  dials=[]
           get_format(capture_url, unknown,   provenance=Counterparty)  dials=[]  -> None
           get_format(harness_url, id,        provenance=Counterparty)  DIALLED

       ``get_format`` delegates to ``get_formats_for_agent``, which takes the egress-seam
       branch only when the provenance is a counterparty AND ``is_operator_agent`` is
       False -- otherwise it consults the override. The nine dials that were measured were
       all on the previous ``DEFAULT_AGENT_URL`` (``https://creative.test.example.com``),
       which is not an operator agent. With the harness naming the capture's own agent the
       dial does not arise, so the patch has nothing left to prevent.

    Kept as a name because ``tests/bdd/conftest.py`` routes scenarios at its dotted path;
    collapsing the two names is a scenario-routing change, not a harness one.
    """
