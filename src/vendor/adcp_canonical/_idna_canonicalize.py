"""IDNA-2008 host canonicalization with IP-literal short-circuit.

Shared by the four signing-side callsites that canonicalize host
strings for comparison: :mod:`adcp.signing.jwks` (JWKS URI host
pinning), :mod:`adcp.signing.ip_pinned_transport` (per-connect pin
normalization), :mod:`adcp.signing.revocation_fetcher` (revocation-
issuer canonicalization), :mod:`adcp.signing.key_origins` (ADCP #3690
step 7 ``identity.key_origins`` consistency check).

**Why IP literals need a short-circuit.** ``idna.encode("192.0.2.1",
uts46=True)`` raises because IDNA-2008 rejects purely-numeric labels
(``a label which consists of digits only``). Stdlib's
``host.encode("idna")`` was lenient and returned the ASCII as-is.
Adopters running on ``allow_private=True`` dev setups with IP-literal
JWKS URIs would see ``SSRFValidationError: URI host '...' is not
IDNA-valid`` after the IDNA-2008 migration in PR #789 — a regression
on the dev-loop path without a security justification (IP literals
are not IDN candidates by definition).

Gating with :func:`ipaddress.ip_address` short-circuits IP inputs
through the encoder untouched. Both v4 (``192.0.2.1``) and v6
(``2001:db8::1`` or bracketed ``[2001:db8::1]``) are handled.

**Why ``transitional=False`` is explicit.** The default in
``idna>=3.x`` is already ``False`` (Eszett-preserving — what UTS#46
calls *non-transitional processing*), but pinning it at the callsite
documents intent and locks the canonicalization regardless of any
future upstream default flip. The package's existing eszett-regression
test (``tests/test_key_origins.py``) covers the load-bearing
behavior; the kwarg here is belt-and-suspenders. (Note: the ``idna``
package spells the kwarg ``transitional``, not the UTS#46-document
spelling ``transitional_processing``.)
"""

from __future__ import annotations

import ipaddress

import idna

__all__ = ["canonicalize_host"]


def canonicalize_host(host: str) -> str:
    """Return the canonical A-label form of ``host`` for byte-equal
    host comparisons.

    Steps:

    1. Strip a single trailing FQDN-root dot.
    2. ASCII-lowercase (IDNA encoding is case-insensitive on the
       wire but we want comparison-friendly bytes).
    3. **Short-circuit IP literals** — both v4 and v6 (with or without
       surrounding brackets) are returned as ``str(ipaddress.ip_address(host))``,
       skipping IDNA entirely. IDNA-2008 rejects purely-numeric labels.
    4. Otherwise call ``idna.encode(host, uts46=True, transitional=False)``
       and return the decoded ASCII (lowercased to match the other
       branches).

    Raises ``idna.IDNAError`` (or its parent ``UnicodeError``) on a
    label the encoder cannot process. Callers decide whether to
    fail-closed (let the exception propagate) or fall back to a
    permissive comparison (catch and use the raw input).
    """
    host = host.strip()
    if host.endswith("."):
        host = host[:-1]
    host = host.lower()
    # IP-literal short-circuit. ``[2001:db8::1]`` form (URL-bracketed)
    # comes in from some callsites; strip brackets before the parse.
    candidate = host
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        # Not an IP literal — fall through to IDNA encoding.
        return idna.encode(host, uts46=True, transitional=False).decode("ascii")
    # Compressed canonical form via str(IPv6Address) etc.
    return str(ip)
