"""MediaBuyDualEnv — composite environment for UC-026 and UC-003 BDD scenarios.

UC-026 scenarios use both create and update flows within the same test:
Given steps create a media buy (create path), then When steps update it
(update path). UC-003 drives the update path directly against
a pre-seeded media buy to grade the manual-approval UpdateMediaBuySubmitted
envelope cross-transport. This env extends MediaBuyCreateEnv with update-module
patches and delegates update requests to the appropriate production code —
A2A/MCP go through the real on_message_send / FastMCP Client pipelines so the
serialized wire (and the A2A submitted reconstruction) are genuinely exercised.

"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.core.schemas import UpdateMediaBuyRequest
from tests.harness._mixins import make_adapter_update_side_effect
from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.transport import DeliverResult

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

    def seed_existing_media_buy(self, media_buy_id: str, *, status: str = "active", revision: int | None = None) -> Any:
        """Persist an existing media buy for the update path, and route to it.

        The one public way to seed the row every update-path wire test needs: it creates
        the tenant/principal/product, mints the buy owned by that principal, commits the
        factory data so the row is visible to the separate unit of work the update flow
        opens, and sets the routing id the flat-kwargs wire legs read (the REST leg builds
        its PUT URL from it). ``revision`` is a repository-managed seam the factory assigns
        the way the repository does, so a test can start from a row production reaches.
        Returns the persisted media buy.
        """
        from tests.factories import MediaBuyFactory

        tenant, principal, _product, _pricing = self.setup_media_buy_data()
        factory_kwargs: dict[str, Any] = {
            "tenant": tenant,
            "principal": principal,
            "media_buy_id": media_buy_id,
            "status": status,
        }
        if revision is not None:
            factory_kwargs["revision"] = revision
        media_buy = MediaBuyFactory(**factory_kwargs)
        self._commit_factory_data()
        self._seeded_media_buy_id = media_buy_id
        return media_buy

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

    def _run_rest_request(self, endpoint: str, **kwargs: Any) -> Any:
        # Set the update-vs-create routing flag and leave it set THROUGH the base
        # dispatch's subsequent parse_rest_response call: the base dispatch runs
        # _run_rest_request then parse_rest_response sequentially, so a finally-reset
        # here would flip the flag back before the parse and misroute the update
        # response to the create parser (yielding None). parse_rest_response resets
        # it after routing, and each request re-sets it (False on create requests).
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
        """Flatten an ``UpdateMediaBuyRequest`` into flat wire parameters.

        The A2A skill and MCP tool accept a flat param dict, not a request model,
        and reject the wrapper-unsupported fields — so pop ``req``, expand it
        (dropping those fields), then overlay any explicit kwargs. ``identity``
        (if present) is passed through; the real handlers pop and apply it.
        Shared by the A2A, MCP and REST update paths (DRY) — REST adapts the
        result in :meth:`_build_update_rest_body` rather than re-spelling it.
        """
        req = kwargs.pop("req", None)
        if req is None:
            return dict(kwargs)
        flat = req.model_dump(mode="json", exclude_none=True)
        flat.update(kwargs)
        return flat

    def _call_update_a2a(self, **kwargs: Any) -> DeliverResult:
        # Drive the REAL on_message_send → _serialize_for_a2a → Task/Artifact
        # pipeline (mirrors MediaBuyCreateEnv.call_a2a), so _run_a2a_handler stashes
        # the true artifact DataPart as the wire_response and the submitted
        # reconstruction in adcp_a2a_server (union discrimination) runs. A prior
        # version synthesized the wire via update_media_buy_raw(...).model_dump(),
        # which tracked the return model rather than the assembled envelope — an
        # update-envelope regression would not be caught. The union
        # (submitted|success|error) needs status/media_buy_id discrimination, so
        # reconstruct via _parse_update_rest_response.
        return self._run_a2a_handler(
            "update_media_buy",
            lambda **data: self._parse_update_rest_response(data),
            **self._flatten_update_request(kwargs),
        )

    def _call_update_mcp(self, **kwargs: Any) -> DeliverResult:
        # Drive the REAL FastMCP Client pipeline (mirrors MediaBuyCreateEnv.call_mcp) so the
        # structured_content — the real MCP wire body — is stashed as wire_response and the
        # full middleware/auth chain runs, including the production with_error_logging
        # boundary decorator (src/core/main.py: mcp.tool()(with_error_logging(fn))): on
        # error it translates the raised AdCPError into an AdCPToolError carrying the
        # two-layer wire envelope, which the dispatcher captures as wire_error_envelope
        # (#1417). A prior version hand-built a mocked Context and invoked the wrapper
        # directly, which bypassed the client/middleware chain.
        return self._run_mcp_client(
            "update_media_buy",
            lambda **data: self._parse_update_rest_response(data),
            **self._flatten_update_request(kwargs),
        )

    def _build_update_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """The REST body, built by the SAME flatten the A2A and MCP paths use.

        Three REST-specific differences, and only three: ``identity`` is resolved
        by ``_prepare_rest_request`` rather than travelling in the body,
        ``media_buy_id`` rides the URL, and the response is parsed from HTTP.

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
        kwargs.pop("identity", None)
        body = self._flatten_update_request(kwargs)
        body.pop("media_buy_id", None)
        return body

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
            UpdateMediaBuySubmitted,
            UpdateMediaBuySuccess,
        )

        # Mirror the production A2A union discrimination (adcp_a2a_server.py:484-489):
        # submitted first (status="submitted"+task_id, no applied media_buy_id — a submitted
        # envelope must not be mis-reconstructed as Success, whose status is Literal completed),
        # then success (has media_buy_id), else error.
        if data.get("status") == "submitted":
            return UpdateMediaBuySubmitted(**data)
        if "media_buy_id" in data:
            # Bare construction on purpose, not carrier(): this reconstructs a response
            # FROM THE WIRE, so a missing spec-required `revision` must raise here rather
            # than be filled in with a placeholder that hides the gap.
            return UpdateMediaBuySuccess(**data)
        return UpdateMediaBuyError(**data)
