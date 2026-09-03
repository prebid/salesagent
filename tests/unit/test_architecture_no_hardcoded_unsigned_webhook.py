"""Structural guard: no webhook-signing seam call may hardcode an unsigned secret.

Backstops GH #1802: ``order_approval_service.py`` called the signing
seam with a LITERAL ``None`` secret -- never deriving it from the stored
``PushNotificationConfig`` -- so a config that asked for
``authentication_type == "HMAC-SHA256"`` signing was silently delivered
unsigned, with no error. The fix threads a config-derived ``secret`` through
instead. This guard AST-scans every call to the function that IS the signing
seam (``prepare_signed_request`` -- see src/core/security/webhook_egress.py)
and fails if any call site hardcodes a literal ``None`` for the secret
argument. A caller that genuinely wants an unsigned delivery must still reach
the seam through a NAME bound to an expression that says why (e.g.
``secret = None  # explicitly unauthenticated``) -- but no *call site* may
spell the literal directly, because that is exactly the shape that let this
bug hide for three independent webhook implementations before it was caught
by a disease scan, not a test failure.

The allowlist is empty and expected to stay empty: the disposition table in
GH #1802's codebase-scan atom found exactly one instance (the bug
itself, MIGRATEd by this same PR) and zero others.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import (
    iter_call_expressions,
    repo_root,
    scan_src,
)

# Repointed twice. Epic D lane C4: the two payload-taking helpers went module-private
# and the seam gained two public entry points. GH #1802 then deleted the
# two private signed-delivery helpers outright, and their only callers --
# ``deliver_webhook`` / ``adeliver_webhook`` -- now
# call ``prepare_signed_request`` themselves, so the seam is that ONE function. Without
# a repoint the set would name symbols that no longer exist, and the guard would scan an
# empty population and pass forever -- see test_the_guard_has_subjects below, added for
# exactly that and strengthened by pldmk.3 to require EVERY name to resolve, not just one.
_SIGNING_SEAM_FUNCTIONS = frozenset({"prepare_signed_request"})

# deliver_webhook / adeliver_webhook are deliberately NOT here. This guard's rule is
# "a signing call that passes a literal None secret delivers unsigned silently" -- and
# those two take no ``secret`` parameter at all. They receive the stored
# (scheme, credentials) pair, decide signing from the pinned type, and hand the derived
# secret to ``prepare_signed_request`` -- which IS the call this guard inspects. Listing
# them would be meaningless: there is no secret argument at those calls to look at.
# They ARE guarded by test_architecture_no_webhook_egress_text_payload, whose rule
# (no text-typed payload parameter) does apply to them.

# prepare_signed_request(payload, secret, headers, *, timestamp=None) -- secret is
# positional index 1, and is a REQUIRED parameter, so "omitted entirely" is not an
# expressible call shape (it is a TypeError, not a silent unsigned delivery). The two
# keyword-only twins that made omission expressible -- and that this detector had a
# dedicated arm for -- were deleted in GH #1802; that arm went with them.
# Every seam function must have an entry here: see
# test_every_seam_function_has_a_known_secret_slot below.
_POSITIONAL_SECRET_INDEX = {"prepare_signed_request": 1}


def test_the_signing_seam_signature_still_matches_what_this_guard_assumes() -> None:
    """The seam's name AND the secret's position, resolved rather than assumed.

    ``_POSITIONAL_SECRET_INDEX`` hardcodes a SIGNATURE fact: that the secret is
    argument 1. A reorder of ``prepare_signed_request``'s parameters would leave
    every scan here passing while the guard checked the wrong argument -- worse
    than a rename, because a rename at least stops matching.
    """
    import inspect

    from src.core.security.webhook_egress import prepare_signed_request

    params = list(inspect.signature(prepare_signed_request).parameters)
    for name, index in _POSITIONAL_SECRET_INDEX.items():
        assert name == prepare_signed_request.__name__, (
            f"this guard scans for {name!r}, but the seam is now "
            f"{prepare_signed_request.__name__!r} -- the scan matches nothing"
        )
        assert "secret" in params[index], (
            f"{name}'s parameter {index} is {params[index]!r}, not the secret. The positional "
            f"index this guard checks is stale, so it is inspecting the wrong argument: {params}"
        )


def _is_none_constant(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def find_hardcoded_unsigned_secret_violations(tree: ast.Module) -> list[int]:
    """Return line numbers of signing-seam calls that hardcode an unsigned secret."""
    violations: list[int] = []
    for func_name in _SIGNING_SEAM_FUNCTIONS:
        for call in iter_call_expressions(tree, func_name):
            secret_kw = next((kw for kw in call.keywords if kw.arg == "secret"), None)
            if secret_kw is not None:
                if _is_none_constant(secret_kw.value):
                    violations.append(call.lineno)
                continue

            positional_index = _POSITIONAL_SECRET_INDEX[func_name]
            if len(call.args) > positional_index and _is_none_constant(call.args[positional_index]):
                violations.append(call.lineno)

    return violations


class TestNoHardcodedUnsignedWebhookSecret:
    """(a) Real src/ has zero violations -- the fixed bug plus every sibling sender."""

    def test_no_violations_in_src(self) -> None:
        # Through the shared scanner: the hand-rolled walk here also called
        # ast.parse directly, bypassing the mtime cache every other guard shares.
        all_violations = scan_src(find_hardcoded_unsigned_secret_violations)

        assert not all_violations, (
            "Signing-seam call(s) hardcode an unsigned secret (a literal None) "
            f"-- silent-unsigned delivery, the GH #1802 disease: {all_violations}"
        )


class TestDetectorCatchesKnownBadSnippets:
    """(b) Positive meta-tests -- every hardcoded-unsigned shape is caught."""

    @pytest.mark.parametrize(
        ("label", "snippet"),
        [
            (
                "literal-none-positional",
                "prepare_signed_request(payload, None, headers)\n",
            ),
            (
                "literal-none-keyword",
                "prepare_signed_request(payload, headers=headers, secret=None)\n",
            ),
            (
                "literal-none-positional-with-explicit-timestamp",
                "prepare_signed_request(payload, None, headers, timestamp=ts)\n",
            ),
            (
                "literal-none-positional-module-qualified",
                "webhook_egress.prepare_signed_request(payload, None, headers)\n",
            ),
            (
                "literal-none-keyword-module-qualified",
                "webhook_egress.prepare_signed_request(payload, headers=headers, secret=None)\n",
            ),
            (
                "literal-none-in-the-seam-own-call-shape",
                "request_headers, body_bytes = prepare_signed_request(payload, None, auth_headers)\n",
            ),
        ],
    )
    def test_catches_known_bad_snippet(self, label: str, snippet: str) -> None:
        tree = ast.parse(snippet, filename=f"<known-bad:{label}>")
        assert find_hardcoded_unsigned_secret_violations(tree), f"detector missed known-bad snippet: {label}"


class TestDetectorAllowsSignedCalls:
    """(c) Negative meta-tests -- a config-derived secret is never flagged."""

    @pytest.mark.parametrize(
        ("label", "snippet"),
        [
            (
                "derived-variable-positional",
                "prepare_signed_request(payload, secret, headers)\n",
            ),
            (
                "derived-variable-keyword",
                "prepare_signed_request(payload, headers=headers, secret=secret)\n",
            ),
            (
                "derived-attribute",
                "prepare_signed_request(payload, config.webhook_secret, headers)\n",
            ),
            (
                "derived-variable-module-qualified",
                "webhook_egress.prepare_signed_request(payload, secret, headers)\n",
            ),
            (
                "name-explicitly-bound-to-none-then-passed",
                "secret = None  # explicitly unauthenticated\nprepare_signed_request(payload, secret, headers)\n",
            ),
            (
                "unrelated-call-not-in-seam",
                "some_other_function(url, payload, secret=None, headers=h)\n",
            ),
        ],
    )
    def test_allows_clean_snippet(self, label: str, snippet: str) -> None:
        tree = ast.parse(snippet, filename=f"<known-good:{label}>")
        assert not find_hardcoded_unsigned_secret_violations(tree), f"detector false-flagged clean snippet: {label}"


@pytest.mark.arch_guard
def test_the_guard_has_subjects() -> None:
    """EVERY scanned name must still be defined in the seam module.

    Added in Epic D lane C4 after a rename drained this guard silently: every name
    in ``_SIGNING_SEAM_FUNCTIONS`` had gone private, so the scan matched nothing and
    reported green. A guard that cannot fail is worse than no guard, because it
    reads as coverage.

    Strengthened in GH #1802 from "at least one resolves" to "all resolve":
    that deletion took out two of the three names while ``prepare_signed_request``
    kept this test green, so two thirds of the scanned population went dead without
    a single failure. Partial drain is the same disease as total drain, just quieter.
    """
    seam = (repo_root() / "src/core/security/webhook_egress.py").read_text(encoding="utf-8")
    missing = sorted(name for name in _SIGNING_SEAM_FUNCTIONS if f"def {name}(" not in seam)
    assert not missing, (
        f"{missing} is scanned for but no longer defined in webhook_egress.py — "
        f"the guard is scanning for symbols that no longer exist and will pass forever"
    )


@pytest.mark.arch_guard
def test_every_seam_function_has_a_known_secret_slot() -> None:
    """The detector reads ``_POSITIONAL_SECRET_INDEX[func_name]`` unconditionally.

    It can do that only while every seam function takes its secret at a known
    positional slot. A future seam function whose secret is keyword-only would need
    the omitted-secret arm this file deleted in GH #1802 brought back —
    this assertion is what forces that to be a deliberate decision rather than a
    KeyError at scan time.
    """
    assert set(_POSITIONAL_SECRET_INDEX) == set(_SIGNING_SEAM_FUNCTIONS), (
        "every name in _SIGNING_SEAM_FUNCTIONS needs a _POSITIONAL_SECRET_INDEX entry"
    )
