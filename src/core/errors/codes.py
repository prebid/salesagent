"""The one table every buyer-facing error resolves against.

An error code is a label. This module maps each label to the four things a
buyer needs with it: how to recover, what to do about it, what happened, and
the HTTP status the failure is signalled with. Nothing else in the codebase
decides those four per raise site.

The 92 codes AdCP publishes are LOADED from the pinned schema bundle inside the
installed ``adcp`` wheel -- ``enums/error-code.json``, whose ``enumMetadata``
block the spec marks normative ("SDKs MUST consume this block instead of parsing
'Recovery: X' from enumDescriptions prose"). A loaded table cannot drift from the
file it came from, so it needs no guard checking that it hasn't. A transcribed
one would.

``recovery``, ``suggestion`` AND ``message`` all come from that file and nowhere
else. A message is the first of two sources that has one: authored below, then
the file's own ``enumDescriptions`` prose.

There used to be a third source between them -- the SDK's
``STANDARD_ERROR_CODES`` -- and it was the one thing here not read from the pin.
It carried a message for 37 of the 92 published codes, so it shadowed published
text for those 37 while adding nothing for the other 55, and its coverage did
not grow with the enum. An ``adcp`` bump could therefore rewrite 37 live
buyer-facing strings with the pinned schema unchanged. Everywhere else in this
codebase the SDK is treated as a cross-check rather than the authority: the
pinned enumMetadata says "SDKs MUST consume this block ... the recovery
classification embedded in that prose is normative and MUST match the value
here", and where the shipped SDK values disagreed with the pin it was the SDK
that was drifting. Message was the last field that still disagreed on which of
the two wins, so it now answers the same way the other three do.

The platform codes in :class:`AppErrorCode` are this seller's own. The spec's
vocabulary is open by design -- ``core/error.json`` (AdCP 3.1.1): ``error.code``
is a wire-typed string rather than a closed enum, the published codes are
documentary, senders MAY emit codes outside that set, and receivers MUST decode
unknown ones by reading ``error.recovery``. So a code outside the 92 is legal,
and ``recovery`` is the part that must stay inside its three values.

Loading happens at import, because deferring it buys nothing. It is one bounded
read of one file shipped inside a package the process has already imported, and
the path is package-relative -- derived from ``importlib.resources``, not the
working directory -- so importability does not depend on where the process was
started or which uid started it. Making it lazy would add a cache, a module
``__getattr__`` and a ``TYPE_CHECKING`` declaration to move that read a few
microseconds later.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources import files
from types import MappingProxyType
from typing import Final

from adcp import get_adcp_spec_version
from adcp.types import ErrorCode
from adcp.validation.version import resolve_bundle_key

from src.core.errors._entry import CodeEntry, Recovery
from src.core.errors.signature_codes import SIGNATURE_CODE_TABLE, SignatureErrorCode

__all__ = [
    "CODE_BY_VALUE",
    "CODE_TABLE",
    "AppErrorCode",
    "CodeEntry",
    "ErrorCodeT",
    "Recovery",
    "SignatureErrorCode",
]


class AppErrorCode(StrEnum):
    """Codes this platform emits that AdCP's published set does not define.

    Legal on the wire: the spec's code vocabulary is open (see the module
    docstring). Each one names a failure this seller can distinguish and a buyer
    can act on differently -- collapsing them onto a published code would throw
    that distinction away, which is the opposite of what an open vocabulary is
    for.

    No member may name a vendor. A buyer integrates with this seller, not with
    whatever ad server sits behind it, and a code is part of the contract: once
    ``GAM_UPDATE_FAILED`` is on the wire, swapping ad servers is a breaking
    change to every buyer parsing it. The two ad-server codes are therefore
    named for the operation that failed, not the system that failed it.

    Each member carries its own :class:`CodeEntry`, so declaring a code and
    declaring what it means are one act -- a member with no entry is a
    ``TypeError`` at class creation, not a lookup that fails later on an
    error path.

    Recovery follows the same rule the spec applies to its own codes --
    correctable when the buyer can change the request and retry, transient when
    the same request may succeed later, terminal when neither is true. Messages
    reach buyer agents, so they say what happened in the buyer's terms and never
    name an internal component.
    """

    entry: CodeEntry

    def __new__(cls, value: str, entry: CodeEntry) -> AppErrorCode:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.entry = entry
        return obj

    ACTIVATION_WORKFLOW_FAILED = (
        "ACTIVATION_WORKFLOW_FAILED",
        CodeEntry(
            recovery=Recovery.TRANSIENT,
            suggestion="Check the media buy's status before retrying; it may activate without further action",
            message="The media buy was created but could not be activated",
            status=502,
        ),
    )
    AD_SERVER_CREATE_FAILED = (
        "AD_SERVER_CREATE_FAILED",
        CodeEntry(
            recovery=Recovery.TRANSIENT,
            suggestion="Retry with backoff; if it persists, the seller's operator must intervene",
            message="The media buy could not be created on the ad server",
            status=502,
        ),
    )
    AD_SERVER_UPDATE_FAILED = (
        "AD_SERVER_UPDATE_FAILED",
        CodeEntry(
            recovery=Recovery.TRANSIENT,
            suggestion="Retry with backoff; the media buy is unchanged",
            message="The media buy could not be updated on the ad server",
            status=502,
        ),
    )
    AGENT_UNREACHABLE = (
        "AGENT_UNREACHABLE",
        CodeEntry(
            recovery=Recovery.TRANSIENT,
            suggestion="Retry to pick up the missing formats; the formats that were returned are complete and usable",
            message="A configured creative agent is unreachable; its formats were not included",
            status=502,
        ),
    )
    INTERNAL_ERROR = (
        "INTERNAL_ERROR",
        CodeEntry(
            recovery=Recovery.TRANSIENT,
            suggestion="Retry with backoff; if it persists, report it to the seller's operator",
            message="The request could not be completed",
            status=500,
        ),
    )
    MEDIA_BUY_REJECTED = (
        "MEDIA_BUY_REJECTED",
        CodeEntry(
            recovery=Recovery.TERMINAL,
            suggestion="Do not retry this buy; contact the seller about the decision",
            message="The media buy was declined",
            status=422,
        ),
    )
    PARTIAL_FAILURE = (
        "PARTIAL_FAILURE",
        CodeEntry(
            recovery=Recovery.CORRECTABLE,
            suggestion="Read the per-item errors and resend only the items that failed",
            message="Some items in the request succeeded and others failed",
            status=502,
        ),
    )
    WORKFLOW_CREATION_FAILED = (
        "WORKFLOW_CREATION_FAILED",
        CodeEntry(
            recovery=Recovery.TRANSIENT,
            suggestion="Retry with backoff; the request itself was accepted",
            message="The request was accepted but its approval workflow could not be started",
            status=502,
        ),
    )


#: Any code this seller can emit: AdCP's published set, this platform's own, and the
#: RFC 9421 transport error taxonomy (:mod:`src.core.errors.signature_codes`, which
#: explains why those are codes rather than a header-only vocabulary).
#: A union rather than a subclass because a Python enum with members cannot be
#: extended -- ``class AppErrorCode(ErrorCode)`` is a TypeError at class
#: creation, not a design choice.
ErrorCodeT = ErrorCode | AppErrorCode | SignatureErrorCode


# ---------------------------------------------------------------------------
# The published 92: loaded, never transcribed
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PublishedCode:
    """One published code as the pinned schema describes it.

    Its three fields come from two sibling blocks of the same file --
    ``enumMetadata`` carries recovery and suggestion, ``enumDescriptions`` the
    prose -- so they are joined here, at the read, rather than carried around as
    parallel dicts keyed by the same 92 codes.
    """

    recovery: Recovery
    suggestion: str
    description: str


#: Trailing "Recovery: correctable (...)" clause in an ``enumDescriptions``
#: entry. It restates ``enumMetadata.recovery`` as prose for human readers, so
#: it is redundant with :attr:`CodeEntry.recovery` and belongs nowhere near a
#: buyer-facing message.
_RECOVERY_CLAUSE: Final = re.compile(r"\s*Recovery:.*\Z", re.DOTALL)


def _load_published_codes() -> Mapping[str, _PublishedCode]:
    """Read every published code out of the pinned schema bundle.

    Reached through public API only -- ``get_adcp_spec_version`` and
    ``resolve_bundle_key`` rather than the SDK's private schema-root resolver --
    because this runs at application import and a private symbol that moves
    between SDK releases would take the application down with it.
    """
    bundle_key = resolve_bundle_key(get_adcp_spec_version())
    schema = files("adcp") / "_schemas" / bundle_key / "enums" / "error-code.json"
    document = json.loads(schema.read_text(encoding="utf-8"))
    metadata = document["enumMetadata"]
    descriptions = document["enumDescriptions"]

    published: dict[str, _PublishedCode] = {}
    for code in document["enum"]:
        if code not in metadata or code not in descriptions:
            raise RuntimeError(
                f"The pinned AdCP schema lists {code} in its enum but describes it in neither "
                f"enumMetadata nor enumDescriptions. The installed adcp wheel is inconsistent."
            )
        published[code] = _PublishedCode(
            recovery=Recovery(metadata[code]["recovery"]),
            suggestion=metadata[code]["suggestion"],
            description=descriptions[code],
        )
    return MappingProxyType(published)


def _message_from_prose(description: str) -> str:
    """Turn one ``enumDescriptions`` entry into a single-line message.

    Drops the trailing recovery clause and collapses whitespace; keeps
    everything else. Deliberately NOT a first-sentence rule: the prose is full
    of abbreviations ("e.g.", "i.e."), and splitting on them silently truncates
    a message mid-clause. Long and whole beats short and cut in half.
    """
    return " ".join(_RECOVERY_CLAUSE.sub("", description).split())


# ---------------------------------------------------------------------------
# First-party entries
# ---------------------------------------------------------------------------


#: Message overrides for published codes. Only the message: recovery and
#: suggestion for these come from the pinned schema, which is authoritative for
#: them.
#:
#: These nine are the published codes that resolve to no buyer-shippable text on
#: their own: the pinned schema's prose for them is normative implementer text
#: rather than a sentence for a buyer. Seven of them say nothing about the
#: failure without also saying MUST/SHOULD about other codes and
#: ``error-details/*.json`` paths -- the shortest is 195 characters and
#: ``PERMISSION_DENIED``'s is over 2000. ``AUTH_REQUIRED``'s prose is not about
#: its failure at all ("**Deprecated** -- use ``AUTH_MISSING`` ... retained as a
#: backward-compatible alias"), and ``AUTHORIZATION_REQUIRED``'s states the
#: failure in its first clause and then spends 700 more characters on "Typical
#: use", "Distinct from", and which ``error-details`` document a seller SHOULD
#: populate. So all nine are authored here, and each one deletes itself the
#: moment the pin ships prose that reduces to a buyer sentence on its own.
#:
#: There were eight until ``CREATIVE_NOT_FOUND`` was removed: the pinned prose
#: resolves it to one clean sentence unaided, which is strictly better than a
#: hand-written override that can drift from the file it duplicates. That is the
#: bar the other 83 published codes clear too: dropping the SDK message step
#: routed 37 codes onto their pinned prose, and ``AUTH_REQUIRED`` and
#: ``AUTHORIZATION_REQUIRED`` are the only two of the 37 whose prose does not
#: reduce to a sentence, so they are authored and the other 35 are not.
_AUTHORED_SPEC_MESSAGES: Final[Mapping[ErrorCode, str]] = MappingProxyType(
    {
        ErrorCode.AUTHORIZATION_REQUIRED: "Downstream authorization is required before this action can complete",
        ErrorCode.AUTH_INVALID: "Credentials were presented but rejected",
        ErrorCode.AUTH_MISSING: "No credentials were presented",
        ErrorCode.AUTH_REQUIRED: "Authentication is required",
        ErrorCode.BILLING_NOT_SUPPORTED: "Billing model is not supported by this seller",
        ErrorCode.CONFIGURATION_ERROR: "Configuration error",
        ErrorCode.PERMISSION_DENIED: "Not authorized for this action",
        ErrorCode.UNSUPPORTED_PROVISIONING: "Settings-update entry matched no existing account",
        ErrorCode.VERSION_UNSUPPORTED: "Requested AdCP version is not supported",
    }
)


#: HTTP status for a published code this seller does not classify below. 500 is
#: what an unclassified code has always resolved to — it was the base exception's
#: own class default — so nothing that reaches this line changes shape. It is a
#: floor, not a judgement: none of this seller's raise sites can emit a code that
#: lands here, because every code a typed class declares is in the table below.
_UNCLASSIFIED_STATUS: Final = 500


#: The HTTP status each published code is signalled with.
#:
#: Keyed by CODE, which is the whole point. These 31 numbers were, until
#: salesagent-pssfi, 26 ``_default_status_code`` declarations spread over the
#: exception classes in ``src/core/exceptions.py`` — where a class was free to
#: redeclare its ``_code`` and keep a status inherited from a parent that meant
#: something else. ``SimulationError`` did exactly that: INVALID_REQUEST with
#: ``AdCPNotFoundError``'s 404, so a buyer's malformed simulation request was
#: answered 404, and the derived plain-``ToolError`` table resolved
#: INVALID_REQUEST to 400 or 404 depending on whether ``src.core.strategy`` had
#: been imported yet. Keyed by code, that state cannot be written down.
#:
#: The pinned schema fixes the BAND per code (see :class:`CodeEntry`); the exact
#: number is this seller's, and the choices are conventional HTTP: 4xx where the
#: buyer can change the request, 5xx where it cannot.
_HTTP_STATUS: Final[Mapping[ErrorCode, int]] = MappingProxyType(
    {
        # 400 — the request is malformed or unsupported as written.
        ErrorCode.INVALID_REQUEST: 400,
        ErrorCode.VALIDATION_ERROR: 400,
        ErrorCode.VERSION_UNSUPPORTED: 400,
        # 401 / 402 / 403 — who is asking, and whether they may.
        ErrorCode.AUTH_INVALID: 401,
        ErrorCode.AUTH_MISSING: 401,
        ErrorCode.ACCOUNT_PAYMENT_REQUIRED: 402,
        ErrorCode.ACCOUNT_SUSPENDED: 403,
        ErrorCode.PERMISSION_DENIED: 403,
        ErrorCode.POLICY_VIOLATION: 403,
        # 404 — a named thing does not exist, or is not the caller's to see.
        ErrorCode.ACCOUNT_NOT_FOUND: 404,
        ErrorCode.CREATIVE_NOT_FOUND: 404,
        ErrorCode.MEDIA_BUY_NOT_FOUND: 404,
        ErrorCode.PACKAGE_NOT_FOUND: 404,
        ErrorCode.PRODUCT_NOT_FOUND: 404,
        ErrorCode.REFERENCE_NOT_FOUND: 404,
        ErrorCode.SESSION_NOT_FOUND: 404,
        # 409 — the request collides with state that already exists.
        ErrorCode.ACCOUNT_AMBIGUOUS: 409,
        ErrorCode.CONFLICT: 409,
        ErrorCode.IDEMPOTENCY_CONFLICT: 409,
        ErrorCode.IDEMPOTENCY_EXPIRED: 409,
        # 410 — the resource's own status forbids the operation.
        ErrorCode.INVALID_STATE: 410,
        # Same band, same sentence: "The media buy or package cannot be canceled in its
        # CURRENT STATE." Absent from this map it fell to the 500 default, which the 5xx
        # band below defines as "the buyer cannot fix it" -- and the pin classifies this one
        # correctable, with "check the seller's cancellation policy or contact the seller".
        # A correctable code answered 500 tells the buyer two different things about one
        # refusal, the same disagreement the SERVICE_UNAVAILABLE note below records.
        ErrorCode.NOT_CANCELLABLE: 410,
        # 422 — well-formed, understood, and refused on its merits.
        ErrorCode.ACCOUNT_SETUP_REQUIRED: 422,
        ErrorCode.BUDGET_EXCEEDED: 422,
        ErrorCode.BUDGET_EXHAUSTED: 422,
        ErrorCode.BUDGET_TOO_LOW: 422,
        ErrorCode.CREATIVE_REJECTED: 422,
        ErrorCode.PROVENANCE_REQUIRED: 422,
        ErrorCode.PROVENANCE_DIGITAL_SOURCE_TYPE_MISSING: 422,
        ErrorCode.PROVENANCE_DISCLOSURE_MISSING: 422,
        ErrorCode.PROVENANCE_EMBEDDED_MISSING: 422,
        ErrorCode.PRODUCT_UNAVAILABLE: 422,
        ErrorCode.UNSUPPORTED_FEATURE: 422,
        # 429 — correct, but too often.
        ErrorCode.RATE_LIMITED: 429,
        # 5xx — the buyer cannot fix it. CONFIGURATION_ERROR and
        # SERVICE_UNAVAILABLE are the two the pin bands explicitly, both 5xx.
        ErrorCode.CONFIGURATION_ERROR: 500,
        # SERVICE_UNAVAILABLE is what the buyer is TOLD, so 503 is what the buyer
        # is answered — including for the adapter and MCP-client failures that
        # used to declare 502 while emitting this code. A 502 answer to a
        # "service unavailable" code told the buyer two different things about
        # one failure; the codes for an ad-server call that actually failed are
        # AD_SERVER_CREATE_FAILED / AD_SERVER_UPDATE_FAILED, which are 502 above.
        ErrorCode.SERVICE_UNAVAILABLE: 503,
    }
)


def _build_code_table() -> dict[ErrorCodeT, CodeEntry]:
    """Assemble the published codes and this platform's own into one table.

    A message comes from the first source that has one: authored here, then the
    pinned schema's own prose. Every code resolves to text, so no code can reach
    a buyer with an empty message. A status comes from ``_HTTP_STATUS`` or, for
    the published codes this seller never raises, from ``_UNCLASSIFIED_STATUS``.
    """
    published = _load_published_codes()
    table: dict[ErrorCodeT, CodeEntry] = {}

    for code in ErrorCode:
        spec = published[code.value]
        table[code] = CodeEntry(
            recovery=spec.recovery,
            suggestion=spec.suggestion,
            message=(_AUTHORED_SPEC_MESSAGES.get(code) or _message_from_prose(spec.description)),
            status=_HTTP_STATUS.get(code, _UNCLASSIFIED_STATUS),
        )

    # Each member carries its own entry, so there is nothing to reconcile: a code
    # without one cannot be declared.
    table.update({member: member.entry for member in AppErrorCode})

    # The signature taxonomy, generated from the SDK's own request-family table rather
    # than authored here -- see src/core/errors/signature_codes.py for why these codes
    # travel in the envelope at all.
    for signature_code, signature_entry in SIGNATURE_CODE_TABLE.items():
        table[signature_code] = signature_entry

    return table


#: Every code this seller can emit, mapped to what a buyer gets with it.
CODE_TABLE: Final[Mapping[ErrorCodeT, CodeEntry]] = MappingProxyType(_build_code_table())

#: The same vocabulary keyed by its wire string, for a reader holding a serialized code.
CODE_BY_VALUE: Final[Mapping[str, ErrorCodeT]] = MappingProxyType({str(code): code for code in CODE_TABLE})
