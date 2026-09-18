"""Guard: every counterparty-URL egress entry point applies the seam's destination policy.

A "counterparty URL" is one this process did not choose — a buyer-supplied agent
URL, an operator-configured agent or webhook URL, a URL read back out of a DB row.
Dialling one without the egress seam's destination policy is SSRF.

RETARGETED (GH #1802). The required seam call used to be ``check_url_ssrf`` /
``reject_unsafe_outbound_webhook_url`` — local gates, each judging the URL just
before a raw client was constructed. #1802 deleted all of them and put the
judgement INSIDE the dial: ``outbound_http.send``/``asend`` and
``webhook_egress.deliver_webhook`` open with ``EgressPolicy.resolve_for_dial``, which
re-resolves DNS and pins the connection to the address it validated. So the required
call per row is now the seam entry point itself, and the property is stronger than it
was: the old gates left a TOCTOU window between their resolution and the client's, and
a caller could call the gate and then dial something else. Routing through the seam
makes "the URL judged" and "the URL dialled" the same act.

WHY THIS GUARD IS A REGISTRY AND NOT A TREE SCAN
------------------------------------------------
The obvious guard — "no raw ``requests.``/``httpx.`` client outside an allowlist" —
is not this module's to write, and writing it here would be a second copy of a gate
that already exists and is strictly stronger. ``ruff-egress.toml`` (run by
``make quality-ci`` as ``ruff check --config ruff-egress.toml --no-respect-gitignore
src/ scripts/``) bans the ``httpx`` / ``requests`` / ``urllib.request`` / ``http.client``
imports outright across the shipped tree, with file-granular exemptions that only ever
shrink and that ``tests/unit/test_ruff_egress_bans.py`` proves non-vacuous and CLOSED.
That ban is spelling-blind — it fires on ``from httpx import AsyncClient`` and on an
aliased import, both of which a text scan for ``httpx.Client(`` misses — and a
``# noqa`` does not silence it.

What the import ban CANNOT state is the positive property: that a named function which
dials a counterparty URL actually reaches the seam. A module can import nothing banned
and still hand a buyer-supplied URL to a helper that never validates it. That is the
invariant this registry decides, per entry point: the KNOWN counterparty egress entry
points each call the seam. Its blind spot is a genuinely NEW entry point, and the
ratchet below is the residual cover for the narrow case the import ban lets through —
a new module that constructs a client inside one of the seam's own exempted files.
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
        ("asend",),
    ),
    (
        # Was ``_validate_agent_url``: a separate pre-check that resolved the URL and then
        # handed it to a client that resolved it again. #1802 removed the function along
        # with that window — the dial itself is now the gate, one frame up in the caller.
        "src/core/property_list_resolver.py",
        "resolve_property_list",
        ("asend",),
    ),
    (
        "src/core/webhook_delivery.py",
        "deliver_webhook_with_retry",
        # The SHARED send-time entry point. ``deliver_webhook`` reaches
        # ``outbound_http.send`` -> ``EgressPolicy.resolve_for_dial``.
        ("deliver_webhook",),
    ),
)

#: Modules in src/ that construct a raw HTTP client. A RATCHET, not an allowlist:
#: the number may shrink freely; growing it means a new egress path exists and
#: must be classified in GATED_ENTRY_POINTS above (or justified here as a
#: fixed/vendor endpoint that is owed no gate).
#:
#: RATCHETED 14 -> 1 when this guard met #1721's tree. On the branch it was written
#: for, 14 modules (the kevel/triton/xandr vendor adapters and friends) each opened
#: their own client; #1721 finished the centralisation those adapters were waiting on,
#: and the only module left that constructs a client is the seam itself. A ceiling of
#: 14 against a true count of 1 is not a ratchet, it is thirteen free slots.
MAX_RAW_HTTP_MODULES = 1

_RAW_HTTP_MARKERS = (
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.patch",
    "requests.delete",
    # Substring match, so the seam's own privately-bound ``_httpx.AsyncClient(``
    # is counted too — binding the import privately hides it from a re-export,
    # not from this scan.
    "httpx.AsyncClient(",
    "httpx.Client(",
)


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
            "Dial through the egress seam (asend / send / deliver_webhook), which applies "
            "EgressPolicy.resolve_for_dial to the URL it is about to open, at the moment it opens it. "
            "Gating a pre-rewrite or pre-suffixed URL means the URL judged is not the URL dialled."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_raw_http_module_count_does_not_grow() -> None:
    """Ratchet: a NEW module opening raw HTTP must be classified, not merged silently.

    This is the registry's blind-spot cover, and the residual behind
    ``ruff-egress.toml``: the import ban is the primary gate, so the only way to
    reach this assertion is to construct a client inside a file that ban already
    exempts. It deliberately counts MODULES, not call sites — adding a method to
    an existing sanctioned dialer is routine; standing up a new outbound module
    is the event worth a review.
    """
    repo = repo_root()
    offenders = sorted(
        str(path.relative_to(repo))
        for path in (repo / "src").rglob("*.py")
        if any(marker in path.read_text(encoding="utf-8") for marker in _RAW_HTTP_MARKERS)
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
            "comment and raise MAX_RAW_HTTP_MODULES in the same commit — deliberately, with the reason. "
            "ruff-egress.toml has to have exempted the file first, so this is a two-gate change."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_detector_catches_an_ungated_entry_point() -> None:
    """Negative control: the detector must fail a function that skips the seam.

    Without this, a bug in ``called_function_names`` (e.g. a missing
    ``ast.Attribute`` arm) would make every row above pass vacuously. The probe
    names ``asend`` — the same seam entry point the live rows require — so the
    control and the rows exercise one vocabulary. The pre-#1802 spelling
    ``check_url_ssrf`` this control used to probe is a deleted symbol, which
    would have kept it green against a name no row can ever ask for.
    """
    ungated = ast.parse(
        "async def _fetch(agent):\n"
        "    async with httpx.AsyncClient() as http:\n"
        "        return await http.post(agent.agent_url)\n"
    )
    func = _find_function(ungated, "_fetch")
    assert func is not None
    assert "asend" not in called_function_names(func)

    gated = ast.parse(
        "async def _fetch(agent):\n"
        "    return await asend(agent.agent_url, json={}, provenance=CounterpartyUrl(field='agent_url'))\n"
    )
    func = _find_function(gated, "_fetch")
    assert func is not None
    assert "asend" in called_function_names(func)
