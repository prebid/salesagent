"""Transport request -> AdCP operation name: ONE registry, derived from the transports.

#1291 B2 (``salesagent-z6nr.13``), plan steps 1, 3 and 5 as amended by the refinement.

Core invariant
--------------
Every inbound AdCP request is NAMED before a posture decides anything about it —
named in exactly one of the two disjoint namespaces the schema defines (an AdCP
operation with no ``/``, or a JSON-RPC protocol method with one), from ONE registry
derived from the transport registries that already exist, so that no request can
reach the posture as "unnamed, therefore not required".

The two namespaces are mutually exclusive, and that is MANDATORY
--------------------------------------------------------------
:meth:`~src.core.signing.posture.RequestSigningPosture.bucket_for` grades ONLY the
``protocol_methods_*`` trio whenever ``protocol_method`` is not None and IGNORES
``operation``. So an MCP ``tools/call`` resolved into BOTH namespaces — both are true
of the wire — would silently disable ``required_for`` across the entire MCP surface
while looking more informative. Every branch below therefore returns exactly one of
the two, never both.

The consequence to know about: an A2A ``message/send`` WITH an explicit skill returns
``(skill, None)``, so a ``protocol_methods_*`` entry naming ``message/send`` never
fires on explicit-skill calls. Acceptable — the spec's protocol-method table
contemplates only the ``tasks/*`` lifecycle — but D1 (``salesagent-z6nr.20``) should
warn on such a declaration.

Where the name comes from, per surface
--------------------------------------
=========================  =========================================  ======================
Surface                    Field that names it                        Result
=========================  =========================================  ======================
``/api/v1/...``            the route table (method AND path)          ``(operation, None)``
``/mcp`` ``tools/call``    ``params.name``                            ``(name, None)``
``/mcp`` other method      the JSON-RPC ``method``                    ``("", method)``
``/a2a`` ``message/send``  ``params.message.parts[].data.skill``      ``(skill, None)``
``/a2a`` no explicit skill the JSON-RPC ``method``                    ``("", method)``
``/mcp`` or ``/a2a``, no   nothing — a transport session frame        ``("", None)``
body
=========================  =========================================  ======================

REST and A2A carry no ``tools/call``, and security.mdx :1053 read literally ("a
``required_for`` membership MUST NOT be satisfied by a body whose JSON-RPC ``method``
is anything other than ``tools/call``") would therefore bar both — contradicting the
same section's own "this is how cross-transport verifiers agree on what 'signed for
create_media_buy' means", and contradicting compliance vectors 001 and 027, which are
plain ``required_for``-shaped POSTs with no JSON-RPC envelope at all. The sentence
governs which FIELD names the operation when the body IS a JSON-RPC envelope: never
the envelope ``method``. ``params.name`` and ``data.skill`` are such fields.

Where the CREDENTIALS come from is a different axis, and it is per LOCATION
--------------------------------------------------------------------------
The table above answers "what is this request called". The webhook-credential
escalation (security.mdx :1375, :1462-1465) answers "does this request hand the seller
credentials", and it must NOT be enumerated the same way: a method list has a default
arm, and a default arm on this question is a default-ACCEPT over every method the list
does not mention. It is enumerated by LOCATION instead — see ``_CONFIG_LOCATIONS`` and
``_application_payloads`` below, and the block of prose above them for what that
distinction cost.

Fail closed on the unnameable, not on the merely unnamed
--------------------------------------------------------
``resolvable=False`` is reserved for a request on an AdCP surface that carries a body
naming nothing: a non-JSON body, no ``method`` key, a ``tools/call`` with no
``params.name``, or an ``/api/v1`` path matching no route. The middleware promotes
those to the strictest bucket the posture declares. What is NOT unresolvable:

* a JSON-RPC method that names no operation (``initialize``, ``tools/list``) — it is
  named in the protocol namespace, and having no ``/`` it can never legally appear in
  a ``protocol_methods_*`` list, so it lands in supported/none by construction rather
  than by special case. That is what keeps plan step 5 from 401-ing every MCP
  handshake;
* a BODILESS ``/mcp`` or ``/a2a`` request (R-M3) — the streamable-HTTP session frames.
  Tested BEFORE the unresolvable rule, because "no body" trivially satisfies "not
  JSON" and every SSE stream open would otherwise 401 under any ``required_for``.

Classification lives in the guard, not here
-------------------------------------------
MCP tool names and A2A skill ids are IDENTITY with AdCP operation names for our
registrations, so this module emits them verbatim; whether a given name is a real AdCP
operation (``create_media_buy``) or one of ours that is not (``get_task``,
``approve_creative``) is asserted by ``tests/unit/test_architecture_signing_operations.py``
against ``ADCP_TOOL_DEFINITIONS``. Blanking a non-AdCP name here would recreate the
silent-``("", None)`` failure this module exists to remove; a non-AdCP name simply
cannot match a bucket, because the schema forbids one appearing in a declaration.

Spec grounding: AdCP 3.1.1 via ``adcp==6.6.0``;
``v3.1.1:docs/building/by-layer/L1/security.mdx`` :1045-1059 (the two namespaces and
the cross-namespace prohibition), :1375 and :1462-1465 (the webhook-registration
escalation), and compliance vectors 001 / 027 / 028.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from src.core.http_utils import path_from_asgi_scope

# The operation VOCABULARY moved to the leaf so src.core.metrics can bound its
# attacker-chosen label without importing this package (salesagent-n78j0.3). RE-EXPORTED
# here — not merely imported — because the resolver below, the signing facade and
# src/routes/rest_compat_middleware.py already read these names from this module. One
# table, the same readers, unchanged import sites.
from src.core.signing_contract.vocabulary import (  # noqa: F401
    operation_for_rest_route,
    resolved_operation_names,
    sdk_operation_names,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The SURFACE vocabulary: which paths are AdCP protocol surfaces at all
# ---------------------------------------------------------------------------
#
# PUBLISHED HERE, not in the verifier, because the verifier is not its only reader:
# the resolver below dispatches on the same three strings, and the structural guard
# grades the same boundary. It lived in ``request_verifier_middleware`` with the rule
# re-typed at every reader, and the cost was measured: the segment boundary existed in
# FOUR places, so rewriting the verifier's copy to a bare ``startswith`` left the guard
# grading its own intact copy and all of ``TestAllowlistTiedToRouteTable`` green. One
# definition, imported by every reader — the verifier, the resolver and the guard.

#: The AdCP protocol surfaces, and ONLY those. Everything else — admin, health, debug,
#: the landing page, the A2A agent card and every A3 trust-root document — passes
#: through untouched by construction rather than by remembering to exempt it.
#: Prefix + segment boundary, so ``/api/v1x`` cannot sneak in.
#:
#: Note ``/a2a/`` (trailing slash) only 307-redirects to ``/a2a``; it matches the
#: allowlist and is verified before being bounced, which is harmless.
ADCP_SURFACE_PREFIXES: tuple[str, ...] = ("/mcp", "/a2a", "/api/v1")

#: The three surfaces, named for the resolver's dispatch. UNPACKED from the allowlist
#: rather than re-typed, so the resolver dispatches on exactly the strings the verifier
#: scopes on. A fourth prefix added above and named nowhere fails LOUDLY here, at
#: import. The case this unpack CANNOT catch — a fourth prefix named here and given no
#: arm in :meth:`RegistryOperationResolver.resolve` — is caught at the other end, by a
#: fall-through that asks :func:`is_adcp_surface` and answers ``resolvable=False``.
#: Between them, a surface the verifier scopes over can never be a surface the resolver
#: waves through.
_MCP_PREFIX, _A2A_PREFIX, _REST_PREFIX = ADCP_SURFACE_PREFIXES


def matches_surface_prefix(path: str, prefix: str) -> bool:
    """Prefix match on a SEGMENT BOUNDARY, so ``/api/v1x`` cannot sneak in.

    The one boundary rule the whole layer shares. ``/mcp`` matches ``/mcp`` and
    ``/mcp/anything``; it does not match ``/mcpx``. A bare ``str.startswith`` would,
    and would silently pull a non-AdCP surface under the verifier.
    """
    return path == prefix or path.startswith(f"{prefix}/")


def is_adcp_surface(path: str) -> bool:
    """Whether *path* targets one of the AdCP protocol surfaces.

    THE boundary predicate: the verifier scopes itself with this (through
    :func:`src.core.signing.request_verifier_middleware._is_adcp_surface`, which only
    adapts the ASGI scope to a path), and the structural guard classifies
    ``app.routes`` with it. Neither holds a copy.
    """
    return any(matches_surface_prefix(path, prefix) for prefix in ADCP_SURFACE_PREFIXES)


@dataclass(frozen=True)
class ResolvedOperation:
    """What one inbound request is NAMED, plus the two decisions naming it produces.

    ``operation`` and ``protocol_method`` are mutually exclusive (see the module
    docstring): a protocol method is graded against a different trio of buckets and
    takes precedence, so filling both hides the other.
    """

    #: The AdCP operation name, or ``""`` when this request is a protocol method or a
    #: session frame. Never contains ``/``.
    operation: str
    #: The JSON-RPC wire method, or ``None`` when the request names an operation.
    protocol_method: str | None
    #: security.mdx :1462-1465 — the payload registers webhook credentials, so a
    #: signature is mandatory whatever bucket the operation falls in.
    signature_forced: bool = False
    #: False only for a request on an AdCP surface whose body names nothing at all.
    resolvable: bool = True


#: The identity element: an unnamed, nameable request. ``"" in required_for`` is False
#: for every real declaration, so it can never fail closed on a guessed operation.
UNNAMED_OPERATION = ResolvedOperation(operation="", protocol_method=None)


@runtime_checkable
class OperationResolver(Protocol):
    """Names the AdCP operation (and JSON-RPC method) an inbound request invokes."""

    def resolve(self, scope: Mapping[str, Any], headers: Mapping[str, str], body: bytes) -> ResolvedOperation:
        """Return the :class:`ResolvedOperation` for this request.

        ``body`` is the fully-buffered request body: on two of the three transports
        the operation name lives IN the body, and the payload escalation lives there
        on all three. Pure — no I/O, so callers may run it on the event loop.
        """
        ...


class UnresolvedOperationResolver:
    """The inert default: every request is an unnamed operation.

    Kept as the identity element the middleware can be constructed with in a test
    that wants naming out of the picture; production gets
    :class:`RegistryOperationResolver`.
    """

    def resolve(self, scope: Mapping[str, Any], headers: Mapping[str, str], body: bytes) -> ResolvedOperation:
        return UNNAMED_OPERATION


# ---------------------------------------------------------------------------
# The REST leg: derived from the route table, never hand-listed
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The JSON-RPC leg: MCP and A2A
# ---------------------------------------------------------------------------

#: The MCP envelope that CARRIES an operation rather than being one.
_TOOL_CALL = "tools/call"

#: The A2A envelopes that carry an explicit skill. ``message/stream`` is the streaming
#: sibling of ``message/send`` and uses the identical part shape.
_MESSAGE_METHODS = frozenset({"message/send", "message/stream"})


def _json_object(body: bytes) -> dict[str, Any] | None:
    """Parse *body* as a JSON object, leniently — for NAMING only.

    Duplicate keys, wrong types and every other malformation are the checklist's to
    reject (security.mdx step 14, ``request_body_malformed``, owned by the SDK). A
    resolver that rejected here would answer the wrong code at the wrong step.
    """
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _authenticated_configs(configs: Any) -> bool:
    """Whether any notification config in *configs* carries an ``authentication`` block."""
    candidates = configs if isinstance(configs, list) else [configs]
    return any(isinstance(config, dict) and config.get("authentication") is not None for config in candidates)


def _forces_signature(payload: Any) -> bool:
    """security.mdx :1462-1465 — does this payload register webhook credentials?

    "Sellers that support request signing MUST require the inbound request to be
    9421-signed … when ``authentication`` is present on
    ``push_notification_config.authentication`` or any
    ``accounts[].notification_configs[].authentication``", restated at :1375 as a
    trigger "regardless of ``required_for`` membership".

    BOTH triggers, not just the first: only ``push_notification_config`` has a
    compliance vector, so a resolver handling it alone passes all 40 vectors and is
    still wrong.
    """
    if not isinstance(payload, dict):
        return False
    if _authenticated_configs(payload.get("push_notification_config")):
        return True
    accounts = payload.get("accounts")
    if not isinstance(accounts, list):
        return False
    return any(
        isinstance(account, dict) and _authenticated_configs(account.get("notification_configs"))
        for account in accounts
    )


def _data_parts(params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The ``data`` payloads of an A2A message's data parts.

    Envelope shape: ``params.message.parts[{kind: "data", data: {skill, input}}]``
    (``src/a2a_server/adcp_a2a_server.py:566-583``).
    """
    message = params.get("message")
    if not isinstance(message, dict):
        return []
    parts = message.get("parts")
    if not isinstance(parts, list):
        return []
    return [part["data"] for part in parts if isinstance(part, dict) and isinstance(part.get("data"), dict)]


def _explicit_skill(params: Mapping[str, Any]) -> str:
    """The skill id an A2A ``message/send`` names explicitly, or ``""``.

    Empty for the natural-language invocation path (``adcp_a2a_server.py:607``), which
    is a real production shape and is named as its protocol method instead.
    """
    for data in _data_parts(params):
        skill = data.get("skill")
        if isinstance(skill, str) and skill:
            return skill
    return ""


# ---------------------------------------------------------------------------
# The CREDENTIAL LOCATIONS: every place a transport can hand over webhook
# credentials — which is what the escalation is a claim about
# ---------------------------------------------------------------------------
#
# The enumeration below is of PLACES, and it is run against EVERY JSON-RPC body
# whatever its method. The axis is the whole point, and having it wrong is SF-4:
# the escalation used to enumerate each transport's TOOL-ARGUMENT envelope —
# ``params.arguments`` for ``tools/call``, ``data.input`` / ``data.parameters`` for
# ``message/send`` — and answer False for every other method. That final answer was
# not "this method carries no credentials"; it was a DEFAULT-ACCEPT over the entire
# rest of the JSON-RPC surface. ``tasks/pushNotificationConfig/set`` registers a
# webhook AND its credentials with no skill invocation anywhere in sight — answered by
# ``AdCPRequestHandler.on_create_task_push_notification_config``, which persists them —
# and inherited that bypass, as would every method the next SDK release adds.
#
# ``src/services/protocol_webhook_service.py`` :7-13 already writes the enumeration
# down, and it is by CONFIGURATION CHANNEL rather than by method:
#
#     protocol-level   A2A: MessageSendConfiguration.pushNotificationConfig
#                      MCP: (future) protocol wrapper extension
#     application      AdCP: the request's own payload
#
# so there are exactly two kinds of location here, and they differ in what the value
# AT the location is:
#
# * APPLICATION payload — an AdCP request body, with the config INSIDE it at
#   ``push_notification_config`` or ``accounts[].notification_configs``;
#   :func:`_forces_signature` is the reader.
# * PROTOCOL configuration — the notification config ITSELF;
#   :func:`_authenticated_configs` is the reader.
#
# MCP contributes no protocol-configuration location because it has none yet — the
# service's own "(future) protocol wrapper extension". When it grows one, it is one
# more entry in the table below and not a new method arm. That is the axis.


def _both_spellings(field: str) -> tuple[str, str]:
    """*field* under both spellings the ``/a2a`` wire accepts.

    ``src/app.py`` builds the one ``/a2a`` route with ``enable_v0_3_compat=True``, so
    the route serves both A2A generations. The v1.0 methods carry protobuf JSON, and
    ``ParseDict`` accepts a field under its ``json_name`` AND its declared proto name;
    the v0.3 compat models are pydantic with ``validate_by_name`` AND
    ``validate_by_alias`` under a camelCase alias generator. So ``pushNotificationConfig``
    and ``push_notification_config`` are the SAME location on the wire, and reading only
    one of them would leave the other an unwatched channel. Derived rather than typed
    out twice, because a typo in a hand-written camelCase spelling fails silently — as
    an accepted credential registration.
    """
    head, *rest = field.split("_")
    return field, head + "".join(part.title() for part in rest)


#: ``(container path, field)`` for every place a JSON-RPC envelope carries a
#: notification config of its OWN. Each expands to both wire spellings below.
_CONFIG_FIELDS: tuple[tuple[tuple[str, ...], str], ...] = (
    # A2A v0.3 ``tasks/pushNotificationConfig/set`` — ``TaskPushNotificationConfig``,
    # a registration on its own JSON-RPC method with no skill invocation at all.
    ((), "push_notification_config"),
    # A2A v0.3 ``message/send`` / ``message/stream`` — ``MessageSendConfiguration``,
    # where ``adcp_a2a_server.on_message_send`` READS the config it persists.
    (("configuration",), "push_notification_config"),
    # The same field on the v1.0 ``SendMessage`` envelope, where the proto names it
    # ``configuration.task_push_notification_config``.
    (("configuration",), "task_push_notification_config"),
)

#: The protocol-configuration locations, as dotted paths into ``params``.
#:
#: The empty path leads, and is not a degenerate case: on A2A v1.0's
#: ``CreateTaskPushNotificationConfig`` the params ARE the config — the proto is flat,
#: so ``url`` / ``token`` / ``authentication`` sit directly on them, which is the shape
#: ``on_create_task_push_notification_config`` reads (``params.authentication.credentials``).
_CONFIG_LOCATIONS: tuple[tuple[str, ...], ...] = ((),) + tuple(
    container + (spelling,) for container, field in _CONFIG_FIELDS for spelling in _both_spellings(field)
)


def _at_path(params: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    """The value *path* names inside *params*, or ``None`` when it names nothing."""
    node: Any = params
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _application_payloads(params: Mapping[str, Any]) -> list[Any]:
    """Every place this envelope can carry an AdCP request PAYLOAD.

    ``params.arguments`` is the MCP ``tools/call`` slot; ``data.input`` and
    ``data.parameters`` are the A2A message-part slots. Read without asking what the
    ``method`` is — the location is the claim, so a method that reuses one of these
    envelopes is covered the day it exists rather than the day someone remembers it.
    """
    payloads: list[Any] = [params.get("arguments")]
    for data in _data_parts(params):
        payloads.append(data.get("input"))
        payloads.append(data.get("parameters"))
    return payloads


def _jsonrpc_payload_forces_signature(params: Mapping[str, Any]) -> bool:
    """Does this JSON-RPC envelope hand the seller webhook credentials ANYWHERE?

    Every credential location the surface carries, per the two tables above — never a
    list of methods, and so never a default answer for the methods such a list forgot.
    """
    if any(_forces_signature(payload) for payload in _application_payloads(params)):
        return True
    return any(_authenticated_configs(_at_path(params, path)) for path in _CONFIG_LOCATIONS)


def _resolve_jsonrpc(body: bytes) -> ResolvedOperation:
    """Name an ``/mcp`` or ``/a2a`` request off its JSON-RPC envelope."""
    if not body.strip():
        # R-M3: streamable-HTTP session frames (GET/DELETE ``/mcp``) carry no body.
        # A non-operation BY CONSTRUCTION, decided ahead of the unresolvable test —
        # "no body" trivially satisfies "not JSON", and promoting it would 401 every
        # SSE stream open under any posture declaring required_for.
        return UNNAMED_OPERATION

    envelope = _json_object(body)
    if envelope is None:
        return ResolvedOperation(operation="", protocol_method=None, resolvable=False)

    method = envelope.get("method")
    if not isinstance(method, str) or not method:
        return ResolvedOperation(operation="", protocol_method=None, resolvable=False)

    raw_params = envelope.get("params")
    params: Mapping[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    forced = _jsonrpc_payload_forces_signature(params)

    if method == _TOOL_CALL:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return ResolvedOperation(operation="", protocol_method=None, signature_forced=forced, resolvable=False)
        # The tool name is the OPERATION and ``tools/call`` is deliberately dropped:
        # returning it as the protocol method would route the grading to
        # protocol_methods_* and disable required_for on the whole MCP surface.
        return ResolvedOperation(operation=name, protocol_method=None, signature_forced=forced)

    if method in _MESSAGE_METHODS:
        skill = _explicit_skill(params)
        if skill:
            return ResolvedOperation(operation=skill, protocol_method=None, signature_forced=forced)

    return ResolvedOperation(operation="", protocol_method=method, signature_forced=forced)


class RegistryOperationResolver:
    """The production resolver: the REST route table plus the two JSON-RPC envelopes.

    Stateless and pure. The route registry it reads is derived once and cached; every
    other transport's name is identity off the wire, so there is nothing else to
    maintain and nothing that can drift without failing
    ``tests/unit/test_architecture_signing_operations.py``.
    """

    def resolve(self, scope: Mapping[str, Any], headers: Mapping[str, str], body: bytes) -> ResolvedOperation:
        path = path_from_asgi_scope(scope)

        if matches_surface_prefix(path, _MCP_PREFIX) or matches_surface_prefix(path, _A2A_PREFIX):
            return _resolve_jsonrpc(body)

        if matches_surface_prefix(path, _REST_PREFIX):
            operation = operation_for_rest_route(str(scope.get("method", "GET")).upper(), path)
            forced = _forces_signature(_json_object(body))
            if not operation:
                logger.warning(
                    "No /api/v1 route names %s %s; the request cannot be named and will be "
                    "graded against the strictest bucket this tenant declares",
                    scope.get("method"),
                    path,
                )
                return ResolvedOperation(operation="", protocol_method=None, signature_forced=forced, resolvable=False)
            return ResolvedOperation(operation=operation, protocol_method=None, signature_forced=forced)

        if is_adcp_surface(path):
            # An AdCP surface with no dispatch arm above. UNREACHABLE TODAY — every
            # prefix in ADCP_SURFACE_PREFIXES has an arm — and this is a DRIFT guard,
            # not a live hole: a fourth prefix added to the vocabulary and taught to
            # the arms nowhere would otherwise land in the fall-through below and be
            # answered "nameable, and named nothing", which is the one answer that
            # exempts a brand-new AdCP surface from every ``required_for`` the tenant
            # declares. Deciding it off the CONSTANT rather than off the arms is what
            # makes the two impossible to drift apart. The answer is the one the
            # ``/api/v1`` arm already gives a route it cannot name.
            logger.warning(
                "%s is an AdCP surface (ADCP_SURFACE_PREFIXES) that this resolver has no "
                "arm for; the request cannot be named and will be graded against the "
                "strictest bucket this tenant declares",
                path,
            )
            return ResolvedOperation(operation="", protocol_method=None, resolvable=False)

        # Not an AdCP surface at all. The middleware never asks about these (its
        # allowlist runs first), so this is the answer for a direct caller, not a
        # bypass.
        return UNNAMED_OPERATION


# ---------------------------------------------------------------------------
# The VOCABULARY: every value ``ResolvedOperation.operation`` can carry
