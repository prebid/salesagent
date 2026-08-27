"""Guard: every counterparty-URL egress entry point applies the seam's destination policy.

A "counterparty URL" is one this process did not choose — a buyer-supplied agent
URL, an operator-configured agent or webhook URL, a URL read back out of a DB row.
Dialling one without ``check_url_ssrf`` / ``WebhookURLValidator`` is SSRF.

WHY THIS GUARD IS A REGISTRY AND NOT A TREE SCAN
------------------------------------------------
The obvious guard — "no raw ``requests.``/``httpx.`` client outside an allowlist" —
was evaluated against the tree and rejected. It matches 46 call sites across 14
modules, and 40+ of them are legitimate: the vendor adapters (kevel, triton,
xandr) build every URL as an f-string over an operator-configured ``base_url``,
which is not a counterparty URL and is owed no gate. That guard would ship a
46-row allowlist, ratchet nothing, and need a new entry for every adapter method.

A sharper AST heuristic was also measured — "flag a call whose URL argument is a
bare Name/Attribute rather than an f-string, in a module that never calls the
seam". On the current tree it yields 3 true positives (all already tracked in
salesagent-og9k.13) against 3 false positives (``xandr`` assigns its f-string to
a local first; ``webhook_sender_factory`` is gated by its callers;
``gam_reporting_service`` carries its own ALLOWED_DOMAINS check), and it MISSES
``tenants.py`` — which gates on write at one line and dials unvalidated at
another, so a module-level "does it call the seam" test reads as clean. 50%
precision plus a false negative on a real defect is not a guard, it is a nag.

So this guard states the invariant it can actually decide: the KNOWN counterparty
egress entry points each call the seam. Its blind spot is a genuinely NEW entry
point, and the ratchet below is what covers that — a new module that opens a raw
HTTP client fails the count and forces the author to classify it here.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import called_function_names, format_failure, parse_module, repo_root

#: Callables that dial a counterparty-supplied URL, and the seam call each must make.
#: Adding a row is how a new egress path gets classified; removing one needs the
#: path to be gone, not merely refactored.
GATED_ENTRY_POINTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "src/core/creative_agent_registry.py",
        "_fetch_formats_raw_mcp",
        ("check_url_ssrf",),
    ),
    (
        "src/core/property_list_resolver.py",
        "_validate_agent_url",
        ("check_url_ssrf",),
    ),
    (
        "src/core/webhook_delivery.py",
        "deliver_webhook_with_retry",
        # The SHARED send-time entry point, not validate_webhook_url — that was the
        # registration-time gate this path used to reach for (salesagent-og9k.8).
        ("reject_unsafe_outbound_webhook_url",),
    ),
)

#: Modules in src/ that construct a raw HTTP client. A RATCHET, not an allowlist:
#: the number may shrink freely; growing it means a new egress path exists and
#: must be classified in GATED_ENTRY_POINTS above (or justified here as a
#: fixed/vendor endpoint that is owed no gate).
MAX_RAW_HTTP_MODULES = 14

_RAW_HTTP_MARKERS = ("requests.get", "requests.post", "requests.put", "requests.patch", "requests.delete")


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    return None


@pytest.mark.arch_guard
@pytest.mark.parametrize(("path", "func_name", "required"), GATED_ENTRY_POINTS)
def test_counterparty_egress_entry_point_calls_the_seam(path: str, func_name: str, required: tuple[str, ...]) -> None:
    tree = parse_module(repo_root() / path)
    assert tree is not None, f"Could not parse {path}"
    func = _find_function(tree, func_name)
    assert func is not None, (
        f"{path}::{func_name} no longer exists. If the egress path was removed, drop its row from "
        "GATED_ENTRY_POINTS; if it was renamed, update the row — do not delete the coverage."
    )
    called = called_function_names(func)
    missing = [name for name in required if name not in called]
    assert not missing, format_failure(
        summary=f"{path}::{func_name} dials a counterparty URL without the seam's destination policy",
        violations=[f"{path}::{func_name}: missing call to {', '.join(missing)}"],
        fix_hint=(
            "Call check_url_ssrf(url) (or WebhookURLValidator) on the URL that is actually DIALLED, before "
            "the HTTP client is constructed. Gating a pre-rewrite or pre-suffixed URL means the URL judged "
            "is not the URL dialled."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_raw_http_module_count_does_not_grow() -> None:
    """Ratchet: a NEW module opening raw HTTP must be classified, not merged silently.

    This is the registry's blind-spot cover. It deliberately counts MODULES, not
    call sites — adding a method to an existing vendor adapter is routine and
    should not fail; standing up a new outbound path is the event worth a review.
    """
    repo = repo_root()
    offenders = sorted(
        str(path.relative_to(repo))
        for path in (repo / "src").rglob("*.py")
        if any(marker in path.read_text(encoding="utf-8") for marker in _RAW_HTTP_MARKERS)
        or "httpx.AsyncClient(" in path.read_text(encoding="utf-8")
        or "httpx.Client(" in path.read_text(encoding="utf-8")
    )
    assert len(offenders) <= MAX_RAW_HTTP_MODULES, format_failure(
        summary=(
            f"{len(offenders)} src/ modules open a raw HTTP client, ratchet is {MAX_RAW_HTTP_MODULES}. "
            "A new outbound path exists."
        ),
        violations=offenders,
        fix_hint=(
            "If the new path dials a counterparty-supplied URL, gate it with the seam and add it to "
            "GATED_ENTRY_POINTS. If it dials a fixed or operator-configured vendor endpoint, say so in a "
            "comment and raise MAX_RAW_HTTP_MODULES in the same commit — deliberately, with the reason."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_detector_catches_an_ungated_entry_point() -> None:
    """Negative control: the detector must fail a function that skips the seam.

    Without this, a bug in ``_called_names`` (e.g. missing the ``ast.Attribute``
    arm) would make every row above pass vacuously.
    """
    ungated = ast.parse(
        "async def _fetch(agent):\n"
        "    async with httpx.AsyncClient() as http:\n"
        "        return await http.post(agent.agent_url)\n"
    )
    func = _find_function(ungated, "_fetch")
    assert func is not None
    assert "check_url_ssrf" not in called_function_names(func)

    gated = ast.parse(
        "async def _fetch(agent):\n"
        "    ok, err = check_url_ssrf(agent.agent_url)\n"
        "    if not ok:\n"
        "        raise AdCPAdapterError('nope')\n"
        "    async with httpx.AsyncClient() as http:\n"
        "        return await http.post(agent.agent_url)\n"
    )
    func = _find_function(gated, "_fetch")
    assert func is not None
    assert "check_url_ssrf" in called_function_names(func)
