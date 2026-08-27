"""Verbatim copies of MERGED upstream ``adcp.signing`` fixes the pinned SDK lacks.

#1291 (``salesagent-z6nr.33``). The signing layer owns the signing contract: where
``adcp==6.6.0`` is right it delegates, and where upstream has already merged a fix the
pin does not carry, the layer vendors that fix HERE — as shipped, not reimplemented. A
reimplementation that merely passes the same vectors diverges from upstream on the
cases the vectors do not cover, and the divergence appears only after the SDK bump.

Vendoring rules (every module in this package follows them):

* **Function granularity.** Only the units upstream CHANGED are copied, plus the new
  private helpers those units introduced. Unchanged units are imported from the pinned
  SDK, never duplicated — two copies of the same parser in one process is the exact
  hazard the layer exists to prevent.
* **Verbatim per unit.** The only permitted edits are import-path rewrites, and each
  module's provenance header lists them. Everything else — bodies, docstrings,
  comments — is byte-equal to upstream at the cited merge commit, so equivalence is
  auditable by diff rather than argued.
* **Provenance per module.** Upstream repo, PR, merge commit and the issue fixed, so
  removal after an SDK bump is mechanical: delete the module, re-point the delegation
  in the layer, change no caller (``salesagent-z6nr.28`` is the shrink ticket).
* **Private to the layer.** Nothing outside ``src/core/signing/`` imports this package
  (enforced by ``tests/unit/test_architecture_signing_layer_boundary.py`` rule B);
  callers see only the facade, which is what makes the SDK-or-copy question
  unobservable to them.

Premise check (re-runnable): the conformance vectors these copies are graded by are
identical upstream and in-repo even though upstream ``main`` has moved to the 3.1.8
spec target —

    git -C ~/projects/adcp-client-python archive upstream/main \
        tests/conformance/vectors/request-signing | tar -t

byte-matches ``tests/fixtures/adcp_conformance_vectors/3.1.1/request-signing/``
(``MANIFEST.json`` excluded — ours records the sha256 pin). Verified 2026-07-31.
"""
