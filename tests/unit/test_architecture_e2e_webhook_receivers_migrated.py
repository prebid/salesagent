"""Structural guard: e2e webhook suites capture through the TLS receiver, not a plaintext socket.

Sibling of ``test_architecture_e2e_webhook_capture_wiring.py``, which asks whether
the TLS-fronted capture ORIGIN is wired into the stack (certificate SAN, compose
alias, nginx SNI route, non-private subnet). That one passed while every delivery
suite still posted to a plaintext in-process receiver on a private compose
address — the origin existed and nothing used it. This guard asks the other half:
have the CALLERS moved onto it.

Why it matters beyond tidiness: ``tests/e2e/test_webhook_signature_e2e.py`` grades
RFC 9421 signing, and a signature covers ``@target-uri`` and ``@authority``. Graded
over ``http://<compose-name>:<port>`` it proves signing works against a destination
production's SSRF gate would refuse; only the ``https://webhooks.adcp-e2e.dev:8443``
origin exercises the shape a real receiver has.

Two rules, and the second is the one with teeth:

1. ``run_webhook_capture_server`` takes no IMPLICIT host. The original default,
   ``os.getenv("ADCP_WEBHOOK_HOST", "localhost")``, falls back to loopback with no
   error when the variable is unset — re-entering the very allowance the migration
   removes, silently. Making the parameter required turns that into a TypeError at
   the call site.
2. The plaintext receiver may only be pointed at a LOOPBACK callback host. A
   compose-network callback delivered over plaintext is exactly what the TLS origin
   replaces; a loopback one is a caller that reaches the live server over host
   loopback and cannot address a compose service at all. This started as a per-module
   allowlist and the migration proved that too weak — one module legitimately holds
   both a migrated in-network class and an in-process unit-style test, so only a
   per-call-site verdict can tell them apart.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import format_failure, iter_call_expressions, parse_module, repo_root

PLAINTEXT_RECEIVER = "run_webhook_capture_server"
RECEIVER_MODULE = "tests/e2e/_webhook_capture.py"

#: The only callback hosts the plaintext receiver may be pointed at.
#:
#: NOT a module allowlist, which is what this guard originally used and what the
#: migration proved too weak: ``test_a2a_webhook_payload_types`` legitimately holds
#: BOTH a migrated in-network class and an in-process unit-style test that must keep
#: a loopback callback, so a per-module verdict cannot separate them. The property
#: that actually distinguishes "genuine loopback caller" from "should have migrated"
#: is the callback HOST, and it is decidable at each call site.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})


def _plaintext_call_sites() -> list[tuple[str, int, str | None]]:
    """Every ``run_webhook_capture_server`` call as ``(file, line, host-literal-or-None)``."""
    repo = repo_root()
    sites: list[tuple[str, int, str | None]] = []
    for path in sorted((repo / "tests").rglob("*.py")):
        rel = str(path.relative_to(repo))
        if rel == RECEIVER_MODULE:
            continue
        tree = parse_module(path)
        if tree is None:
            continue
        for node in iter_call_expressions(tree, PLAINTEXT_RECEIVER):
            host: str | None = None
            for kw in node.keywords:
                if kw.arg == "host" and isinstance(kw.value, ast.Constant):
                    host = kw.value.value
            sites.append((rel, node.lineno, host))
    return sites


@pytest.mark.arch_guard
def test_plaintext_receiver_is_only_pointed_at_loopback() -> None:
    """A non-loopback callback on the plaintext receiver means the suite should have migrated.

    A compose-network callback delivered over plaintext is precisely what the TLS
    capture origin replaces; a loopback one is a caller that reaches the live server
    over host loopback and genuinely cannot address a compose service.
    """
    offenders = [
        f"{rel}:{line}: host={host!r}" for rel, line, host in _plaintext_call_sites() if host not in LOOPBACK_HOSTS
    ]
    assert not offenders, format_failure(
        summary="e2e suites still capture webhooks on a plaintext receiver instead of the TLS capture origin",
        violations=offenders,
        fix_hint=(
            "Capture through the compose webhook-capture service: register the delivery URL with "
            f"delivery_url(key) and read it back with the helpers in {RECEIVER_MODULE}. Rebuild any "
            "@target-uri as https://{host}{path} — the tls-proxy terminates TLS and forwards Host "
            "verbatim, so an http:// reconstruction fails as webhook_signature_invalid and reads like "
            "a crypto bug. Only a caller that reaches the live server over HOST loopback may keep the "
            f"plaintext receiver, and it must say so with an explicit host from {sorted(LOOPBACK_HOSTS)}."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_detector_reads_a_synthetic_non_loopback_call() -> None:
    """Negative control: a compose-name callback must be flagged, a loopback one must not."""
    tree = ast.parse(
        "run_webhook_capture_server(H, H.received, host='tests')\n"
        "run_webhook_capture_server(H, H.received, host='127.0.0.1')\n"
    )
    hosts = []
    for node in iter_call_expressions(tree, PLAINTEXT_RECEIVER):
        hosts.extend(kw.value.value for kw in node.keywords if kw.arg == "host")
    assert hosts == ["tests", "127.0.0.1"], hosts
    assert [h for h in hosts if h not in LOOPBACK_HOSTS] == ["tests"]


@pytest.mark.arch_guard
def test_plaintext_receiver_requires_an_explicit_callback_host() -> None:
    """No implicit ADCP_WEBHOOK_HOST fallback: the danger was the silence, not the helper.

    ``host=None`` defaulting to ``os.getenv("ADCP_WEBHOOK_HOST", "localhost")``
    means an unset variable silently re-enters the loopback allowance. A required
    parameter makes the same mistake a TypeError.
    """
    tree = parse_module(repo_root() / RECEIVER_MODULE)
    assert tree is not None, f"could not parse {RECEIVER_MODULE}"
    func = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == PLAINTEXT_RECEIVER
        ),
        None,
    )
    assert func is not None, f"{PLAINTEXT_RECEIVER} not found in {RECEIVER_MODULE}"

    args = func.args
    host_index = next((i for i, a in enumerate(args.args) if a.arg == "host"), None)
    assert host_index is not None, f"{PLAINTEXT_RECEIVER} no longer takes a 'host' argument"
    # Defaults align to the TAIL of args.args.
    first_defaulted = len(args.args) - len(args.defaults)
    assert host_index < first_defaulted, format_failure(
        summary=f"{PLAINTEXT_RECEIVER}'s 'host' is optional — an unset ADCP_WEBHOOK_HOST silently means localhost",
        violations=[f"{RECEIVER_MODULE}:{func.lineno}: host has a default"],
        fix_hint="Make 'host' a required parameter and delete the os.getenv fallback.",
        docs_link="docs/development/structural-guards.md",
    )

    # An AST check on the CALL, not a substring: the module's docstring names
    # ADCP_WEBHOOK_HOST to explain why the default was removed, and a guard that
    # forbade the words would forbid the explanation.
    env_reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "getenv"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "ADCP_WEBHOOK_HOST"
    ]
    assert not env_reads, format_failure(
        summary=f"{RECEIVER_MODULE} still reads ADCP_WEBHOOK_HOST at runtime",
        violations=[f"{RECEIVER_MODULE}:{n.lineno}: os.getenv('ADCP_WEBHOOK_HOST', ...) survives" for n in env_reads],
        fix_hint="The callback host comes from the caller now. Delete the env lookup.",
        docs_link="docs/development/structural-guards.md",
    )
