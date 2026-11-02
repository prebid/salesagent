"""AdCP URL canonicalization, vendored from ``adcp`` 7.0.2 (Apache-2.0).

**Delete this package when salesagent-3xcdk lands.** It exists only to close the
gap between the SDK version this repo pins and the one that implements the
spec's canonicalization algorithm correctly.

## Why it is here

``core/format-id.json`` makes canonicalizing ``agent_url`` a MUST before two
format references may be treated as the same, per the eight-step algorithm at
``docs/reference/url-canonicalization``. The spec publishes 37 conformance
vectors for it. Measured against those vectors:

    adcp 6.6.0 (this repo's pin)   14 of 37 FAIL
    adcp 7.0.2                     37 of 37 pass

6.6.0's failures are not cosmetic: IDN hosts are never converted to Punycode,
the DNS root dot is not stripped, a trailing empty query is dropped, and six of
the eight authority shapes the spec requires be REJECTED are accepted instead.
6.6.0 is terminal on the 6.x line, so no patch is coming.

Bumping to 7.0.2 is not available as a small change — it forces mcp 2.x, which
forces fastmcp 3.2 -> 4.x, and 7.0.2 replaces ``Creative.format_id`` with
``format_kind`` + ``format_option_ref``, which is a model migration measured at
571 broken unit tests. That work is salesagent-3xcdk. This vendored copy is the
bridge: correct behaviour now, at the cost of one directory that gets deleted.

## What was changed

``canonical.py`` and ``_idna_canonicalize.py`` are BYTE-IDENTICAL to adcp 7.0.2
except for a single line — ``canonical.py``'s import of its sibling, repointed
into this package. Verbatim on purpose: a trimmed copy would risk introducing
exactly the divergence this exists to remove, and a faithful one diffs cleanly
against upstream when the migration lands.

``canonical.py`` also carries request-signing helpers (``build_signature_base``,
``parse_signature_input_header``, structured-field parsing). **Nothing in this
repo uses them, and nothing should** — request signing is the SDK's, per
CLAUDE.md pattern #9. They are present only because the file is verbatim. This
module deliberately re-exports the canonicalization entry points and nothing
else, so an unrelated caller cannot reach the signing half through here.

## The one thing this does NOT fix

Spec step 5 says an empty path with an authority present becomes ``/``. 7.0.2
applies it only when a query is present (``https://h.com?x=1`` ->
``https://h.com/?x=1``), leaving ``https://h.com`` alone; no conformance vector
covers the no-query case, so 7.0.2 passes 37/37 while still diverging. The
two-line correction lives with the caller, in
``src.core.schemas.canonical_agent_url``, cited to the step it implements.
"""

from src.vendor.adcp_canonical.canonical import (
    TargetUriMalformedError,
    canonicalize_authority,
    canonicalize_target_uri,
)

__all__ = [
    "TargetUriMalformedError",
    "canonicalize_authority",
    "canonicalize_target_uri",
]
