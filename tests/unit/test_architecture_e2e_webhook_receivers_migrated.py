"""Structural guard: e2e webhook suites capture through the TLS receiver, not an in-process socket.

Sibling of ``test_architecture_e2e_webhook_capture_wiring.py``, which asks whether
the TLS-fronted capture ORIGIN is wired into the stack (certificate SAN, compose
alias, nginx SNI route, non-private subnet). That one passed while every delivery
suite still posted to an in-process receiver on a private compose address — the
origin existed and nothing used it. This guard asks the other half: have the
CALLERS moved onto it.

Why it matters beyond tidiness: ``tests/e2e/test_webhook_signature_e2e.py`` grades
RFC 9421 signing, and a signature covers ``@target-uri`` and ``@authority``. Graded
over ``http://<compose-name>:<port>`` it proves signing works against a destination
production's SSRF gate would refuse; only the ``https://webhooks.adcp-e2e.dev:8443``
origin exercises the shape a real receiver has.

The obligation, in one line: **the in-process receiver is never pointed anywhere
but loopback.** Everything below is that sentence made decidable.

Two functions now share the name ``run_webhook_capture_server``
--------------------------------------------------------------

Upstream #1802 split the module this guard used to introspect:

* ``tests/e2e/_webhook_capture_loopback.py`` — the in-process receiver that
  actually BINDS a socket. Signature ``(handler_class, received, host=...)``.
  This is the thing the obligation is about, and the only reason it still
  exists is the hermetic (no-Docker-stack) caller whose sender runs on the host
  and cannot resolve a Docker-embedded-DNS name at all.
* ``tests/e2e/_webhook_capture.py`` — a ZERO-ARG handle that registers a capture
  key against the long-lived compose service behind the shared ``tls-proxy``. It
  starts no server and takes no host BY CONSTRUCTION. It is the migration
  TARGET, not an offender.

So this guard can no longer identify the receiver by function name. It resolves
every call site to its DEFINING MODULE (via the import that bound the name, or a
module-level ``def`` in the calling file) and grades only the loopback one. A
call it cannot attribute to either known module is itself a failure — an
unattributable call, or a THIRD in-process receiver, is exactly how the hatch
gets re-opened, and the shrink-only property depends on this guard seeing it.

What "no implicit host" means now (a retirement, and what replaced it)
---------------------------------------------------------------------

This guard used to require ``host`` to be a REQUIRED parameter. That rule is
retired. It was never the obligation — it was one mechanism for it. The hazard
it was written against, recorded in the original docstring, was the default
``os.getenv("ADCP_WEBHOOK_HOST", "localhost")``: a host read from the AMBIENT
ENVIRONMENT that silently fell back to loopback when unset, so the receiver's
destination was neither visible at the call site nor fixed by the code.

Post-split the default is the literal ``"127.0.0.1"``. That is strictly SAFER
than a required parameter against the stated obligation:

* a required parameter forces every caller to spell a host, and a caller can
  spell a wrong one — the safe case costs vigilance and the unsafe case is one
  typo away;
* a hard-coded loopback default cannot be forgotten, cannot be influenced by the
  environment, and makes the omitted case provably loopback.

The default is only safe while a caller cannot override it to something
non-loopback, so the replacement rule has two halves that must BOTH hold:

1. :func:`test_loopback_receiver_host_cannot_be_implicit` — ``host`` must have no
   default, or a default that is a LITERAL loopback string. A default that is any
   call or name (``os.getenv(...)``, a module constant) is an implicit host by
   definition and fails, which generalizes the original ADCP_WEBHOOK_HOST ban to
   every spelling of it. The specific ``ADCP_WEBHOOK_HOST`` read is still checked
   in the body, where a literal default cannot see it.
2. :func:`test_loopback_receiver_is_only_pointed_at_loopback` — every override at
   every call site must be a literal loopback host. Overrides are read
   POSITIONALLY as well as by keyword (``host`` is the third positional
   parameter, and its index is read off the real signature rather than hardcoded),
   and a ``**kwargs`` splat counts as unreadable, so the keyword spelling is not a
   way around the rule.

Rule 2 stayed a per-CALL-SITE verdict, not a per-module allowlist: the migration
proved a module allowlist too weak, because ``test_a2a_webhook_payload_types``
legitimately holds BOTH a migrated in-network class and a hermetic test that must
keep a loopback callback.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import format_failure, iter_call_expressions, parse_module, repo_root

RECEIVER_NAME = "run_webhook_capture_server"

#: The in-process receiver that binds a socket — the subject of the obligation.
LOOPBACK_MODULE = "tests/e2e/_webhook_capture_loopback.py"

#: The zero-arg compose-service handle that shares the name. Migration target.
COMPOSE_MODULE = "tests/e2e/_webhook_capture.py"

#: The only callback hosts the in-process receiver may be pointed at.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})

#: Call omits ``host`` entirely — the definition's default applies, and rule 1
#: is what proves that default is loopback.
_DEFAULTED = "<omitted>"

#: ``host`` supplied as a non-literal (variable, f-string, ``**kwargs`` splat).
#: Never acceptable: an unreadable destination is exactly the silence the
#: original ADCP_WEBHOOK_HOST rule existed to remove.
_UNREADABLE = "<not a literal>"


def _dotted(rel: str) -> str:
    """``tests/e2e/_webhook_capture.py`` -> ``tests.e2e._webhook_capture``."""
    return rel.removesuffix(".py").replace("/", ".")


LOOPBACK_DOTTED = _dotted(LOOPBACK_MODULE)
COMPOSE_DOTTED = _dotted(COMPOSE_MODULE)


def _dotted_expr(node: ast.expr) -> str | None:
    """Flatten a ``Name``/``Attribute`` chain to ``a.b.c``, or ``None`` if it isn't one."""
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    return ".".join(reversed(parts))


def _receiver_bindings(tree: ast.Module, rel: str) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve, for one file, what the name ``run_webhook_capture_server`` refers to.

    Returns ``(name_bindings, module_aliases)``:

    * ``name_bindings`` maps a local NAME (``from X import run_webhook_capture_server
      [as alias]``, or a module-level ``def`` in this very file) to the dotted module
      that defines it. The self-``def`` case is what keeps the defining modules from
      registering as unattributable calls without having to skip-list them.
    * ``module_aliases`` maps a local module alias (``import tests.e2e._webhook_capture
      [as m]``) to its dotted name, so ``m.run_webhook_capture_server(...)`` resolves too.
    """
    name_bindings: dict[str, str] = {}
    module_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == RECEIVER_NAME:
                    name_bindings[alias.asname or alias.name] = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module_aliases[alias.asname or alias.name] = alias.name
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == RECEIVER_NAME:
            name_bindings[node.name] = _dotted(rel)
    return name_bindings, module_aliases


def _host_parameter() -> tuple[int | None, ast.arg | None, ast.expr | None, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Read ``host`` off the real loopback signature: ``(positional index, arg, default, func)``.

    The positional index is derived, never hardcoded, so rule 2's positional
    override check tracks the signature if a parameter is inserted before ``host``.
    Handles keyword-only placement too (index ``None``, default from ``kw_defaults``).
    """
    tree = parse_module(repo_root() / LOOPBACK_MODULE)
    assert tree is not None, f"could not parse {LOOPBACK_MODULE}"
    func = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == RECEIVER_NAME
        ),
        None,
    )
    assert func is not None, (
        f"{RECEIVER_NAME} not found in {LOOPBACK_MODULE} — the in-process receiver moved again. "
        f"Repoint LOOPBACK_MODULE at its new home; do not delete this guard."
    )

    args = func.args
    positional = args.posonlyargs + args.args
    for i, a in enumerate(positional):
        if a.arg == "host":
            first_defaulted = len(positional) - len(args.defaults)
            default = args.defaults[i - first_defaulted] if i >= first_defaulted else None
            return i, a, default, func
    for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        if a.arg == "host":
            return None, a, d, func
    return None, None, None, func


def _loopback_call_sites() -> tuple[list[tuple[str, int, str]], list[tuple[str, int, str]]]:
    """Every call of the name, split into ``(loopback_sites, unattributed_sites)``.

    ``loopback_sites`` are ``(file, line, host)`` for calls that resolve to the
    in-process receiver, where ``host`` is a literal, ``_DEFAULTED`` or ``_UNREADABLE``.
    ``unattributed_sites`` are calls of this name that resolve to neither known
    capture module — a new in-process receiver, or an import shape this guard
    cannot read. Both are failures; neither may pass silently.
    """
    repo = repo_root()
    host_index, _, _, _ = _host_parameter()
    loopback: list[tuple[str, int, str]] = []
    unattributed: list[tuple[str, int, str]] = []

    for path in sorted((repo / "tests").rglob("*.py")):
        rel = str(path.relative_to(repo))
        tree = parse_module(path)
        if tree is None:
            continue
        name_bindings, module_aliases = _receiver_bindings(tree, rel)

        for node in iter_call_expressions(tree, RECEIVER_NAME):
            if isinstance(node.func, ast.Name):
                origin = name_bindings.get(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                receiver = _dotted_expr(node.func.value)
                origin = module_aliases.get(receiver, receiver) if receiver else None
            else:
                origin = None

            if origin == COMPOSE_DOTTED:
                # Zero-arg compose handle: starts no server, takes no host by
                # construction. This is where callers are supposed to end up.
                continue
            if origin != LOOPBACK_DOTTED:
                unattributed.append((rel, node.lineno, str(origin)))
                continue

            loopback.append((rel, node.lineno, _call_host(node, host_index)))

    return loopback, unattributed


def _call_host(node: ast.Call, host_index: int | None) -> str:
    """The host one call points the receiver at, by keyword OR position."""
    if any(kw.arg is None for kw in node.keywords):
        return _UNREADABLE  # **splat could smuggle a host
    for kw in node.keywords:
        if kw.arg == "host":
            return kw.value.value if isinstance(kw.value, ast.Constant) else _UNREADABLE
    if host_index is not None and len(node.args) > host_index:
        positional = node.args[host_index]
        return positional.value if isinstance(positional, ast.Constant) else _UNREADABLE
    return _DEFAULTED


@pytest.mark.arch_guard
def test_loopback_receiver_is_only_pointed_at_loopback() -> None:
    """A non-loopback callback on the in-process receiver means the suite should have migrated.

    A compose-network callback delivered from an in-process socket is precisely
    what the TLS capture origin replaces; a loopback one is a hermetic caller
    that genuinely cannot address a compose service. Omitting ``host`` is fine —
    the sibling rule proves the default is loopback.
    """
    sites, unattributed = _loopback_call_sites()
    offenders = [
        f"{rel}:{line}: host={host!r}" for rel, line, host in sites if host != _DEFAULTED and host not in LOOPBACK_HOSTS
    ]
    offenders += [
        f"{rel}:{line}: {RECEIVER_NAME} resolves to {origin!r}, neither {LOOPBACK_DOTTED} nor {COMPOSE_DOTTED}"
        for rel, line, origin in unattributed
    ]
    assert not offenders, format_failure(
        summary="e2e suites still capture webhooks on an in-process receiver instead of the TLS capture origin",
        violations=offenders,
        fix_hint=(
            "Capture through the compose webhook-capture service: take the zero-arg handle from "
            f"{COMPOSE_MODULE} (it registers a key and yields the TLS delivery URL). Rebuild any "
            "@target-uri as https://{host}{path} — the tls-proxy terminates TLS and forwards Host "
            "verbatim, so an http:// reconstruction fails as webhook_signature_invalid and reads like "
            "a crypto bug. Only a HERMETIC caller (no Docker stack, so no compose DNS to resolve) may "
            f"keep the in-process receiver from {LOOPBACK_MODULE}, and it must either omit 'host' or "
            f"pass a literal from {sorted(LOOPBACK_HOSTS)}. A call resolving to some third module means "
            "a new in-process receiver appeared — this guard is shrink-only; migrate instead."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_detector_separates_the_loopback_receiver_from_the_compose_handle() -> None:
    """Negative control: the shared name must be graded by DEFINING MODULE, not by name.

    Guards the exact staleness that made this file fail after #1802 — the compose
    handle's zero-arg calls being read as ``host=None`` offenders of a rule about
    a different function.
    """
    loopback_src = (
        f"from {LOOPBACK_DOTTED} import {RECEIVER_NAME}\n"
        f"{RECEIVER_NAME}(H, H.received, host='tests')\n"
        f"{RECEIVER_NAME}(H, H.received, host='127.0.0.1')\n"
        f"{RECEIVER_NAME}(H, H.received, 'tests')\n"
        f"{RECEIVER_NAME}(H, H.received)\n"
    )
    tree = ast.parse(loopback_src)
    name_bindings, _ = _receiver_bindings(tree, "tests/scratch_probe.py")
    assert name_bindings == {RECEIVER_NAME: LOOPBACK_DOTTED}, name_bindings

    host_index, _, _, _ = _host_parameter()
    hosts = [_call_host(n, host_index) for n in iter_call_expressions(tree, RECEIVER_NAME)]
    # Keyword non-loopback, keyword loopback, POSITIONAL non-loopback, omitted.
    assert hosts == ["tests", "127.0.0.1", "tests", _DEFAULTED], hosts
    assert [h for h in hosts if h != _DEFAULTED and h not in LOOPBACK_HOSTS] == ["tests", "tests"], hosts

    # The compose handle's zero-arg calls bind to the other module and are not graded.
    compose_tree = ast.parse(f"from {COMPOSE_DOTTED} import {RECEIVER_NAME}\n{RECEIVER_NAME}()\n")
    compose_bindings, _ = _receiver_bindings(compose_tree, "tests/scratch_probe.py")
    assert compose_bindings == {RECEIVER_NAME: COMPOSE_DOTTED}, compose_bindings


@pytest.mark.arch_guard
def test_loopback_receiver_host_cannot_be_implicit() -> None:
    """The receiver's destination is fixed by the code, never by the ambient environment.

    Retired: the old demand that ``host`` be REQUIRED. That was a mechanism, not
    the obligation — see the module docstring. What survives is the property that
    mechanism was protecting: an omitted ``host`` must resolve to a LITERAL
    loopback address, so it can be neither forgotten nor steered from outside.
    """
    _, host_arg, default, func = _host_parameter()
    assert host_arg is not None, format_failure(
        summary=f"{RECEIVER_NAME} no longer takes a 'host' argument",
        violations=[f"{LOOPBACK_MODULE}:{func.lineno}: 'host' parameter is gone"],
        fix_hint=(
            "The in-process receiver's bind/callback host must stay explicit in the signature. "
            "If the destination moved elsewhere, repoint this guard at it rather than dropping the rule."
        ),
        docs_link="docs/development/structural-guards.md",
    )

    if default is not None:
        bad_default = not (isinstance(default, ast.Constant) and default.value in LOOPBACK_HOSTS)
        shown = ast.unparse(default)
        assert not bad_default, format_failure(
            summary=(
                f"{RECEIVER_NAME}'s 'host' default is not a literal loopback address — "
                "an implicit host can silently point the in-process receiver off loopback"
            ),
            violations=[f"{LOOPBACK_MODULE}:{func.lineno}: host={shown}"],
            fix_hint=(
                f"Either drop the default (every caller then spells a host, graded per call site) or "
                f"make it a literal from {sorted(LOOPBACK_HOSTS)}. A call or a name as the default — "
                "os.getenv(...), a module constant — is the ADCP_WEBHOOK_HOST hazard in a new spelling."
            ),
            docs_link="docs/development/structural-guards.md",
        )

    # An AST check on the CALL, not a substring: the module's docstring may name
    # ADCP_WEBHOOK_HOST to explain why the default was removed, and a guard that
    # forbade the words would forbid the explanation. A literal default cannot
    # see an env read in the BODY, so this stays a separate check.
    tree = parse_module(repo_root() / LOOPBACK_MODULE)
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
        summary=f"{LOOPBACK_MODULE} still reads ADCP_WEBHOOK_HOST at runtime",
        violations=[f"{LOOPBACK_MODULE}:{n.lineno}: os.getenv('ADCP_WEBHOOK_HOST', ...) survives" for n in env_reads],
        fix_hint="The callback host comes from the caller or a literal loopback default now. Delete the env lookup.",
        docs_link="docs/development/structural-guards.md",
    )
