"""``canonical_agent_url`` observes the signing layer's GATED canonicalization.

#1291 (``salesagent-z6nr.33`` plan steps 4-5). ``canonical_agent_url`` is the single
canonical form for federation identity — it keys ``format_id_identity`` comparisons and
the creative-agent format cache. Identity comparison is COMPARER-side work, and
url-canonicalization.mdx (v3.1.1) steps 2-3 enumerate authority shapes a comparer MUST
reject: no host, userinfo/port with no host, raw non-ASCII hosts the producer never
ToASCII-normalized, IPv6 zone identifiers, unclosed IPv6 brackets.

The measured defect this pins: ``_base.py`` imported ``canonicalize_target_uri``
straight from ``adcp.signing`` — the pinned SDK (6.6.0) does not implement the
MUST-reject rules, so federation identity silently ACCEPTED every one of those shapes
(``https:///p`` canonicalized to itself; two distinct malformed references could
compare equal). The fix routes ``canonical_agent_url`` through the signing facade's
gated ``canonical_target_uri``, so schema-land sees the same rejection set the
conformance vectors grade.

The caller-side contract pinned here (design step 4): the rejection is a ValueError —
schema-land URL helpers keep ValueError semantics; a transport-agnostic schema helper
must not demand callers know an SDK verifier exception — carrying the graded
canonicalization code ``request_target_uri_malformed`` as ``.code``. The full exception
type/MRO pin (also a SignatureVerificationError, for the middleware catcher) belongs to
the facade's own tests, not to this boundary.
"""

from __future__ import annotations

import pytest

from src.core.schemas import canonical_agent_url

#: The graded rejection code — canonicalization.json's six ``reject: true`` cases
#: grade this string byte-for-byte.
REQUEST_TARGET_URI_MALFORMED = "request_target_uri_malformed"


class TestCanonicalAgentUrlRejectsMalformedAuthorities:
    """Federation identity refuses the authority shapes a comparer MUST reject."""

    @pytest.mark.parametrize(
        "agent_url",
        [
            pytest.param("https:///path", id="no-host-at-all"),
            pytest.param("https://:443/path", id="port-but-no-host"),
            pytest.param("https://user@/path", id="userinfo-but-no-host"),
            pytest.param("https://münchen.example/path", id="raw-non-ascii-host"),
            pytest.param("https://[fe80::1%25eth0]/path", id="ipv6-zone-identifier"),
            pytest.param("https://[::1/path", id="unclosed-ipv6-bracket"),
        ],
    )
    def test_malformed_authority_raises_valueerror_with_the_graded_code(self, agent_url):
        """A malformed agent_url raises ValueError carrying the typed rejection code.

        Today the SDK path accepts the first five shapes outright (e.g.
        ``https:///path`` -> ``https:///path``) and the sixth raises a BARE ValueError
        from urlsplit with no graded code — both are the unfixed-canonicalization leak.
        """
        with pytest.raises(ValueError) as excinfo:
            canonical_agent_url(agent_url)

        assert getattr(excinfo.value, "code", None) == REQUEST_TARGET_URI_MALFORMED, (
            f"canonical_agent_url({agent_url!r}) raised {type(excinfo.value).__name__} "
            f"without the graded code — the rejection must come from the signing layer's "
            f"gate, not incidentally from urlsplit."
        )


class TestCanonicalAgentUrlStillCanonicalizes:
    """The gate rejects; it must not disturb the canonical form of well-formed URLs."""

    @pytest.mark.parametrize(
        ("agent_url", "expected"),
        [
            pytest.param("https://Example.COM:443/x", "https://example.com/x", id="lowercase-and-default-port"),
            pytest.param("https://x.org/", "https://x.org", id="trailing-slash-stripped"),
            pytest.param("https://x.org", "https://x.org", id="bare-origin-unchanged"),
        ],
    )
    def test_well_formed_urls_keep_the_single_canonical_form(self, agent_url, expected):
        assert canonical_agent_url(agent_url) == expected
