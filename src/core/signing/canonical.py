"""The URL-canonicalization seam: upstream's algorithm plus the comparer's rejection set.

#1291 B3 (``salesagent-z6nr.14``), re-scoped by ``salesagent-z6nr.33``. This module is
the layer's canonicalization surface. It delegates every canonical form to the PINNED
SDK (:mod:`adcp.signing.canonical`) and adds exactly one thing on top: the COMPARER-side
rejection of raw non-ASCII hosts.

The delegation used to point at ``src/vendor/adcp_canonical`` — the pinned SDK's
implementation copied verbatim — because the then-pinned ``adcp==6.6.0`` failed 14 of
the 37 published canonicalization vectors and was terminal on its line. That copy is
deleted. The pin is now ``9.0.0b1``, measured against the same vectors at **37/37 with
zero failures**, so the only reason the copy existed is gone.

This seam is PERMANENT; only its contents vary
----------------------------------------------
An earlier framing called this module a temporary workaround to be shrunk when the SDK
catches up. That framing coupled callers to SDK release timing — the exact coupling
the layer exists to remove. The stable property is the seam itself: callers get the
layer's canonicalization semantics and can never tell where a behaviour came from. The
vendored-copy era ended by re-pointing these two imports and deleting the units
(``salesagent-z6nr.28``); no caller changed, which was the point.

Why it still must not re-derive
-------------------------------
Two independently-DERIVED canonicalizers in one verify path agree only by luck. This
module NEVER adds a second: it gates, then hands the string to the SDK's canonicalizer.

The one place the layer still does more than gate is ``canonical_agent_url`` in
``src.core.schemas._base``, which completes spec step 5 ("if the path is empty AND an
authority is present, substitute ``/``"). The SDK applies it only when a query is
present, so ``https://host`` comes back unchanged where RFC 3986 §6.2.3 and the AdCP
algorithm both require ``https://host/``. No published vector covers the
empty-path-no-query case, which is how the SDK passes 37/37 while diverging. An upstream
PR fixes it; when it reaches the pin, that completion goes too.

What the comparer gate adds, and where each rule is grounded
------------------------------------------------------------
``adcontextprotocol/adcp@<pin>:docs/reference/url-canonicalization.mdx``
is the authoritative algorithm — security.mdx §"@target-uri canonicalization" defers
to it explicitly. The SDK's canonicalizer enforces the step-3 malformed-authority
rejections and step 2's IPv6 rules itself. One rule differs BY DESIGN between the
signing and comparing sides:

* **step 2** — "A host containing raw non-ASCII bytes that has not been
  ToASCII-normalized by the producer MUST be rejected by the comparer — receivers do
  not silently re-normalize." The SIGNER-side obligation is the opposite: convert the
  U-label to its A-label (upstream's ``_canon_host`` does exactly that). This module
  sits on the comparer side, so it REJECTS before delegating. The predicate is shared
  (upstream's ``host_has_raw_non_ascii``); the decision is ours. One rule, one
  predicate, two sides.

The error code
--------------
``request_target_uri_malformed`` — the string ``canonicalization.json``'s six
``reject: true`` cases grade byte-for-byte. The SDK's canonicalizer raises its own
``TargetUriMalformedError(ValueError)`` carrying it; this module re-raises everything
as the facade's :class:`TargetUriMalformedError`, which additionally subclasses the
SDK's ``SignatureVerificationError`` so BOTH caller populations keep their idiom: the
verifier's ``except SignatureVerificationError`` 401 path, and schema-land URL helpers
that treat a bad URL as a ``ValueError``. One public type, pinned by the
facade tests.

**It is the same code on the wire.** A signed request's authority is the as-received
``Host`` header (:func:`~src.core.signing.capture._target_uri` builds the URL from it),
and the verifier judges it through :func:`reject_malformed_target` — so request vectors
``negative/026`` (raw U-label host) and ``negative/031`` (unbracketed IPv6 ``Host``) are
refused with ``request_target_uri_malformed``, the code security.mdx's error table files
both shapes under. :func:`malformed_authority_reason` is the one rule, and this module is
its one caller on the comparer side.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from adcp.signing.canonical import REQUEST_TARGET_URI_MALFORMED, host_has_raw_non_ascii
from adcp.signing.canonical import TargetUriMalformedError as _UpstreamTargetUriMalformedError
from adcp.signing.canonical import canonicalize_authority as _sdk_canonicalize_authority
from adcp.signing.canonical import canonicalize_target_uri as _sdk_canonicalize_target_uri
from adcp.signing.canonical import malformed_authority_reason as _upstream_malformed_authority_reason
from adcp.signing.errors import SignatureVerificationError

#: The WEBHOOK-profile twin of ``REQUEST_TARGET_URI_MALFORMED``.
#:
#: It lives HERE, beside the request code and the one canonicalizer both profiles share,
#: because it names the same refusal judged on the same bytes — a target URI the comparer
#: will not canonicalize — and only the PROFILE differs. The pre-merge branch carried it in a
#: vendored ``signing_contract/_upstream/errors.py``; that package is retired (its
#: canonicalization is this module, its vocabulary is ``src.core.signing.vocabulary``), and a
#: whole vendored module for one string would be the second source this seam exists to remove.
#:
#: ``adcp==6.6.0``'s ``REQUEST_TO_WEBHOOK_CODE`` omitted the
#: ``request_target_uri_malformed`` -> ``webhook_target_uri_malformed`` row, so that row was
#: merged in locally (upstream fix: adcp-client-python PR #987 / fbab8f44). The 9.0.0b1 pin
#: carries it, so the local addition is gone: ``src.core.errors.signature_codes`` now takes
#: the table whole from the SDK.
WEBHOOK_TARGET_URI_MALFORMED = "webhook_target_uri_malformed"

__all__ = [
    "REQUEST_TARGET_URI_MALFORMED",
    "WEBHOOK_TARGET_URI_MALFORMED",
    "TargetUriMalformedError",
    "canonical_authority",
    "canonical_target_uri",
    "malformed_authority_reason",
    "producer_target_uri",
    "reject_malformed_target",
]


class TargetUriMalformedError(SignatureVerificationError, ValueError):
    """The ONE public rejection type for a URL the layer's canonicalization refuses.

    Deliberately a subclass of BOTH caller idioms (design step 4 of
    ``salesagent-z6nr.33``):

    * ``SignatureVerificationError`` — the verifier's single ``except`` clause turns it
      into the graded 401 envelope, and ``record_signature_failed`` reads ``.code`` off it
      like every other rejection;
    * ``ValueError`` — schema-land URL helpers (``canonical_agent_url`` and its
      consumers) keep the "a bad URL is a ValueError" contract without importing any
      verifier machinery.

    ``step`` is deliberately left unset: these are canonicalization-ALGORITHM
    rejections (url-canonicalization.mdx steps 1-8), not verifier-CHECKLIST steps
    (security.mdx 1-15); the graded artifact is the code.
    """

    def __init__(self, subject: str, reason: str) -> None:
        SignatureVerificationError.__init__(self, REQUEST_TARGET_URI_MALFORMED, message=f"{reason}: {subject!r}")
        self.reason = reason


def malformed_authority_reason(authority: str) -> str | None:
    """Why *authority* is malformed per url-canonicalization.mdx steps 2-3, or ``None``.

    A REASON rather than a bool, so every caller's rejection message names the rule that
    fired instead of restating "malformed". *authority* is the raw netloc as received —
    from a URL's ``netloc`` here, from the as-received ``Host`` header at the verifier
    boundary; the rule is identical on both and must not be written twice.

    The step-3 shapes and step 2's IPv6 rules come from the vendored upstream
    predicate. The raw-non-ASCII rejection is added HERE because it is comparer-side
    only — upstream's predicate deliberately omits it so the signing path can convert
    U-labels instead (see the vendored module's docstring).
    """
    reason = _upstream_malformed_authority_reason(authority)
    if reason is not None:
        return reason
    host = authority.rsplit("@", 1)[-1]  # step 3: strip userinfo before judging the host
    if not host.startswith("[") and host_has_raw_non_ascii(host.split(":", 1)[0]):
        # Step 2. The A-label MAPPING is the producer's job; a comparer that
        # re-normalized would pick one of several legitimate UTS-46 outcomes and
        # disagree with whoever signed.
        return "the host carries raw non-ASCII bytes and was not ToASCII-normalized by the producer"
    return None


def reject_malformed_target(url: str) -> None:
    """Raise unless *url*'s authority is one the spec permits a COMPARER to accept.

    The gate in front of the delegation. The vendored canonicalizer would itself refuse
    the step-3 shapes, but it would CONVERT a raw U-label (its side of step 2); running
    this gate first is what makes the seam comparer-correct.
    """
    try:
        authority = urlsplit(url).netloc
    except ValueError as exc:
        # ``urlsplit`` already refuses SOME shapes (an unterminated IPv6 literal) — but
        # with a bare ``ValueError``, which carries none of the graded code. Normalizing
        # it here is why the conformance case cannot pass on the wrong exception.
        raise TargetUriMalformedError(url, f"the authority is unparseable as a URI ({exc})") from exc
    reason = malformed_authority_reason(authority)
    if reason is not None:
        raise TargetUriMalformedError(url, reason)


def producer_target_uri(url: str) -> str:
    """The canonical form a PRODUCER computes: U-labels converted, nothing rejected for it.

    The other side of the one rule that differs between the two (see the module docstring):
    a producer's obligation under url-canonicalization.mdx step 2 is to ToASCII-normalize a
    U-label, where a comparer's is to refuse one it was handed raw. So this skips the gate and
    delegates straight to the vendored algorithm.

    It is THE seam for every non-verifier caller of the vendored canonicalizer, which today
    means ``src.core.schemas.canonical_agent_url`` — ``core/format-id.json`` makes
    canonicalizing ``agent_url`` a MUST before two format references may be treated as the
    same, and that is a producer computing a comparison key from a value it holds, not a
    verifier judging a URL a counterparty sent.

    Both callers reaching the vendored package through THIS module is what keeps the copy
    removable in one edit when the pinned SDK catches up
    (``tests/unit/test_architecture_vendored_code.py``), and what stops a second caller
    picking the wrong side of step 2 by importing whichever entry point it found first.
    """
    return _delegated(_sdk_canonicalize_target_uri, url)


def canonical_target_uri(url: str) -> str:
    """The ``@target-uri`` derived component — comparer-gated, then the SDK's, verbatim."""
    reject_malformed_target(url)
    return _delegated(_sdk_canonicalize_target_uri, url)


def canonical_authority(url: str) -> str:
    """The ``@authority`` derived component — comparer-gated, then the SDK's, verbatim."""
    reject_malformed_target(url)
    return _delegated(_sdk_canonicalize_authority, url)


def _delegated(canonicalize: Callable[[str], str], url: str) -> str:
    """Run a vendored canonicalizer, folding its rejections onto the facade type.

    The vendored code catches shapes the raw-netloc gate cannot see (a host that
    empties once the FQDN root dot is stripped, a non-digit port, an IDNA-invalid
    name). Those must surface as the SAME public type as the gate's own rejections —
    one type, one code, regardless of which layer of the seam refused.
    """
    try:
        return canonicalize(url)
    except _UpstreamTargetUriMalformedError as exc:
        raise TargetUriMalformedError(url, exc.reason) from exc


def origin_of(url: str | None) -> str | None:
    """The ``scheme://netloc`` of *url*, or ``None`` when it has neither.

    Shared by the revocation reader (which compares a revocation list's issuer against
    the counterparty's origin) and the verifier (which derives the ONE location a
    counterparty's keys may live at, ``<origin>/.well-known/jwks.json``). One spelling,
    because two would be two chances to disagree about a port or a default scheme -- and
    both callers feed their answer into an equality test.
    """
    if not url:
        return None
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"
