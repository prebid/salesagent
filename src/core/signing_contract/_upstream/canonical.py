"""Upstream ``adcp.signing.canonical`` fixes, vendored verbatim at function granularity.

Provenance (see the package docstring for the vendoring rules):

* repo   ``adcontextprotocol/adcp-client-python``, branch ``main``
* PR #985, merge commit ``be233e4b`` — fixes upstream issues #977 (malformed
  authorities are canonicalized instead of rejected) and #978 (U-label hosts are not
  converted to A-labels): ``TargetUriMalformedError``, ``host_has_raw_non_ascii``,
  ``_split_or_reject``, ``_malformed_authority_reason``, ``_bracketed_host_reason``,
  the gated ``_canon_authority`` and ``_canon_host``.
* PR #987, merge commit ``afa04545`` — fixes upstream issue #979 (trailing empty query
  dropped from ``@target-uri``), plus its review follow-ups ``fbab8f44`` and
  ``4331ac84`` (RFC 3986 §3.2.3 port validation): ``canonicalize_target_uri``,
  ``canonicalize_authority``, ``_port_or_reject``.

Import-path rewrites (the ONLY permitted deviation from upstream bytes):

* ``_DEFAULT_PORTS`` and ``_normalize_path`` are UNCHANGED 6.6.0 units and are
  imported from the pinned SDK instead of living module-locally as they do upstream.
* ``canonicalize_host`` is imported from ``adcp.signing._idna_canonicalize`` exactly
  as upstream does — the installed 6.6.0 copy of that module is byte-identical to
  upstream ``main`` (verified 2026-07-31), so it is not vendored.

Everything below this docstring is upstream's code, byte-equal per unit at the cited
merge commits.
"""

from __future__ import annotations

from urllib.parse import SplitResult, urlsplit, urlunsplit

import idna
from adcp.signing._idna_canonicalize import canonicalize_host
from adcp.signing.canonical import _DEFAULT_PORTS, _normalize_path

#: Spec code for an authority the AdCP profile requires be rejected rather than
#: canonicalized. Declared here rather than imported from ``errors`` so this
#: module stays a leaf: ``errors`` may depend on canonicalization, never the
#: reverse. ``verifier`` maps this onto the wire error at its boundary.
REQUEST_TARGET_URI_MALFORMED = "request_target_uri_malformed"


class TargetUriMalformedError(ValueError):
    """A URL whose authority the profile requires be rejected.

    Subclasses ``ValueError`` because every existing caller of the
    canonicalizers already treats a bad URL as a ``ValueError``; carrying
    ``.code`` lets the verifier boundary emit the spec code instead of
    re-deriving one. The ``reason`` names which rule fired, so a rejection
    message says what was wrong rather than restating "malformed".
    """

    code = REQUEST_TARGET_URI_MALFORMED

    def __init__(self, subject: str, reason: str) -> None:
        super().__init__(f"{reason}: {subject!r}")
        self.reason = reason


def host_has_raw_non_ascii(host: str) -> bool:
    """Whether *host* carries raw non-ASCII bytes (an un-normalized U-label).

    One definition, deliberately two call sites with two different codes: here
    it selects the UTS-46 branch on the signing path, while the verifier's
    header precheck uses it to reject a U-label arriving on the wire as
    ``request_signature_header_malformed``. Share the predicate, never the code.
    """
    return not host.isascii()


def canonicalize_target_uri(url: str) -> str:
    """Produce the `@target-uri` derived-component value per AdCP profile."""
    parts = _split_or_reject(url)
    scheme = parts.scheme.lower()
    netloc = _canon_authority(parts.netloc, scheme)
    path = _normalize_path(parts.path)
    if not path and parts.query:
        path = "/"
    # RFC 9421 §2.2.2 + RFC 7230 §5.5: effective request URI excludes the
    # fragment (client-local, never sent on wire).
    target = urlunsplit((scheme, netloc, path, parts.query, ""))
    if not parts.query and "?" in url.split("#", 1)[0]:
        # `urlsplit` maps both `/p` and `/p?` to `query == ""`, and `urlunsplit`
        # emits no `?` for an empty string -- so the two collapse to one
        # signature base. A signer that sent `/p?` and a verifier that
        # reconstructs `/p` then sign different bytes for different URLs and
        # agree, which is exactly the confusion `@target-uri` exists to prevent.
        # The distinction has to be recovered from the raw URL because it is
        # already lost by the time `parts` exists.
        target += "?"
    return target


def canonicalize_authority(url: str) -> str:
    """Produce the `@authority` derived-component value per AdCP profile."""
    parts = _split_or_reject(url)
    return _canon_authority(parts.netloc, parts.scheme.lower())


def _split_or_reject(url: str) -> SplitResult:
    """`urlsplit`, with its bare refusals normalized onto the typed error.

    `urlsplit` rejects some malformed authorities itself -- `https://[::1/p`
    among them -- with a plain `ValueError` carrying no code. That refusal is
    correct but anonymous, so it is re-raised here as the same typed error the
    authority gate raises. Without this, one of the six malformed-authority
    vectors would "pass" on someone else's exception.
    """
    try:
        return urlsplit(url)
    except ValueError as exc:
        raise TargetUriMalformedError(url, f"the URL could not be parsed ({exc})") from exc


def _malformed_authority_reason(authority: str) -> str | None:
    """Why *authority* is malformed per the profile's steps 2-3, or `None`.

    A reason rather than a bool, so every caller's rejection names the rule that
    fired. *authority* is the raw netloc as received -- from a URL's `netloc`
    here, from the as-received `Host` header at the verifier boundary; the rule
    is identical on both and must not be written twice.

    Note what is deliberately absent: a raw-non-ASCII-host rejection. This
    module is shared by the signer and the verifier, and the signer is required
    to CONVERT a U-label to its A-label form, not refuse it (see the
    `idn-to-punycode` vector). Rejecting a U-label received on the wire is the
    verifier's header precheck, which raises a different code at an earlier
    step; it shares `host_has_raw_non_ascii` with this module rather than
    re-deriving the test.
    """
    host = authority.rsplit("@", 1)[-1]  # step 3: strip userinfo before judging the host
    if not host:
        return "the authority carries no host (empty, or userinfo/port with nothing before it)"
    if host.startswith("["):
        return _bracketed_host_reason(host)
    if host.count(":") > 1:
        return "an IPv6 address outside brackets is ambiguous with a port and is malformed"
    if not host.split(":", 1)[0]:
        return "the authority carries a port but no host"
    return None


def _bracketed_host_reason(host: str) -> str | None:
    """Step 2's two IPv6-literal rejections."""
    end = host.find("]")
    if end < 0:
        return "a bracketed IPv6 host missing its closing bracket is malformed"
    if "%" in host[1:end]:
        return (
            "an IPv6 zone identifier (RFC 6874) is node-local "
            "and MUST be rejected in signed URLs"
        )
    return None


def _canon_authority(netloc: str, scheme: str) -> str:
    reason = _malformed_authority_reason(netloc)
    if reason is not None:
        raise TargetUriMalformedError(netloc, reason)
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    host: str
    port: int | None = None
    if netloc.startswith("["):
        end = netloc.find("]")
        if end < 0:  # pragma: no cover - _malformed_authority_reason rejects this first
            raise TargetUriMalformedError(
                netloc, "a bracketed IPv6 host missing its closing bracket"
            )
        host = netloc[: end + 1]
        tail = netloc[end + 1 :]
        if tail.startswith(":"):
            port = _port_or_reject(tail[1:], netloc)
    elif ":" in netloc:
        host, portstr = netloc.rsplit(":", 1)
        port = _port_or_reject(portstr, netloc)
    else:
        host = netloc
    host = _canon_host(host, netloc)
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        return f"{host}:{port}"
    return host


_ASCII_DIGITS = frozenset("0123456789")


def _port_or_reject(portstr: str, netloc: str) -> int | None:
    """Parse a port per RFC 3986 §3.2.3 (`port = *DIGIT`), or reject.

    The port used to go straight into `int()`, which is far more permissive
    than the grammar and produced three distinct problems:

    * `int("-80")` gave the authority `host:-80`, which is not an authority.
    * `int("8_0")` is 80 -- Python accepts underscore digit separators.
    * `int("٨٠")` is also 80 -- `int()` accepts non-ASCII digits, so
      `host:٨٠` and `host:80` collapsed to the SAME canonical authority. A
      peer that does not fold Arabic-Indic digits derives a different
      `@authority` from identical bytes, and the signature fails for a reason
      neither side can see in its own logs.

    `str.isdigit()` does not close that last one -- `"٨٠".isdigit()` is True --
    so the test is ASCII digits specifically.

    An EMPTY port is legal and means "default": the grammar is `*DIGIT`, and
    §3.2.3 says a normalizer should drop the port and its colon when empty. So
    `https://host:/p` normalizes to `host` rather than being rejected.
    """
    if not portstr:
        return None
    if not all(ch in _ASCII_DIGITS for ch in portstr):
        raise TargetUriMalformedError(
            netloc, f"the port {portstr!r} is not a sequence of ASCII digits"
        )
    return int(portstr)


def _canon_host(host: str, netloc: str) -> str:
    """Lower-case an ASCII host, or convert a U-label to its A-label form.

    The trailing FQDN-root dot is stripped BEFORE the branch, not inside it.
    `canonicalize_host` strips it as part of UTS-46 preparation while a bare
    `.lower()` keeps it, so branching first would make `example.com.` and
    `bücher.example.` normalize differently -- a signer and a verifier reading
    the same host in two spellings would derive two authorities and every
    signature between them would fail. Stripping once, up front, is the only
    place the two branches can be made to agree without re-implementing the
    helper here and becoming another host normalizer in a tree that already has
    six.

    The ASCII fast path is load-bearing, not an optimization: `canonicalize_host`
    strips IPv6 brackets and raises on underscore hosts and over-long labels,
    all of which sign fine today.
    """
    if host.endswith("."):
        host = host[:-1]
    if not host or any(label == "" for label in host.split(".")):
        # Re-checked AFTER the strip. `_malformed_authority_reason` runs on the
        # raw netloc, where `.` and `..` are non-empty and look like hosts; it
        # is only stripping the root dot that empties them. Without this,
        # `https://./p` canonicalized to `https:///p` -- the empty authority
        # this very module rejects two functions up, reached by a path that
        # skipped the check.
        raise TargetUriMalformedError(netloc, "the authority carries no host once normalized")
    if not host_has_raw_non_ascii(host):
        return host.lower()
    try:
        return canonicalize_host(host)
    except (idna.IDNAError, UnicodeError) as exc:
        # Fail closed. A signature base must never be computed over a host we
        # could not canonicalize: the alternative is signing bytes the peer will
        # derive differently. The permissive fallback used by some sibling
        # callers is right for comparison paths, where raising would turn a
        # mismatch into an outage; it is wrong here.
        raise TargetUriMalformedError(netloc, f"the host is not a valid IDNA name ({exc})") from exc
