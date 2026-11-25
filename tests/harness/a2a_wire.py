"""A2A framing and evidence readers, independent of dispatch owners."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from tests.harness.transport import (
    Transport,
    TransportResult,
    WireError,
    WireRefusal,
    _jsonrpc_body,
    _wire_envelope,
    _wire_envelope_from_exception,
    derive_error_status,
)


def _a2a_first_data_part(task: dict[str, Any]) -> dict[str, Any]:
    """Read the one artifact DataPart of a finished native invocation.

    AdCP 3.2.7 L0/a2a-profile-extension.mdx completes each invocation,
    including submitted work and polls. Both HTTP and protobuf readers use
    this check, so neither can accept the retired status-message payload.
    """
    status = task.get("status")
    if not isinstance(status, dict):
        raise ValueError("Expected an A2A Task status object")
    state = status.get("state")
    if state not in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED"}:
        raise ValueError(f"Expected a completed or failed native A2A Task, got {state!r}")
    artifacts = task.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1 or not isinstance(artifacts[0], dict):
        raise ValueError("Expected exactly one A2A artifact object")
    parts = artifacts[0].get("parts")
    if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
        raise ValueError("Expected an A2A artifact parts array")
    data = [part["data"] for part in parts if "data" in part]
    if len(data) != 1 or not isinstance(data[0], dict):
        raise ValueError("Expected exactly one object DataPart in the A2A artifact")
    return data[0]


def _a2a_error_envelope(data: Any) -> dict[str, Any] | None:
    """AdCP 3.2.7 permits an envelope-only failure; retain either emitted layer."""
    if not isinstance(data, dict):
        return None
    return data if isinstance(data.get("adcp_error"), dict) else _wire_envelope(data)


def _jsonrpc_error_evidence(error: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The complete refusal and its AdCP data, without reading message text."""
    metadata = {"jsonrpc_error": error}
    if isinstance(error, dict):
        metadata["jsonrpc_error_code"] = error.get("code")
        return metadata, _a2a_error_envelope(error.get("data"))
    return metadata, None


def native_task_result(transport: Transport, task: dict[str, Any], *, response: Any = None) -> TransportResult:
    """Read one native receipt, retaining its authentic Task and AdCP body.

    AdCP 3.2.7 L0 response mapping distinguishes business results from fatal
    adcp_error failures. Advisory errors[] alone do not fail a completed result.
    """
    from pydantic import RootModel

    metadata = {"transport": transport.value, "task": task}
    data = None
    wire_error = None
    error = None
    try:
        data = _a2a_first_data_part(task)
        metadata.update(a2a_task_id=task["id"], a2a_task_state=task["status"]["state"])
        failed = task["status"]["state"] == "TASK_STATE_FAILED"
        has_adcp_error = isinstance(data.get("adcp_error"), dict)
        if failed or has_adcp_error:
            wire_error = _a2a_error_envelope(data)
            if wire_error is not None:
                raise WireError(wire_error, response)
            raise WireRefusal("Failed A2A Task carried no AdCP error envelope", response)
    except (ValueError, TypeError, KeyError) as exc:
        error = WireRefusal(str(exc), response)
        error.__cause__ = exc
    except (WireError, WireRefusal) as exc:
        error = exc
    if error is not None:
        metadata["status"] = derive_error_status(wire_error)
    result = TransportResult(
        has_wire=False,
        payload=RootModel(copy.deepcopy(data)) if error is None else None,
        error=error,
        envelope=metadata,
        wire_response=data,
        wire_error_envelope=wire_error,
    )
    if response is not None:
        return replace(
            result, has_wire=True, raw_response=response, envelope={**metadata, "status_code": response.status_code}
        )
    return result


def jsonrpc_http_result(transport: Transport, response: Any) -> TransportResult:
    """Read HTTP framing once; native_task_result owns the Task outcome."""

    metadata = {"transport": transport.value, "status_code": response.status_code}
    wire_error = None
    try:
        frame = _jsonrpc_body(response, surface="/a2a")
        if not isinstance(frame, dict):
            raise WireRefusal("A2A returned a non-object JSON-RPC frame", response)
        if "error" in frame:
            refusal = frame["error"]
            evidence, wire_error = _jsonrpc_error_evidence(refusal)
            metadata.update(evidence)
            if wire_error is not None:
                raise WireError(wire_error, response)
            raise WireRefusal(f"A2A JSON-RPC refusal: {refusal!r}", response)
        result = frame.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("task"), dict):
            raise WireRefusal("A2A JSON-RPC result carried no native Task", response)
        captured = native_task_result(transport, result["task"], response=response)
        if response.status_code >= 400 and captured.error is None:
            return replace(
                captured,
                payload=None,
                error=WireRefusal(f"A2A returned HTTP {response.status_code} without an AdCP error", response),
                envelope={**captured.envelope, "status": derive_error_status(None)},
            )
        return captured
    except (WireError, WireRefusal, ValueError, TypeError, KeyError, AssertionError) as exc:
        error = exc
        metadata["status"] = derive_error_status(wire_error)
    return TransportResult(
        has_wire=True,
        error=error,
        envelope=metadata,
        raw_response=response,
        wire_error_envelope=wire_error,
    )


def parse_response_result(result: TransportResult, parser: Callable[[dict[str, Any]], Any]) -> TransportResult:
    """Parse a copy once, retaining the original parser exception and evidence."""
    if result.error is not None:
        return result
    try:
        return replace(result, payload=parser(copy.deepcopy(result.wire_response)))
    except Exception as exc:
        return replace(result, payload=None, error=exc)


def _response_reader(model: Any) -> Callable[[dict[str, Any]], Any]:
    """Read served responses through their boundary-aware reader when available."""
    if model is None:
        return lambda data: None
    revive = getattr(model, "revive", None)
    return revive if revive is not None else lambda data: model(**data)


def unwrap_delivery_error(exc: Exception, transport: Transport = Transport.A2A) -> TransportResult:
    """Capture a pre-response delivery exception without re-reading any frame."""
    from a2a.utils.errors import JSON_RPC_ERROR_CODE_MAP, A2AError

    wire = _wire_envelope_from_exception(exc)
    metadata = {"transport": transport.value}
    if isinstance(exc, A2AError):
        code = JSON_RPC_ERROR_CODE_MAP[type(exc)]
        evidence, wire = _jsonrpc_error_evidence({"code": code, "message": exc.message, "data": exc.data})
        metadata.update(evidence)
    return TransportResult(
        has_wire=False,
        error=exc,
        envelope={**metadata, "status": derive_error_status(wire)},
        wire_error_envelope=wire,
    )


def _a2a_protobuf_task(task: Any) -> dict[str, Any]:
    """Match the HTTP route's integer restoration at the protobuf conversion edge."""
    from google.protobuf import json_format

    from src.a2a_server.adcp_a2a_server import restore_a2a_integer_types

    return restore_a2a_integer_types(json_format.MessageToDict(task))


def send_inprocess_a2a(request: Any, context: Any) -> TransportResult:
    """Call the native handler once and preserve any original delivery exception."""
    from a2a.types import Task

    from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

    try:
        task = asyncio.run(AdCPRequestHandler().on_message_send(request, context))
        if not isinstance(task, Task):
            raise TypeError(f"Expected Task, got {type(task).__name__}: {task}")
        return native_task_result(Transport.A2A, _a2a_protobuf_task(task))
    except Exception as exc:
        return unwrap_delivery_error(exc, Transport.A2A)
