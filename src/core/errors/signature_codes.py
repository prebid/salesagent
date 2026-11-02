"""The RFC 9421 transport error taxonomy, as codes this seller can put on the wire.

AdCP 3.1.1 ``security.mdx`` § Transport error taxonomy:

    Stable codes returned in ``WWW-Authenticate: Signature error="<code>"`` on 401, and
    surfaced by SDK verifiers as typed errors.

and § ``WWW-Authenticate`` format:

    Verifiers MUST emit ``WWW-Authenticate: Signature error="<code>"`` with no ``realm``
    parameter and no other parameters.

WHY THEY ARE IN ``CODE_TABLE`` AT ALL
-------------------------------------
Before #1721 the verifier was an ASGI middleware that SENT its own bodyless 401 with the
challenge attached, so the code never had to be an AdCP error code. On #1721's boundary a
refusal is an identity failure raised inside ``_resolve_identity``: ``invoke_tool`` catches
it, ``failure_response`` renders it, and ``AuthChallengeResponder`` — the ONE renderer —
lifts it to 401 and writes the challenge from the code it reads off the FINISHED body. So
the specific code has to survive into the envelope, because the renderer has nothing else
to read. A generic ``AUTH_INVALID`` would pass our tests and fail conformance:
``dist/compliance/3.1.1/universal/signed-requests.yaml`` grades the challenge string
byte-for-byte.

That is legal, and the spec says so directly: ``core/error.json`` types ``error.code`` as a
wire string rather than a closed enum, the published codes are documentary, senders MAY
emit codes outside that set, and receivers MUST decode an unknown one by reading
``error.recovery``. ``recovery`` is the part that must stay inside its three values, and
:data:`SIGNATURE_CODE_TABLE` is where that is decided.

DERIVED FROM THE SDK'S OWN TAXONOMY, NOT RE-LISTED
---------------------------------------------------
The 28 members come from ``adcp.signing.errors.REQUEST_TO_WEBHOOK_CODE`` (27 of them), which is the
table the SDK's verifier raises from. A transcribed copy would be a second source for a
string the spec grades byte-for-byte, and it would drift the first time the SDK's
taxonomy grew — silently, as an unclassified code reaching ``CodeEntry`` construction.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from adcp.signing.errors import REQUEST_TO_WEBHOOK_CODE

from src.core.errors._entry import CodeEntry, CodeGroup, Recovery
from src.core.signing.canonical import REQUEST_TARGET_URI_MALFORMED, WEBHOOK_TARGET_URI_MALFORMED

#: The SDK's retag table plus the ONE row it omits at ``adcp==6.6.0``.
#:
#: ``REQUEST_TO_WEBHOOK_CODE`` maps each request-profile code to its webhook-profile twin,
#: and it is missing ``request_target_uri_malformed`` -> ``webhook_target_uri_malformed``
#: (upstream fix: adcp-client-python PR #987 / fbab8f44). Merged here rather than
#: transcribed, so the other 27 rows still come from the SDK and the local addition
#: disappears by itself when the pin advances past the fix.
_TAXONOMY: Final = {**REQUEST_TO_WEBHOOK_CODE, REQUEST_TARGET_URI_MALFORMED: WEBHOOK_TARGET_URI_MALFORMED}

__all__ = ["SIGNATURE_CODE_TABLE", "SignatureErrorCode", "challenge_for"]

if TYPE_CHECKING:
    # The MEMBERS are generated, so no static checker can know their names -- and this enum
    # is a member of the ``ErrorCodeT`` union, which has to be usable as an annotation in
    # sixty places. A functional-API call assigned at module level is a VARIABLE to mypy, and
    # a variable is not valid as a type; suppressing that would be a silencing comment the
    # repo's ratchet counts, rightly, as a claim nobody checks.
    #
    # So: a spelled-out declaration for the checker, the generated one at runtime. The two
    # agree or the build breaks LOUDLY at a source line: ``src/core/exceptions.py`` names
    # every member below on a class of its own, so a member the runtime enum stops carrying
    # is an ``AttributeError`` at import, and a member the runtime enum gains that is absent
    # here is a mypy error where it is used.
    #
    # That noise is the POINT. The 28 error classes are this seller's public API, and an SDK
    # upgrade that respells a code must not silently respell a class nobody can see change.
    # The roster is written out so an upstream change arrives as a conflict a human reads.
    class SignatureErrorCode(StrEnum):
        REQUEST_SIGNATURE_AGENT_NOT_IN_BRAND_JSON = "request_signature_agent_not_in_brand_json"
        REQUEST_SIGNATURE_ALG_NOT_ALLOWED = "request_signature_alg_not_allowed"
        REQUEST_SIGNATURE_BRAND_JSON_AMBIGUOUS = "request_signature_brand_json_ambiguous"
        REQUEST_SIGNATURE_BRAND_JSON_MALFORMED = "request_signature_brand_json_malformed"
        REQUEST_SIGNATURE_BRAND_JSON_UNREACHABLE = "request_signature_brand_json_unreachable"
        REQUEST_SIGNATURE_BRAND_JSON_URL_MISSING = "request_signature_brand_json_url_missing"
        REQUEST_SIGNATURE_BRAND_ORIGIN_MISMATCH = "request_signature_brand_origin_mismatch"
        REQUEST_SIGNATURE_CAPABILITIES_UNREACHABLE = "request_signature_capabilities_unreachable"
        REQUEST_SIGNATURE_COMPONENTS_INCOMPLETE = "request_signature_components_incomplete"
        REQUEST_SIGNATURE_COMPONENTS_UNEXPECTED = "request_signature_components_unexpected"
        REQUEST_SIGNATURE_DIGEST_MISMATCH = "request_signature_digest_mismatch"
        REQUEST_SIGNATURE_HEADER_MALFORMED = "request_signature_header_malformed"
        REQUEST_SIGNATURE_INVALID = "request_signature_invalid"
        REQUEST_SIGNATURE_JWKS_UNAVAILABLE = "request_signature_jwks_unavailable"
        REQUEST_SIGNATURE_JWKS_UNTRUSTED = "request_signature_jwks_untrusted"
        REQUEST_SIGNATURE_KEY_ORIGIN_MISMATCH = "request_signature_key_origin_mismatch"
        REQUEST_SIGNATURE_KEY_ORIGIN_MISSING = "request_signature_key_origin_missing"
        REQUEST_SIGNATURE_KEY_PURPOSE_INVALID = "request_signature_key_purpose_invalid"
        REQUEST_SIGNATURE_KEY_REVOKED = "request_signature_key_revoked"
        REQUEST_SIGNATURE_KEY_UNKNOWN = "request_signature_key_unknown"
        REQUEST_SIGNATURE_PARAMS_INCOMPLETE = "request_signature_params_incomplete"
        REQUEST_SIGNATURE_RATE_ABUSE = "request_signature_rate_abuse"
        REQUEST_SIGNATURE_REPLAYED = "request_signature_replayed"
        REQUEST_SIGNATURE_REQUIRED = "request_signature_required"
        REQUEST_SIGNATURE_REVOCATION_STALE = "request_signature_revocation_stale"
        REQUEST_SIGNATURE_TAG_INVALID = "request_signature_tag_invalid"
        REQUEST_SIGNATURE_WINDOW_INVALID = "request_signature_window_invalid"
        REQUEST_TARGET_URI_MALFORMED = "request_target_uri_malformed"

else:
    #: Every request-family signature code. The member NAME is the upper-cased code and the
    #: VALUE is the wire string, which is the one that matters: it is what reaches
    #: ``error.code`` and the ``WWW-Authenticate`` challenge.
    SignatureErrorCode = StrEnum("SignatureErrorCode", {code.upper(): code for code in sorted(_TAXONOMY)})


#: How a buyer reacts to each code, ONE ROW PER CODE, transcribed from the spec.
#:
#: The SDK publishes the VOCABULARY (``_TAXONOMY``, above) and not the classification:
#: ``enums/error-code.json``'s ``enumMetadata`` carries ``{recovery, suggestion}`` for the
#: 92 codes ``_load_published_codes`` reads, and for NONE of these 28 — measured, not
#: assumed. So the classification has to be stated here, and this is the whole of it.
#:
#: WHY A TABLE AND NOT A RULE. This was a pair of suffix-marker tuples and a
#: ``_recovery_for`` that matched them, so a code's class followed from how it was SPELLED.
#: That put ``request_signature_brand_json_malformed`` in ``correctable`` on the
#: ``_malformed`` suffix, while L1122 says "Verifier: do not retry; surface to operations"
#: — it is the COUNTERPARTY's document and the buyer cannot edit it. Because the message
#: and the suggestion are selected by class, the buyer was also told to re-sign a request
#: that was fine. A rule cannot see whose document is malformed; the spec states it per row,
#: so this does too. ``AppErrorCode`` declares its eight the same way, and for the same
#: reason: declaring a code and declaring what it means is one act.
#:
#: SOURCES, both at v3.1.1. The ``Failure | Retry? | Code`` table (L1373) classifies 20;
#: the discovery table (L1119-1127) classifies the other 9 in its remediation column. Note
#: that ``Retry? No`` does NOT mean terminal — it spans "the caller can fix this and send
#: it again" and "no autonomous recovery", which is exactly the distinction a spelling rule
#: erased. Each row below takes the reading its remediation text states.
_RECOVERY: Final[Mapping[str, Recovery]] = MappingProxyType(
    {
        # The buyer signs, or re-signs, and sends it again. Every one of these is a fault
        # in what the CALLER put on the wire.
        "request_signature_required": Recovery.CORRECTABLE,
        "request_signature_header_malformed": Recovery.CORRECTABLE,
        "request_signature_params_incomplete": Recovery.CORRECTABLE,
        "request_signature_tag_invalid": Recovery.CORRECTABLE,
        "request_signature_alg_not_allowed": Recovery.CORRECTABLE,
        "request_signature_window_invalid": Recovery.CORRECTABLE,
        "request_signature_components_incomplete": Recovery.CORRECTABLE,
        "request_signature_components_unexpected": Recovery.CORRECTABLE,
        "request_target_uri_malformed": Recovery.CORRECTABLE,
        # Somebody else's availability, so the SAME request may succeed later.
        # L1120 "Surface as transient"; L1121 "Same retry/cache discipline";
        # L1373 "Yes (with backoff)".
        "request_signature_capabilities_unreachable": Recovery.TRANSIENT,
        "request_signature_brand_json_unreachable": Recovery.TRANSIENT,
        "request_signature_jwks_unavailable": Recovery.TRANSIENT,
        # Refused on the signature's own merits: the bytes arrive identically on every
        # retry, so every retry fails identically (L1373 "No" throughout).
        "request_signature_invalid": Recovery.TERMINAL,
        "request_signature_digest_mismatch": Recovery.TERMINAL,
        "request_signature_replayed": Recovery.TERMINAL,
        "request_signature_rate_abuse": Recovery.TERMINAL,
        "request_signature_revocation_stale": Recovery.TERMINAL,
        # The signer's KEY material or its declaration. An operator fixes these; a buyer
        # cannot, and retrying changes nothing.
        "request_signature_key_unknown": Recovery.TERMINAL,
        "request_signature_key_purpose_invalid": Recovery.TERMINAL,
        "request_signature_key_revoked": Recovery.TERMINAL,
        "request_signature_jwks_untrusted": Recovery.TERMINAL,
        "request_signature_key_origin_mismatch": Recovery.TERMINAL,
        "request_signature_key_origin_missing": Recovery.TERMINAL,
        # The counterparty's TRUST ROOT. The buyer does not own these documents, which is
        # why the spelling rule got this group wrong. L1119/L1122 "surface to operations;
        # do not retry"; L1123-L1125 "Not retryable".
        "request_signature_brand_json_url_missing": Recovery.TERMINAL,
        "request_signature_brand_json_malformed": Recovery.TERMINAL,
        "request_signature_brand_origin_mismatch": Recovery.TERMINAL,
        "request_signature_agent_not_in_brand_json": Recovery.TERMINAL,
        "request_signature_brand_json_ambiguous": Recovery.TERMINAL,
    }
)


def _recovery_for(code: str) -> Recovery:
    """How a buyer should react to *code*, read off :data:`_RECOVERY`.

    A lookup, deliberately with no default. A code in the SDK's taxonomy with no row here
    raises at import rather than being classified by a fallback — the classification is the
    only machine-readable signal a buyer gets for this family (``core/error.json`` types
    ``error.code`` as an open string, so a receiver decodes an unknown code by reading
    ``error.recovery``), and guessing it silently is what this table replaced.
    """
    return _RECOVERY[code]


#: What a buyer is TOLD, per recovery class. One sentence each rather than 28, because the
#: distinguishing information is the CODE — which is on the wire, twice (``error.code`` and
#: the challenge) — and because a buyer-facing message must not narrate which checklist step
#: refused: AdCP 3.1.1 ``transport-errors.mdx`` § Security Considerations forbids putting
#: internal detail in a client-facing field, and "your nonce was already claimed" is a
#: verifier-internal fact an unauthenticated caller should not be able to probe for.
_TEXT: Final[dict[Recovery, tuple[str, str]]] = {
    Recovery.CORRECTABLE: (
        "The request signature was missing or malformed",
        "Sign the request per RFC 9421 as this agent's request_signing capability describes, and send it again",
    ),
    Recovery.TRANSIENT: (
        "This agent could not reach the documents that establish the signer's keys",
        "Retry with backoff; if it persists, check that your brand.json and JWKS are reachable",
    ),
    Recovery.TERMINAL: (
        "The request signature was rejected",
        "Do not retry unchanged; check the named code, then rotate or re-register the signing key",
    ),
}

#: Every signature code, mapped to what a buyer gets with it. Merged into ``CODE_TABLE``.
#:
#: ``status`` is 401 for all of them, from the taxonomy's own first sentence ("returned in
#: ``WWW-Authenticate: Signature error="<code>"`` on 401"). It has to agree with what
#: ``AuthChallengeResponder`` writes, and it does, because both are that one number.
SIGNATURE_CODE_TABLE: Final = MappingProxyType(
    {
        member: CodeEntry(
            recovery=_recovery_for(member.value),
            suggestion=_TEXT[_recovery_for(member.value)][1],
            message=_TEXT[_recovery_for(member.value)][0],
            status=401,
            # What makes these 28 different from every other code, declared once, here.
            # ``AuthChallengeResponder`` asks the table rather than holding its own list:
            # see CodeGroup for why the fact lives on the code and not on the renderer.
            group=CodeGroup.SIGNATURE,
        )
        for member in SignatureErrorCode
    }
)


def challenge_for(code: str) -> str:
    """The ``WWW-Authenticate`` value for a signature refusal.

    THE one expression of the string the compliance vectors grade byte-for-byte. It is the
    SDK's ``unauthorized_response_headers`` reduced to its f-string, because the renderer
    that needs it holds a CODE read off a finished response body and not the exception the
    SDK's helper takes. ``tests/unit/test_signature_challenge_string.py`` pins this against
    that helper for all 28 codes, so the SDK stays the cross-check.

    No ``realm`` and no other parameters, per the pin.
    """
    return f'Signature error="{code}"'
