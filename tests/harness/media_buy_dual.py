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

from typing import Any, Self
from unittest.mock import MagicMock, patch

from src.core.schemas import UpdateMediaBuyRequest
from tests.harness._mixins import make_adapter_update_side_effect
from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.media_buy_update import _WRAPPER_UNSUPPORTED_FIELDS

_UPDATE_MODULE = "src.core.tools.media_buy_update"

_UPDATE_PATCHES = {
    "update_adapter": f"{_UPDATE_MODULE}.get_adapter",
    "update_audit": f"{_UPDATE_MODULE}.get_audit_logger",
    "update_context_mgr": f"{_UPDATE_MODULE}.get_context_manager",
}


def _is_update_request(kwargs: dict[str, Any]) -> bool:
    req = kwargs.get("req")
    if isinstance(req, UpdateMediaBuyRequest):
        return True
    if req is not None:
        return False  # an explicit non-update req (e.g. a CreateMediaBuyRequest)
    # No req object — a raw payload (e.g. an update whose invalid revision failed
    # UpdateMediaBuyRequest construction, so the step sends the raw fields). It
    # MUST still route to update, not create: ``revision`` is an update-only
    # optimistic-concurrency token and ``media_buy_id`` targets an existing buy,
    # so either identifies a raw update payload. Routing it to create would fail
    # on missing brand/packages/start_time instead of validating the revision.
    return "revision" in kwargs or "media_buy_id" in kwargs


class MediaBuyDualEnv(MediaBuyCreateEnv):
    """Extends MediaBuyCreateEnv with update-path dispatch for UC-026 scenarios.

    Adds patches for the update module (adapter, audit, context_mgr) alongside
    the create module patches. Routes UpdateMediaBuyRequest through update
    wrappers instead of create wrappers.
    """

    _seeded_media_buy_id: str = "NOT_SEEDED"

    def __enter__(self) -> Self:
        result = super().__enter__()
        self._update_patchers: list = []
        for name, target in _UPDATE_PATCHES.items():
            patcher = patch(target)
            self.mock[name] = patcher.start()
            self._update_patchers.append(patcher)
        self._configure_update_mocks()
        return result

    def __exit__(self, *exc: object) -> bool:
        for patcher in reversed(self._update_patchers):
            try:
                patcher.stop()
            except Exception:
                pass
        self._update_patchers = []
        return super().__exit__(*exc)

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

    def call_a2a(self, **kwargs: Any) -> Any:
        if _is_update_request(kwargs):
            return self._call_update_a2a(**kwargs)
        return super().call_a2a(**kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        if _is_update_request(kwargs):
            return self._call_update_mcp(**kwargs)
        return super().call_mcp(**kwargs)

    def _run_rest_request(self, endpoint: str, **kwargs: Any) -> Any:
        # Set the update-vs-create routing flag and leave it set THROUGH the base
        # dispatch's subsequent parse_rest_response call: _base.py runs
        # _run_rest_request then parse_rest_response sequentially, so a finally-reset
        # here would flip the flag back before the parse and misroute the update
        # response to the create parser (yielding None). The flag is reset in
        # parse_rest_response after routing, and each request re-sets it here
        # (unconditional assignment, so a create request clears a stale flag).
        self._active_update = _is_update_request(kwargs)
        if self._active_update:
            return self._run_update_rest_request(**kwargs)
        return super()._run_rest_request(endpoint, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        # The E2E dispatcher (RestE2EDispatcher) reads REST_ENDPOINT/REST_METHOD as
        # plain attrs and never calls _run_rest_request, so set the mode flag + target
        # id HERE (deterministically per request, not via parse_rest_response's reset —
        # the E2E error path calls parse_rest_error, which would leave a stale flag).
        if _is_update_request(kwargs):
            self._active_update = True
            req = kwargs.get("req")
            target = self._seeded_media_buy_id
            if req is not None and getattr(req, "media_buy_id", None):
                target = req.media_buy_id
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
        """Flatten an ``UpdateMediaBuyRequest`` into flat A2A/MCP skill parameters.

        The A2A skill and MCP tool accept a flat param dict, not a request model,
        and reject the wrapper-unsupported fields — so pop ``req``, expand it
        (dropping those fields), then overlay any explicit kwargs. ``identity``
        (if present) is passed through; the real handlers pop and apply it.
        Shared by the A2A and MCP update paths (DRY).
        """
        req = kwargs.pop("req", None)
        if req is None:
            return dict(kwargs)
        flat = req.model_dump(mode="json", exclude_none=True)
        for key in _WRAPPER_UNSUPPORTED_FIELDS:
            flat.pop(key, None)
        flat.update(kwargs)
        return flat

    def _call_update_a2a(self, **kwargs: Any) -> Any:
        # Drive the REAL on_message_send → _serialize_for_a2a → Task/Artifact
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

    def _call_update_mcp(self, **kwargs: Any) -> Any:
        # Drive the REAL FastMCP Client pipeline (mirrors MediaBuyCreateEnv.call_mcp) so the
        # structured_content — the real MCP wire body — is stashed as wire_response and the
        # full middleware/auth chain runs. This subsumes the earlier mock-Context invocation
        # through with_error_logging (#1417): the real pipeline applies the production
        # boundary decorator via registration (src/core/main.py: mcp.tool()(with_error_logging(fn))),
        # so a raised AdCPError still surfaces as the two-layer wire envelope captured as
        # wire_error_envelope.
        return self._run_mcp_client(
            "update_media_buy",
            lambda **data: self._parse_update_rest_response(data),
            **self._flatten_update_request(kwargs),
        )

    def _build_update_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.pop("identity", None)
        req = kwargs.pop("req", None)
        if req is not None:
            body = req.model_dump(mode="json", exclude_none=True)
            body.pop("media_buy_id", None)
            return body
        kwargs.pop("media_buy_id", None)
        return kwargs

    def _run_update_rest_request(self, **kwargs: Any) -> Any:
        # Shared preamble (identity resolution + commit + client + auth-dep
        # override): with no identity the REST auth dep rejects, so the no-auth
        # update scenario fires instead of test-mode auth letting it through.
        client, identity = self._prepare_rest_request(kwargs)

        headers: dict[str, str] = {}
        if identity is not None:
            auth_token = identity.auth_token
            if auth_token:
                headers["x-adcp-auth"] = auth_token
            if identity.tenant_id:
                headers["x-adcp-tenant"] = identity.tenant_id

        body = self._build_update_rest_body(**kwargs)
        req = kwargs.get("req")
        media_buy_id = self._seeded_media_buy_id
        if req is not None and hasattr(req, "media_buy_id") and req.media_buy_id:
            media_buy_id = req.media_buy_id
        endpoint = f"/api/v1/media-buys/{media_buy_id}"
        return client.put(endpoint, json=body, headers=headers)

    def _parse_update_rest_response(self, data: dict[str, Any]) -> Any:
        from src.core.schemas._base import (
            UpdateMediaBuyError,
            UpdateMediaBuyResult,
            UpdateMediaBuySubmitted,
            UpdateMediaBuySuccess,
        )

        # Harness-side union discrimination for the REST/synthesized wires: submitted first
        # (status="submitted"+task_id, no applied media_buy_id — a submitted envelope must
        # not be mis-reconstructed as Success, whose status is Literal completed), then
        # error (non-empty errors), else success. The submitted arm serves the REST wire and
        # the harness-synthesized A2A submitted dict — production A2A has NO submitted
        # reconstruction (Task early-return; PR #1567 round-2 follow-up). The reconstructed
        # union member is wrapped in the UpdateMediaBuyResult task envelope carrying the
        # top-level wire status (#1417).
        status = data.pop("status", "completed")
        response: UpdateMediaBuySubmitted | UpdateMediaBuyError | UpdateMediaBuySuccess
        if status == "submitted":
            response = UpdateMediaBuySubmitted(status=status, **data)
        elif "errors" in data and data["errors"]:
            response = UpdateMediaBuyError(**data)
        else:
            response = UpdateMediaBuySuccess(**data)
        return UpdateMediaBuyResult(response=response, status=status)
