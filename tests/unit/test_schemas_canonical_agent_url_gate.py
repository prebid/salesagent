"""``canonical_agent_url`` observes the signing layer's GATED canonicalization.

#1291 (``salesagent-z6nr.33`` plan steps 4-5). ``canonical_agent_url`` is the single
canonical form for federation identity — it keys ``format_id_identity`` comparisons and
the creative-agent format cache. url-canonicalization.mdx (v3.1.1) steps 2-3 enumerate
authority shapes that MUST be rejected rather than canonicalized into something
plausible: no host, userinfo/port with no host, IPv6 zone identifiers, unclosed IPv6
brackets.

The measured defect this pins: ``_base.py`` imported ``canonicalize_target_uri``
straight from ``adcp.signing`` — the pinned SDK (6.6.0) does not implement the
MUST-reject rules, so federation identity silently ACCEPTED every one of those shapes
(``https:///p`` canonicalized to itself; two distinct malformed references could
compare equal). The fix routes ``canonical_agent_url`` through ``src.core.signing.canonical``,
the ONE seam over the vendored canonicalizer, so schema-land sees the rejection set the
conformance vectors grade.

The caller-side contract pinned here (design step 4): the rejection is a ValueError —
schema-land URL helpers keep ValueError semantics; a transport-agnostic schema helper
must not demand callers know an SDK verifier exception — carrying the graded
canonicalization code ``request_target_uri_malformed`` as ``.code``. The full exception
type/MRO pin (also a SignatureVerificationError) belongs to the facade's own tests, not
to this boundary.

Scope, against the two neighbours that grade the rest:

* A raw non-ASCII host is NOT in the table below. ``canonical_agent_url`` is the
  PRODUCER side and maps it to its A-label (``münchen.example`` ->
  ``xn--mnchen-3ya.example``); step 2's "MUST be rejected ... receivers do not silently
  re-normalize" binds the COMPARER, and is graded on ``canonical_authority`` by
  ``test_signing_conformance_canonicalization.py::test_raw_u_label_authority_is_rejected_not_normalized``.
* What the gate must NOT disturb — the canonical form of well-formed URLs — is graded
  on the spec's equivalences by ``test_url_canonicalization_vectors.py``, including
  step 5 (``https://x.org`` and ``https://x.org/`` are one agent, rendered ``/``).
  This file pins only the refusals, which that module covers for one shape and without
  the graded code.
"""

from __future__ import annotations

import pytest

from src.core.schemas import canonical_agent_url

#: The graded rejection code — canonicalization.json's ``reject: true`` cases grade
#: this string byte-for-byte.
REQUEST_TARGET_URI_MALFORMED = "request_target_uri_malformed"


class TestCanonicalAgentUrlRejectsMalformedAuthorities:
    """Federation identity refuses the authority shapes that MUST be rejected."""

    @pytest.mark.parametrize(
        "agent_url",
        [
            pytest.param("https:///path", id="no-host-at-all"),
            pytest.param("https://:443/path", id="port-but-no-host"),
            pytest.param("https://user@/path", id="userinfo-but-no-host"),
            pytest.param("https://[fe80::1%25eth0]/path", id="ipv6-zone-identifier"),
            pytest.param("https://[::1/path", id="unclosed-ipv6-bracket"),
        ],
    )
    def test_malformed_authority_raises_valueerror_with_the_graded_code(self, agent_url):
        """A malformed agent_url raises ValueError carrying the typed rejection code.

        On the unfixed SDK path the first three shapes were accepted outright (e.g.
        ``https:///path`` -> ``https:///path``) and the bracket case raised a BARE
        ValueError from urlsplit with no graded code — both are the
        unfixed-canonicalization leak.
        """
        with pytest.raises(ValueError) as excinfo:
            canonical_agent_url(agent_url)

        assert getattr(excinfo.value, "code", None) == REQUEST_TARGET_URI_MALFORMED, (
            f"canonical_agent_url({agent_url!r}) raised {type(excinfo.value).__name__} "
            f"without the graded code — the rejection must come from the signing layer's "
            f"gate, not incidentally from urlsplit."
        )
