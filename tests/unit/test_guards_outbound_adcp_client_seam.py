"""Guard: an outbound ADCPMultiAgentClient/ADCPClient is only ever built through one seam.

#1291 (C3: sign outbound AdCP requests to other agents). The disease this guard pins: an outbound AdCP
protocol call site (to another agent -- creative agent, signals agent, or any
future counterparty) constructs ``ADCPMultiAgentClient``/``ADCPClient``
directly instead of going through
``src.core.helpers.adapter_helpers.build_adcp_multi_agent_client``, the one
place that resolves a tenant's RFC 9421 request-signing key and gates it on
origin publishability. A future call site that constructs either client type
directly bypasses that gate silently -- the disease-scan (codebase-scan atom
for this task) found exactly this happening at both
``CreativeAgentRegistry._build_adcp_client`` and
``SignalsAgentRegistry._build_adcp_client`` before the fix; this guard bans
the pattern from recurring anywhere else in ``src/``.

Allowlist is TWO files, both deliberate and load-bearing (shrink-only, per the
repository rule):

* ``src/core/helpers/adapter_helpers.py`` -- the ONE seam itself.
* ``src/core/signing/_mcp_client_signing_shim.py`` -- builds a throwaway,
  always-unsigned ``ADCPClient`` purely to pre-fetch a counterparty's
  ``get_adcp_capabilities`` (the SDK's own signing-exempt bootstrap carve-out)
  before a real, seam-built client makes its signed call. It is never used to
  make an actual outbound business request.

This is an AST Call-node guard (not regex), so per the sweep-verify atom's own
rule it needs positive + negative meta-tests only -- there is no "regex slip"
failure mode.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"

#: Shrink-only. Never add a new entry without also justifying it in this
#: module's docstring above.
ALLOWLIST: frozenset[str] = frozenset(
    {
        "core/helpers/adapter_helpers.py",
        "core/signing/_mcp_client_signing_shim.py",
    }
)

BANNED_CALLEES = frozenset({"ADCPMultiAgentClient", "ADCPClient"})


def _banned_local_names(tree: ast.Module) -> set[str]:
    """Local names (including aliases like ``_ADCPMultiAgentClient``) bound to a banned callee."""
    local_names = set(BANNED_CALLEES)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"adcp", "adcp.client"}:
            for alias in node.names:
                if alias.name in BANNED_CALLEES:
                    local_names.add(alias.asname or alias.name)
    return local_names


def _direct_construction_sites(root: Path) -> set[tuple[str, int]]:
    """(relative path, line) for every direct construction of a banned callee (aliases resolved)."""
    found: set[tuple[str, int]] = set()
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        local_names = _banned_local_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", None)
            if name in local_names:
                found.add((rel, node.lineno))
    return found


def _violations(root: Path) -> set[tuple[str, int]]:
    return {(rel, line) for rel, line in _direct_construction_sites(root) if rel not in ALLOWLIST}


def test_outbound_adcp_client_only_constructed_through_the_seam():
    """No file outside the allowlist directly constructs ADCPMultiAgentClient/ADCPClient."""
    violations = _violations(SRC_ROOT)
    assert not violations, (
        "Outbound ADCPMultiAgentClient/ADCPClient constructed outside "
        "build_adcp_multi_agent_client (src/core/helpers/adapter_helpers.py): "
        f"{sorted(violations)}. Route the call through "
        "build_adcp_multi_agent_client(agents, tenant_id=...) instead of "
        "constructing the client directly -- that seam is what resolves RFC 9421 "
        "request signing for the tenant (#1291 C3)."
    )


def test_allowlisted_files_are_the_only_direct_construction_sites():
    """Sanity check: every currently-allowlisted file actually has a construction site.

    Guards against a stale allowlist entry masking a site that moved or was deleted.
    """
    sites = _direct_construction_sites(SRC_ROOT)
    files_with_sites = {rel for rel, _line in sites}
    for allowlisted in ALLOWLIST:
        assert allowlisted in files_with_sites, (
            f"{allowlisted} is allowlisted but has no direct "
            "ADCPMultiAgentClient/ADCPClient construction -- shrink the allowlist."
        )


# ---------------------------------------------------------------------------
# Meta-tests: the detector can actually go red on a synthetic tree.
# ---------------------------------------------------------------------------


def test_detector_flags_a_new_direct_construction_site(tmp_path):
    """Positive: a new file constructing ADCPMultiAgentClient directly is caught."""
    bad_file = tmp_path / "some_new_registry.py"
    bad_file.write_text("from adcp import ADCPMultiAgentClient\n\nclient = ADCPMultiAgentClient(agents=[])\n")

    violations = _violations(tmp_path)

    assert ("some_new_registry.py", 3) in violations


def test_detector_ignores_unrelated_calls(tmp_path):
    """Negative: calls to unrelated names (including near-miss names) are not flagged."""
    clean_file = tmp_path / "some_new_registry.py"
    clean_file.write_text(
        "from src.core.helpers.adapter_helpers import build_adcp_multi_agent_client\n\n"
        "def f():\n"
        "    return build_adcp_multi_agent_client(agents=[], tenant_id=None)\n\n"
        "class ADCPMultiAgentClientFactory:\n"
        "    pass\n\n"
        "factory = ADCPMultiAgentClientFactory()\n"
    )

    violations = _violations(tmp_path)

    assert violations == set()
