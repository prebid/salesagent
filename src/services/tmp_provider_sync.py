"""TMP Provider package sync service.

Pushes package definitions from the Sales Agent to every syncable (active or
draining) TMP Provider for a tenant whenever a media buy is created or updated.

Per the AdCP TMP spec (Package Sync section):
  "Package metadata is synced from seller agents to TMP providers at media buy
   creation time and whenever the media buy materially changes."

Each synced AvailablePackage includes a seller_agent reference so the TMP
Provider can attribute offers back to the originating seller agent.

Design principles:
- Triggered ONCE per media-buy write, by ``@fires_tmp_sync`` on
  ``_create_media_buy_impl`` / ``_update_media_buy_impl``. The sync is a
  transport-agnostic consequence of a write — it takes the ``_impl`` return value
  and an already-resolved ``ResolvedIdentity`` — so it belongs at the one place
  every transport passes through, not at the four transport edges it used to sit
  at, where a fifth entry point could silently forget it. Do not add a trigger
  anywhere else: a second one (a transport wrapper, a route-layer
  ``BackgroundTasks``) would double-fire, and the decorator already covers every
  caller of both impls.
- Spawns a daemon thread so the caller is never blocked, registered in
  ``_active_syncs`` (the shared ``ThreadRegistry``) so two writes to the same media
  buy serialize and callers have a deterministic drain
  (``join_active_syncs()``) instead of patching ``threading.Thread``.
- Reads packages and provider endpoints via **repositories** (UoW pattern) —
  no raw get_db_session() / select() calls.
- HTTP calls are made **after** the DB session is closed — no open transaction
  during network I/O.
- Failures are per item, never per batch, and are **swallowed after logging**:
  nothing is re-raised out of the fan-out, because the media buy is already
  committed and one provider's failure must not affect another's delivery or the
  buyer's response.

beads: salesagent-tmp-sync
"""

from __future__ import annotations

import functools
import inspect
import logging
import os
import threading
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, cast

from adcp.types import AvailablePackage, SellerAgentReference
from pydantic import JsonValue, ValidationError

from src.core.database.models import MediaPackage, TMPProvider
from src.core.database.repositories.uow import MediaBuyUoW, TenantConfigUoW, TMPProviderUoW
from src.core.domain_config import is_local_host
from src.core.exceptions import AdCPConfigurationError
from src.core.logging_config import log_safe
from src.core.schemas import FormatId
from src.core.schemas._base import (
    CreateMediaBuyResult,
    CreateMediaBuySuccess,
    UpdateMediaBuyResult,
    UpdateMediaBuySubmitted,
    UpdateMediaBuySuccess,
)
from src.core.security.outbound_http import OperatorEndpoint, send
from src.core.thread_registry import ThreadRegistry
from src.services._provider_http import _DEFAULT_SYNC_TIMEOUT_SECONDS, provider_auth_headers, provider_url

if TYPE_CHECKING:
    from src.core.resolved_identity import ResolvedIdentity

logger = logging.getLogger(__name__)


#: The sync body's authority, declared once next to the builder that produces it.
#:
#: The sibling discovery wire has had ``PROVIDER_REGISTRATION_SCHEMA`` since
#: round 18 and is therefore graded by the schema; this wire had no declaration,
#: so its tests restated the key set instead — and a restatement cannot see a
#: ``seller_agent`` that lost ``agent_url`` (#1197 review). The citation guard
#: (``tests/unit/test_architecture_pinned_schema_citations.py``) keeps the path
#: resolvable.
AVAILABLE_PACKAGE_SCHEMA = "trusted-match/available-package.json"

# Log-sanitization rule across the TMP surfaces (this module,
# tmp_health_scheduler, the admin blueprint, the discovery route): a value goes
# through ``log_safe`` (CWE-117) when it enters the process from outside
# — operator form input (provider ``endpoint``/``name``), env (``ADCP_AGENT_URL``),
# or a request path (the discovery route's ``tenant_id``). Values that are only
# ever DB-resolved inside the process are logged raw; here that is ``tenant_id``
# and ``media_buy_id``, which reach this module from ResolvedIdentity and the
# media_buys table, never from a caller-controlled string.


#: In-flight package syncs, keyed by ``media_buy_id``.
#:
#: The shared seam (``src/core/thread_registry.py``, #1264) rather than a bare
#: ``threading.Thread(...).start()``: five services had hand-rolled the dict +
#: lock + reaper before it existed, and this was the sixth site to do so
#: (#1197 review).  What the registry buys here is ordering — see
#: :func:`fire_tmp_sync` — plus a deterministic completion signal
#: (``_active_syncs.get(media_buy_id).join()``) for callers and tests, which is
#: what replaces patching ``threading.Thread``.
_active_syncs = ThreadRegistry()

#: How long a sync waits for the previous sync of the SAME media buy.
#: A cap, not a correctness knob: exceeding it means the predecessor is wedged
#: (a hung provider connection), and the newer package data is more valuable
#: than strict ordering against a thread that may never finish.
_PREDECESSOR_JOIN_TIMEOUT_SECONDS = 60


def fire_tmp_sync(response: MediaBuyWriteResult, identity: ResolvedIdentity | None) -> None:
    """Spawn a daemon thread to sync TMP packages after a successful media buy operation.

    The sole trigger for ``sync_packages_for_media_buy``, invoked by
    ``@fires_tmp_sync`` on the two media-buy ``_impl`` functions — which every
    transport reaches, so this fires exactly once per write on all of them. Do not
    call it from anywhere else: a transport-wrapper or route-layer
    (``BackgroundTasks``) trigger would double-fire, since the decorated impl has
    already fired by the time either of those runs.

    ``response`` is whatever the two ``_impl`` functions return:
    ``CreateMediaBuyResult`` (create path) or
    ``UpdateMediaBuyResult | UpdateMediaBuySubmitted`` (update path).  The id is
    read by ``_extract_media_buy_id`` as a typed attribute after narrowing the
    union, never by attribute name — see that function.

    Keep this union in step with those two return annotations: it is what caught
    the update path's type change when the pin moved to adcp 6.6.0 / spec 3.1.1
    (``UpdateMediaBuySuccess | UpdateMediaBuyError`` became
    ``UpdateMediaBuyResult | UpdateMediaBuySubmitted``), which ``response: Any``
    would have swallowed.

    ``identity`` is a ``ResolvedIdentity`` — ``tenant_id`` is extracted here so the
    caller (the decorator) does not have to.

    Two rapid operations on the SAME media buy are **serialized**, not raced:
    each sync joins the one already in flight for that media_buy_id before
    reading the database, so the last operation to fire is the last to POST and
    "every provider holds current package data" is a property of the code rather
    than of thread scheduling.  Superseding (dropping the second sync) would
    publish the older package set; racing publishes whichever thread happens to
    finish last.  ``_active_syncs`` holds the newest thread per media buy, which
    is therefore also the last to finish — so ``get(media_buy_id).join()`` is a
    complete completion signal (#1197 review).

    Boundedness (a pool rather than a thread per fire) remains the separately
    accepted follow-up; ordering is what the registry makes expressible.

    No-ops when ``media_buy_id`` or ``tenant_id`` is absent (e.g. on error or
    submitted responses, which carry no ID); every no-op is logged.
    """
    tenant_id = identity.tenant_id if identity is not None else None

    media_buy_id = _extract_media_buy_id(response)

    if not media_buy_id or not tenant_id:
        if media_buy_id and not tenant_id:
            logger.warning(
                "[TMP sync] Skipping sync for media_buy=%s — no tenant on the resolved identity",
                media_buy_id,
            )
        return

    # Read the predecessor BEFORE registering ourselves: `add` is
    # last-writer-wins, so registering first would make us our own predecessor.
    predecessor = _active_syncs.get(media_buy_id)
    t = threading.Thread(
        target=_run_sync,
        args=(tenant_id, media_buy_id, predecessor),
        daemon=True,
        name=f"tmp-sync-{media_buy_id}",
    )
    _active_syncs.add(media_buy_id, t)
    t.start()


#: What a media-buy write returns — the union ``fire_tmp_sync`` accepts and
#: ``_extract_media_buy_id`` narrows. Naming it once bounds the decorator's return
#: TypeVar, so ``@fires_tmp_sync`` can only be applied to a function that actually
#: returns a media-buy result (and the wrapper keeps that exact type rather than
#: widening it to ``Any``).
type MediaBuyWriteResult = CreateMediaBuyResult | UpdateMediaBuyResult | UpdateMediaBuySubmitted | None


def _write_result(result: object) -> MediaBuyWriteResult:
    """Narrow a decorated ``_impl``'s return value to the media-buy write union.

    The decorator is generic over its wrapped function's return type (so the
    wrapper preserves that type rather than widening it to ``Any``), and for the
    async impl that type is a ``Coroutine`` — which is why the bound cannot simply
    be this union. ``fire_tmp_sync`` then handles an unrecognised member by logging
    it, so a wrong type here is reported rather than silently skipped.
    """
    return cast("MediaBuyWriteResult", result)


def _identity_of(kwargs: Mapping[str, Any]) -> ResolvedIdentity | None:
    """Read the ``identity`` keyword a decorated ``_impl`` was called with.

    Exhaustive by construction: both decorated functions declare ``identity``
    keyword-only, so it cannot arrive positionally. Cast rather than suppressed —
    ``ParamSpec`` types the kwargs mapping as ``object``-valued, and a cast states
    the narrowing where a pragma would only silence it.
    """
    return cast("ResolvedIdentity | None", kwargs.get("identity"))


#: Stamped on every wrapper :func:`fires_tmp_sync` produces.
#:
#: A test asserting ``__wrapped__`` is set only proves SOME ``functools.wraps``
#: decorator is applied — swap this one for any other and the assertion still
#: passes while package sync is dead on every transport. The marker is specific to
#: this decorator, so the guard can fail (#1197 review).
FIRES_TMP_SYNC_MARKER = "__fires_tmp_sync__"


def fires_tmp_sync[**P, R](impl: Callable[P, R]) -> Callable[P, R]:
    """Fire the package sync once, on the return of a media-buy ``_impl``.

    Package sync is a transport-agnostic consequence of a media-buy write: it
    takes the ``_impl`` return value and an already-resolved ``ResolvedIdentity``,
    and nothing about it is transport-specific.  It nevertheless lived at four
    transport edges (two MCP wrappers, two ``_raw`` wrappers), with the import
    deferred inside each function body to make the repetition tolerable, so
    "exactly one sync per media-buy write" was held by a docstring warning future
    authors where not to add a fifth trigger (#1197 review).

    A decorator rather than a literal call at the return statement because
    ``_create_media_buy_impl`` has seven return points; one of them is the replay
    path, and every one of them is a write the router must see. Decorating gives
    the property the four call sites were approximating — fires once per ``_impl``
    invocation, on every transport, and cannot double-fire — at a single site a
    new entry point cannot forget.

    This does not make ``_impl`` transport-aware: Pattern #5's guard bans
    transport imports, ``Context``/``ToolContext`` parameters and ``ToolError``,
    none of which this module has, and both ``_impl`` functions already dispatch
    sibling post-write side effects (Slack notification, activity feed, push
    notifications) from inside the business logic.

    ``identity`` is read from the keyword arguments, and that is exhaustive by
    CONSTRUCTION rather than by convention: both decorated ``_impl`` functions
    declare ``identity`` keyword-only, so there is no call shape that passes it
    positionally. It used to be positional-or-keyword with the contract stated in
    this docstring — changing one caller to pass it positionally would have
    silently stopped the sync on every transport, and no unit double could catch it
    because every double was already keyword-only (#1197 review).

    Typed with ``ParamSpec``/``TypeVar`` so the wrapper preserves the wrapped
    signature and return type instead of widening both to ``Any`` — the result
    union is what ``_extract_media_buy_id`` narrows.
    """
    if inspect.iscoroutinefunction(impl):

        @functools.wraps(impl)
        async def _async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            result = await impl(*args, **kwargs)
            fire_tmp_sync(_write_result(result), _identity_of(kwargs))
            return result

        setattr(_async_wrapper, FIRES_TMP_SYNC_MARKER, True)
        return cast(Callable[P, R], _async_wrapper)

    @functools.wraps(impl)
    def _sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        result = impl(*args, **kwargs)
        fire_tmp_sync(_write_result(result), _identity_of(kwargs))
        return result

    setattr(_sync_wrapper, FIRES_TMP_SYNC_MARKER, True)
    return _sync_wrapper


def _run_sync(tenant_id: str, media_buy_id: str, predecessor: threading.Thread | None) -> None:
    """Registry-managed body of one sync: serialize behind *predecessor*, then sync.

    Joining the predecessor here (rather than in :func:`fire_tmp_sync`) keeps the
    caller non-blocking — the transport wrapper returns as soon as the thread is
    spawned, exactly as before.
    """
    try:
        if predecessor is not None and predecessor.is_alive():
            predecessor.join(timeout=_PREDECESSOR_JOIN_TIMEOUT_SECONDS)
            if predecessor.is_alive():
                logger.warning(
                    "[TMP sync] Previous sync for media_buy=%s still running after %ds — "
                    "proceeding, provider ordering for this media buy is not guaranteed",
                    media_buy_id,
                    _PREDECESSOR_JOIN_TIMEOUT_SECONDS,
                )
        sync_packages_for_media_buy(tenant_id, media_buy_id)
    finally:
        # Only drop the entry if it is still ours: a newer fire may already have
        # replaced it, and that thread is the one callers must be able to join.
        if _active_syncs.get(media_buy_id) is threading.current_thread():
            _active_syncs.remove(media_buy_id)


def join_active_syncs(timeout: float = 30.0) -> list[str]:
    """Wait for every in-flight package sync to finish; return the keys still running.

    The feature's observation seam.  ``fire_tmp_sync`` is fire-and-forget by
    design, so without this a caller (an operator draining a worker, a test
    asserting on what a provider received) has nothing to wait on and has to
    reach for ``patch("threading.Thread")`` — an in-process artifact no
    transport observes, which is what every test tier ended up doing
    independently (#1197 review).

    Because ``_active_syncs`` holds the NEWEST thread per media buy and each
    sync serializes behind its predecessor, joining the registered threads joins
    the whole chain.

    Returns the media_buy_ids whose sync was still running when *timeout*
    expired — empty on a clean drain.
    """
    stragglers: list[str] = []
    for key in _active_syncs.list_active():
        thread = _active_syncs.get(key)
        if thread is None:
            continue
        thread.join(timeout=timeout)
        if thread.is_alive():
            stragglers.append(key)
    return stragglers


def _extract_media_buy_id(response: MediaBuyWriteResult) -> str | None:
    """Read ``media_buy_id`` off a media-buy result as a *typed* attribute.

    The union is narrowed with ``isinstance`` and the id is read from the
    concrete member, so renaming ``media_buy_id`` on any member is a type-check
    error here.  The previous ``getattr(response, "media_buy_id", None)`` dodged
    the union entirely: a rename would have switched TMP sync off on all four
    transports with no error and no log line — the exact regression the union
    annotation exists to prevent (#1197 review).

    Only the ``*Success`` members carry an id.  ``*Error`` and ``*Submitted``
    have no ``media_buy_id`` field at all, so "no id" is the correct, expected
    outcome there — logged at DEBUG.  An unrecognised type is a contract drift
    rather than an expected shape, so it is logged at WARNING instead of
    vanishing.

    ``CreateMediaBuyResult`` / ``UpdateMediaBuyResult`` are ``TaskResultEnvelope``
    shapes: they serialize flat but store the domain response in ``.response``,
    so the id lives on the inner model, not the envelope.
    """
    if response is None:
        return None

    if isinstance(response, CreateMediaBuyResult | UpdateMediaBuyResult):
        inner = response.response
        if isinstance(inner, CreateMediaBuySuccess | UpdateMediaBuySuccess):
            return inner.media_buy_id
        logger.debug(
            "[TMP sync] No media_buy_id on %s.response (%s) — skipping sync",
            type(response).__name__,
            type(inner).__name__,
        )
        return None

    if isinstance(response, UpdateMediaBuySubmitted):
        logger.debug("[TMP sync] Update submitted for async completion — no media_buy_id yet, skipping sync")
        return None

    logger.warning(
        "[TMP sync] Unrecognised media-buy result type %s — skipping sync. "
        "Add it to fire_tmp_sync's union and to _extract_media_buy_id.",
        type(response).__name__,
    )
    return None


def _resolve_seller_agent_url(tenant_id: str) -> str | None:
    """Resolve the seller agent URL for the AvailablePackage.seller_agent field.

    Per ``adcp/_schemas/3.1/core/seller-agent-ref.json``, ``agent_url``
    MUST use the ``https://`` scheme.  Returns ``None`` when no valid https URL
    can be resolved so the caller can skip the sync rather than emit a
    spec-invalid binding.

    Resolution order:
      1. ADCP_AGENT_URL env var (explicit override for non-standard deployments)
         — validated to use https:// like the virtual_host path; a non-https
         override is rejected (logged, falls through) rather than emitted.
      2. Tenant virtual_host (the public domain, e.g. "tenant.salesagent.example.com")
         — local hosts (localhost / *.localhost / 127.0.0.1) are skipped because
         they cannot produce a valid https URL.
      3. Returns None — caller logs and skips sync.

    IMPORTANT: this opens its own UoW/session. Callers MUST NOT invoke this
    function from inside another open UoW block (e.g. MediaBuyUoW) — nesting
    two UoWs means the inner UoW's __exit__ closes/removes the scoped session
    the outer block is still using (get_db_session() is a scoped session).
    sync_packages_for_media_buy() resolves the seller_agent URL before
    opening the MediaBuyUoW block for exactly this reason.
    """
    override = os.environ.get("ADCP_AGENT_URL")
    if override:
        override = override.rstrip("/")
        if override.startswith("https://"):
            return override
        logger.error(
            "[TMP sync] ADCP_AGENT_URL=%s does not use https:// — ignoring override "
            "(adcp/_schemas/3.1/core/seller-agent-ref.json requires https for agent_url). "
            "Falling back to tenant virtual_host resolution.",
            log_safe(override),
        )

    # Load tenant to resolve virtual_host.
    # Uses TenantConfigUoW for architecture compliance (no raw get_db_session).
    try:
        with TenantConfigUoW(tenant_id) as uow:
            tenant = uow.tenant_config.get_tenant()
            if tenant and tenant.virtual_host:
                host = tenant.virtual_host
                if not is_local_host(host):
                    return f"https://{host}/mcp"
    except Exception:
        logger.warning(
            "[TMP sync] Failed to load tenant %s for seller_agent URL",
            tenant_id,
            exc_info=True,
        )

    # No valid https URL available — the spec requires https for agent_url.
    # Log an error and return None so the caller skips the sync rather than
    # emitting a spec-invalid binding that providers will reject.
    logger.error(
        "[TMP sync] Cannot resolve a valid https seller_agent URL for tenant=%s "
        "(ADCP_AGENT_URL not set and no public virtual_host configured). "
        "Set ADCP_AGENT_URL to the public https MCP endpoint to enable TMP sync.",
        tenant_id,
    )
    return None


def _build_package_payload(
    media_buy_id: str,
    pkg_row: MediaPackage,
    seller_agent_url: str,
) -> AvailablePackage:
    """Build the POST /packages/sync payload from a MediaPackage DB row.

    The body is produced by the pinned SDK's ``AvailablePackage`` — the codegen
    of ``adcp/_schemas/3.1/trusted-match/available-package.json`` — rather than
    a hand-written TypedDict copy.  The schema is closed
    (``additionalProperties: false``) and requires exactly ``package_id``,
    ``media_buy_id``, ``seller_agent``; ``format_ids`` and ``catalogs`` are the
    optional members.  Constructing the model validates the shape here, and a
    spec bump that renames or adds a required field becomes a construction
    error instead of silently shipping the old body (#1197 review).

    ``seller_agent`` is ``SellerAgentReference``
    (``adcp/_schemas/3.1/core/seller-agent-ref.json``): ``{"agent_url": ...}``,
    whose ``agent_url`` MUST use the ``https://`` scheme.  Callers resolve a valid
    https URL before calling this (see ``_resolve_seller_agent_url``), and the
    scheme is re-checked HERE so the rule holds by construction at the point the
    body is built.  The check is not redundant: the SDK model does not enforce it
    (the requirement is prose in the field's ``description``, not a validator), so
    the previous claim that "an invalid one raises out of the model" was false and
    a non-https URL would have reached a provider (#1197 review).

    Returns the MODEL, not a ``dict``. Collapsing it here left an untyped mapping
    travelling to ``httpx`` with no declared authority for the sync body, so its
    tests restated the key set instead of validating against the schema — and a
    restatement cannot see a ``seller_agent`` that lost ``agent_url`` or a
    non-https ``agent_url``, the one constraint this module spends a docstring, an
    error branch and a skip path on (#1197 review). The ``model_dump`` happens
    once, in ``_post_packages_sync``, where JSON is actually needed.
    """
    format_ids = _package_format_ids(pkg_row)
    if not seller_agent_url.startswith("https://"):
        raise ValueError(
            f"seller_agent.agent_url must use https:// per seller-agent-ref.json, got {seller_agent_url!r}"
        )
    return AvailablePackage(
        package_id=pkg_row.package_id,
        media_buy_id=media_buy_id,
        # seller_agent is required by the schema; agent_url MUST be https.
        seller_agent=SellerAgentReference(agent_url=seller_agent_url),
        format_ids=format_ids,
    )


def _package_format_ids(pkg_row: MediaPackage) -> list[FormatId] | None:
    """The package's eligible creative formats, for the sync body.

    ``available-package.json`` types ``format_ids`` as the standard AdCP format-id
    object (``core/format-id.json``), and the value is on the row: the create path
    stores the request's ``format_ids`` in ``package_config``. Omitting it was not a
    decision — the docstring named it as an optional member and then declined to
    populate it with no reason — and the spec is explicit that package metadata "is
    synced at media buy time, not sent per request", which is exactly why a router
    needs the formats to have arrived (#1197 review).

    Returns ``None`` (the member omitted) when the row carries none, since the
    schema types it as an array and ``exclude_none=True`` drops it.
    """
    config = pkg_row.package_config or {}
    # ``format_ids`` is the request's eligible formats for this package;
    # ``format_ids_to_provide`` is the adapter's statement of which formats the
    # buyer must supply creatives for. Either answers "which formats is this
    # package eligible for", which is what the schema's ``format_ids`` means, so
    # the request's value wins and the adapter's is the fallback.
    raw = config.get("format_ids") or config.get("format_ids_to_provide")
    if not raw:
        return None

    formats: list[FormatId] = []
    for entry in raw:
        # Stored either as the AdCP object (a dict after the JSONType round trip, or
        # a FormatId if it was never persisted) or, on legacy rows, as a bare id
        # string. Anything unconvertible is skipped rather than failing the whole
        # sync: the formats are advisory to the router, the packages are not.
        try:
            if isinstance(entry, FormatId):
                formats.append(entry)
            elif isinstance(entry, dict):
                formats.append(FormatId.model_validate(entry))
            else:
                logger.debug(
                    "[TMP sync] Skipping non-object format_id %r on package %s — "
                    "the wire requires the {agent_url, id} form",
                    entry,
                    pkg_row.package_id,
                )
        except ValidationError:
            logger.warning(
                "[TMP sync] Skipping unconvertible format_id %r on package %s",
                entry,
                pkg_row.package_id,
                exc_info=True,
            )
    return formats or None


def _post_packages_sync(
    endpoint: str, payloads: list[AvailablePackage], auth_credentials: str = "", auth_type: str | None = None
) -> None:
    """POST /packages/sync to a single TMP Provider endpoint.

    Sends the full list as a JSON array.  The pinned spec is deliberately SILENT
    on this framing — the sync transport between a seller agent and a provider is
    deployment-specific, and ``available-package.json`` types one package, not the
    envelope around it.  So the array is this deployment's choice, not a spec
    requirement, and the previous citation of ``handlers_packages.go`` named a file
    in another repository as the authority for it: unverifiable from here, and it
    obscured the more useful fact that nothing in the spec constrains it
    (#1197 review).

    Auth: Bearer token — when auth_credentials is set, sends
    ``Authorization: Bearer <credentials>``.  The TMP Provider resolves
    the tenant server-side from the credential.

    Sent through the outbound egress seam (``outbound_http.send``), which owns
    address policy, TLS, redirect refusal and retry classification — this module
    states none of them. Redirect refusal, which used to be this function's own
    ``follow_redirects=False``, is httpx's default inside the seam; the SSRF
    guard the flag stood in for is now the seam's pinned-IP transport (#1802).

    ``provenance`` is an :class:`OperatorEndpoint`, not a ``CounterpartyUrl``: a
    TMP provider endpoint is registered by the operator through the admin form,
    so a refusal has no buyer-request field to name, and the role name must not
    be an address (AdCP 3.1.1 security point 6).

    Raises :class:`OutboundError` on a refusal, a transport failure or a non-2xx
    the seam will not retry, so the caller can log and continue to the next
    provider — the same contract ``raise_for_status()`` gave it before.
    """
    url = provider_url(endpoint, "/packages/sync")
    headers = provider_auth_headers(auth_type, auth_credentials)
    # The single model_dump in the whole path: the fan-out carries validated
    # AvailablePackage models and JSON is produced only here, at the transport
    # edge, so `Any` never travels through this module (#1197 review).
    # Annotated as the seam's own JsonValue: model_dump returns dict[str, Any],
    # and Any inside the element type does not satisfy the recursive alias on its
    # own. Stating the boundary type here keeps the seam call checked instead of
    # casting at it.
    body: JsonValue = [pkg.model_dump(mode="json", exclude_none=True) for pkg in payloads]
    result = send(
        url,
        method="POST",
        json=body,
        headers=headers,
        timeout=_DEFAULT_SYNC_TIMEOUT_SECONDS,
        provenance=OperatorEndpoint("the TMP provider"),
    )
    # Sync fires on every media-buy create/update; keep at DEBUG (failures stay
    # at WARNING in sync_packages_for_media_buy's fan-out loop below).
    logger.debug(
        "[TMP sync] POST %s → %d (%d package(s), auth=%s)",
        log_safe(url),
        result.http_status,
        len(payloads),
        "bearer" if auth_credentials else "none",
    )


def _readable_providers(provider_rows: list[TMPProvider], tenant_id: str) -> list[tuple[str, str, str, str | None]]:
    """Materialise ``(name, endpoint, credential, auth_type)`` per provider, skipping unreadable ones.

    The unit of work is one provider, so the failure handling is per provider:
    :attr:`TMPProvider.auth_credentials` decrypts on read and raises
    ``AdCPConfigurationError`` on a ciphertext the current key cannot open — a
    key-rotation state, not a corrupt database.  A list comprehension over the
    whole set turned that single row into "no providers synced for this tenant",
    logged as a repository failure, so one provider's rotated credential silently
    stopped the sync for every other provider the tenant had registered
    (#1197 review).

    Narrowed to ``AdCPConfigurationError`` — the decrypt failure — rather than
    ``Exception``: a broad catch here silently dropped a provider for ANY error
    raised while materialising it (an ``AttributeError`` from a renamed column, for
    instance), which is the same "swallow the programming error, under-deliver the
    feature" shape as the capability handler's (#1197 review).

    Runs inside the caller's UoW block: the attribute reads (and the decrypt)
    need a live session.
    """
    providers: list[tuple[str, str, str, str | None]] = []
    for p in provider_rows:
        try:
            credential = p.auth_credentials or ""
        except AdCPConfigurationError:
            # Named per provider, and at WARNING like the fan-out failures — an
            # operator reading this line must be able to tell WHICH registration
            # to re-enter the credential for.
            logger.warning(
                "[TMP sync] Skipping provider '%s' (%s) for tenant=%s — its stored auth credential "
                "could not be read (re-enter it in the admin UI); other providers are unaffected",
                log_safe(p.name),
                log_safe(p.endpoint),
                tenant_id,
                exc_info=True,
            )
            continue
        providers.append((p.name, p.endpoint, credential, p.auth_type))
    return providers


def sync_packages_for_media_buy(tenant_id: str, media_buy_id: str) -> None:
    """Background task: push all packages for a media buy to syncable TMP providers.

    Reached from ``fire_tmp_sync()`` on a daemon thread, so the buyer's request is
    never blocked by it. That is fired by ``@fires_tmp_sync`` at the return of
    either media-buy ``_impl`` — one trigger, every transport.

    Steps:
      1. Resolve seller_agent URL from tenant config (its own UoW, opened and
         closed BEFORE the MediaBuyUoW block — see note below).
      2. Load packages from media_packages table via MediaBuyRepository.
      3. Load active/draining TMP provider endpoints via TMPProviderRepository,
         materialised into plain tuples before the UoW block closes.
      4. POST /packages/sync to each provider (best-effort, errors logged).

    Args:
        tenant_id:    Tenant scope — used for both repository queries.
        media_buy_id: The media buy whose packages should be synced.
    """
    # --- Step 1: resolve seller_agent URL BEFORE opening MediaBuyUoW ---
    # _resolve_seller_agent_url() opens its own TenantConfigUoW. get_db_session()
    # is a scoped session, so nesting it inside another open UoW block means the
    # inner UoW's __exit__ closes/removes the session the outer block still
    # needs — the subsequent row access and outer commit then run against a
    # removed session. Resolving it here, before MediaBuyUoW opens, avoids the
    # nesting entirely.
    #
    # Returns None when no valid https URL is available (spec requires https for
    # seller_agent.agent_url). Skip sync rather than emit a spec-invalid binding.
    seller_agent_url = _resolve_seller_agent_url(tenant_id)
    if seller_agent_url is None:
        logger.warning(
            "[TMP sync] Skipping sync for media_buy=%s tenant=%s — no valid https seller_agent URL. "
            "Set ADCP_AGENT_URL to enable TMP sync.",
            media_buy_id,
            tenant_id,
        )
        return

    # --- Step 2: load packages and build payloads (inside session scope) ---
    # Payloads are built while the session is still open so that ORM attribute
    # access (pkg_row.package_id) does not hit a detached instance.
    # HTTP calls happen after this block — no open transaction during network I/O.
    try:
        with MediaBuyUoW(tenant_id) as uow:
            pkg_rows = uow.media_buys.get_packages(media_buy_id)

            if not pkg_rows:
                logger.debug(
                    "[TMP sync] No packages found for media_buy_id=%s — skipping sync",
                    media_buy_id,
                )
                return

            payloads = [_build_package_payload(media_buy_id, row, seller_agent_url) for row in pkg_rows]
    except Exception:
        logger.exception(
            "[TMP sync] Failed to load packages for media_buy_id=%s tenant=%s",
            media_buy_id,
            tenant_id,
        )
        return

    # Sync fires on every media-buy create/update — DEBUG matches the poll-path
    # per-cycle summaries (tmp_health_scheduler); failures below stay at WARNING.
    logger.debug(
        "[TMP sync] Built %d package payload(s) for media_buy=%s seller_agent=%s",
        len(payloads),
        media_buy_id,
        log_safe(seller_agent_url),
    )

    # --- Step 3: load active + draining TMP provider endpoints ---
    # Draining providers still serve in-flight requests and need current package data.
    # The router stops sending NEW requests to draining providers, but packages must
    # stay up-to-date for requests already in the pipeline.
    #
    # Materialise into plain tuples INSIDE the UoW block — provider.endpoint /
    # provider.auth_credentials / provider.name are ORM attributes that expire
    # on commit (default expire_on_commit=True). Reading them after the `with`
    # block closes hits a detached session and raises DetachedInstanceError,
    # which then repeats in the except-handler's own attribute reads below.
    try:
        with TMPProviderUoW(tenant_id) as uow:
            providers = _readable_providers(uow.tmp_providers.list_syncable(), tenant_id)
    except Exception:
        logger.exception(
            "[TMP sync] Failed to load TMP providers for tenant=%s",
            tenant_id,
        )
        return

    if not providers:
        logger.debug(
            "[TMP sync] No active TMP providers for tenant=%s — skipping sync",
            tenant_id,
        )
        return

    # --- Step 4: fan out to each provider (best-effort) ---
    for provider_name, provider_endpoint, provider_auth_credentials, provider_auth_type in providers:
        try:
            _post_packages_sync(provider_endpoint, payloads, provider_auth_credentials, provider_auth_type)
        except Exception:
            # Log with full context but do NOT re-raise — one provider failure
            # must not block the others.  The media buy is already committed.
            logger.warning(
                "[TMP sync] Failed to sync %d package(s) to provider '%s' (%s) for tenant=%s media_buy=%s",
                len(payloads),
                log_safe(provider_name),
                log_safe(provider_endpoint),
                tenant_id,
                media_buy_id,
                exc_info=True,
            )
