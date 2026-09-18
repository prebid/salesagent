"""The RFC 9421 signing layer (#1291), on #1721's boundary. Inbound AND outbound.

DELIBERATELY EMPTY. Every module here is imported by its own dotted path, and that is
what keeps the layer's import graph acyclic without a PEP 562 lazy-export table:
``src.core.metrics`` reads :mod:`src.core.signing.vocabulary` to bound an
attacker-chosen Prometheus label, and :mod:`src.core.signing.verifier` reads
``src.core.metrics``. A facade re-exporting either would close that cycle at import
time, which is what the separate ``src.core.signing_contract`` package existed to
break on the pre-merge branch. With no re-exports there is nothing to break, which is
why that package is retired rather than merged.

Where each piece lives:

:mod:`.vocabulary`      every name an operation label can carry, derived from TOOLS
:mod:`.canonical`       the URL-canonicalization seam over ``src.vendor.adcp_canonical``;
                        comparer side and producer side, and both target-uri codes
:mod:`.posture`         a tenant's ``request_signing`` block (declared, inbound) and its
                        ``webhook_signing`` block (derived, outbound)
:mod:`.capture`         the HTTP message an inbound signature covers
:mod:`.verifier`        THE inbound verifier, called by ``_resolve_identity``
:mod:`.replay_store`    the Postgres-backed nonce store the SDK checklist calls
:mod:`.revocation`      checklist step 9, the counterparty's revocation list
:mod:`.webhook_credentials`  the escalation read off the VALIDATED request
:mod:`.outbound`        the per-tenant signer resolution both outbound profiles share
:mod:`.request_signer`  the RFC 9421 REQUEST profile as one ``(method, url, body)`` callback
:mod:`.provider`        signing material: row -> key ref -> PEM -> provider
:mod:`.keys`            key lifecycle (mint, rotate, revoke)
:mod:`.trust_root`      the published JWKS / brand.json documents
:mod:`.revocation_list` the published revocation list
"""
