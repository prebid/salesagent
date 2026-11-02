"""RFC 9421 canonicalization for the AdCP request-signing profile.

Implements the canonical signature base per RFC 9421 §2.5 with the covered
components mandated by the AdCP profile (@method, @target-uri, @authority,
content-type, content-digest). URI canonicalization follows RFC 3986 §6.2
scheme-based normalization plus AdCP-specific rules: query preserved
byte-for-byte, percent-encoding hex uppercased, unreserved chars decoded,
default ports stripped, dot-segments collapsed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

import idna

from src.vendor.adcp_canonical._idna_canonicalize import canonicalize_host

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


@dataclass(frozen=True)
class SignatureInputLabel:
    """A single label in a Signature-Input header (e.g. the `sig1=...` entry)."""

    label: str
    components: tuple[str, ...]
    params: dict[str, str | int]
    raw_value: str


def parse_signature_input_header(header_value: str) -> dict[str, SignatureInputLabel]:
    """Parse a Signature-Input header value into a dict keyed by label.

    A Signature-Input header may contain multiple labels separated by commas:
    `sig1=(...);..., sig2=(...);...`. The AdCP profile mandates that verifiers
    process exactly one label (conventionally `sig1`) and ignore others.
    """
    labels: dict[str, SignatureInputLabel] = {}
    for entry in split_structured_field(header_value, ","):
        entry = entry.strip()
        if not entry:
            continue
        eq_paren = entry.find("=(")
        if eq_paren < 0:
            raise ValueError(f"malformed Signature-Input entry: {entry!r}")
        label = entry[:eq_paren].strip()
        remainder = entry[eq_paren + 1 :]
        close = remainder.find(")")
        if close < 0:
            raise ValueError(f"unterminated component list in label {label!r}")
        components_str = remainder[1:close]
        params_str = remainder[close + 1 :]
        components = tuple(_unquote_component(tok) for tok in components_str.split())
        params = _parse_params(params_str)
        labels[label] = SignatureInputLabel(
            label=label,
            components=components,
            params=params,
            raw_value=remainder,
        )
    return labels


def build_signature_base(
    method: str,
    url: str,
    headers: Mapping[str, str],
    parsed: SignatureInputLabel,
) -> str:
    """Build the RFC 9421 signature base string for the AdCP profile.

    Lines are joined with a single `\\n` (LF, not CRLF). No trailing newline.
    Components appear in the exact order listed in `Signature-Input`, followed
    by `@signature-params` as the last line.
    """
    lines: list[str] = []
    for comp in parsed.components:
        value = _resolve_component(comp, method, url, headers)
        lines.append(f'"{comp}": {value}')
    lines.append(f'"@signature-params": {parsed.raw_value}')
    return "\n".join(lines)


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


_DEFAULT_PORTS = {"http": 80, "https": 443}

# RFC 3986 §2.3 unreserved = ALPHA / DIGIT / "-" / "." / "_" / "~"
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _resolve_component(comp: str, method: str, url: str, headers: Mapping[str, str]) -> str:
    if comp == "@method":
        return method.upper()
    if comp == "@target-uri":
        return canonicalize_target_uri(url)
    if comp == "@authority":
        return canonicalize_authority(url)
    if comp.startswith("@"):
        raise ValueError(f"unsupported derived component for AdCP profile: {comp}")
    value = _lookup(headers, comp.lower())
    if value is None:
        raise ValueError(f"missing header for covered component: {comp}")
    return value.strip()


def _lookup(headers: Mapping[str, str], name_lower: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == name_lower:
            return v
    return None


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
        return "an IPv6 zone identifier (RFC 6874) is node-local and MUST be rejected in signed URLs"
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
            raise TargetUriMalformedError(netloc, "a bracketed IPv6 host missing its closing bracket")
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
        raise TargetUriMalformedError(netloc, f"the port {portstr!r} is not a sequence of ASCII digits")
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


def _normalize_path(path: str) -> str:
    return _normalize_pct(_remove_dot_segments(path))


def _remove_dot_segments(path: str) -> str:
    """RFC 3986 §5.2.4 remove_dot_segments."""
    input_buf = path
    output = ""
    while input_buf:
        if input_buf.startswith("../"):
            input_buf = input_buf[3:]
        elif input_buf.startswith("./"):
            input_buf = input_buf[2:]
        elif input_buf.startswith("/./"):
            input_buf = "/" + input_buf[3:]
        elif input_buf == "/.":
            input_buf = "/"
        elif input_buf.startswith("/../"):
            input_buf = "/" + input_buf[4:]
            slash = output.rfind("/")
            output = output[:slash] if slash >= 0 else ""
        elif input_buf == "/..":
            input_buf = "/"
            slash = output.rfind("/")
            output = output[:slash] if slash >= 0 else ""
        elif input_buf in (".", ".."):
            input_buf = ""
        else:
            if input_buf.startswith("/"):
                next_slash = input_buf.find("/", 1)
            else:
                next_slash = input_buf.find("/")
            if next_slash < 0:
                output += input_buf
                input_buf = ""
            else:
                output += input_buf[:next_slash]
                input_buf = input_buf[next_slash:]
    return output


def _normalize_pct(s: str) -> str:
    """Uppercase %XX hex and decode percent-encoded unreserved chars."""
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "%" and i + 2 < n:
            hex2 = s[i + 1 : i + 3]
            try:
                b = int(hex2, 16)
            except ValueError:
                out.append(c)
                i += 1
                continue
            ch = chr(b)
            if ch in _UNRESERVED:
                out.append(ch)
            else:
                out.append("%" + hex2.upper())
            i += 3
        else:
            out.append(c)
            i += 1
    return "".join(out)


def split_structured_field(s: str, sep: str) -> list[str]:
    """Split on `sep` occurrences that are outside RFC 8941 sf-string quotes and parens.

    sf-string escapes per RFC 8941 §3.3.3 are `\\\\` and `\\"` only; the state
    machine tracks an `esc` flag so that `\\\\"` closes the quoted span (the
    backslash escapes itself, the following quote is unescaped).
    """
    out: list[str] = []
    depth = 0
    in_q = False
    esc = False
    start = 0
    for i, c in enumerate(s):
        if in_q:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_q = False
        elif c == '"':
            in_q = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == sep and depth == 0:
            out.append(s[start:i])
            start = i + 1
    out.append(s[start:])
    return out


def _unquote_component(token: str) -> str:
    t = token.strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        return t[1:-1]
    raise ValueError(f"component name not quoted: {token!r}")


def _parse_params(params_str: str) -> dict[str, str | int]:
    out: dict[str, str | int] = {}
    s = params_str.strip()
    if not s:
        return out
    if s.startswith(";"):
        s = s[1:]
    for part in split_structured_field(s, ";"):
        part = part.strip()
        if not part:
            continue
        k, eq, v = part.partition("=")
        k = k.strip()
        v = v.strip()
        if not k:
            raise ValueError(f"signature param with empty name: {part!r}")
        if not eq or not v:
            raise ValueError(f"signature param {k!r} has empty value")
        if v.startswith('"'):
            if not (v.endswith('"') and len(v) >= 2):
                raise ValueError(f"signature param {k!r} has unterminated quoted value")
            out[k] = _unescape_sf_string(v[1:-1])
        else:
            try:
                out[k] = int(v)
            except ValueError as exc:
                raise ValueError(f"signature param {k!r} must be a quoted string or integer, got {v!r}") from exc
    return out


def _unescape_sf_string(s: str) -> str:
    """Unescape RFC 8941 §3.3.3 sf-string contents (only `\\\\` and `\\"`)."""
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n and s[i + 1] in ("\\", '"'):
            out.append(s[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)
