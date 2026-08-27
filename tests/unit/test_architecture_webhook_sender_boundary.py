"""Guard: every outbound AdCP webhook SENDER goes through the one signing boundary.

#1291 C1 (``salesagent-z6nr.18``), design step 6, last bullet — and the reason it
is written this way is a measured hole, not a style preference:

    A guard counting ``sign_webhook`` call sites passes with five senders and two
    routed. That hole is exactly what let ``order_approval_service`` exist as a
    fourth, unsigned AdCP webhook sender for as long as it did (salesagent-98t2) —
    nobody was counting SENDERS.

So this guard enumerates senders. It AST-discovers every outbound HTTP POST in
``src/`` whose destination is DYNAMIC (a name or attribute, not a literal or
f-string — i.e. a URL somebody else supplied rather than a fixed API path), and
requires every one of them to be classified in one of the three tables below. A
new POST to a caller-supplied URL anywhere in ``src/`` therefore fails this guard
until someone states which kind it is.

Of the three kinds, only :data:`ADCP_WEBHOOK_SENDERS` is subject to the boundary
rule: those destinations come from a buyer's own registration (a
``PushNotificationConfig`` row, an account notification subscriber, or the mock
adapter's async-completion URL), so what they carry is an AdCP webhook and the
receiver's registration selects how it is authenticated. When C1 routes a sender
through ``adcp.webhooks.WebhookSender``, its hand-rolled POST DISAPPEARS — the
sender stops being discovered here at all. That is the pass condition, and it is
not fakeable by adding a call somewhere in the module: the raw POST has to go.

``ALLOWED_UNROUTED`` therefore holds only the senders that are still UNSIGNED, each of
which must carry a ``FIXME(#1291)`` at its source location so the debt is visible
where the code is, not only in this table. Per the repository's allowlist rule,
it can only shrink.

A sender that cannot use the DELIVERY act moves its POST into the boundary module rather
than earning an exemption where it stands. The proof-of-control challenge (#1291 C2) is
the case: ``send_raw`` injects ``idempotency_key`` into the body and the challenge schema
forbids extra properties, and its fire-time SSRF check is destination policy this repo
deliberately still owns (GH #1802 for the seam, GH #1890 for the defect). Its send
therefore lives in
:data:`BOUNDARY_MODULE`, which the scan skips — because that module's POST is the
destination every routed sender is routed TO — and which
:meth:`TestOutboundWebhookSenderBoundary.test_the_boundary_signs_what_it_posts` holds to
signing everything it posts. Exemption plus positive check, not exemption alone.

Known blind spots, measured by mutation rather than assumed (#1291 sweep):
a destination written as an INTERPOLATED f-string (``client.post(f"{config.url}",
...)``) is skipped as a "fixed API path", and a POST issued as
``client.send(client.build_request("POST", config.url, ...))`` is not a ``.post``
call at all. Both slip this detector. Closing the f-string case means classifying
the 15 adapter call sites that legitimately build a path under their own base URL,
which is a deliberate decision rather than a typo fix — so it is tracked, not done
here. The bare-name and ``requests.request`` shapes ARE caught; see
:class:`TestSenderDetector`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

#: The GitHub issue every allowlisted sender must reference at its source location.
#: A beads id would not resolve for an outside contributor, so it is never used here.
FIXME_MARKER = "FIXME(#1291)"

#: Destinations that are a THIRD-PARTY API this agent calls, not a webhook it emits.
#: Nothing about AdCP webhook signing applies to them.
AD_SERVER_AND_API_CALLS: frozenset[tuple[str, str]] = frozenset(
    {
        ("src/adapters/broadstreet/client.py", "url"),
        ("src/adapters/xandr.py", "auth_url"),
        ("src/adapters/xandr.py", "url"),
        ("src/core/creative_agent_registry.py", "mcp_url"),
    }
)

#: Destinations that ARE outbound webhooks but whose receiver is not an AdCP buyer.
#: Slack is the only one; it must never be sent an RFC 9421 signature, so routing it
#: through the AdCP boundary would be a defect rather than a fix.
NON_ADCP_RECEIVERS: frozenset[tuple[str, str]] = frozenset(
    {
        # The shared gated sender for stored non-AdCP URLs (salesagent-og9k.13).
        # base_workflow and tenants below now DELEGATE their POST to it, so their
        # own rows describe the destination they pass in rather than a POST they
        # still hold. It is classified here rather than as an AdCP sender by
        # construction: an AdCP webhook must be authenticated by the mode the
        # buyer's registration selects, which a generic JSON POST cannot do — so
        # anything buyer-registered must go to the signing boundary, never here.
        ("src/core/webhook_validator.py", "url"),
        ("src/adapters/base_workflow.py", "slack_webhook_url"),
        ("src/admin/blueprints/tenants.py", "tenant.slack_webhook_url"),
        ("src/core/webhook_delivery.py", "delivery.webhook_url"),
    }
)

#: The AdCP webhook senders. Every one of these POSTs to a URL a buyer registered,
#: so every one of them must be authenticated by the mode that buyer's registration
#: selects — which is what the single boundary decides.
ADCP_WEBHOOK_SENDERS: frozenset[tuple[str, str]] = frozenset(
    {
        ("src/services/protocol_webhook_service.py", "url"),
        ("src/services/webhook_delivery_service.py", "config.url"),
        ("src/services/order_approval_service.py", "webhook_url"),
        ("src/adapters/mock_ad_server.py", "self.async_webhook_url"),
    }
)

#: AdCP senders that are still UNSIGNED, each with the ticket that will sign them. Only
#: shrinks, and every entry must carry the FIXME at its source.
ALLOWED_UNROUTED: frozenset[tuple[str, str]] = frozenset(
    {
        # salesagent-hop4 — the mock adapter's task_completed notification. A
        # registration-derived destination with no signature.
        ("src/adapters/mock_ad_server.py", "self.async_webhook_url"),
    }
)

#: The boundary module itself. Its POST is the one every routed sender is routed TO, so
#: discovering it as an unrouted sender would be the detector reporting the destination as
#: the problem.
#:
#: Skipped the same way the scan already skips ``/tests/``, and it is the step most likely
#: to be missed when a sender's POST MOVES here: #1291 C2 relocated the proof-of-control
#: challenge's send into this module, and ``_dynamic_post_sites`` — which scans all of
#: ``src/`` and classifies by ``(path, destination)`` — re-discovers it as
#: ``("src/core/signing/webhook_sender_factory.py", "url")``. Without this exemption the
#: identical three-way trap reappears at the new address and someone rebuilds the same
#: workaround under a different name: the defect relocated, not fixed.
#:
#: The exemption is not a hole, because it is paired with
#: :meth:`TestOutboundWebhookSenderBoundary.test_the_boundary_signs_what_it_posts` — a
#: POSITIVE check that this module's POST takes its headers from a ``JwkSignerStrategy``.
#: Exempting without that check would let an UNSIGNED send hide here, which is exactly what
#: the guard exists to prevent.
BOUNDARY_MODULE = "src/core/signing/webhook_sender_factory.py"

#: The one call that turns a body into RFC 9421 headers. Checked on the SEAM rather than on
#: a signing function's name because this is what every auth strategy implements, so a POST
#: that stopped making it would be shipping unsigned bytes whatever else the module imports.
STRATEGY_SEAM = "build_auth_headers"

CLASSIFIED = AD_SERVER_AND_API_CALLS | NON_ADCP_RECEIVERS | ADCP_WEBHOOK_SENDERS


def _destination(call: ast.Call) -> ast.expr | None:
    """The URL argument of a ``.post()`` / ``.request()`` call, if it has one."""
    if call.args:
        # ``requests.request(method, url)`` puts the URL second; every other form
        # here puts it first.
        index = 1 if isinstance(call.func, ast.Attribute) and call.func.attr == "request" and len(call.args) > 1 else 0
        return call.args[index]
    for keyword in call.keywords:
        if keyword.arg == "url":
            return keyword.value
    return None


def _dynamic_post_sites(tree_root: Path = SRC, *, relative_to: Path = ROOT) -> list[tuple[str, str, int]]:
    """(relative path, destination expression, line) for every dynamic-URL POST in src/.

    A literal or f-string destination is a fixed endpoint this agent calls; only a
    bare name or attribute can be a URL someone else registered, which is what makes
    a call site a candidate webhook sender.

    ``tree_root`` is a parameter so the meta-tests below can run the detector over a
    synthetic tree: a guard whose detector is only ever pointed at the real ``src/``
    is asserted to be green, never asserted to be able to go red.
    """
    sites: list[tuple[str, str, int]] = []
    for path in sorted(tree_root.rglob("*.py")):
        rel = path.relative_to(relative_to).as_posix()
        if "/tests/" in f"/{rel}" or rel == BOUNDARY_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in ("post", "request"):
                continue
            destination = _destination(node)
            if destination is None or isinstance(destination, (ast.Constant, ast.JoinedStr)):
                continue
            sites.append((rel, ast.unparse(destination), node.lineno))
    return sites


def _enclosing_function_source(rel_path: str, lineno: int) -> str:
    """The source of the function containing *lineno*, for the FIXME check.

    Scoped to the function rather than the file so a ``FIXME(#1291)`` written about
    something else elsewhere in the module cannot silence this one.
    """
    path = ROOT / rel_path
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, rel_path)
    lines = text.splitlines()
    best: tuple[int, int] | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            if node.lineno <= lineno <= end and (best is None or node.lineno > best[0]):
                best = (node.lineno, end)
    if best is None:
        return text
    return "\n".join(lines[best[0] - 1 : best[1]])


@pytest.mark.arch_guard
class TestOutboundWebhookSenderBoundary:
    """Senders are enumerated, and the AdCP ones go through one boundary."""

    def test_every_dynamic_destination_post_is_classified(self):
        """A new POST to a caller-supplied URL must be classified before it ships.

        This is the half that makes the guard an ENUMERATION: a sixth outbound
        webhook sender is a build failure the moment it is written, whether or not
        anyone remembers this file exists.
        """
        unclassified = sorted({(rel, expr) for rel, expr, _ in _dynamic_post_sites()} - CLASSIFIED)

        assert not unclassified, (
            "Unclassified outbound POST to a caller-supplied URL:\n"
            + "\n".join(f"  {rel}  ->  {expr}" for rel, expr in unclassified)
            + "\n\nAdd it to exactly one table in this file: AD_SERVER_AND_API_CALLS (a third-party "
            "API this agent calls), NON_ADCP_RECEIVERS (an outbound webhook to a non-AdCP receiver, "
            "e.g. Slack), or ADCP_WEBHOOK_SENDERS (a webhook to a URL a buyer registered — which must "
            "then go through the single signing boundary)."
        )

    def test_every_adcp_webhook_sender_goes_through_the_boundary(self):
        """No AdCP sender hand-rolls its own POST, and the allowlist has no stale rows.

        Routing a sender through ``adcp.webhooks.WebhookSender`` removes its raw
        POST, so a routed sender is simply absent from the discovered set. Anything
        still present is still hand-rolled — and anything allowlisted but absent is
        an entry someone forgot to delete when they fixed it. Both directions are
        the same equality, so they are asserted by the shared helper rather than by
        two hand-rolled set diffs.
        """
        discovered = {(rel, expr) for rel, expr, _ in _dynamic_post_sites()}

        assert_violations_match_allowlist(
            discovered & ADCP_WEBHOOK_SENDERS,
            set(ALLOWED_UNROUTED),
            fix_hint=(
                "Each of these POSTs to a URL a buyer registered, so the buyer's registration must "
                "select the authentication mode (security.mdx @ v3.1.1 :1424). Build the sender with "
                "src.core.signing.webhook_sender_factory.build_webhook_sender and deliver through it; "
                "the hand-rolled POST goes away, and the entry leaves ALLOWED_UNROUTED with it. A "
                "sender that cannot use the delivery act for a structural reason moves its POST INTO "
                "the boundary module instead — it does not get an exemption where it stands."
            ),
        )

    def test_the_boundary_signs_what_it_posts(self):
        """Every POST in the boundary module takes its headers from a JWK signer.

        The paired half of :data:`BOUNDARY_MODULE`'s exemption from the scan. Exempting the
        boundary is necessary — its POST is the destination every routed sender is routed TO
        — but an exemption with nothing behind it is a place to hide an unsigned send, which
        is the precise failure this guard exists to prevent.

        So the check is positive and AST-based rather than a substring: every ``.post`` call
        in the module must sit in a function that also calls ``build_auth_headers``, the one
        seam that turns a body into RFC 9421 headers. A new POST added here without signing
        fails immediately, and it cannot be satisfied by the module merely importing
        something signing-shaped elsewhere.
        """
        tree = ast.parse((ROOT / BOUNDARY_MODULE).read_text(encoding="utf-8"), BOUNDARY_MODULE)
        unsigned: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            posts = [
                sub
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "post"
            ]
            if not posts:
                continue
            signs = any(
                isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == STRATEGY_SEAM
                for sub in ast.walk(node)
            )
            if not signs:
                unsigned.extend(f"{node.name}@{post.lineno}" for post in posts)

        assert not unsigned, (
            f"{BOUNDARY_MODULE} is exempt from the sender scan because it IS the boundary, but these "
            f"POSTs in it never call {STRATEGY_SEAM}(), so they go out unsigned: {unsigned}. The "
            "exemption is only sound while everything the boundary posts is signed by it."
        )

    def test_allowlisted_senders_carry_the_fixme_at_the_source(self):
        """An allowlisted sender says so where it lives, referencing the GH issue.

        A table entry alone is invisible to whoever next reads the sender. The
        marker is a GitHub issue rather than a beads id because beads ids do not
        resolve for outside contributors.
        """
        missing = [
            (rel, expr, lineno)
            for rel, expr, lineno in _dynamic_post_sites()
            if (rel, expr) in ALLOWED_UNROUTED and FIXME_MARKER not in _enclosing_function_source(rel, lineno)
        ]

        assert not missing, (
            f"Allowlisted webhook senders with no {FIXME_MARKER} comment in the sending function:\n"
            + "\n".join(f"  {rel}:{lineno}  ->  {expr}" for rel, expr, lineno in missing)
        )


#: A re-introduced sender: it POSTs to a destination somebody else supplied, so the
#: detector must surface it for classification.
_REINTRODUCED_SENDER = """
import httpx

def send(config, payload):
    with httpx.Client() as client:
        return client.post(config.url, json=payload)
"""

#: The same disease reached through ``requests.request``, whose URL is the SECOND
#: positional argument. An off-by-one in :func:`_destination` would read ``"POST"``
#: as the destination and classify it as a literal — i.e. silently pass.
_REINTRODUCED_SENDER_VIA_REQUEST = """
import requests

def send(config, payload):
    return requests.request("POST", config.url, json=payload)
"""

#: A sender routed through the boundary: serialization, signing and the POST all
#: happen inside the SDK sender, so there is no raw POST left to discover. This is
#: what a FIXED sender looks like, and the detector must stay silent about it.
_ROUTED_SENDER = """
from src.core.signing.webhook_sender_factory import deliver_adcp_webhook_sync

def send(config, payload, tenant_id, repo):
    return deliver_adcp_webhook_sync(
        url=config.url,
        payload=payload,
        idempotency_key="k",
        config=config,
        tenant_id=tenant_id,
        repo=repo,
    )
"""

#: A fixed endpoint this agent calls. Nothing about webhook signing applies, and a
#: detector that flagged it would make the guard cost more than it catches.
_FIXED_ENDPOINT_CALL = """
import requests

def send(payload):
    return requests.post("https://api.example.com/v1/things", json=payload)
"""


@pytest.mark.arch_guard
class TestSenderDetector:
    """Meta-tests: the detector can actually go red, and does not cry wolf.

    Without these, every assertion above is only ever evaluated against a tree that
    already satisfies it — which proves the tree is clean, not that the guard would
    notice if it stopped being.
    """

    @staticmethod
    def _probe(tmp_path: Path, source: str) -> list[tuple[str, str, int]]:
        probe = tmp_path / "src" / "probe.py"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text(source, encoding="utf-8")
        return _dynamic_post_sites(tmp_path / "src", relative_to=tmp_path)

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (_REINTRODUCED_SENDER, "config.url"),
            (_REINTRODUCED_SENDER_VIA_REQUEST, "config.url"),
        ],
    )
    def test_detector_finds_a_reintroduced_sender(self, tmp_path, source, expected):
        """POSITIVE: a hand-rolled POST to a caller-supplied URL is discovered."""
        assert [expr for _, expr, _ in self._probe(tmp_path, source)] == [expected]

    @pytest.mark.parametrize("source", [_ROUTED_SENDER, _FIXED_ENDPOINT_CALL])
    def test_detector_ignores_routed_and_fixed_destinations(self, tmp_path, source):
        """NEGATIVE: a routed sender and a fixed endpoint are not reported."""
        assert self._probe(tmp_path, source) == []
