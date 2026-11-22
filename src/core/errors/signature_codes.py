"""The RFC 9421 transport error taxonomy, as codes this seller can put on the wire.

``security.mdx`` § Transport error taxonomy:

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
``dist/compliance/<pin>/universal/signed-requests.yaml`` grades the challenge string
byte-for-byte.

That is legal, and the spec says so directly: ``core/error.json`` types ``error.code`` as a
wire string rather than a closed enum, the published codes are documentary, senders MAY
emit codes outside that set, and receivers MUST decode an unknown one by reading
``error.recovery``. ``recovery`` is the part that must stay inside its three values, and the
pin's ``enums/request-signing-error-code.json`` publishes it: "SDKs MUST consume enumMetadata for
recovery classification".

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

from src.core.errors._entry import Recovery

#: The SDK's retag table, whole.
#:
#: ``REQUEST_TO_WEBHOOK_CODE`` maps each request-profile code to its webhook-profile twin.
#: At ``adcp==6.6.0`` it omitted ``request_target_uri_malformed`` ->
#: ``webhook_target_uri_malformed``, so this merged that one row in locally (upstream fix:
#: adcp-client-python PR #987 / fbab8f44) and said the addition would disappear by itself
#: when the pin advanced past the fix. the pinned SDK carries it, so it has: the table is 29 rows
#: from the SDK and nothing local.
_TAXONOMY: Final = dict(REQUEST_TO_WEBHOOK_CODE)

__all__ = ["SIGNATURE_MESSAGES", "SIGNATURE_STATUS", "SignatureErrorCode", "challenge_for"]

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
        REQUEST_BODY_MALFORMED = "request_body_malformed"
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


#: What a buyer is TOLD, per recovery class. One sentence each rather than 29, because the
#: distinguishing information is the CODE -- which is on the wire, twice (``error.code`` and
#: the challenge) -- and because a buyer-facing message must not narrate which checklist step
#: refused. The pin's ``enumDescriptions`` for this family do exactly that ("The keyid was absent
#: from the signer JWKS after the required refetch"), so they are not served:
#: ``transport-errors.mdx`` § Security Considerations forbids putting internal detail in a
#: client-facing field, and "your nonce was already claimed" is a verifier-internal fact an
#: unauthenticated caller should not be able to probe for.
#:
#: The recovery and suggestion are the pin's, read with the other published codes
#: (``enums/request-signing-error-code.json``, ``src/core/errors/codes.py``).
SIGNATURE_MESSAGES: Final[Mapping[Recovery, str]] = MappingProxyType(
    {
        Recovery.CORRECTABLE: "The request signature was missing or was not accepted",
        Recovery.TRANSIENT: "This agent could not reach the documents that establish the signer's keys",
        Recovery.TERMINAL: "The request signature was rejected",
    }
)

#: The taxonomy's own first sentence: "returned in ``WWW-Authenticate: Signature error="<code>"``
#: on 401". It has to agree with what ``AuthChallengeResponder`` writes.
SIGNATURE_STATUS: Final = 401


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
