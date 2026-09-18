"""MediaBuyDualEnv — composite environment for UC-026 and UC-003 BDD scenarios.

UC-026 scenarios use both create and update flows within the same test:
Given steps create a media buy (create path), then When steps update it
(update path). UC-003 (PR #1567) drives the update path directly against
a pre-seeded media buy to grade the manual-approval UpdateMediaBuySubmitted
envelope cross-transport. This env extends MediaBuyCreateEnv with update-module
patches and delegates update requests to the appropriate production code —
A2A/MCP go through the real on_message_send / FastMCP Client pipelines so the
serialized wire (and the A2A submitted reconstruction) are genuinely exercised.

Introduced by PR #1567.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from src.core.schemas import UpdateMediaBuyRequest
from tests.harness._mixins import make_adapter_update_side_effect
from tests.harness.media_buy_create import OMIT_ACCOUNT, OMIT_IDEMPOTENCY_KEY, MediaBuyCreateEnv
from tests.harness.transport import DeliverResult

if TYPE_CHECKING:
    from tests.helpers.signing import SignatureRealization

_UPDATE_MODULE = "src.core.tools.media_buy_update"

_UPDATE_PATCHES = {
    "update_adapter": f"{_UPDATE_MODULE}.get_adapter",
    "update_audit": f"{_UPDATE_MODULE}.get_audit_logger",
    "update_context_mgr": f"{_UPDATE_MODULE}.get_context_manager",
}


def _is_update_request(kwargs: dict[str, Any]) -> bool:
    """Route to the update wrappers for a typed request OR a RAW flat update body.

    ``req=UpdateMediaBuyRequest(...)`` is the typed dispatch every UC-003/UC-026
    scenario uses. The RAW form (flat kwargs, no ``req``) exists for scenarios
    whose payload the LOCAL ``UpdateMediaBuyRequest`` must reject: constructing
    the model in the test process would raise inside the step, so the rejection
    would never reach a wire and could not be graded as an envelope. Dispatching
    the flat body sends it through the real route + production Pydantic instead,
    mirroring the create side's ``dispatch_mode="create_raw"``.

    ``media_buy_id`` is the discriminator: it identifies the buy being updated and
    is absent from every create request (the seller assigns it), so a flat body
    carrying it is unambiguously an update.
    """
    req = kwargs.get("req")
    if isinstance(req, UpdateMediaBuyRequest):
        return True
    return req is None and "media_buy_id" in kwargs


class MediaBuyDualEnv(MediaBuyCreateEnv):
    """Extends MediaBuyCreateEnv with update-path dispatch for UC-026 scenarios.

    Adds patches for the update module (adapter, audit, context_mgr) alongside
    the create module patches. Routes UpdateMediaBuyRequest through update
    wrappers instead of create wrappers.
    """

    _seeded_media_buy_id: str = "NOT_SEEDED"

    # The update patches ride the base's own patch loop rather than a second
    # registry of their own — the sibling precedent is
    # ``MediaBuyCreateEnv.EXTERNAL_PATCHES``. The base starts them, registers
    # each with ``_guard``, and collects teardown errors instead of swallowing
    # them, so no hook is needed here at all.
    EXTERNAL_PATCHES = {**MediaBuyCreateEnv.EXTERNAL_PATCHES, **_UPDATE_PATCHES}

    def _configure_mocks(self) -> None:
        super()._configure_mocks()
        self._configure_update_mocks()

    def _configure_update_mocks(self) -> None:
        mock_adapter = MagicMock()
        mock_adapter.manual_approval_required = False
        mock_adapter.manual_approval_operations = []
        mock_adapter.validate_media_buy_request.return_value = None
        mock_adapter.add_creative_assets.return_value = None
        mock_adapter.associate_creatives.return_value = None

        mock_adapter.update_media_buy.side_effect = make_adapter_update_side_effect()
        self.mock["update_adapter"].return_value = mock_adapter

        mock_audit = MagicMock()
        mock_audit.log_operation.return_value = None
        mock_audit.log_security_violation.return_value = None
        self.mock["update_audit"].return_value = mock_audit

        self.mock["update_context_mgr"].return_value = self._build_mock_context_manager(tool_name="update_media_buy")

    # -- Update dispatch methods -----------------------------------------------

    def call_impl(self, **kwargs: Any) -> Any:
        if _is_update_request(kwargs):
            return self._call_update_impl(**kwargs)
        return super().call_impl(**kwargs)

    def deliver_a2a(self, **kwargs: Any) -> DeliverResult:
        # JUSTIFIED OVERRIDE: this env selects BOTH the tool and the parser from
        # request CONTENT, so it can declare no single A2A_SKILL.
        if _is_update_request(kwargs):
            # Returned AS IS: _call_update_a2a drives _run_a2a_handler, which
            # already yields a DeliverResult carrying the REAL artifact wire.
            # Re-wrapping it would both double-nest the payload and throw that
            # wire away.
            return self._call_update_a2a(**kwargs)
        return super().deliver_a2a(**kwargs)

    def deliver_mcp(self, **kwargs: Any) -> DeliverResult:
        # JUSTIFIED OVERRIDE: see deliver_a2a above.
        if _is_update_request(kwargs):
            # As in deliver_a2a: _call_update_mcp already returns a DeliverResult
            # carrying the real structured_content wire.
            return self._call_update_mcp(**kwargs)
        return super().deliver_mcp(**kwargs)

    def _run_rest_request(self, endpoint: str, *, signed: SignatureRealization = False, **kwargs: Any) -> Any:
        # ``signed`` is DECLARED, never swallowed into ``**kwargs`` — the same
        # discipline CapabilitiesEnv._run_rest_request states, and for the same
        # reason. ``RestDispatcher`` passes ``signed=`` to EVERY
        # ``_run_rest_request``; it is a dispatch-level fact (how the request is
        # sent), not an AdCP request field. The create arm below was only ever
        # safe because the BASE declares it, but the update arm never reaches the
        # base — so an undeclared ``signed`` rode the kwargs into
        # ``_build_update_rest_body`` and became a ``signed`` FIELD in the PUT
        # body, which ``UpdateMediaBuyBody`` (extra="forbid", src/routes/api_v1.py)
        # refuses as ``INVALID_REQUEST: Extra inputs are not permitted
        # (field=signed)`` — masking whatever refusal the scenario was grading.
        #
        # Set the update-vs-create routing flag and leave it set THROUGH the base
        # dispatch's subsequent parse_rest_response call: _base.py runs
        # _run_rest_request then parse_rest_response sequentially, so a finally-reset
        # here would flip the flag back before the parse and misroute the update
        # response to the create parser (yielding None). The flag is reset in
        # parse_rest_response after routing, and each request re-sets it here
        # (unconditional assignment, so a create request clears a stale flag).
        self._active_update = _is_update_request(kwargs)
        if self._active_update:
            return self._run_update_rest_request(signed=signed, **kwargs)
        return super()._run_rest_request(endpoint, signed=signed, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        # The E2E dispatcher (RestE2EDispatcher) reads REST_ENDPOINT/REST_METHOD as
        # plain attrs and never calls _run_rest_request, so set the mode flag + target
        # id HERE (deterministically per request, not via parse_rest_response's reset —
        # the E2E error path calls parse_rest_error, which would leave a stale flag).
        if _is_update_request(kwargs):
            self._active_update = True
            # The id in the URL must be the one the SCENARIO asked for, from wherever it
            # supplied it: a typed ``req`` or a plain ``media_buy_id`` kwarg. Reading only
            # ``req`` meant a kwarg-style call silently fell back to the seeded buy -- so
            # "Media buy not found -- by media_buy_id" PUT to the EXISTING media buy and
            # got a success, while the same scenario failed correctly on every other
            # transport because they carry the id in the body rather than the path.
            req = kwargs.get("req")
            target = getattr(req, "media_buy_id", None) or kwargs.get("media_buy_id") or self._seeded_media_buy_id
            self._update_target_id = target
            return self._build_update_rest_body(**kwargs)
        self._active_update = False
        return super().build_rest_body(**kwargs)

    @property
    def REST_ENDPOINT(self) -> str:  # noqa: N802 — matches the inherited class-attr name
        """Update scenarios PUT a per-id endpoint; create scenarios POST the collection.

        A @property (not a static attr) because the E2E dispatcher reads it directly and
        the update path needs the seeded media_buy_id in the URL. The in-process path
        ignores this value (it builds its own PUT URL in _run_update_rest_request)."""
        if self._active_update:
            return f"/api/v1/media-buys/{self._update_target_id}"
        return "/api/v1/media-buys"

    @property
    def REST_METHOD(self) -> str:  # noqa: N802 — dispatcher reads getattr(env, "REST_METHOD", "post")
        return "put" if self._active_update else "post"

    def parse_rest_response(self, data: dict[str, Any]) -> Any:
        if self._active_update:
            self._active_update = False
            return self._parse_update_rest_response(data)
        return super().parse_rest_response(data)

    _active_update: bool = False
    _update_target_id: str = "NOT_SEEDED"

    # -- Concrete update transport implementations -----------------------------

    def _call_update_impl(self, **kwargs: Any) -> Any:
        from src.core.tools.media_buy_update import _update_media_buy_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        req = kwargs.pop("req", None)
        if req is None:
            from src.core.schemas import UpdateMediaBuyRequest as UMR

            req = UMR(**kwargs)
        return _update_media_buy_impl(req=req, identity=identity)

    def _flatten_update_request(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Flatten an ``UpdateMediaBuyRequest`` into flat wire parameters.

        The A2A skill and MCP tool accept a flat param dict, not a request model,
        and reject the wrapper-unsupported fields — so pop ``req``, expand it
        (dropping those fields), then overlay any explicit kwargs.
        Shared by the A2A, MCP and REST update paths (DRY) — REST adapts the
        result in :meth:`_build_update_rest_body` rather than re-spelling it.

        ``identity`` is NOT a wire parameter and is not passed through. This used to say
        "``identity`` (if present) is passed through; the real handlers pop and apply it",
        which was false: neither ``_run_a2a_handler`` nor ``_run_mcp_client`` pops it, so it
        travelled into the flat params and the DTO refused it under extra="forbid". A
        no-auth UC-003 row that dispatched ``identity=None`` therefore got INVALID_REQUEST
        on all three transports while asserting AUTH_MISSING. The caller presents a
        credential instead; nothing on this path supplies an identity.

        The ``OMIT_*`` sentinels are stripped here, which is what makes "the request does
        NOT include an account field" (and the ``<not provided>`` idempotency rows) send a
        body with the field genuinely absent. Same seat and same reason as
        ``MediaBuyCreateEnv._ensure_required_request_fields`` on the create side: the ONE
        function all three update transports funnel through, so no step can forget it, and
        the sentinel still reaches ``tests/bdd/payload_capture.py`` (which records it as
        ``<omit:account>`` / ``<omit:idempotency_key>``) before this strip runs.
        """
        req = kwargs.pop("req", None)
        if req is None:
            flat = dict(kwargs)
        else:
            flat = req.model_dump(mode="json", exclude_none=True)
            flat.update(kwargs)
        for field, sentinel in (("idempotency_key", OMIT_IDEMPOTENCY_KEY), ("account", OMIT_ACCOUNT)):
            if flat.get(field) is sentinel:
                del flat[field]
        return flat

    def _call_update_a2a(self, **kwargs: Any) -> DeliverResult:
        # Drive the REAL on_message_send → _dispatch_skill → to_wire → Task/Artifact
        # pipeline (mirrors MediaBuyCreateEnv.call_a2a), so _run_a2a_handler stashes
        # the true artifact DataPart as the wire_response. A prior version synthesized
        # the wire via update_media_buy_raw(...).model_dump(), which tracked the return
        # model rather than the assembled envelope — an update-envelope regression
        # would not be caught. A SUBMITTED update never carries an artifact body:
        # on_message_send early-returns a Task (state=SUBMITTED, no artifacts) and the
        # base handler synthesizes the submitted wire from the Task (tests/harness/
        # _base.py) — production has no A2A submitted reconstruction (PR #1567 round-2
        # follow-up). Completed/error results DO carry an artifact, stashed as
        # wire_response; _parse_update_rest_response recovers the union from the
        # flattened artifact (needs the top-level status the plain model drops).
        return self._run_a2a_handler(
            "update_media_buy",
            lambda **data: self._parse_update_rest_response(data),
            **self._flatten_update_request(kwargs),
        )

    def _call_update_mcp(self, **kwargs: Any) -> DeliverResult:
        # Drive the REAL FastMCP Client pipeline (mirrors MediaBuyCreateEnv.call_mcp) so the
        # structured_content — the real MCP wire body — is stashed as wire_response and the
        # full middleware/auth chain runs. The real pipeline reaches the production boundary
        # through RegistryTool.run (src/core/main.py), which calls serve and raises
        # AdCPToolError(to_wire(response)) on a failure, so a raised AdCPSalesAgentError
        # surfaces as the two-layer wire envelope captured as wire_error_envelope.
        return self._run_mcp_client(
            "update_media_buy",
            lambda **data: self._parse_update_rest_response(data),
            **self._flatten_update_request(kwargs),
        )

    def _build_update_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """The REST body, built by the SAME flatten the A2A and MCP paths use.

        Three REST-specific differences, and only three: ``credential`` rides the
        request headers rather than travelling in the body, ``media_buy_id``
        rides the URL, and the response is parsed from HTTP.

        The wrapper-unsupported pop is NOT a fourth: ``UpdateMediaBuyBody``
        forbids the same field set for the same reason the flat A2A/MCP params
        do — both mirror ``update_media_buy_raw``'s signature — so dropping them
        here is equivalent, not a REST-specific liberty.

        This used to be a second, non-overlaying spelling of the same flatten:
        with a ``req`` present it returned early and silently discarded every
        remaining kwarg, so a step passing ``req=`` plus an explicit field sent
        an EMPTY body and graded production's answer to the empty request. The
        When step's own docstring already named ``_flatten_update_request`` as
        the one owner of that overlay; now it is.
        """
        kwargs.pop("credential", None)
        body = self._flatten_update_request(kwargs)
        body.pop("media_buy_id", None)
        return body

    def _run_update_rest_request(self, *, signed: SignatureRealization = False, **kwargs: Any) -> Any:
        # ``signed`` is DECLARED here for the same reason ``_run_rest_request`` above
        # declares it: this arm never reaches the base, so an undeclared keyword would
        # ride the kwargs into ``_build_update_rest_body`` and become a body FIELD.
        #
        # The credential rides the request headers, so a no-auth update scenario is
        # refused by the real resolver rather than let through by a test-mode override.
        credential = self._pop_credential(kwargs)
        self._commit_factory_data()
        client = self.get_rest_client()

        body = self._build_update_rest_body(**kwargs)
        # Same rule as REST_ENDPOINT above: the id in the URL is the one the SCENARIO
        # named, from a typed ``req`` OR a plain ``media_buy_id`` kwarg. Reading only
        # ``req`` sent a kwarg-style call to the SEEDED buy, so "Media buy not found --
        # by media_buy_id" PUT to an existing media buy and got a success. It passed on
        # a2a/mcp because they carry the id in the body; only REST puts it in the path,
        # which is why one transport disagreed with the other two about a request the
        # scenario had specified unambiguously.
        req = kwargs.get("req")
        media_buy_id = getattr(req, "media_buy_id", None) or kwargs.get("media_buy_id") or self._seeded_media_buy_id
        endpoint = f"/api/v1/media-buys/{media_buy_id}"
        # Same signed/unsigned fork as the base POST dispatch, with the update's
        # PUT verb: an env that cannot sign must REFUSE a signed dispatch (a
        # silent unsigned send would let a signing scenario pass with no
        # signature), and once it can sign, ``wire_request`` OWNS the request —
        # it serializes once so the bytes signed are the bytes sent, and it emits
        # the single ``Authorization`` identity plus this env's tenant hint
        # itself. That is why ``credential`` is deliberately NOT merged into the
        # signed leg: a second bearer would win the precedence race in
        # ``resolved_identity._extract_auth_token`` and swap the acting
        # principal, so signed and unsigned would differ by more than the
        # signature.
        #
        # ``credentialed`` is read off the credential HEADERS because that is
        # what "presented nothing" is under #1721 — ``credential={}`` and
        # ``credential(token=None)`` both omit ``Authorization``
        # (tests/helpers/credentials.py). It is the only way an in-process leg
        # reaches the verifier's refusal branch at all: security.mdx :1269 makes
        # an unsigned request carrying a valid bearer a spec-correct 200.
        if not self.can_sign:
            if signed:
                self.signing  # raises, naming enable_request_signing()  # noqa: B018
            return client.put(endpoint, json=body, headers=credential)
        raw, wire_headers = self.wire_request(
            path=endpoint,
            body=body,
            signed=signed,
            credentialed="Authorization" in credential,
            method="PUT",
        )
        return client.put(endpoint, content=raw, headers=wire_headers)

    def _parse_update_rest_response(self, data: dict[str, Any]) -> Any:
        """Rebuild an update_media_buy wire body as the branch the buyer received.

        ``UpdateMediaBuyResult.revive`` is production's own discrimination, so every
        transport hands steps the branch production would resolve rather than a
        harness copy of that rule which can disagree with it. It replaces a
        hand-rolled submitted/success/error ladder that re-stated the same rule and
        re-wrapped the branch in a ``UpdateMediaBuyResult(response=..., status=...)``
        envelope; under #1721 ``UpdateMediaBuyResult`` IS the union root its three
        branches inherit (``_BRANCH_ADAPTERS``, src/core/schemas/_base.py), so the
        wrapper has no ``response`` field to fill and the ladder has nothing left to
        decide. This serves the REST wire and the harness-synthesized A2A submitted
        dict alike — production A2A has NO submitted reconstruction (Task
        early-return; PR #1567 round-2 follow-up).
        """
        from src.core.schemas._base import UpdateMediaBuyResult

        return UpdateMediaBuyResult.revive(data)
