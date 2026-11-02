"""Guard: one name, one URL policy — no security-shaped name may bind to two answers.

``validate_webhook_url`` named TWO functions with opposite security properties:
``WebhookURLValidator.validate_webhook_url`` performs the SSRF check, while
``FormValidator.validate_webhook_url`` (src/core/validation.py) did not — it
delegated to ``FormValidator.validate_url``, a local scheme+netloc lookalike
unrelated to today's seam entry point of the same name, and then pattern-matched
Slack URLs. A reviewer reading a call site could not tell which one ran, and the
two disagree on exactly the inputs the gate exists to refuse.

This is a NON-BEHAVIORAL disease: measured, the non-gating binding had zero
callers anywhere in ``src/`` or ``tests/``, so no request could reach it and no
behavioral reproduction exists. The guard IS the reproduction — it failed before
the dead bindings were deleted and passes after.

Scope note — why "name collision" and not "dead code": a second, unreferenced
URL helper is untidy; a second *security-shaped* name that answers the opposite
question is a trap. The rule below is deliberately about names that decide URL
policy, not about dead code in general.

RETARGETED (GH #1802), for the same reason and in the same direction as
``test_architecture_counterparty_egress_gated``. When this guard was written the
gating answer was ``check_url_ssrf``/``check_url_syntax`` — hand-rolled address
policy living in ``src/core/security/url_validator.py``. #1802 deleted both and
moved the judgement into the egress seam: ``outbound_http.validate_url`` renders
the verdict for a URL that is *stored* now and dialled later, and
``EgressPolicy.check_registration`` renders it at ingest. Leaving the retired
names in :data:`SEAM_CALLS` would be worse than stale vocabulary — a reinstated
``validate_webhook_url`` that called a function which no longer exists would read
as gating and pass, while the one implementation a reviewer would actually write
today would read as non-gating and fail. So the retired names are demoted to the
negative control below, exactly as the counterparty guard demotes them.

Both policy names currently have ZERO definitions in ``src/``, which is the
quiescent state a ratchet is supposed to sit in, not a hole: the deletions this
guard was written to pin are still in place, and the test reddens the moment a
second binding comes back.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import (
    called_function_names,
    format_failure,
    parse_module,
    repo_root,
    src_python_files,
)

#: Names that answer "is this URL allowed?". Each may have exactly ONE definition
#: in src/, and that definition must consult the seam.
POLICY_NAMES: frozenset[str] = frozenset({"validate_webhook_url", "validate_agent_url"})

#: The seam calls that count as actually deciding URL policy. Matched by
#: ATTRIBUTE as well as bare name, so ``EgressPolicy.check_registration(...)``
#: counts. See the RETARGETED note above for why ``check_url_ssrf`` is absent.
SEAM_CALLS: frozenset[str] = frozenset(
    {"validate_url", "check_registration", "reserved_tld_for_host", "is_reserved_tld_host"}
)

#: Gate names #1802 deleted. Kept ONLY as a negative control: a body that calls
#: one of these is calling nothing, and must not read as gating.
RETIRED_GATES: frozenset[str] = frozenset({"check_url_ssrf", "check_url_syntax"})


def _policy_definitions() -> dict[str, list[str]]:
    """Map each policy name to the ``path:line`` of every definition in src/."""
    found: dict[str, list[str]] = {name: [] for name in POLICY_NAMES}
    repo = repo_root()
    for path in src_python_files(repo):
        tree = parse_module(path)
        if tree is None:
            continue
        rel = str(path.relative_to(repo))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in POLICY_NAMES:
                found[node.name].append(f"{rel}:{node.lineno}")
    return found


@pytest.mark.arch_guard
@pytest.mark.parametrize("name", sorted(POLICY_NAMES))
def test_url_policy_name_has_at_most_one_definition(name: str) -> None:
    definitions = _policy_definitions()[name]
    assert len(definitions) <= 1, format_failure(
        summary=f"'{name}' names {len(definitions)} different URL policies in src/",
        violations=definitions,
        fix_hint=(
            "A security-shaped name must bind to one answer. Either delete the binding that does not gate, "
            "or rename it to say what it actually checks (e.g. form-shape validation). A call site must not "
            "depend on which import happened to win."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
@pytest.mark.parametrize("name", sorted(POLICY_NAMES))
def test_surviving_url_policy_definition_consults_the_seam(name: str) -> None:
    """A single definition is not enough — the survivor must be the GATING one.

    Without this, deleting the gating binding and keeping the permissive one
    would satisfy the uniqueness test above while removing the check entirely.
    """
    definitions = _policy_definitions()[name]
    if not definitions:
        pytest.skip(f"'{name}' has no definition in src/ — nothing to grade")
    path_str, lineno = definitions[0].rsplit(":", 1)
    tree = parse_module(repo_root() / path_str)
    assert tree is not None
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name and node.lineno == int(lineno)
    )
    called = called_function_names(func)
    assert called & SEAM_CALLS, format_failure(
        summary=f"'{name}' ({definitions[0]}) decides URL policy without consulting the seam",
        violations=[f"{definitions[0]}: calls {sorted(called) or 'nothing'}"],
        fix_hint=(
            f"Call one of {sorted(SEAM_CALLS)} — 'validate_url' from "
            "src.core.security.outbound_http, 'check_registration' from "
            "src.core.security.egress.policy.EgressPolicy, the reserved-TLD matchers from "
            "src.core.security.url_validator."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_detector_distinguishes_gating_from_non_gating() -> None:
    """Negative control: a scheme+netloc lookalike must NOT read as gating.

    This is the exact body that made ``FormValidator.validate_webhook_url`` look
    like a security check while performing none.
    """
    non_gating = ast.parse(
        "def validate_webhook_url(url):\n"
        "    result = urlparse(url)\n"
        "    if not all([result.scheme, result.netloc]):\n"
        "        return 'Invalid URL format'\n"
        "    return None\n"
    )
    func = next(n for n in ast.walk(non_gating) if isinstance(n, ast.FunctionDef))
    assert not (called_function_names(func) & SEAM_CALLS), "urlparse+netloc must not count as consulting the seam"

    retired = ast.parse(
        "def validate_webhook_url(url):\n    return check_url_ssrf(url, require_https=_require_https())\n"
    )
    func = next(n for n in ast.walk(retired) if isinstance(n, ast.FunctionDef))
    called = called_function_names(func)
    assert called & RETIRED_GATES, "control must actually name a retired gate, or it proves nothing"
    assert not (called & SEAM_CALLS), "a call to the #1802-deleted check_url_ssrf must not count as gating"

    gating = ast.parse(
        "def validate_webhook_url(url):\n"
        "    validate_url(url, provenance=CounterpartyUrl(field=None))\n"
        "    return True\n"
    )
    func = next(n for n in ast.walk(gating) if isinstance(n, ast.FunctionDef))
    assert called_function_names(func) & SEAM_CALLS, "a validate_url call must count as consulting the seam"

    registration = ast.parse(
        "def validate_webhook_url(url):\n    return EgressPolicy.check_registration(url, allow_loopback=False)\n"
    )
    func = next(n for n in ast.walk(registration) if isinstance(n, ast.FunctionDef))
    assert called_function_names(func) & SEAM_CALLS, (
        "EgressPolicy.check_registration must count — the seam is reached as an attribute too"
    )
