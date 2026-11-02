"""The one place this codebase decides what a duplicate JSON object key means.

Core Invariant (GH #1802): duplicate-key detection is a property of
the ONE parse that turns webhook bytes into a value — a single strict-parse
primitive, built on the stdlib ``object_pairs_hook`` the spec itself
prescribes, never a hand-rolled recursive walker, and never re-decided at a
call site.

Spec grounding: pinned AdCP 3.1.1, ``docs/building/by-layer/L1/security.mdx``
§Duplicate object keys — the legacy HMAC-SHA256 fallback's signer and verifier
MUSTs, and step 14a: *"stdlib ``json.loads(..., object_pairs_hook=...)`` —
detect duplicates inside the hook and raise. Satisfies the check."* The
vendored conformance vectors (``tests/fixtures/adcp_webhook_vectors_pinned/webhook-hmac-sha256.json``,
``signer_side.rejection_vectors``: top-level / nested / array-contained /
three-deep) exist specifically to catch a hand-rolled recursive walker that
misses array-contained duplicates or halts at a fixed depth — the stdlib hook
fires for every JSON object at every nesting depth, including objects nested
inside arrays, which is what satisfies all four by construction.

Placed beside ``outbound_http.py``/``webhook_egress.py``: this module widens
this package's charter to wire bytes in BOTH directions (egress via
``webhook_egress.py``, ingress via this module), not egress only. It does
**not** own every untrusted JSON parse in ``src/`` — notably
``outbound_http.OutboundResult.json()`` (external HTTP response bytes, no
spec MUST, stays lenient — see GH #1802) and
``core.helpers.mcp_tool_payload.extract_tool_payload`` (external MCP text
content, a different DRY primitive for a different untrusted-but-unsigned
input class) are deliberately out of this module's scope.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

# Step 14b sub-rule (b): truncate to the last UTF-8 codepoint at or below this
# many bytes. No existing helper in this codebase does byte-denominated,
# codepoint-safe truncation (every other truncation in src/ is a raw `str`
# character slice) -- this is the one part of 14b with no precedent to reuse.
_MAX_SANITIZED_KEY_BYTES = 32

# Step 14b sub-rule (c): cap the number of reported key names, appending a
# count marker for the rest.
_MAX_KEY_NAMES = 4


def _truncate_to_max_bytes(name: str) -> str:
    """Truncate *name* to the last UTF-8 codepoint at or below 32 bytes."""
    encoded = name.encode("utf-8")
    if len(encoded) <= _MAX_SANITIZED_KEY_BYTES:
        return name
    truncated = encoded[:_MAX_SANITIZED_KEY_BYTES]
    while truncated:
        try:
            return truncated.decode("utf-8")
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return ""


def _sanitize_one_key_name(name: str) -> str:
    """One duplicate key name, made safe to log, per step 14b (a) then (b).

    (a) truncate at the first non-printable character to ``<sanitized:N>``
        (``N`` is the original character count) -- this is a stricter,
        different operation than ``core.logging_config.log_safe`` (which only
        neutralizes CR/LF by removal, not truncation-with-marker), but the
        same underlying concern: an untrusted string must never reach a log
        line able to forge or corrupt it.
    (b) else truncate to the last UTF-8 codepoint at or below 32 bytes.
    """
    for ch in name:
        if not ch.isprintable():
            return f"<sanitized:{len(name)}>"
    return _truncate_to_max_bytes(name)


def sanitize_key_names(names: Sequence[str]) -> tuple[str, ...]:
    """Total helper: every duplicate key name, safe to log.

    Mirrors ``core.webhook_validator.webhook_url_for_log``'s
    partial-function-plus-total-wrapper convention -- a caller of this
    function can never fall through to an unsanitized name. Caps at 4 names
    (step 14b sub-rule (c)), appending a count marker for the rest.
    """
    sanitized = tuple(_sanitize_one_key_name(name) for name in names)
    if len(sanitized) > _MAX_KEY_NAMES:
        remainder = len(sanitized) - _MAX_KEY_NAMES
        sanitized = (*sanitized[:_MAX_KEY_NAMES], f"<...{remainder} more>")
    return sanitized


class DuplicateKeyInput(Exception):
    """A JSON object in the parsed input repeats a key at some depth.

    ``code`` is the spec's error identifier (AdCP 3.1.1 L1/security.mdx
    §Duplicate object keys) -- byte-exact, case-sensitive, never renamed to
    match a refactor. It is a plain class attribute, deliberately never
    surfaced as ``AdCPSalesAgentError.error_code``: ``duplicate_key_input`` is a
    lowercase spec-fixture discriminator, absent from the pinned
    ``WIRE_STANDARD_CODES`` table (all SCREAMING_SNAKE), and
    ``translate_error_code`` passes unknown codes to the buyer unchanged --
    setting it as ``error_code`` would emit a non-conformant wire code.

    ``keys`` are already sanitized per step 14b (:func:`sanitize_key_names`)
    and safe to log or include in an operator-facing message.
    """

    code = "duplicate_key_input"

    def __init__(self, keys: tuple[str, ...]):
        self.keys = keys
        joined = ", ".join(keys) if keys else "(unknown)"
        super().__init__(f"duplicate object key(s): {joined}")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook``: build the dict, raising if any key repeats.

    Called once per JSON object encountered during the parse, at every
    nesting depth including objects nested inside arrays -- the property that
    satisfies the vendored conformance vectors' array-contained and
    three-deep cases by construction, with no recursion written here at all.
    """
    seen: set[str] = set()
    duplicates: list[str] = []
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            duplicates.append(key)
        else:
            seen.add(key)
        result[key] = value
    if duplicates:
        raise DuplicateKeyInput(sanitize_key_names(tuple(duplicates)))
    return result


def loads_rejecting_duplicate_keys(data: bytes | str) -> Any:
    """``json.loads`` that rejects a JSON object repeating a key, at any depth.

    Raises :class:`DuplicateKeyInput` — never a generic parse error — when a
    duplicate key is found. Any OTHER parse failure (empty body, non-JSON
    content, malformed syntax) raises the normal
    ``json.JSONDecodeError``/``ValueError`` unchanged: this function adds
    exactly the duplicate-key MUST and nothing else about what counts as
    valid JSON.
    """
    return json.loads(data, object_pairs_hook=_reject_duplicate_pairs)
