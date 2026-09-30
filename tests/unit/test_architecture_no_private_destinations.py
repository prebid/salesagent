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
passes on its own terms and there is little left for a hatch to do.

RECONCILED AGAINST #1721. The withdrawal did not hold: upstream's in-runner
``local_http_origin`` fixtures bind LOOPBACK inside the test container, which is
private under any subnet, so ONE reader ships — and #1721's critical pattern #7
moved it out of a hand-rolled ``_env_flag`` in the seam and onto the typed
settings boundary as ``LimitSettings.adcp_outbound_allow_private``. What the
guard grades is therefore not the absence of the name but the absence of a
SECOND reader: exactly one place under ``src/`` may turn the pin off, its
default is OFF, and every other file naming the variable is a build failure.
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

    Pinning the parameter to the literal ``False`` cannot relax anything, and an
    SDK constructor may REQUIRE it as a keyword-only argument, which a caller
    that must keep the pin then cannot omit. Everything else — ``True``, a name,
    an attribute, a call — is a relaxation and stays a build failure, which is
    what the guard's own name says it grades.
    """
    value = keyword.value
    return not (isinstance(value, ast.Constant) and value.value is False)


#: The egress seam — the ONE owner of address policy after GH #1802 — plus the settings
#: boundary that hands it the operator's posture. These files plumb ``allow_private=`` and
#: name the env variable once; that is the policy deciding its own terms, not a caller
#: opting out of it. Everywhere else in ``src/`` the keyword and the env name remain a
#: build failure, which is the invariant this file has always been about: a
#: counterparty-supplied URL must never be followed into the private network by a call
#: site that decided to skip the gate.
#:
#: RECONCILED AT MERGE (GH #1802 x salesagent-mp53.9 / OWNER DECISION 3). This branch
#: withdrew the env hatch and reached its e2e receiver by moving the compose network onto
#: a NON-PRIVATE per-stack subnet instead; that route is live and measured working (the
#: stack now sits on 223.255.255.0/24, which EgressPolicy accepts on its own terms). The
#: hatch is still needed for a case the subnet cannot answer: upstream's in-runner
#: ``local_http_origin`` fixtures bind LOOPBACK inside the test container, which is
#: private under any subnet. So both survive, and what this guard now pins is the thing
#: that actually protects production — the posture is owned by the policy, and its
#: DEFAULT IS OFF (``test_the_env_hatch_defaults_to_off``). A deployment that does not
#: set the variable keeps the pin, which is what the old absolute ban was buying.
#:
#: ``src/core/config.py`` is the third entry because #1721 reads the environment ONCE into
#: typed settings (critical pattern #7): the reader this guard used to find in the seam is
#: now :class:`LimitSettings`'s ``adcp_outbound_allow_private`` field, and the settings
#: module is where the variable is legitimately named. Exempting it is what keeps the guard
#: from failing the build for the architecture it is meant to protect — the rule it still
#: enforces is that NO FOURTH file reads the hatch.
SEAM_OWNED = (
    "src/core/security/outbound_http.py",
    "src/core/security/egress/policy.py",
    "src/core/config.py",
)


def _is_seam_owned(relative: str) -> bool:
    return relative.replace("\\", "/") in SEAM_OWNED


def _find_private_destination_violations(repo) -> list[str]:
    violations: list[str] = []
    for path in src_python_files(repo):
        if _is_seam_owned(str(path.relative_to(repo))):
            continue
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
def test_the_env_hatch_defaults_to_off() -> None:
    """Production, which sets nothing, keeps the pin.

    This is what the absolute ban on the NAME was really buying, and it is now asserted
    on BEHAVIOUR rather than on a string's absence — so it survives the seam legitimately
    owning the posture, and it catches the failure the old rule could not: a hatch whose
    default flipped to on.

    Graded on #1721's reader, not the one this guard was written against. The seam's
    hand-rolled ``_env_flag`` is gone (critical pattern #7 — the environment is read once,
    into typed settings), so the posture is ``LimitSettings.adcp_outbound_allow_private``
    and the seam reaches it through :func:`_allow_private`. Both are asserted: the field's
    default, and that the dial actually reads THAT field, so repointing the seam at some
    other flag cannot leave this test passing.
    """
    import os
    from types import SimpleNamespace
    from unittest.mock import patch

    import pydantic

    from src.core.config import LimitSettings
    from src.core.security.outbound_http import _allow_private

    with patch.dict(os.environ, {}, clear=True):
        unset = LimitSettings()

    assert unset.adcp_outbound_allow_private is False, (
        f"{FORBIDDEN_ENV_HATCH} must default to OFF. A deployment that sets nothing has to keep "
        "the SSRF pin; a default-on hatch opens 127.0.0.1, 169.254.169.254 and all of RFC1918 "
        "in every deployment at once."
    )

    # The seam's own read, against the real settings object: this is what every dial calls,
    # and it is the half the field's default cannot prove on its own.
    with patch("src.core.config.get_settings", return_value=SimpleNamespace(limits=unset)):
        assert _allow_private() is False, (
            "the dial must take its posture from limits.adcp_outbound_allow_private — a seam "
            "reading some other flag would keep this file green while shipping an open pin."
        )

    # Asserted so the default-off check above cannot pass vacuously against a hatch that
    # ignores every value, and so the compose stack's spelling stays one the reader accepts.
    # The typed boundary takes pydantic's standard truthy set, WIDER than the ``== "true"``
    # the withdrawn ``_env_flag`` accepted; "1" is here to record that, not to invite it.
    for truthy in ("true", "TRUE", "True", "1"):
        with patch.dict(os.environ, {FORBIDDEN_ENV_HATCH: truthy}, clear=True):
            assert LimitSettings().adcp_outbound_allow_private is True, (
                f"the hatch must respond to {truthy!r} — otherwise the in-runner loopback fixtures "
                "it exists for are silently unreachable and the default-off assertion is vacuous."
            )

    # An empty value is NOT a relaxation (``env_ignore_empty``), and neither is any spelling
    # of off. An unset-looking variable must land on the pin, not off it.
    for falsy in ("", "false", "FALSE", "0", "no", "off"):
        with patch.dict(os.environ, {FORBIDDEN_ENV_HATCH: falsy}, clear=True):
            assert LimitSettings().adcp_outbound_allow_private is False, (
                f"{falsy!r} must NOT open the hatch — a near-miss spelling that silently relaxed the "
                "pin would be worse than one that silently kept it."
            )

    # The typed boundary's real answer to a near-miss: it refuses at startup rather than
    # silently picking a side. That is stronger than the string reader it replaced, which
    # quietly returned False for anything it did not recognise.
    with patch.dict(os.environ, {FORBIDDEN_ENV_HATCH: "maybe"}, clear=True):
        with pytest.raises(pydantic.ValidationError):
            LimitSettings()


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

#: Withdrawn by OWNER DECISION 3 (salesagent-mp53.9, 2026-08-06), then re-admitted
#: at exactly ONE site for the in-runner loopback fixtures the non-private subnet
#: cannot answer — see :data:`SEAM_OWNED`. Outside that site it is checked as a
#: NAME anywhere under src/ rather than as an ``os.getenv`` call, because the name
#: reaching a second settings model, a docstring-documented knob or a rendered
#: template is the same regression: a second operator-settable way to turn the
#: private-destination pin off, one of which nobody audits. Tests, compose files
#: and docs are deliberately out of scope — the e2e stack has to say the name out
#: loud to use the hatch at all.
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
        if _is_seam_owned(str(path.relative_to(repo))):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN_ENV_HATCH in line:
                violations.append(f"{path.relative_to(repo)}:{lineno}: names {FORBIDDEN_ENV_HATCH}")
    return violations


@pytest.mark.arch_guard
def test_no_src_file_introduces_the_outbound_private_env_hatch() -> None:
    """``src/`` never gains a SECOND operator-settable way to reach a private destination.

    The e2e webhook receiver is reached by putting the compose network on a
    NON-PRIVATE per-stack subnet, so ``check_url_ssrf`` accepts it on its own
    documented terms — address arithmetic, unchanged and unpatched. The one
    remaining env read exists for the loopback fixtures that route cannot answer,
    it lives at the settings boundary, and it defaults off
    (:func:`test_the_env_hatch_defaults_to_off`). A second read would open
    127.0.0.1, 169.254.169.254, host.docker.internal and all of RFC1918 from a
    place the first one's default no longer governs.
    """
    violations = _find_env_hatch_violations(repo_root())

    assert not violations, format_failure(
        summary=f"{FORBIDDEN_ENV_HATCH} must not appear under src/ outside the egress seam and settings",
        violations=violations,
        fix_hint=(
            "The hatch has exactly one reader, LimitSettings.adcp_outbound_allow_private, and the "
            "seam reaches it through _allow_private(). Reaching a test destination is the stack's "
            "job, not production's: move the destination, do not add a second way to relax the gate."
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
