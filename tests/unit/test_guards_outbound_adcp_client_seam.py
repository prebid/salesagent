"""Guard: an outbound agent dial never silently skips the RFC 9421 signing gate.

#1291 (C3: sign outbound AdCP requests to other agents). The disease this guard
pins has always been ONE thing: an outbound AdCP call site (to a creative
agent, a signals agent, or any future counterparty) reaches the wire WITHOUT
passing through the single place that decides whether this tenant signs. Only
the spelling of that bypass has changed.

RETARGETED by #1802, which moved the seam. This module used to ban direct
construction of ``ADCPMultiAgentClient``/``ADCPClient`` outside
the one (now deleted) multi-agent-client factory in ``adapter_helpers``,
because building the SDK client was how a dial acquired (or skipped) its
``SigningConfig``. That half is
now RETIRED HERE, deliberately and without loss:

* ``ruff-egress.toml``'s TID251 table bans ``adcp.ADCPClient`` /
  ``adcp.ADCPMultiAgentClient`` (and their ``adcp.client`` re-export paths)
  OUTRIGHT across ``src/`` and ``scripts/`` -- at IMPORT granularity, so the
  name cannot even be bound, let alone called, and one directory wider than
  this AST scan ever reached.
* That ban's exemption set is CLOSED and executable:
  ``tests/unit/test_ruff_egress_bans.py`` computes the violation set with
  ``--ignore-noqa`` and compares it against a recorded constant, so a new
  sanctioned construction site fails until it is recorded there.
* ``ruff-egress.toml`` says of its ``[lint.per-file-ignores]`` table:
  "EXEMPTIONS LIVE HERE, and only here." Keeping a second ALLOWLIST of
  sanctioned construction sites in this module would re-create precisely the
  divergent second record that table exists to abolish -- and it had already
  gone stale: ``adapter_helpers.py`` constructs no client at all anymore.

What ruff CANNOT see is the bypass that #1802 created in its place. Dialing an
agent is now ``call_mcp_tool``/``call_operator_mcp_tool``, whose ``sign``
parameter defaults to ``None``. Omitting one keyword argument is a valid
import, a valid call, and a silently unsigned dial -- a missing keyword is not
an import, so no banned-api table can reach it. So this guard now pins the two
halves of the seam that replaced the client builder:

1. **Every dial delegates the decision.** Every ``call_mcp_tool`` /
   ``call_operator_mcp_tool`` call in ``src/`` passes ``sign=``. Passing
   ``request_signer_for_tenant(tenant_id=...)`` that answers ``None`` is a
   DECIDED unsigned dial and is fine; omitting the argument is the undecided
   one, and is what this bans. ``DIAL_ALLOWLIST`` is empty and shrink-only --
   there is no sanctioned undecided dial.
2. **One posture gate builds the signer.** ``RequestSignerStrategy`` is
   constructed only in ``src/core/helpers/adapter_helpers.py``
   (``request_signer_for_tenant``). A second site could mint headers outside
   the guarded transport -- defeating the per-message, per-retry
   ``created``/``nonce`` the seam computes over the exact wire bytes -- or
   re-derive the posture and drift from the gate, which is the concrete bug
   #1291 C3 already hit once: the two registries held three independent copies
   of this conditional and had already disagreed on a broken KEK.

The behavior behind gate 2 -- WHICH tenants sign, and that a signing-capable
tenant whose material cannot be built raises instead of dialing unsigned -- is
graded on its own surface by
``tests/integration/test_outbound_signing_client_seam.py``. This module only
pins that the shape stays reachable from exactly one place.

These are AST Call-node scans (not regex), so per the sweep-verify rule they
need positive + negative meta-tests only -- there is no "regex slip" failure
mode.

The module keeps its filename: it is the same guard, on the same disease, at
the seam that replaced the one in the name.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"

#: Shrink-only. Never add a new entry without also justifying it in this
#: module's docstring above.
ALLOWLIST: frozenset[str] = frozenset({"core/helpers/adapter_helpers.py"})

#: Shrink-only, and EMPTY: there is no sanctioned agent dial that leaves the
#: signing decision undecided.
DIAL_ALLOWLIST: frozenset[str] = frozenset()

#: The outbound MCP dial and its operator-facing forwarder. Both take a
#: keyword-only ``sign``; both default it to ``None``.
DIAL_CALLEES = frozenset({"call_mcp_tool", "call_operator_mcp_tool"})
DIAL_MODULES = frozenset({"src.core.utils.mcp_client", "src.core.utils.operator_mcp"})

#: The RFC 9421 request-signing strategy. There is exactly ONE import path for
#: it, and no facade: ``src/core/signing/__init__.py`` re-exports nothing --
#: that emptiness is what keeps the signing layer's import graph acyclic -- so
#: ``from src.core.signing import RequestSignerStrategy`` raises ImportError
#: and is not a bypass this scan has to model. Every consumer spells the dotted
#: path. A bare (un-aliased) ``RequestSignerStrategy(...)`` is caught from any
#: module regardless, because ``_local_names`` seeds the callee name itself.
SIGNER_CALLEES = frozenset({"RequestSignerStrategy"})
SIGNER_MODULES = frozenset({"src.core.signing.request_signer"})


def _local_names(tree: ast.Module, *, modules: frozenset[str], callees: frozenset[str]) -> set[str]:
    """Local names (including aliases like ``_RequestSignerStrategy``) bound to a watched callee."""
    local_names = set(callees)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in modules:
            for alias in node.names:
                if alias.name in callees:
                    local_names.add(alias.asname or alias.name)
    return local_names


def _call_sites(root: Path, *, modules: frozenset[str], callees: frozenset[str]) -> Iterator[tuple[str, ast.Call]]:
    """Yield ``(relative path, Call node)`` for every call of a watched callee, aliases resolved.

    The ONE tree walk both scans below are phrased in terms of: they differ only
    in which callees they watch and in what they then ask of the call node.
    """
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        names = _local_names(tree, modules=modules, callees=callees)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", None)
            if name in names:
                yield rel, node


def _dial_sites(root: Path) -> set[tuple[str, int]]:
    """(relative path, line) for every outbound MCP dial."""
    return {(rel, node.lineno) for rel, node in _call_sites(root, modules=DIAL_MODULES, callees=DIAL_CALLEES)}


def _undecided_dials(root: Path) -> set[tuple[str, int]]:
    """(relative path, line) for every dial that does not pass ``sign=``."""
    return {
        (rel, node.lineno)
        for rel, node in _call_sites(root, modules=DIAL_MODULES, callees=DIAL_CALLEES)
        if rel not in DIAL_ALLOWLIST and not any(keyword.arg == "sign" for keyword in node.keywords)
    }


def _signer_construction_sites(root: Path) -> set[tuple[str, int]]:
    """(relative path, line) for every construction of a request-signing strategy."""
    return {(rel, node.lineno) for rel, node in _call_sites(root, modules=SIGNER_MODULES, callees=SIGNER_CALLEES)}


def _signer_violations(root: Path) -> set[tuple[str, int]]:
    return {(rel, line) for rel, line in _signer_construction_sites(root) if rel not in ALLOWLIST}


def test_every_outbound_agent_dial_delegates_the_signing_decision():
    """No dial in ``src/`` leaves ``sign`` to its ``None`` default."""
    undecided = _undecided_dials(SRC_ROOT)
    assert not undecided, (
        "Outbound MCP dial with no signing decision: "
        f"{sorted(undecided)}. ``call_mcp_tool``/``call_operator_mcp_tool`` default "
        "``sign=None``, so an omitted argument dials UNSIGNED and looks identical to a "
        "tenant that legitimately signs nothing. Pass "
        "sign=request_signer_for_tenant(tenant_id=...) -- that function is the one place "
        "allowed to answer None (#1291 C3, #1802)."
    )


def test_the_request_signer_is_built_only_in_the_posture_gate():
    """No file outside the allowlist constructs a RequestSignerStrategy."""
    violations = _signer_violations(SRC_ROOT)
    assert not violations, (
        "RequestSignerStrategy constructed outside request_signer_for_tenant "
        f"(src/core/helpers/adapter_helpers.py): {sorted(violations)}. Call "
        "request_signer_for_tenant(tenant_id=...) instead: a second builder either "
        "re-derives the posture and drifts from the gate, or mints headers outside the "
        "guarded transport, defeating the per-message, per-retry created/nonce the seam "
        "computes over the exact wire bytes (#1291 C3)."
    )


def test_allowlisted_files_are_the_only_signer_construction_sites():
    """Sanity check: every currently-allowlisted file actually has a construction site.

    Guards against a stale allowlist entry masking a site that moved or was deleted.
    """
    files_with_sites = {rel for rel, _line in _signer_construction_sites(SRC_ROOT)}
    for allowlisted in ALLOWLIST:
        assert allowlisted in files_with_sites, (
            f"{allowlisted} is allowlisted but constructs no RequestSignerStrategy -- shrink the allowlist."
        )


def test_the_dial_scan_still_sees_the_real_dial_sites():
    """Non-vacuity on the live tree: the dial scan finds the dials we know exist.

    ``test_every_outbound_agent_dial_delegates_the_signing_decision`` asserts an
    EMPTY set, so it passes just as loudly when the scan finds nothing at all --
    which is what a rename of ``call_operator_mcp_tool``, or a move of the
    registries out of ``src/``, would produce. Pinning the known dialers keeps
    that failure visible.
    """
    dialing_files = {rel for rel, _line in _dial_sites(SRC_ROOT)}
    for expected in (
        "core/creative_agent_registry.py",
        "core/signals_agent_registry.py",
        "core/utils/operator_mcp.py",
    ):
        assert expected in dialing_files, (
            f"{expected} makes no outbound MCP dial the scan can see -- either the dial "
            f"moved or the watched callee names are stale. Found: {sorted(dialing_files)}"
        )


# ---------------------------------------------------------------------------
# Meta-tests: the detectors can actually go red on a synthetic tree.
# ---------------------------------------------------------------------------


def test_detector_flags_a_dial_that_omits_the_signing_decision(tmp_path):
    """Positive: a new dial with no ``sign=`` is caught."""
    bad_file = tmp_path / "some_new_registry.py"
    bad_file.write_text(
        "from src.core.utils.operator_mcp import call_operator_mcp_tool\n\n"
        "async def f():\n"
        "    return await call_operator_mcp_tool(url, 'get_signals', {}, label='x')\n"
    )

    assert ("some_new_registry.py", 4) in _undecided_dials(tmp_path)


def test_detector_accepts_a_dial_that_delegates_the_decision(tmp_path):
    """Negative: a dial passing ``sign=`` -- even one that resolves to None -- is not flagged."""
    clean_file = tmp_path / "some_new_registry.py"
    clean_file.write_text(
        "from src.core.helpers.adapter_helpers import request_signer_for_tenant\n"
        "from src.core.utils.operator_mcp import call_operator_mcp_tool\n\n"
        "async def f():\n"
        "    return await call_operator_mcp_tool(\n"
        "        url, 'get_signals', {}, label='x',\n"
        "        sign=request_signer_for_tenant(tenant_id=None),\n"
        "    )\n"
    )

    assert _undecided_dials(tmp_path) == set()


def test_detector_flags_a_second_signer_builder(tmp_path):
    """Positive: a strategy built outside the posture gate is caught, alias and all."""
    bad_file = tmp_path / "some_new_registry.py"
    bad_file.write_text(
        "from src.core.signing.request_signer import RequestSignerStrategy as _Strategy\n\n"
        "signer = _Strategy(config).build_signed_headers\n"
    )

    assert ("some_new_registry.py", 3) in _signer_violations(tmp_path)


def test_detector_ignores_unrelated_calls(tmp_path):
    """Negative: near-miss names are not flagged by either scan."""
    clean_file = tmp_path / "some_new_registry.py"
    clean_file.write_text(
        "class RequestSignerStrategyFactory:\n"
        "    pass\n\n"
        "factory = RequestSignerStrategyFactory()\n\n"
        "def call_mcp_tool_name() -> str:\n"
        "    return 'list_creative_formats'\n\n"
        "name = call_mcp_tool_name()\n"
    )

    assert _signer_violations(tmp_path) == set()
    assert _undecided_dials(tmp_path) == set()
