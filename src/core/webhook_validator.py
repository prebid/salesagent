"""Webhook URL validation to prevent SSRF attacks.

This module provides security validation for webhook URLs to prevent
Server-Side Request Forgery (SSRF) attacks where malicious users could
trick the server into making requests to internal services.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from adcp.types import TaskType

from src.core.bounded_executor import AsyncThreadPoolBulkhead
from src.core.exceptions import AdCPValidationError
from src.core.logging_config import scrub_control_chars
from src.core.security.url_validator import HTTPS_SCHEME_ERROR_PREFIX, check_url_ssrf

logger = logging.getLogger(__name__)

# Buyer-facing message for a rejected callback URL. Deliberately GENERIC: the
# detailed reason (resolved IP, matched CIDR range, resolver diagnostics) is an
# SSRF oracle — it lets the URL supplier probe internal network topology through
# the error channel — so it goes to the server log ONLY, never back over the wire.
_GENERIC_CALLBACK_REJECTION = "URL failed SSRF validation"
# The one non-sensitive hint we DO surface: the scheme requirement. It echoes the
# buyer's own submitted scheme and reveals nothing about internal resolution.
_HTTPS_REQUIRED_MESSAGE = "URL must use HTTPS scheme"

# DNS resolution is a blocking libc operation. Buyer-controlled callback URLs
# reach this module from async MCP/A2A/REST handlers, so registration uses the
# async guard below and fails closed if resolution cannot finish promptly.
CALLBACK_URL_VALIDATION_TIMEOUT_SECONDS = 2.0
_CALLBACK_VALIDATION_MAX_WORKERS = 4
_CALLBACK_VALIDATION_EXECUTOR = AsyncThreadPoolBulkhead(
    max_workers=_CALLBACK_VALIDATION_MAX_WORKERS,
    thread_name_prefix="callback-url-validation",
)

# A per-process key seals validation proofs. The proof never crosses a transport
# boundary; it only lets an async entry point hand the exact values it validated
# to a synchronous shared builder/raw helper without performing DNS twice. A
# caller cannot manufacture a proof merely by constructing the dataclass, and a
# proof for one URL/policy snapshot cannot be replayed for another.
_CALLBACK_PROOF_KEY = secrets.token_bytes(32)

type _CallbackURLSnapshot = tuple[tuple[str, bool, bool, str | None], ...]


@dataclass(frozen=True, slots=True)
class CallbackURLValidationProof:
    """Opaque, value-bound evidence of completed callback URL validation.

    Instances are issued only by :func:`require_valid_callback_config_urls_async`.
    ``require_valid_callback_config_urls`` verifies the HMAC and compares the
    complete callback/policy snapshot before accepting one, so mutation or reuse
    with different callback values fails closed without a second DNS lookup.
    """

    _snapshot: _CallbackURLSnapshot
    _allow_private: bool
    _signature: bytes


_CURRENT_CALLBACK_VALIDATION_PROOF: ContextVar[CallbackURLValidationProof | None] = ContextVar(
    "current_callback_validation_proof",
    default=None,
)


def _allow_private_webhook_targets() -> bool:
    """Whether buyer callbacks to private/loopback targets are permitted.

    A DEDICATED opt-in (``ADCP_ALLOW_PRIVATE_WEBHOOKS``), deliberately NOT tied to
    ``ENVIRONMENT``: a real staging/dev deployment that serves buyers must still block
    private/internal targets, so gating on "not production" was too broad (#1512). This
    flag is set ONLY by the E2E harness, whose webhook receiver lives on the compose
    network / loopback. Cloud-metadata and link-local targets stay blocked even when it
    is set.
    """
    return os.getenv("ADCP_ALLOW_PRIVATE_WEBHOOKS", "").strip().lower() in ("1", "true", "yes")


# Fallback used when an action label is not a member of the SDK's closed
# TaskType enum. create_mcp_webhook_payload() restricts task_type to that
# enum and would otherwise reject the payload as schema-invalid.
WEBHOOK_TASK_TYPE_FALLBACK = "update_media_buy"


class _CallbackConfigModel(Protocol):
    """Structural type shared by the SDK callback config models."""

    url: Any


def webhook_url_has_embedded_credentials(url: str) -> bool:
    """Return whether a webhook URL carries ``user:password@`` userinfo.

    Callback authentication belongs in ``push_notification_config.authentication``.
    Allowing URL userinfo is ambiguous and unsafe: ``requests`` applies URL Basic
    auth after caller-supplied headers, so it can silently replace the configured
    Bearer credential at delivery time.
    """
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return False
    return parsed.username is not None or parsed.password is not None


def _callback_config_snapshot_entry(
    field_name: str,
    config: Mapping[str, Any] | _CallbackConfigModel | None,
) -> tuple[str, bool, bool, str | None]:
    """Capture the callback URL value and its presence semantics."""
    if config is None:
        return field_name, False, False, None
    if isinstance(config, Mapping):
        url_present = "url" in config
        raw_url = config.get("url")
    else:
        url_present = hasattr(config, "url")
        raw_url = getattr(config, "url", None)
    return field_name, True, url_present, None if raw_url is None else str(raw_url)


def _callback_url_snapshot(
    *,
    push_notification_config: Mapping[str, Any] | _CallbackConfigModel | None,
    reporting_webhook: Mapping[str, Any] | _CallbackConfigModel | None,
) -> _CallbackURLSnapshot:
    """Return the immutable callback values to which a proof is bound."""
    return (
        _callback_config_snapshot_entry("push_notification_config", push_notification_config),
        _callback_config_snapshot_entry("reporting_webhook", reporting_webhook),
    )


def _callback_proof_signature(snapshot: _CallbackURLSnapshot, allow_private: bool) -> bytes:
    payload = json.dumps(
        {"allow_private": allow_private, "callbacks": snapshot},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(_CALLBACK_PROOF_KEY, payload, hashlib.sha256).digest()


def _issue_callback_validation_proof(
    snapshot: _CallbackURLSnapshot,
    allow_private: bool,
) -> CallbackURLValidationProof:
    return CallbackURLValidationProof(
        _snapshot=snapshot,
        _allow_private=allow_private,
        _signature=_callback_proof_signature(snapshot, allow_private),
    )


def _proof_matches(
    proof: CallbackURLValidationProof,
    snapshot: _CallbackURLSnapshot,
    allow_private: bool,
) -> bool:
    expected = _callback_proof_signature(proof._snapshot, proof._allow_private)
    return (
        hmac.compare_digest(proof._signature, expected)
        and proof._snapshot == snapshot
        and proof._allow_private == allow_private
    )


def _validate_callback_url_with_policy(url: str, *, allow_private: bool) -> tuple[bool, str]:
    """Validate one callback URL against an explicit, immutable policy."""
    if webhook_url_has_embedded_credentials(url):
        logger.warning("Push callback URL rejected: embedded URL credentials are not permitted")
        return False, _GENERIC_CALLBACK_REJECTION

    # Require HTTPS whenever private targets are NOT permitted (every real
    # deployment). The E2E opt-in that permits private receivers also permits HTTP.
    require_https = not allow_private
    is_valid, detail = check_url_ssrf(url, require_https=require_https, allow_private=allow_private)
    if is_valid:
        return True, ""

    # Info-disclosure guard (#1546): never hand the resolved IP / matched CIDR
    # range back to the buyer — that is an SSRF oracle. Log the precise reason
    # server-side (scrubbed: the detail embeds buyer-controlled URL fragments,
    # and VT/FF/ESC survive urlparse); return a generic message. The
    # HTTPS-scheme requirement is the sole exception (it is not a resolution
    # diagnostic and helps the buyer fix a plain-http callback) — classified by
    # the validator's exact scheme-error prefix, not a substring match that an
    # unresolvable "https-..." hostname would also satisfy.
    logger.warning("Push callback URL rejected by SSRF validation: %s", scrub_control_chars(detail))
    if require_https and detail.startswith(HTTPS_SCHEME_ERROR_PREFIX):
        return False, _HTTPS_REQUIRED_MESSAGE
    return False, _GENERIC_CALLBACK_REJECTION


def validate_webhook_task_type(task_type: str, fallback: str = WEBHOOK_TASK_TYPE_FALLBACK) -> str:
    """Coerce a task_type to a value accepted by the SDK webhook payload builder.

    ``create_mcp_webhook_payload()`` validates ``task_type`` against the closed
    :class:`adcp.types.TaskType` enum. Action labels sourced from untrusted data
    (e.g. ``workflow_steps.tool_name``) may not be enum members, which would make
    the payload schema-invalid. This helper returns ``task_type`` unchanged when
    it is a valid enum value, otherwise returns ``fallback``.

    This validates ONLY the value destined for the SDK/webhook payload. Callers
    must keep the original action label for internal metadata (audit log,
    delivery-webhook guards, ``WebhookDeliveryLog.task_type``) — see
    salesagent-yi3s.

    Args:
        task_type: The candidate action label.
        fallback: The value to return when ``task_type`` is not a TaskType member.

    Returns:
        ``task_type`` if it is a valid TaskType, otherwise ``fallback``.
    """
    try:
        TaskType(task_type)
    except ValueError:
        return fallback
    return task_type


class WebhookURLValidator:
    """Validates webhook URLs to prevent SSRF attacks."""

    @classmethod
    def validate_webhook_url(cls, url: str) -> tuple[bool, str]:
        """
        Validate webhook URL for SSRF protection.

        Args:
            url: The webhook URL to validate

        Returns:
            (is_valid, error_message) - is_valid is True if safe, error_message explains failures
        """
        if webhook_url_has_embedded_credentials(url):
            return False, "URL must not contain embedded credentials"
        return check_url_ssrf(url)

    @classmethod
    def validate_for_testing(cls, url: str, allow_localhost: bool = False) -> tuple[bool, str]:
        """
        Validate webhook URL with optional localhost allowance for testing.

        This is useful for development/testing scenarios where webhooks need to
        point to localhost services. Production should use validate_webhook_url().

        Args:
            url: The webhook URL to validate
            allow_localhost: If True, allows localhost and 127.0.0.1

        Returns:
            (is_valid, error_message)
        """
        is_valid, error = cls.validate_webhook_url(url)

        # If validation failed but it's a localhost error and we allow it
        if not is_valid and allow_localhost:
            if "localhost" in error.lower() or "127.0.0" in error or "loopback" in error.lower():
                return True, ""

        return is_valid, error

    @classmethod
    def validate_callback_url(cls, url: str) -> tuple[bool, str]:
        """Env-gated validation for a buyer-supplied push callback URL (#1512).

        The single gate used at callback registration AND delivery. By default —
        production, staging, and ordinary dev — it requires HTTPS and blocks all
        internal targets (loopback, RFC-1918, localhost/Docker aliases), validating
        EVERY resolved A/AAAA record. Only the dedicated ``ADCP_ALLOW_PRIVATE_WEBHOOKS``
        opt-in (set solely by the E2E harness) relaxes this to permit a trusted
        private/loopback receiver over plain HTTP. Cloud-metadata / link-local targets
        (169.254.x, fe80::, metadata.google.internal) stay blocked in EVERY environment.

        Callers connect to the validated address (connection pinning in
        protocol_webhook_service) so the checked IP is the one actually used;
        disabled redirects + HTTPS close the redirect-to-metadata and plain-HTTP vectors.
        """
        allow_private = _allow_private_webhook_targets()
        return _validate_callback_url_with_policy(url, allow_private=allow_private)


def _validate_callback_url_snapshot(
    snapshot: _CallbackURLSnapshot,
    *,
    allow_private: bool,
) -> None:
    """Synchronously validate a frozen callback snapshot.

    This function may perform DNS resolution. Async transport entry points must
    invoke it only through the dedicated executor in the async guard below.
    """
    for field_name, _config_present, _url_present, url in snapshot:
        if url is None:
            # The typed request model owns required-field validation. This guard
            # only applies the outbound callback security policy to a present URL.
            continue
        is_valid, error_msg = _validate_callback_url_with_policy(url, allow_private=allow_private)
        if not is_valid:
            raise AdCPValidationError(
                f"{field_name}.url failed callback URL validation: {error_msg}",
                field=f"{field_name}.url",
                suggestion="Supply a publicly routable HTTPS callback URL without embedded credentials.",
            )


def _first_callback_url_field(snapshot: _CallbackURLSnapshot) -> str | None:
    """Return the first supplied callback URL field for a generic rejection."""
    return next((f"{field_name}.url" for field_name, _, _, url in snapshot if url is not None), None)


async def _validate_callback_snapshot_off_loop(
    snapshot: _CallbackURLSnapshot,
    *,
    allow_private: bool,
) -> None:
    """Run one DNS validation in the dedicated, capacity-limited bulkhead.

    A permit is released by the *concurrent* future's completion callback, not
    by coroutine cancellation. Therefore a caller deadline cannot free capacity
    while its libc resolver is still running and let a timeout flood build an
    unbounded executor queue.
    """
    await _CALLBACK_VALIDATION_EXECUTOR.run(
        _validate_callback_url_snapshot,
        snapshot,
        allow_private=allow_private,
    )


@contextmanager
def callback_url_validation_scope(proof: CallbackURLValidationProof) -> Iterator[None]:
    """Make an async boundary's proof available to nested synchronous helpers.

    The scope is task-local through ``ContextVar`` and always reset. It keeps
    proof plumbing out of buyer-visible ``*_raw`` signatures (and therefore out
    of REST/OpenAPI completeness checks) while the receiving sync guard still
    verifies the proof against its exact callback values.
    """
    token = _CURRENT_CALLBACK_VALIDATION_PROOF.set(proof)
    try:
        yield
    finally:
        _CURRENT_CALLBACK_VALIDATION_PROOF.reset(token)


def require_valid_callback_config_urls(
    *,
    push_notification_config: Mapping[str, Any] | _CallbackConfigModel | None = None,
    reporting_webhook: Mapping[str, Any] | _CallbackConfigModel | None = None,
    validation_proof: CallbackURLValidationProof | None = None,
) -> None:
    """Reject unsafe buyer callback URLs at the shared protocol boundary.

    Both media-buy callback fields ultimately drive seller-initiated HTTP POSTs,
    so they must use the same registration policy as delivery: HTTPS in normal
    deployments, the dedicated private-test opt-in for local receivers, no URL
    userinfo, and full SSRF resolution checks.  Configs may be SDK models (MCP)
    or wire dictionaries (A2A/REST).

    Raises:
        AdCPValidationError: If either supplied callback URL is unsafe.
    """
    snapshot = _callback_url_snapshot(
        push_notification_config=push_notification_config,
        reporting_webhook=reporting_webhook,
    )
    allow_private = _allow_private_webhook_targets()
    effective_proof = validation_proof or _CURRENT_CALLBACK_VALIDATION_PROOF.get()
    if effective_proof is not None:
        if _proof_matches(effective_proof, snapshot, allow_private):
            return
        # Never silently fall back to a second synchronous resolution after an
        # async caller supplied proof. A mismatch means the callback or policy
        # changed after validation (or the proof was fabricated); fail closed.
        raise AdCPValidationError(
            "Callback URL changed after security validation.",
            field=_first_callback_url_field(snapshot),
            suggestion="Retry the request so the submitted callback URL can be validated again.",
        )

    _validate_callback_url_snapshot(snapshot, allow_private=allow_private)


async def require_valid_callback_config_urls_async(
    *,
    push_notification_config: Mapping[str, Any] | _CallbackConfigModel | None = None,
    reporting_webhook: Mapping[str, Any] | _CallbackConfigModel | None = None,
    timeout_seconds: float = CALLBACK_URL_VALIDATION_TIMEOUT_SECONDS,
) -> CallbackURLValidationProof:
    """Validate callbacks off the event loop and return sealed evidence.

    ``socket.getaddrinfo`` is synchronous on CPython. Running the frozen
    snapshot in a small dedicated executor prevents a slow or hostile resolver
    from stalling the event loop *or* starving unrelated default-executor work.
    A capacity guard prevents timed-out resolver calls from accumulating an
    unbounded queue. ``wait_for`` imposes a hard caller-facing deadline; timeout
    fails closed and never exposes DNS diagnostics.
    """
    snapshot = _callback_url_snapshot(
        push_notification_config=push_notification_config,
        reporting_webhook=reporting_webhook,
    )
    allow_private = _allow_private_webhook_targets()
    if _first_callback_url_field(snapshot) is None:
        return _issue_callback_validation_proof(snapshot, allow_private)
    try:
        await asyncio.wait_for(
            _validate_callback_snapshot_off_loop(snapshot, allow_private=allow_private),
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        raise AdCPValidationError(
            "Callback URL security validation did not complete before the safety deadline.",
            field=_first_callback_url_field(snapshot),
            suggestion="Retry the request or supply a different publicly routable HTTPS callback URL.",
        ) from exc
    return _issue_callback_validation_proof(snapshot, allow_private)


@asynccontextmanager
async def validated_callback_url_scope(
    *,
    push_notification_config: Mapping[str, Any] | _CallbackConfigModel | None = None,
    reporting_webhook: Mapping[str, Any] | _CallbackConfigModel | None = None,
    timeout_seconds: float = CALLBACK_URL_VALIDATION_TIMEOUT_SECONDS,
) -> AsyncIterator[CallbackURLValidationProof]:
    """Bridge async DNS validation into nested synchronous boundary guards.

    Transport entry points use this single funnel: it validates off-loop with
    the caller deadline, binds the resulting proof to the current async task,
    and resets the binding on exit. Nested sync builders/raw helpers then call
    ``require_valid_callback_config_urls`` normally and verify the proof instead
    of resolving DNS again.
    """
    proof = await require_valid_callback_config_urls_async(
        push_notification_config=push_notification_config,
        reporting_webhook=reporting_webhook,
        timeout_seconds=timeout_seconds,
    )
    with callback_url_validation_scope(proof):
        yield proof
