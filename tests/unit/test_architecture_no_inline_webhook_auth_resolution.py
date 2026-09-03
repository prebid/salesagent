"""Structural guard: no module resolves webhook auth inline, outside the resolver.

The disease, stated once: *a webhook sender resolves push-notification auth
inline from the config — picking its own FIELD and its own VALUE spelling —
instead of calling one shared resolver.* A codebase-wide enumeration
(GH #1802) found three independent senders doing it three different
ways:

* ``order_approval_service`` compares lowercase ``"bearer"`` / ``"basic"``, and
  takes its HMAC secret from ``webhook_secret`` — a column with ZERO writers in
  ``src/``, so every real HMAC row hits its refusal branch while the code
  believes the buyer supplied nothing.
* ``protocol_webhook_service`` compares spec-cased ``"Bearer"`` /
  ``"HMAC-SHA256"`` and reads ``authentication_token`` — the AdCP 3.1.1 field.
* ``webhook_delivery_service`` does a third thing again.

Three copies is not a coincidence; it is what happens when the auth decision is
a two-line ``if`` that is cheaper to retype than to look up. The fix is one
seam (``deliver_webhook``/``adeliver_webhook`` in
:mod:`src.core.security.webhook_egress`), and
this guard is what keeps copy #4 from appearing — the same role
``test_architecture_no_hardcoded_unsigned_webhook.py`` plays for the sibling
defect, whose own docstring records that the previous instance was "caught by a
disease scan, not a test failure".

Two shapes are detected, because the disease has two halves and either one
alone is enough to diverge:

**A. Comparing ``authentication_type`` against a scheme literal.** This is where
the SPELLING divergence lives (``"bearer"`` vs ``"Bearer"``). Reading the column
for logging or for a read-back response is fine; deciding from it is not.

**B. Reading ``authentication_token`` / ``webhook_secret`` off another object.**
This is where the FIELD divergence lives. Writes (``x.authentication_token =
...``, ``authentication_token=...`` keyword arguments) are untouched — the
persistence path is not the disease. ``self.<attr>`` is untouched too: an object
reading its own attribute (``WebhookVerifier.webhook_secret``, the RECEIVER-side
verifier) is not a sender picking a column off a stored config.

Scope note — the guard deliberately does NOT try to detect
``src/admin/blueprints/principals.py``'s hand-built ``PushNotificationConfig``
(disposition row 5, deferred to GH #1802). That site names ``auth_type=``
/ ``auth_config=``, kwargs that are not columns on the model at all; it is a
different defect (it writes nothing that any sender can read) and belongs to its
own ticket. Widening this detector to catch it would mean matching on invented
names, which no rule can state.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import (
    SCHEME_LITERAL_CASINGS,
    assert_detector_catches_ast_snippets,
    assert_violations_match_allowlist,
    scan_src,
)

# The scheme spellings a sender may not compare against inline. Both cases are
# listed because the whole point is that the two senders disagree about which
# one the column holds; the resolver is where that comparison becomes
# case-insensitive, and it is the ONLY place allowed to make it.
_SCHEME_LITERALS = SCHEME_LITERAL_CASINGS

# Credential columns a sender may not read off a config object.
# NARROWED in Epic D lane C4 to ``webhook_secret`` alone.
#
# ``authentication_token`` was removed from this set deliberately, and the reason
# is that the rule it served became unsatisfiable rather than unimportant. While a
# resolver existed, a sender touching that column was about to DECIDE something,
# so banning the read was a good proxy. Senders now pass the stored
# ``(scheme, credentials)`` pair to the egress seam and branch on the returned
# outcome — they must read the column to hand it over, so flagging the read would
# forbid the very shape this epic installed.
#
# What the guard still catches is unchanged and is the real disease: comparing
# ``authentication_type`` against a scheme literal (a sender answering "is this
# HMAC?" itself), and reading ``webhook_secret`` — a column with ZERO writers in
# src/, so any read of it is reaching for a credential nothing populates
# (GH #1894 defect 1).
_CREDENTIAL_ATTRS = frozenset({"webhook_secret"})

# The resolver's own module — "outside the resolver" is the rule, so the
# resolver is not scanned. Not an allowlist entry: an allowlist records debt,
# and this records the one legitimate home for the logic.

# NO exemption for arguments forwarded to the seam.
#
# There used to be one: a sender migrating onto the resolver necessarily wrote
# ``resolver(config.authentication_type, config.authentication_token)``, and that
# read was the fix rather than the disease. Epic D lane C4 narrowed
# ``_CREDENTIAL_ATTRS`` to ``webhook_secret`` alone and thereby removed the
# exemption's reason to exist; it survived the narrowing as residue.
#
# Measured before deleting it: its ONLY reachable effect was to whitelist a read
# of ``webhook_secret`` -- the column this module's own docstring calls one with
# ZERO writers in src/, so any read of it reaches for a credential nothing
# populates -- plus a scheme-literal comparison written inline as a seam
# argument, which is a hole in the primary rule too. Keeping it would have meant
# certifying both holes as intended.

# Files permitted to still contain the disease, by path. Shrink-only.
#
# Two kinds of entry, and they are documented differently at the source:
#
# * DEBT — the disease is really there and someone will remove it. Needs a
#   ``# FIXME(#<gh-issue>)`` comment at the source location (a GitHub issue, never
#   a beads id, so an outside contributor can resolve it). Closing the issue
#   removes the entry.
# * JUSTIFIED FALSE POSITIVE — the detector matches but the semantic is different,
#   and there is nothing to fix. A FIXME would be a lie about future work; the
#   reason lives in the comment here and beside the code.
#
# This guard grades the file SET; the source comments tell the next reader which
# kind each one is.
# NOTE: `set()`, not `{}` — a brace literal containing only comments is an
# empty DICT, which the allowlist helper cannot subtract.
# ``src/services/webhook_delivery_service.py`` was here until
# GH #1802 (GH #1894) converged it on the resolver. Do not re-add
# it, or anything else: the only remaining entry below is a justified false
# positive, so a NEW debt entry would mean a sender started resolving auth
# inline again -- which is the whole disease.
#
# The a2a_server entry (a read-BACK of authentication_token, echoing the
# buyer's own registration to them) was REMOVED in Epic D lane C4: with the
# set narrowed to webhook_secret, that read is no longer flagged at all, so the
# entry became stale. The allowlist is now EMPTY and must stay that way.
_ALLOWLIST: set[tuple] = set()

_FIX_HINT = (
    "Pass the stored (scheme, credentials) pair straight to "
    "src.core.security.webhook_egress.deliver_webhook/adeliver_webhook and branch on the returned "
    "WebhookDeliveryOutcome. Do not compare authentication_type against a scheme literal, and do not "
    "stash authentication_token in a local to build headers with — that is how three senders ended up "
    "with three different answers."
)


def _is_scheme_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in _SCHEME_LITERALS


def _names_authentication_type(node: ast.expr) -> bool:
    """True for ``x.authentication_type`` or a bare ``authentication_type``."""
    if isinstance(node, ast.Attribute):
        return node.attr == "authentication_type"
    return isinstance(node, ast.Name) and node.id == "authentication_type"


def _is_self(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "self"


def _credential_read_lineno(node: ast.AST) -> int | None:
    """Retained ONLY for ``webhook_secret``, a column with zero writers in src/.

    Reading it means a sender is reaching for a credential that no writer ever
    populates, which is a defect on its own terms (GH #1894 defect 1) and unrelated
    to the resolver's removal.
    """
    if isinstance(node, ast.Attribute):
        reads_dead_column = node.attr == "webhook_secret" and isinstance(node.ctx, ast.Load)
        if reads_dead_column and not _is_self(node.value):
            return node.lineno
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
        # getattr(config, "webhook_secret", None) — the duck-typed spelling of
        # the same read, and the one webhook_delivery_service actually uses.
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            if node.args[1].value == "webhook_secret" and not _is_self(node.args[0]):
                return node.lineno
    return None


def find_inline_webhook_auth_violations(tree: ast.Module) -> list[int]:
    """Return line numbers where webhook auth is resolved inline."""
    violations: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            names_column = any(_names_authentication_type(operand) for operand in operands)
            names_literal = any(_is_scheme_literal(operand) for operand in operands)
            if names_column and names_literal:
                violations.append(node.lineno)
                continue
        lineno = _credential_read_lineno(node)
        if lineno is not None:
            violations.append(lineno)
    return sorted(violations)


def _violating_src_files() -> dict[str, list[int]]:
    """Modules that resolve webhook auth inline instead of through the resolver.

    The resolver module itself carried a blanket skip until the shared scanner
    started raising on suppressions that suppress nothing: measured, the detector
    finds NO violation in it, so the skip excluded nothing while silently
    pre-authorizing one if the module ever grew an inline resolution. Nothing is
    skipped now, and if the resolver ever does trip its own detector that will
    be a finding to look at rather than a hole.
    """
    return scan_src(find_inline_webhook_auth_violations)


class TestNoInlineWebhookAuthResolution:
    """(a) Real ``src/`` — only the deferred rows may still resolve auth inline."""

    def test_src_matches_allowlist(self) -> None:
        found = _violating_src_files()
        assert_violations_match_allowlist(
            {(relpath,) for relpath in found},
            _ALLOWLIST,
            fix_hint=f"{_FIX_HINT}\n\nViolating lines: {found}",
        )


class TestDetectorCatchesKnownBadSnippets:
    """(b) Positive meta-tests — every inline-resolution shape is caught."""

    def test_catches_known_bad_snippets(self) -> None:
        assert_detector_catches_ast_snippets(
            find_inline_webhook_auth_violations,
            snippets={
                "compare-lowercase-bearer": 'if config.authentication_type == "bearer":\n    pass\n',
                "compare-spec-cased-bearer": 'if config.authentication_type == "Bearer":\n    pass\n',
                "compare-hmac": 'if cfg.authentication_type == "HMAC-SHA256":\n    pass\n',
                "compare-literal-on-the-left": 'if "HMAC-SHA256" == cfg.authentication_type:\n    pass\n',
                "compare-bare-name": 'if authentication_type == "Bearer":\n    pass\n',
                "read-webhook-secret": "secret = config.webhook_secret\n",
                "getattr-webhook-secret": 'secret = getattr(config, "webhook_secret", None)\n',
                # Forwarding the dead column to the seam is a violation like any
                # other read. An exemption used to whitelist exactly this, which
                # made the guard silent about a credential nothing populates; the
                # snippet is here so deleting the exemption stays deliberate.
                "forward-webhook-secret-to-the-seam": (
                    "deliver_webhook(url, payload, credentials=config.webhook_secret)\n"
                ),
                # Same, written as a scheme comparison inline in a seam call --
                # the other hole the exemption opened.
                "compare-scheme-inline-in-a-seam-call": (
                    'deliver_webhook(url, payload, is_hmac=(config.authentication_type == "HMAC-SHA256"))\n'
                ),
            },
        )


class TestDetectorAllowsCleanSnippets:
    """(c) Negative meta-tests — persistence, logging, read-back and FORWARDING stay legal.

    ``forward-to-the-seam`` passes on its own merits, not because an exemption
    whitelists it: measured before the exemption was deleted, it stayed clean
    either way. Forwarding the LIVE columns is legal; forwarding the dead
    ``webhook_secret`` is not, and that pair is what the exemption blurred.
    """

    @pytest.mark.parametrize(
        ("label", "snippet"),
        [
            (
                "forward-to-the-seam",
                "deliver_webhook(url, payload, scheme=config.authentication_type, "
                "credentials=config.authentication_token)\n",
            ),
            (
                "keyword-write",
                "existing.authentication_type = registration.authentication_type\n",
            ),
            ("attribute-write", "existing.authentication_token = authentication_token\n"),
            ("secret-write", "existing.webhook_secret = webhook_secret\n"),
            ("own-attribute-read", "digest = hmac.new(self.webhook_secret.encode())\n"),
            ("log-the-scheme", 'logger.info("scheme=%s", config.authentication_type)\n'),
            ("compare-against-a-variable", "if config.authentication_type == expected_scheme:\n    pass\n"),
            ("compare-against-non-scheme-literal", 'if config.authentication_type == "none":\n    pass\n'),
            ("unrelated-attribute", "url = config.url\n"),
        ],
    )
    def test_allows_clean_snippet(self, label: str, snippet: str) -> None:
        tree = ast.parse(snippet, filename=f"<known-good:{label}>")
        assert not find_inline_webhook_auth_violations(tree), f"detector false-flagged clean snippet: {label}"
