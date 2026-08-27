"""Guard: the SSRF relaxation is a TEST argument and never reaches production.

``allow_private_destinations`` (and the SDK's underlying ``allow_private``) turns
off the pin that stops key discovery from being pointed at RFC1918, link-local
and cloud-metadata addresses. Tests need it — the local stack is
``http://localhost:<port>`` — and nothing in ``src/`` may pass it, because a
resolver that follows a counterparty-supplied URL into the private network is a
straight SSRF.

#1291 A3 (salesagent-z6nr.9), R-L: A3 has no production reader to gate, so the
acceptance is discharged mechanically here rather than with a runtime flag that
would itself be settable in production. When B1 (salesagent-z6nr.12) wires the
inbound verifier — where the resolver DOES run in production — this guard is what
makes an accidental production call site fail the build.

The keyword half above is one spelling of the pin being turned off. The ENV half
below is the other, and it lives here rather than in a module of its own because
it is the same invariant: this file's own docstring already names "a config
lookup, an env read or a parameter" as how the pin gets turned off without anyone
writing ``True``. salesagent-mp53.9 / OWNER DECISION 3 (2026-08-06) withdrew
``ADCP_OUTBOUND_ALLOW_PRIVATE`` for exactly that reason — the e2e stack reaches
its webhook receiver by moving onto a NON-PRIVATE per-stack subnet, so the gate
passes on its own terms and there is nothing left for a hatch to do. The NAME
stays reserved as the documented fallback should that route measurably fail, so
this branch converges with #1589 at merge rather than colliding; reserving a name
and shipping a reader for it are different things, and this is what keeps them
different.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import (
    format_failure,
    iter_call_expressions,
    repo_root,
    safe_parse,
    src_python_files,
)

# Both spellings: ours (``adcp.signing.async_resolve_agent``) and the SDK's own
# lower-level fetchers (``adcp.signing.jwks`` / ``brand_jwks`` / the IP-pinned
# transport builders).
FORBIDDEN_KEYWORDS = {"allow_private_destinations", "allow_private"}

_KNOWN_BAD_SNIPPET = "resolve_agent(url, allow_private_destinations=True)\n"

#: A value that is not the literal ``False`` is a relaxation whether or not it
#: reads ``True`` at the call site — a config lookup, an env read or a parameter
#: is exactly how the pin gets turned off in production without anyone writing
#: ``True``.
_NON_LITERAL_BAD_SNIPPET = "resolve_agent(url, allow_private=cfg.allow_private)\n"


def _relaxes(keyword: ast.keyword) -> bool:
    """Whether *keyword* turns the SSRF pin OFF, or merely restates it.

    Pinning the parameter to the literal ``False`` cannot relax anything, and some
    SDK constructors REQUIRE it (``adcp.webhooks.WebhookSender._from_strategy``
    takes it as a required keyword-only argument, so the outbound webhook boundary
    cannot omit it). Everything else — ``True``, a name, an attribute, a call — is
    a relaxation and stays a build failure, which is what the guard's own name
    says it grades.
    """
    value = keyword.value
    return not (isinstance(value, ast.Constant) and value.value is False)


def _find_private_destination_violations(repo) -> list[str]:
    violations: list[str] = []
    for path in src_python_files(repo):
        tree = safe_parse(path)
        if tree is None:
            continue
        for node in iter_call_expressions(tree):
            for keyword in node.keywords:
                if keyword.arg in FORBIDDEN_KEYWORDS and _relaxes(keyword):
                    location = path.relative_to(repo)
                    violations.append(f"{location}:{node.lineno}: passes {keyword.arg}=")
    return violations


@pytest.mark.arch_guard
def test_no_src_call_site_relaxes_private_destinations() -> None:
    violations = _find_private_destination_violations(repo_root())
    assert not violations, format_failure(
        summary="allow_private_destinations / allow_private must not be passed from src/",
        violations=violations,
        fix_hint=(
            "Key discovery in production must keep the SSRF pin. Relax it only inside a test, against a local stack."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
@pytest.mark.parametrize("snippet", [_KNOWN_BAD_SNIPPET, _NON_LITERAL_BAD_SNIPPET])
def test_private_destination_detector_catches_known_bad_snippet(tmp_path, snippet) -> None:
    bad_file = tmp_path / "src" / "probe.py"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text(snippet, encoding="utf-8")

    assert _find_private_destination_violations(tmp_path), (
        "Detector must flag a src/ call site passing allow_private_destinations"
    )


@pytest.mark.arch_guard
def test_detector_accepts_the_pin_restated_as_a_literal(tmp_path) -> None:
    """``allow_private_destinations=False`` restates the pin; it cannot relax it.

    Without this the guard would be unsatisfiable for any SDK constructor that
    takes the parameter as REQUIRED keyword-only, forcing src/ to route around the
    detector rather than keep the pin.
    """
    pinned = tmp_path / "src" / "pinned.py"
    pinned.parent.mkdir(parents=True)
    pinned.write_text("build(url, allow_private_destinations=False)\n", encoding="utf-8")

    assert not _find_private_destination_violations(tmp_path)


# ---------------------------------------------------------------------------
# The ENV half: no ``ADCP_OUTBOUND_ALLOW_PRIVATE`` reader in src/
# ---------------------------------------------------------------------------

#: Withdrawn by OWNER DECISION 3 (salesagent-mp53.9, 2026-08-06) BEFORE anything
#: was written to read it — so this guard is a pin, not a cleanup. It is checked
#: as a NAME anywhere under src/ rather than as an ``os.getenv`` call, because
#: the name reaching production config, a settings model, a docstring-documented
#: knob or a rendered template is the same regression: an operator-settable way
#: to turn the private-destination pin off. Tests, compose files and docs are
#: deliberately out of scope — the e2e stack may need to say the name out loud
#: the day the fallback is ever taken.
FORBIDDEN_ENV_HATCH = "ADCP_OUTBOUND_ALLOW_PRIVATE"


def _find_env_hatch_violations(repo) -> list[str]:
    """Return one ``<file>:<line>`` per mention of the withdrawn env hatch under src/.

    Walks the directory rather than the git index: an untracked file under
    ``src/`` that reads the hatch is the same production regression, and it is
    the shape a half-finished local change takes.
    """
    src_dir = repo / "src"
    violations: list[str] = []
    for path in sorted(src_dir.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if FORBIDDEN_ENV_HATCH not in text:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN_ENV_HATCH in line:
                violations.append(f"{path.relative_to(repo)}:{lineno}: names {FORBIDDEN_ENV_HATCH}")
    return violations


@pytest.mark.arch_guard
def test_no_src_file_introduces_the_outbound_private_env_hatch() -> None:
    """``src/`` never gains an operator-settable way to reach a private destination.

    The e2e webhook receiver is reached by putting the compose network on a
    NON-PRIVATE per-stack subnet, so ``check_url_ssrf`` accepts it on its own
    documented terms — address arithmetic, unchanged and unpatched. An env read
    would instead open 127.0.0.1, 169.254.169.254, host.docker.internal and all
    of RFC1918, in every deployment that sets it.
    """
    violations = _find_env_hatch_violations(repo_root())

    assert not violations, format_failure(
        summary=f"{FORBIDDEN_ENV_HATCH} must not appear anywhere under src/",
        violations=violations,
        fix_hint=(
            "The name is RESERVED as a documented fallback, to be used only if the non-private "
            "subnet route measurably fails. Reaching a test destination is the stack's job, not "
            "production's: move the destination, do not relax the gate."
        ),
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_env_hatch_detector_catches_a_synthetic_src_reader(tmp_path) -> None:
    """The detector reports a src/ file that reads the withdrawn hatch — it is not vacuous."""
    reader = tmp_path / "src" / "core" / "egress.py"
    reader.parent.mkdir(parents=True)
    reader.write_text(
        f'allow_private = os.getenv("{FORBIDDEN_ENV_HATCH}", "false") == "true"\n',
        encoding="utf-8",
    )

    assert _find_env_hatch_violations(tmp_path) == [f"src/core/egress.py:1: names {FORBIDDEN_ENV_HATCH}"]


@pytest.mark.arch_guard
def test_env_hatch_detector_ignores_the_name_outside_src(tmp_path) -> None:
    """Compose files and tests may name the reserved fallback; only ``src/`` may not read it."""
    compose = tmp_path / "docker-compose.e2e.yml"
    compose.write_text(f'      {FORBIDDEN_ENV_HATCH}: "true"\n', encoding="utf-8")

    assert _find_env_hatch_violations(tmp_path) == []
