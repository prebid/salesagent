"""REST API v1 endpoints.

REST transport for AdCP tools, proving the 3-transport pattern
(MCP + A2A + REST). Every route reaches its implementation through
``src.core.tools._boundary.invoke_tool``.
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from src.core.exceptions import AdcpFailure
from src.core.resolved_identity import TransportProtocol
from src.core.signing.capture import captured_exchange
from src.core.tools._announced_shape import apply_signature
from src.core.tools._boundary import serve
from src.core.tools._wire import to_wire
from src.core.tools.registry import TOOLS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api-v1"])


# ---------------------------------------------------------------------------------------
# Routes are DERIVED from the registry. There is no @router decorator to write and no body
# model to assign: TOOLS says which tools are reachable over REST, with what verb and at
# what path, and everything else is resolved from the row. Every row with a ``rest`` binding
# gets a route, and the handler names the tool for ``serve``, which reaches the implementation
# through the registry.


async def _payload(request: Request) -> Any:
    """The body as the buyer sent it: parsed JSON, or the raw bytes when it is not JSON.

    Both go to ``validated_request``, which refuses a non-object the same way, so a body that
    is not JSON earns the same INVALID_REQUEST as a JSON list rather than a decode error
    escaping to a handler that types it differently.

    A body that never finished arriving joins them. Starlette raises ``ClientDisconnect`` out
    of ``body()`` when the receive channel answers ``http.disconnect`` instead of the chunk it
    promised, and it DISCARDS what did arrive, so there is nothing left to parse. That is a
    truncated request, not a server fault: it earns the boundary's INVALID_REQUEST like every
    other unparseable body. Letting it escape would leave the one route function that reads
    its own body raising past ``except AdcpFailure`` into a 500 — the reads FastAPI does for a
    declared body model are wrapped this way too (``fastapi/routing.py``, "There was an error
    parsing the body"), and this route declares ``Request`` precisely so that
    ``validated_request`` owns the refusal.
    """
    try:
        raw = await request.body()
    except ClientDisconnect:
        raw = b""
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _rest_handler(tool_name: str, spec: Any) -> Any:
    """One route handler, built from a registry row.

    It takes ``Request`` and validates nothing itself: declaring the DTO as the body parameter
    would make FastAPI validate ahead of this function, and a rejected request owes the buyer
    its ``context`` back, which only ``validated_request`` returns. The published contract is
    unchanged: the route advertises the DTO's JSON Schema through ``openapi_extra``, derived
    from the same model that validates.

    PATH FIELDS are the one place the body is not the whole request. A row whose path is
    templated (``PUT /media-buys/{media_buy_id}``) names those fields in ``path_fields``, and
    the URL is the resource identity, so the path value WINS over a body that disagrees.
    """

    async def handler(request: Request, **path_values: Any) -> Any:
        # ONE try around everything that can produce a failure response, so every failure
        # leaves through the same except and none can escape as a 500.
        try:
            body = await _payload(request)
            if path_values:
                # Caught, not probed for type: a body that is not a JSON object cannot take a
                # path value and is handed on UNMERGED, so ``validated_request`` refuses it
                # with the same INVALID_REQUEST and ``issues`` every other malformed body earns.
                try:
                    body = {**body, **path_values}
                except TypeError:
                    pass
            # The request HEADERS and the captured HTTP MESSAGE, not an identity: the boundary
            # resolves the caller and reads the row's auth declaration itself. The capture
            # comes off the scope rather than from ``await request.body()`` because a
            # signature covers ``@method`` and ``@target-uri`` as well as the bytes, and
            # ``@target-uri`` can only be rebuilt from ``raw_path``.
            response = await serve(
                tool_name, body, request.headers, TransportProtocol.REST, captured_exchange(request.scope)
            )
        except AdcpFailure as failure:
            # REST's wire failure marker is the HTTP STATUS, and that is all this transport
            # adds. The BODY is the response the boundary built, serialized by the same
            # function the success path uses.
            return JSONResponse(status_code=failure.response.http_status, content=to_wire(failure.response))
        return JSONResponse(status_code=200, content=to_wire(response))

    handler.__name__ = tool_name
    handler.__doc__ = (spec.impl.__doc__ or "").strip().split("\n")[0]
    path_params = [
        # Typed from the DTO field, so the path segment is validated as the field it fills.
        inspect.Parameter(
            name,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=spec.dto.model_fields[name].annotation,
        )
        for name in sorted(spec.rest.path_fields)
    ]
    apply_signature(
        handler,
        inspect.Signature(
            [
                *path_params,
                # ``Request``, not the DTO: a typed body parameter is exactly what makes
                # FastAPI validate before the handler runs.
                inspect.Parameter("request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Request),
            ]
        ),
    )
    return handler


def _rest_body_schemas() -> tuple[dict[type[Any], dict[str, Any]], dict[str, Any]]:
    """Every REST DTO's request-body schema, and the ONE ``$defs`` they all refer into.

    One ``models_json_schema`` call over all the DTOs, so pydantic assigns each nested model
    a name that is unique ACROSS the set. Generating per-DTO and merging by name let two models
    that happened to share a name -- ``Status``, ``Disclosure``, seventeen of them -- overwrite
    each other in ``components/schemas``, so a ref from one tool resolved to another tool's
    definition.
    """
    from pydantic import BaseModel
    from pydantic.json_schema import models_json_schema

    # ``cast`` states a guarantee the registry already enforces: ``_register_tool`` refuses a
    # row whose DTO is not a pydantic model, but a row's static type is the ``BuyerRequest``
    # mixin, which cannot say so.
    dtos: list[type[BaseModel]] = [cast(type[BaseModel], spec.dto) for spec in TOOLS.values() if spec.rest is not None]
    per_model, shared = models_json_schema(
        [(dto, "validation") for dto in dtos], ref_template="#/components/schemas/{model}"
    )
    return {dto: per_model[(dto, "validation")] for dto in dtos}, shared.get("$defs", {})


_BODY_SCHEMA_BY_DTO, REST_COMPONENT_SCHEMAS = _rest_body_schemas()


for _name, _spec in TOOLS.items():
    if _spec.rest is None:
        continue
    # The DTO is ADVERTISED here, not enforced: ``openapi_extra`` publishes the model's own
    # JSON Schema so a client reads the shape it always did, while the handler receives the
    # payload untouched and ``serve`` decides it. Both come from one declaration, so the
    # advertised shape and the accepted shape cannot drift.
    #
    # ``$ref``s point into ``#/components/schemas``, and the nested models they name are
    # published there by ``src.app``'s OpenAPI hook. A verbatim ``model_json_schema()`` puts
    # its ``$defs`` at the root of the SCHEMA, but a schema nested under a request body is not
    # the root of the DOCUMENT, so its ``#/$defs/...`` pointers resolved against the document
    # and found nothing -- 1466 dangling references under a comment claiming the contract was
    # unchanged.
    router.add_api_route(
        _spec.rest.path,
        _rest_handler(_name, _spec),
        methods=[_spec.rest.verb],
        name=_name,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": _BODY_SCHEMA_BY_DTO[_spec.dto]}},
            }
        },
    )
