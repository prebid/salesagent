"""The URL-canonicalization seam: upstream's algorithm plus the comparer's rejection set.

#1291 B3 (``salesagent-z6nr.14``), re-scoped by ``salesagent-z6nr.33``. This module is
the layer's canonicalization surface. It delegates every canonical form to the VENDORED
upstream canonicalizer (:mod:`src.core.signing_contract._upstream.canonical` — the merged fixes
for upstream #977/#978/#979 that the pinned ``adcp==6.6.0`` lacks, byte-equal per unit)
and adds exactly one thing on top: the COMPARER-side rejection of raw non-ASCII hosts.

This seam is PERMANENT; only its contents vary
----------------------------------------------
An earlier framing called this module a temporary workaround to be shrunk when the SDK
catches up. That framing coupled callers to SDK release timing — the exact coupling
the layer exists to remove. The stable property is the seam itself: callers get the
layer's canonicalization semantics and can never tell whether a behaviour came from the
SDK or from the vendored copy. When the pinned SDK ships the same fixes, the vendored
units are deleted and the delegation re-pointed (``salesagent-z6nr.28``) — and no
caller changes, which is the point.

Why it still must not re-derive
-------------------------------
Two independently-DERIVED canonicalizers in one verify path agree only by luck. The
vendored module does not violate that rule, because it is not a derivation: it is
upstream's own code, verbatim, provenance-cited, auditable by diff. This module NEVER
adds a third: it gates, then hands the string to the vendored canonicalizer.

What the comparer gate adds, and where each rule is grounded
------------------------------------------------------------
``adcontextprotocol/adcp@v3.1.1:docs/reference/url-canonicalization.mdx``
is the authoritative algorithm — security.mdx §"@target-uri canonicalization" defers
to it explicitly. The vendored canonicalizer enforces the step-3 malformed-authority
rejections and step 2's IPv6 rules itself (that IS the upstream fix). One rule differs
BY DESIGN between the signing and comparing sides:

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
``reject: true`` cases grade byte-for-byte. The vendored canonicalizer raises its own
``TargetUriMalformedError(ValueError)`` carrying it; this module re-raises everything
as the facade's :class:`TargetUriMalformedError`, which additionally subclasses the
SDK's ``SignatureVerificationError`` so BOTH caller populations keep their idiom: the
verifier middleware's ``except SignatureVerificationError`` 401 path, and schema-land
URL helpers that treat a bad URL as a ``ValueError``. One public type, pinned by the
facade tests.

**It is NOT the same code as the wire-header rejection.** Request vector
``negative/026`` (non-ASCII ``Host`` on a signed request) legitimately expects
``request_signature_header_malformed`` — a verifier-checklist step-1 rejection. The two
are kept apart on purpose. What they SHARE is the predicate, not the code:
:func:`malformed_authority_reason` is the single source of the rule, called from here
with the canonicalization code and from
:func:`~src.core.signing.request_verifier_middleware._strict_header_precheck` with the
checklist code. One rule, two graded artifacts.

Note also that the vector README's worked example is STALE on this point — it shows the
reject cases expecting ``request_signature_header_malformed``. The shipped DATA wins;
do not "correct" the code from the prose.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from adcp.signing.errors import SignatureVerificationError

from src.core.signing_contract._upstream.canonical import REQUEST_TARGET_URI_MALFORMED, host_has_raw_non_ascii
from src.core.signing_contract._upstream.canonical import TargetUriMalformedError as _UpstreamTargetUriMalformedError
from src.core.signing_contract._upstream.canonical import (
    _malformed_authority_reason as _upstream_malformed_authority_reason,
)
from src.core.signing_contract._upstream.canonical import canonicalize_authority as _vendored_canonicalize_authority
from src.core.signing_contract._upstream.canonical import canonicalize_target_uri as _vendored_canonicalize_target_uri

__all__ = [
    "REQUEST_TARGET_URI_MALFORMED",
    "TargetUriMalformedError",
    "canonical_authority",
    "canonical_target_uri",
    "malformed_authority_reason",
    "reject_malformed_target",
]


class TargetUriMalformedError(SignatureVerificationError, ValueError):
    """The ONE public rejection type for a URL the layer's canonicalization refuses.

    Deliberately a subclass of BOTH caller idioms (design step 4 of
    ``salesagent-z6nr.33``):

    * ``SignatureVerificationError`` — the verifier middleware's single ``except``
      clause turns it into the graded 401 envelope (warn-mode aware), and
      ``record_signature_failed`` reads ``.code`` off it like every other rejection;
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


def canonical_target_uri(url: str) -> str:
    """The ``@target-uri`` derived component — comparer-gated, then upstream's, verbatim."""
    reject_malformed_target(url)
    return _delegated(_vendored_canonicalize_target_uri, url)


def canonical_authority(url: str) -> str:
    """The ``@authority`` derived component — comparer-gated, then upstream's, verbatim."""
    reject_malformed_target(url)
    return _delegated(_vendored_canonicalize_authority, url)


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
