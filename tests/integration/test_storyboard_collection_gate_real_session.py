"""Grader for the storyboard-conformance COLLECTION GATE (regression from #1858).

The storyboard-conformance job has never graded a check. Measured, run
``sa-94579f36``: ``1 passed  1 skipped  (2 collected)``, the skipped item being
``test_storyboard_check[environment-not-configured]`` with reason
``config: missing env: STORYBOARD_COMPLIANCE_DIR, STORYBOARD_SCHEMA_ROOT`` — and
nothing in the repo sets either variable (``tox.ini`` defaults both to the empty
string; the CI job sets neither and never has). The gate short-circuits on
env-set-ness, so the bundle derivation ``_bundle_path()`` was written to perform
is unreachable in every automated context.

**Core Invariant.** A conformance session is "unconfigured" only when the pinned
bundle cannot be RESOLVED — never merely because an env var is unset — and an
unconfigured session says which resolved path was missing.

Three cases, all collection-time properties of production's own
``pytest_generate_tests``, graded through a real nested pytest session so that
what is measured is the session's OUTCOME (skipped vs. errored) and the exact
``storyboard_check`` values it parametrized, never a rendered log line:

(a) bundle RESOLVABLE with no ``STORYBOARD_*`` env at all -> the suite
    parametrizes real check ids and NOT the ``environment-not-configured``
    sentinel. Graded twice: once against an explicit ``$ADCP_HOME`` (hermetic,
    runs anywhere) and once against the in-repo release bundle at
    ``tests/storyboard/runner/adcp-<version>/`` (the derivation CI actually
    depends on, since ``.github/actions/_adcp-bundle`` extracts it there).

(b) NOTHING resolvable -> the sentinel is still produced, the session SKIPS
    rather than failing hard, and the skip reason names the resolved PATHS that
    were looked for. A contributor without the bundle must not get a hard
    failure, and must not be sent after an env var that was never the mechanism.

(c) resolution FAILS -- the gate now runs ``storyboard_spec.pinned_version()``
    at collection in EVERY session (via ``adcp_home()``), and that function
    reads ``docs/adcp-spec-version.md`` and raises ``StoryboardAuditError`` on
    doc/SDK pin drift. A drifted or incomplete checkout must therefore SKIP,
    not raise out of ``pytest_generate_tests`` into a collection ERROR, and the
    skip reason must say what went wrong.

The rig (nested session, runner stub, parametrization capture) is shared with
``test_storyboard_ledger_fitness_real_session.py`` via
``tests/helpers/storyboard_session.py`` — one owner, two graders.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.audit import ledger, storyboard_spec
from tests.helpers import storyboard_session as rig
from tests.storyboard import test_storyboard_conformance as conformance

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]

_COMPLIANCE_DIR_ENV = "STORYBOARD_COMPLIANCE_DIR"
_SCHEMA_ROOT_ENV = "STORYBOARD_SCHEMA_ROOT"

# Where the compliance/schema trees live inside an extracted bundle. Duplicated
# from the module under test on purpose: this grader asserts the layout the CI
# bundle really has, so importing the subject's own constant would let a wrong
# value agree with itself.
_BUNDLE_LEAVES = ("compliance", "schemas")

# The checks the stubbed runner reports. Two protocols, because the ids the gate
# must produce are per-protocol; distinctive step ids so the assertion below
# names the exact parametrization rather than "something was collected".
_PROBE_CHECKS = (
    ledger.LedgerCheckId("mcp", "core", "collection_gate_probe", "step_one"),
    ledger.LedgerCheckId("a2a", "core", "collection_gate_probe", "step_two"),
)
_PROBE_REASON = "synthetic failure injected by the collection-gate grader"

# What a drifted checkout's pinned_version() failure says. Asserted verbatim in
# the skip reason: "the skip reason must still say what went wrong (the
# exception), not just a path".
_PIN_FAILURE_MESSAGE = "synthetic pin drift injected by the collection-gate grader"

_SENTINEL_ID = "environment-not-configured"


def _expected_probe_checks() -> dict[str, dict[str, object]]:
    """The exact ``storyboard_check`` dict production must parametrize per probe."""
    return {
        check.format(): {
            "protocol": check.protocol,
            "track": check.track,
            "storyboard_id": check.storyboard_id,
            "step_id": check.step_id,
            "status": "fail",
            "reason": _PROBE_REASON,
            "reason_kind": "synthetic",
        }
        for check in _PROBE_CHECKS
    }


def _resolved_bundle_dirs(bundle_root: Path) -> list[str]:
    """The two paths ``_bundle_path()`` derives from a bundle root, spelled as it spells them."""
    return [str((bundle_root / leaf).resolve()) for leaf in _BUNDLE_LEAVES]


def _read_captured(sink: Path, proc: subprocess.CompletedProcess[str]) -> dict[str, dict[str, object]]:
    """The parametrization the nested session produced, or a failure naming why there is none."""
    output = proc.stdout + proc.stderr
    assert sink.exists(), (
        "the capture plugin never wrote its sink, so it was not loaded at all — check the -p "
        f"wiring, not production:\n{output[-4000:]}"
    )
    # NOT a proxy for "collection succeeded": pytest_collection_modifyitems still runs
    # when pytest_generate_tests raised, and the sink then lands as an empty dict.
    # Callers gate on outcome_counts() first; this only proves the plugin ran.
    return json.loads(sink.read_text(encoding="utf-8"))


@pytest.fixture
def in_repo_bundle(monkeypatch: pytest.MonkeyPatch) -> Path:
    """The extracted release bundle CI's ``_adcp-bundle`` action puts in the tree.

    Gitignored, so a checkout that has never run the action does not have it.
    The hermetic ``$ADCP_HOME`` variant of case (a) grades the same invariant
    everywhere; this one additionally grades the derivation the CI job depends
    on, and is the only thing here allowed to be conditional.
    """
    # Resolve it the way the nested session will: that session has ADCP_HOME
    # removed, so an ambient value here would have the fixture guard a different
    # tree from the one under test.
    monkeypatch.delenv(storyboard_spec.ADCP_HOME_ENV_VAR, raising=False)
    bundle = storyboard_spec.adcp_home(REPO_ROOT)
    for leaf in _BUNDLE_LEAVES:
        if not (bundle / leaf).is_dir():
            pytest.skip(
                f"pinned bundle not extracted at {bundle} (missing {leaf}/) — "
                "run .github/actions/_adcp-bundle, or grade the hermetic ADCP_HOME variant"
            )
    return bundle


@pytest.fixture
def fake_bundle(tmp_path: Path) -> Path:
    """A minimal resolvable bundle: the two directories the gate must find."""
    bundle = tmp_path / "adcp-home"
    for leaf in _BUNDLE_LEAVES:
        (bundle / leaf).mkdir(parents=True)
    return bundle


@pytest.mark.parametrize("env_name", [_COMPLIANCE_DIR_ENV, _SCHEMA_ROOT_ENV])
def test_empty_override_derives_instead_of_resolving_to_the_repo_root(
    monkeypatch: pytest.MonkeyPatch, env_name: str, in_repo_bundle: Path
) -> None:
    """A SET-BUT-EMPTY override is not an override — tox.ini:223-224 exports exactly that.

    `{env:STORYBOARD_COMPLIANCE_DIR:}` exports the var set and empty, and that is the
    state the silent-skip bug was measured in. `_bundle_path`'s `if override:` reads it
    as absent and derives.

    Why this cannot be graded through a collection session (measured, not assumed):
    under `if override is not None:` the empty string becomes an override, `Path("")`
    resolves to the REPO ROOT, `is_dir()` says True, and the gate reports "resolvable"
    while handing the runner a directory holding no storyboards. Collection still
    succeeds and every session-level case stays green. Only the resolved path itself
    discriminates, so this asserts on the path.
    """
    monkeypatch.setenv(env_name, "")

    resolved = Path(conformance._bundle_path(env_name))

    assert resolved.is_relative_to(in_repo_bundle), (
        f"an empty {env_name} was treated as an override: {resolved} is outside the pinned "
        f"bundle at {in_repo_bundle}. An empty env var is not a path."
    )


def _collect_with_resolvable_bundle(
    tmp_path: Path, adcp_home: dict[str, str]
) -> tuple[dict[str, dict[str, object]], subprocess.CompletedProcess[str]]:
    """Collect the conformance module with NO ``STORYBOARD_*`` env set."""
    stub_name, stub_env = rig.stub_runner(tmp_path, rig.synthetic_summaries(list(_PROBE_CHECKS), _PROBE_REASON))
    capture_name, capture_env, sink = rig.capture_params(tmp_path)
    proc = rig.run_conformance_session(
        tmp_path,
        env={
            **rig.without(_COMPLIANCE_DIR_ENV, _SCHEMA_ROOT_ENV),
            **adcp_home,
            **stub_env,
            **capture_env,
        },
        plugins=(stub_name, capture_name),
        args=("--collect-only",),
    )
    return _read_captured(sink, proc), proc


def test_resolvable_bundle_via_adcp_home_parametrizes_real_checks(tmp_path: Path, fake_bundle: Path) -> None:
    """(a) Bundle resolvable, no env: real check ids, not the sentinel.

    ``$ADCP_HOME`` is the first branch of ``adcp_home()``, so this variant needs
    nothing extracted and grades the invariant on any machine.
    """
    captured, proc = _collect_with_resolvable_bundle(tmp_path, {"ADCP_HOME": str(fake_bundle)})

    output = proc.stdout + proc.stderr
    assert _SENTINEL_ID not in captured, (
        f"a resolvable bundle at {fake_bundle} still collected the {_SENTINEL_ID!r} sentinel — "
        f"the gate asked whether an env var was set, not whether the bundle resolves:\n{output[-4000:]}"
    )
    expected = _expected_probe_checks()
    assert {cid: captured.get(cid) for cid in expected} == expected, output[-4000:]


def test_resolvable_in_repo_bundle_parametrizes_real_checks(tmp_path: Path, in_repo_bundle: Path) -> None:
    """(a) Same invariant against the in-repo release bundle — the CI derivation."""
    captured, proc = _collect_with_resolvable_bundle(tmp_path, rig.without(storyboard_spec.ADCP_HOME_ENV_VAR))

    output = proc.stdout + proc.stderr
    assert _SENTINEL_ID not in captured, (
        f"the extracted bundle at {in_repo_bundle} still collected the {_SENTINEL_ID!r} sentinel — "
        f"the CI job's own derivation is unreachable:\n{output[-4000:]}"
    )
    expected = _expected_probe_checks()
    assert {cid: captured.get(cid) for cid in expected} == expected, output[-4000:]


def test_unresolvable_bundle_skips_and_names_the_resolved_paths(tmp_path: Path) -> None:
    """(b) Nothing resolvable: the sentinel is produced, the session SKIPS, the reason names the paths."""
    missing_bundle = tmp_path / "no-bundle-here"
    capture_name, capture_env, sink = rig.capture_params(tmp_path)
    proc = rig.run_conformance_session(
        tmp_path,
        env={
            **rig.without(_COMPLIANCE_DIR_ENV, _SCHEMA_ROOT_ENV),
            "ADCP_HOME": str(missing_bundle),
            **capture_env,
        },
        plugins=(capture_name,),
    )

    output = proc.stdout + proc.stderr
    assert rig.outcome_counts(proc) == {"skipped": 1}, (
        f"an unresolvable bundle must skip, not fail or error:\n{output[-4000:]}"
    )
    assert proc.returncode == 0, output[-4000:]

    captured = _read_captured(sink, proc)
    assert set(captured) == {_SENTINEL_ID}, output[-4000:]
    check = captured[_SENTINEL_ID]
    assert check["status"] == "skip"
    assert check["reason_kind"] == "config"
    for resolved in _resolved_bundle_dirs(missing_bundle):
        assert resolved in check["reason"], (
            f"the skip reason must name the resolved path that was looked for and not found; "
            f"{resolved!r} is absent from {check['reason']!r}"
        )


@pytest.mark.parametrize("exc_name", ["StoryboardAuditError", "FileNotFoundError"])
def test_resolution_failure_skips_rather_than_erroring_at_collection(tmp_path: Path, exc_name: str) -> None:
    """(c) ``pinned_version()`` raising must land in the skip branch, not out of collection.

    Resolution now runs in EVERY session, so a drifted ``docs/adcp-spec-version.md``
    (``StoryboardAuditError``) or an incomplete checkout (``FileNotFoundError``)
    reaches the gate. Either one escaping ``pytest_generate_tests`` is the hard
    failure a contributor without the bundle must never get.
    """
    pin_name, pin_env = rig.stub_raising_pinned_version(tmp_path, exc_name, _PIN_FAILURE_MESSAGE)
    capture_name, capture_env, sink = rig.capture_params(tmp_path)
    proc = rig.run_conformance_session(
        tmp_path,
        env={
            **rig.without(_COMPLIANCE_DIR_ENV, _SCHEMA_ROOT_ENV, storyboard_spec.ADCP_HOME_ENV_VAR),
            **pin_env,
            **capture_env,
        },
        plugins=(pin_name, capture_name),
    )

    output = proc.stdout + proc.stderr
    assert rig.outcome_counts(proc) == {"skipped": 1}, (
        f"a {exc_name} out of pinned_version() must skip the session, not error it:\n{output[-4000:]}"
    )
    assert proc.returncode == 0, output[-4000:]

    captured = _read_captured(sink, proc)
    assert set(captured) == {_SENTINEL_ID}, output[-4000:]
    check = captured[_SENTINEL_ID]
    assert check["status"] == "skip"
    assert check["reason_kind"] == "config"
    assert _PIN_FAILURE_MESSAGE in check["reason"], (
        f"the skip reason must say what went wrong, not name an env var: {check['reason']!r}"
    )
