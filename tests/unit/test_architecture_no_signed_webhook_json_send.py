"""Guard: no module holding a webhook signature header may send it via json=.

KEPT DELIBERATELY, with the measurement that decided it. A proposal retired this
guard on the grounds that its subject "became a type error" when the seam was
typed. Measured instead of assumed: write the violation into ``src/`` and run
mypy, and mypy ACCEPTS it — ``json=`` survives on both ``send`` and ``asend``
alongside ``content: bytes | None``, so a third signer in this shape is still
perfectly representable. This guard is live protection, not residue.

GH #1802 deleted the two signers (``WebhookAuthenticator.sign_payload``,
``WebhookDeliveryService._generate_hmac_signature``) that recreated the
signed-bytes-vs-wire-bytes divergence bug: each computed an HMAC over one
serialization of a payload dict, then handed the SAME dict to
``send(..., json=payload)``, letting httpx re-serialize it independently — a
receiver verifying the signature against the bytes it actually received
rejected every delivery. Deleting those two signers makes the OLD bug
unconstructible, but nothing stops a THIRD signer from being written tomorrow
in the same shape. This guard closes that class going forward: no module under
``src/`` may hold a computed ``X-*-Signature`` header key and also call
``send``/``asend`` with ``json=`` — the only sound pattern is
``src.core.security.webhook_egress.deliver_webhook``/``adeliver_webhook``
(both of which are ``prepare_signed_request`` + ``content=`` internally, the
shape any other caller must use directly), which serializes once and
transmits exactly those bytes.

What counts as a violation: a module that (a) uses a string matching
``X-*-Signature`` (case-insensitive) as a dict KEY — a headers-dict subscript
assignment or a dict literal — AND (b) calls ``send``/``asend`` with a
``json=`` keyword, anywhere in the same module. Same one-hop, same-module
scope as ``test_architecture_no_call_site_backoff.py`` — a guard is not a
call graph.

What does NOT count, deliberately: the string appearing only in a docstring
or comment (prose, not a header-dict key) — this module's own docstring
mentions ``X-*-Signature`` and must not self-flag. A module that holds a
signature-header key but sends via ``content=`` (the correct shape). A module
that calls ``send``/``asend`` with ``json=`` but never computes a signature
header (Slack notifications, vendor callbacks with no HMAC).
"""

from __future__ import annotations

import ast
import re

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    iter_call_expressions,
    parse_module,
    repo_root,
    scan_src,
)

# Matches "X-AdCP-Signature", "X-ADCP-Signature", "X-Webhook-Signature",
# "X-Hub-Signature-256", etc. — any X-prefixed header whose name contains
# "Signature", case-insensitive.
_SIGNATURE_HEADER_KEY = re.compile(r"^x-[a-z0-9-]*signature[a-z0-9-]*$", re.IGNORECASE)

SEND_NAMES = frozenset({"send", "asend"})

# Pre-existing violations: none. Seeded empty at GH #1802 — the two
# known signers were migrated onto the webhook_egress seam (today:
# ``deliver_webhook``/``adeliver_webhook``), not allowlisted. Any entry here must
# name the ticket that removes it.
ALLOWLIST: frozenset[str] = frozenset()

FIX_HINT = (
    "A module that computes an X-*-Signature header must not also call send/asend "
    "with json= -- httpx re-serializes the dict independently of whatever was signed. "
    "Route through src.core.security.webhook_egress.deliver_webhook / "
    "adeliver_webhook (or prepare_signed_request + content=) instead."
)


def _is_signature_header_key(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(_SIGNATURE_HEADER_KEY.match(node.value))
    )


def _has_signature_header_key(tree: ast.AST) -> bool:
    """True if a signature-style header name is used as a dict KEY anywhere.

    Two contexts, both real code (never a docstring/comment, since only
    dict keys and subscripts are matched):

    - ``headers["X-AdCP-Signature"] = value`` (Subscript assignment target)
    - ``{"X-AdCP-Signature": value, ...}`` (Dict literal key, however it's
      used — assigned, passed to ``.update()``, or passed straight to a call)
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _is_signature_header_key(node.slice):
            return True
        if isinstance(node, ast.Dict):
            if any(key is not None and _is_signature_header_key(key) for key in node.keys):
                return True
    return False


def find_signed_webhook_json_send_violations(tree: ast.Module) -> list[int]:
    """Line numbers of send/asend(..., json=...) calls in a module that also
    holds a computed signature-header key.
    """
    if not _has_signature_header_key(tree):
        return []
    violations: list[int] = []
    for name in SEND_NAMES:
        for call in iter_call_expressions(tree, name=name):
            if any(kw.arg == "json" for kw in call.keywords):
                violations.append(call.lineno)
    return violations


def _scan_src() -> dict[str, list[int]]:
    """Every module in src/ where the disease pattern appears, minus the allowlist.

    ``exempt=`` rather than a post-filter on the result: the same suppression
    written downstream escapes ``scan_src``'s liveness rule entirely, so a future
    entry could sit here suppressing nothing. That is exactly the shape the
    scanner exists to make unwritable.
    """
    return scan_src(find_signed_webhook_json_send_violations, exempt=ALLOWLIST)


class TestNoSignedWebhookJsonSend:
    """No module that holds a computed signature header sends it via json=."""

    @pytest.mark.arch_guard
    def test_no_signed_webhook_json_send_in_src(self):
        unexpected = _scan_src()
        assert not unexpected, (
            "modules computing a webhook signature header but sending via json= "
            f"(re-serialization divergence risk): {unexpected}\n{FIX_HINT}"
        )

    @pytest.mark.arch_guard
    def test_allowlist_is_empty(self):
        """The allowlist is a ratchet pinned at zero. Any growth reopens the disease."""
        assert len(ALLOWLIST) == 0, f"allowlist grew to {len(ALLOWLIST)} entries — a signer was allowlisted, not fixed."


class TestGuardDetector:
    """The guard's own correctness, on synthetic sources."""

    @pytest.mark.arch_guard
    def test_detector_catches_known_bad(self):
        assert_detector_catches_ast_snippets(
            find_signed_webhook_json_send_violations,
            snippets={
                "subscript assignment, mixed case": (
                    "from src.core.security.outbound_http import send\n"
                    "headers = {}\n"
                    "headers['X-AdCP-Signature'] = sig\n"
                    "send(url, json=payload, headers=headers)\n"
                ),
                "subscript assignment, upper case": (
                    "from src.core.security.outbound_http import send\n"
                    "headers = {}\n"
                    "headers['X-ADCP-SIGNATURE'] = sig\n"
                    "send(url, json=payload, headers=headers)\n"
                ),
                "dict literal key": (
                    "from src.core.security.outbound_http import asend\n"
                    "headers = {'X-Webhook-Signature': sig, 'Content-Type': 'application/json'}\n"
                    "async def f():\n"
                    "    await asend(url, json=payload, headers=headers)\n"
                ),
                "dict literal passed to update": (
                    "from src.core.security.outbound_http import send\n"
                    "headers = {}\n"
                    "headers.update({'X-Hub-Signature-256': sig})\n"
                    "send(url, json=payload, headers=headers)\n"
                ),
                "non-standard signature header spelling": (
                    "from src.core.security.outbound_http import send\n"
                    "headers = {}\n"
                    "headers['X-Custom-Signature-V2'] = sig\n"
                    "send(url, json=payload, headers=headers)\n"
                ),
            },
        )

    @pytest.mark.arch_guard
    @pytest.mark.parametrize(
        ("label", "source"),
        [
            (
                "correct shape: content= not json=",
                (
                    "from src.core.security.outbound_http import send\n"
                    "headers = {}\n"
                    "headers['X-AdCP-Signature'] = sig\n"
                    "send(url, content=body_bytes, headers=headers)\n"
                ),
            ),
            (
                "json= with no signature header",
                (
                    "from src.core.security.outbound_http import send\n"
                    "headers = {'Content-Type': 'application/json'}\n"
                    "send(url, json=payload, headers=headers)\n"
                ),
            ),
            (
                "signature header mentioned only in a docstring",
                (
                    '"""Sends via X-AdCP-Signature per spec."""\n'
                    "from src.core.security.outbound_http import send\n"
                    "send(url, json=payload, headers={})\n"
                ),
            ),
            (
                "signature header mentioned only in a plain string, not a dict key",
                (
                    "from src.core.security.outbound_http import send\n"
                    "message = 'X-AdCP-Signature header set'\n"
                    "send(url, json=payload, headers={})\n"
                ),
            ),
            (
                "unrelated header key, json= present",
                (
                    "from src.core.security.outbound_http import send\n"
                    "headers = {'Authorization': 'Bearer x'}\n"
                    "send(url, json=payload, headers=headers)\n"
                ),
            ),
            (
                "non-send call with json= kwarg (unrelated function)",
                ("headers = {'X-AdCP-Signature': sig}\nsome_other_function(url, json=payload, headers=headers)\n"),
            ),
        ],
    )
    def test_detector_ignores_non_violations(self, label, source):
        assert find_signed_webhook_json_send_violations(ast.parse(source)) == [], f"false positive on {label}"

    @pytest.mark.arch_guard
    def test_own_module_docstring_does_not_self_flag(self):
        """This guard's own module mentions X-*-Signature in prose; must not self-trigger."""
        this_file = repo_root() / "tests" / "unit" / "test_architecture_no_signed_webhook_json_send.py"
        assert find_signed_webhook_json_send_violations(parse_module(this_file)) == []

    @pytest.mark.arch_guard
    def test_webhook_egress_module_is_genuinely_clean(self):
        """The migration target has no signature-header-key + json= pair (proves the fix, not just the guard)."""
        egress = repo_root() / "src" / "core" / "security" / "webhook_egress.py"
        assert find_signed_webhook_json_send_violations(parse_module(egress)) == []
