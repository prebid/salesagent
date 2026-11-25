"""MediaBuyDualEnv — composite environment for UC-026 and UC-003 BDD scenarios.

UC-026 scenarios use both create and update flows within the same test:
Given steps create a media buy (create path), then When steps update it
(update path). UC-003 (PR #1567) drives the update path directly against
a pre-seeded media buy to grade the manual-approval UpdateMediaBuySubmitted
envelope cross-transport. This env extends MediaBuyCreateEnv with update-module
patches and delegates update requests to the appropriate production code —
A2A/MCP go through the real on_message_send / FastMCP Client pipelines so the
serialized wire (and the A2A submitted payload) are genuinely exercised.

Introduced by PR #1567.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from src.core.schemas import UpdateMediaBuyRequest
from tests.factories.request import OMIT
from tests.harness._mixins import make_adapter_update_side_effect
from tests.harness._realize import realize_e2e
from tests.harness.media_buy_create import (
    OMIT_ACCOUNT,
    OMIT_IDEMPOTENCY_KEY,
    MediaBuyCreateEnv,
    _adapter_operation_receipts,
)
from tests.harness.transport import DeliverResult

if TYPE_CHECKING:
    from tests.helpers.signing import SignatureRealization

_UPDATE_MODULE = "src.core.tools.media_buy_update"


def _adapter_update_actions_on_the_live_stack(self: MediaBuyDualEnv) -> list[str]:
    """The actions the SERVER's ad server was handed, read off the receipts it wrote."""
    return [details["action"] for details in _adapter_operation_receipts(self, "update_media_buy")]


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
    carrying it is unambiguously an update. An update that means to send NO id carries
    the key with :data:`~tests.factories.request.OMIT`, which routes it here and which
    :meth:`MediaBuyDualEnv._flatten_update_request` strips at the wire.
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

    @realize_e2e(_adapter_update_actions_on_the_live_stack)
    def adapter_update_actions(self) -> list[str]:
        """The ``action`` of each ``update_media_buy`` the ad server was handed, in order."""
        calls = self.mock["update_adapter"].return_value.update_media_buy.call_args_list
        return [call.kwargs["action"] for call in calls]

    def adapter_mocks(self) -> list[MagicMock]:
        """The create module's adapter, then the update module's (``_UPDATE_PATCHES``)."""
        return [*super().adapter_mocks(), self.mock["update_adapter"].return_value]

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

    def address_tool(self, tool: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if _is_update_request(payload):
            return "update_media_buy", self._flatten_update_request(payload)
        return super().address_tool(tool, payload)

    def response_parser(self, tool: str) -> Any:
        if tool == "update_media_buy":
            return lambda **data: self._parse_update_rest_response(data)
        return super().response_parser(tool)

    def deliver_mcp(self, **kwargs: Any) -> DeliverResult:
        # Legacy MCP content routing; A2A uses address_tool above.
        if _is_update_request(kwargs):
            # _call_update_mcp already returns a DeliverResult
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
            self._update_target_id = self._requested_update_target(kwargs)
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
        for field, sentinel in (
            ("idempotency_key", OMIT_IDEMPOTENCY_KEY),
            ("account", OMIT_ACCOUNT),
            ("media_buy_id", OMIT),
        ):
            if flat.get(field) is sentinel:
                del flat[field]
        return flat

    def _requested_update_target(self, kwargs: dict[str, Any]) -> str:
        """The media_buy_id the REST update PUTs to: the one the SCENARIO named.

        From wherever it was supplied, a typed ``req`` or a plain ``media_buy_id`` kwarg.
        Reading only ``req`` sent a kwarg-style call to the SEEDED buy, so "Media buy not
        found -- by media_buy_id" PUT to an existing media buy and got a success. It passed
        on a2a/mcp because they carry the id in the body; only REST puts it in the path.

        An update sent with NO id has no REST request at all: the id is the resource path
        (``PUT /media-buys/{media_buy_id}``, src/core/tools/registry.py). The rows that send
        none are declared not applicable on rest and e2e_rest in
        ``tests/bdd/conftest.py::_TRANSPORT_INEXPRESSIBLE_ROWS`` and never reach this method.
        """
        req = kwargs.get("req")
        return getattr(req, "media_buy_id", None) or kwargs.get("media_buy_id") or self._seeded_media_buy_id

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
        credential = self._pop_credential(kwargs)
        self._commit_factory_data()
        endpoint = f"/api/v1/media-buys/{self._requested_update_target(kwargs)}"
        return self._http_request(
            "PUT", endpoint, body=self._build_update_rest_body(**kwargs), credential=credential, signed=signed
        )

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
        decide. This serves the REST wire and the A2A submitted payload alike — the
        latter read from the completed native Task's artifact DataPart.
        """
        from src.core.schemas._base import UpdateMediaBuyResult

        return UpdateMediaBuyResult.revive(data)
