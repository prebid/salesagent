"""The pinned AdCP URL-canonicalization conformance vectors, run against what we ship.

``core/format-id.json`` makes canonicalizing ``agent_url`` a MUST before two format
references may be treated as the same, and points at the eight-step algorithm in
``docs/reference/url-canonicalization``. The spec publishes conformance vectors for that
algorithm and says an implementer SHOULD run them on every commit, because
"canonicalization divergence is silent until a production interop bug surfaces". This is
that run.

Two things are graded here, deliberately separately:

* the VENDORED implementation (``src/vendor/adcp_canonical``) against the raw vectors —
  a contract test on code we copied but did not write, which goes red if the copy is
  edited or if a future refresh drifts;
* ``canonical_agent_url``, the seam the rest of the codebase actually calls, against the
  EQUIVALENCES the spec defines — which is the property call sites depend on, and the
  one that must survive salesagent-3xcdk deleting the vendored copy.

The second set is written against the SPEC, not against either implementation. When the
migration lands and the seam points back at the SDK, these assertions do not change.
That is the point of writing them this way.

Why the vendored copy exists at all: the pinned SDK (adcp 6.6.0) fails 14 of the 37
vectors published for the current spec — IDN hosts never reach Punycode, the DNS root
dot survives, a trailing empty query is dropped, and six of the eight authority shapes
the spec requires be REJECTED are accepted. 6.6.0 is terminal on its line. See
``src/vendor/adcp_canonical/__init__.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.core.schemas import canonical_agent_url
from src.vendor.adcp_canonical import TargetUriMalformedError, canonicalize_target_uri

VECTORS = Path(__file__).parent.parent / "fixtures" / "adcp_canonicalization_vectors_pinned" / "canonicalization.json"


def _load() -> list[dict[str, Any]]:
    return json.loads(VECTORS.read_text())["cases"]


def _case_id(case: dict[str, Any]) -> str:
    return str(case["name"])


CASES = _load()
CANONICALIZING = [c for c in CASES if c.get("expected_target_uri") is not None]
REJECTING = [c for c in CASES if c.get("expected_target_uri") is None]


def test_the_vector_file_is_the_full_published_set():
    """Guard the fixture itself against silently shrinking.

    PROVENANCE, because it is not what you would guess. Upstream serves a vector file
    at every VERSIONED compliance path, and they are all the same stale artifact:

        /compliance/3.1.1/...   md5 17538a58...  declares "version": "3.0"  31 cases
        /compliance/3.1.15/...  md5 17538a58...  declares "version": "3.0"  31 cases
        /compliance/3.2.0/...   md5 17538a58...  declares "version": "3.0"  31 cases
        /compliance/latest/...  md5 713616e8...  declares "version": "3.2"  37 cases

    So "pin the vectors for the spec version we target" is not a thing that can be
    done here -- every versioned path hands back the identical 3.0-declared file. The
    choice is only between that file and `latest`.

    `latest` is taken, and it is the stronger choice on the merits rather than in
    spite of being newer: its 37 cases are a STRICT SUPERSET -- all 31 shared cases
    carry byte-identical expectations, and the 6 additions (DNS root dot, empty DNS
    label) introduce no conflicting rule. The implementation vendored here satisfies
    all 37 today, and the SDK release that eventually replaces it is the same code, so
    grading the extra six costs nothing now and nothing at migration.

    This is a deliberate departure from `adcp_webhook_vectors_pinned/_refresh.py`,
    which pins its vectors to a tag and says so. That convention protects against a
    moving target changing an expectation underneath us. Here the moving target is the
    only source that HAS the current rules, and the subset check above is what guards
    the risk it was protecting against -- if a refresh ever alters a shared
    expectation rather than adding cases, the grading tests below go red, which is the
    detection that matters.
    """
    assert CASES, "vector file is empty or failed to parse"
    assert len(CASES) == 37, (
        f"expected the full 37-case published set, got {len(CASES)}. A SHRINK means "
        f"someone swapped in a versioned path's 31-case file (see the provenance table "
        f"above); a GROWTH means upstream added rules -- re-run the suite and confirm "
        f"the vendored copy still satisfies them before accepting it."
    )


@pytest.mark.parametrize("case", CANONICALIZING, ids=_case_id)
def test_vendored_canonicalizer_matches_the_expected_target_uri(case: dict[str, Any]):
    """Contract test on vendored code: byte-for-byte, as the algorithm requires."""
    assert canonicalize_target_uri(case["input_url"]) == case["expected_target_uri"], (
        f"rule under test -- {case.get('rule', 'unspecified')}"
    )


@pytest.mark.parametrize("case", REJECTING, ids=_case_id)
def test_vendored_canonicalizer_rejects_malformed_authorities(case: dict[str, Any]):
    """A malformed authority is REFUSED, not canonicalized into something plausible.

    Accepting one is worse than a wrong string: it silently mints an identity for a URL
    the spec says has none, and every downstream comparison then agrees about a
    fiction.
    """
    with pytest.raises(TargetUriMalformedError):
        canonicalize_target_uri(case["input_url"])


# --- the seam, graded on the spec's EQUIVALENCES ------------------------------------
#
# These are the assertions that must survive salesagent-3xcdk. They name a rule and two
# spellings, and say whether the spec makes them one agent. No implementation detail
# appears in them.

SAME_AGENT = [
    ("step 5: empty path and `/` are one", "https://agent.example.com", "https://agent.example.com/"),
    ("step 1: scheme case", "HTTPS://agent.example.com/p", "https://agent.example.com/p"),
    ("step 2: host case", "https://Agent.Example.COM/p", "https://agent.example.com/p"),
    ("step 3: userinfo is stripped", "https://user:pw@agent.example.com/p", "https://agent.example.com/p"),
    ("step 4: default port is dropped", "https://agent.example.com:443/p", "https://agent.example.com/p"),
    ("step 5: dot segments resolve", "https://agent.example.com/a/../b", "https://agent.example.com/b"),
    ("step 6: unreserved pct-decoding", "https://agent.example.com/a%2Db", "https://agent.example.com/a-b"),
    ("step 8: fragment is stripped", "https://agent.example.com/p#section", "https://agent.example.com/p"),
]

DIFFERENT_AGENTS = [
    ("the PATH is part of the identity", "https://agent.example.com", "https://agent.example.com/mcp"),
    ("one host may serve MCP and A2A at different paths", "https://a.example.com/mcp", "https://a.example.com/a2a"),
    (
        "a well-known sales path is an agent_url, not noise (the pin's own example)",
        "https://publisher.com",
        "https://publisher.com/.well-known/adcp/sales",
    ),
    (
        "nor is a trailing slash on it free -- the exact pair the old rstrip collapsed",
        "https://publisher.com/.well-known/adcp/sales",
        "https://publisher.com/.well-known/adcp/sales/",
    ),
    ("a trailing slash on a NON-empty path is a different path", "https://a.example.com/x", "https://a.example.com/x/"),
    ("step 5 preserves consecutive slashes", "https://a.example.com/admin//foo", "https://a.example.com/admin/foo"),
    ("step 1: scheme is preserved, not coerced", "http://a.example.com/p", "https://a.example.com/p"),
    ("step 7: query order is not normalized", "https://a.example.com/p?x=1&y=2", "https://a.example.com/p?y=2&x=1"),
    ("different host", "https://a.example.com/p", "https://b.example.com/p"),
]


@pytest.mark.parametrize("rule,left,right", SAME_AGENT, ids=[r[0] for r in SAME_AGENT])
def test_canonical_agent_url_treats_these_as_one_agent(rule: str, left: str, right: str):
    assert canonical_agent_url(left) == canonical_agent_url(right), rule


@pytest.mark.parametrize("rule,left,right", DIFFERENT_AGENTS, ids=[r[0] for r in DIFFERENT_AGENTS])
def test_canonical_agent_url_keeps_these_apart(rule: str, left: str, right: str):
    assert canonical_agent_url(left) != canonical_agent_url(right), rule


def test_the_seam_applies_step_5_which_the_vendored_copy_does_not():
    """The one rule `canonical_agent_url` adds, and the reason it has to.

    Step 5 substitutes `/` for an empty path when an authority is present. The vendored
    implementation applies it only when a query is present, and no conformance vector
    covers the no-query case -- so it passes every vector while still leaving
    `https://h.com` and `https://h.com/` as two agents. That is the spelling difference
    a human actually types, so the seam closes it.

    This test is the tripwire for the migration: when the SDK closes the gap, the first
    assertion below starts failing, and the shim in `canonical_agent_url` can go.
    """
    assert canonicalize_target_uri("https://h.example.com") == "https://h.example.com", (
        "vendored copy no longer leaves an empty path alone -- drop the step-5 shim in "
        "canonical_agent_url (salesagent-3xcdk)"
    )
    assert canonical_agent_url("https://h.example.com") == "https://h.example.com/"


def test_a_malformed_agent_url_raises_rather_than_comparing_equal_to_something():
    """The refusal reaches our callers as a ValueError subclass, by design."""
    with pytest.raises(ValueError):
        canonical_agent_url("https:///no-host")
