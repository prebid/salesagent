"""Drive REAL nested pytest sessions of the storyboard-conformance module.

Two graders need the same rig and must not each grow their own copy of it
(DRY invariant): ``tests/integration/test_storyboard_ledger_fitness_real_session.py``
grades the in-session ledger join, and
``tests/integration/test_storyboard_collection_gate_real_session.py`` grades the
collection gate that decides whether that join has anything to join against.

Both need the same three things, and nothing here is specific to either:

* run ``pytest tests/storyboard/test_storyboard_conformance.py`` as a real
  subprocess session, so what is graded is production's own
  ``pytest_generate_tests``/``conftest`` code path rather than a re-implementation;
* replace the ONE function that needs a live in-network agent plus the runner's
  npm deps (``_run_storyboard_runner``) with an injected ``-p`` plugin, leaving
  every other production path intact;
* read the outcome back — either pytest's summary line, or (more precisely) the
  exact parametrized ``storyboard_check`` values the session produced.

``capture_params`` exists because the alternative is scraping a log line: the
skip REASON and the parametrized ids are the graded objects, and pytest's short
summary wraps and folds them. The capture plugin hands back the dict production
actually parametrized, so assertions compare values with values.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts.audit import ledger

REPO_ROOT = Path(__file__).resolve().parents[2]

# The one module under measurement. Named explicitly rather than by directory so
# the graded outcome counts stay a function of the subject alone: ``tests/storyboard/``
# also holds ``test_runner_sdk_pin.py``, which deliberately fails when ``npm ci``
# has not been run in ``tests/storyboard/runner/`` — a real, correct failure
# everywhere except the one job that installs those deps, and one extra failed
# item in every count graded here.
CONFORMANCE_MODULE = "tests/storyboard/test_storyboard_conformance.py"

# Injected via ``-p``. Replaces the single function that needs a live agent; every
# other production code path (``_collect_checks``, ``pytest_generate_tests``,
# ``LedgerCheckId.format``, ``conftest.pytest_collection_modifyitems``) runs for real.
STUB_RUNNER_PLUGIN = """
import json
import os
from pathlib import Path


def pytest_configure(config):
    from tests.storyboard import test_storyboard_conformance as mod

    summaries = json.loads(Path(os.environ["STUB_STORYBOARD_SUMMARIES"]).read_text(encoding="utf-8"))
    mod._run_storyboard_runner = lambda protocol: summaries[protocol]
"""

# Injected via ``-p``. Records what the session PARAMETRIZED — id plus the whole
# ``storyboard_check`` dict — so a grader asserts on values rather than on
# pytest's rendered output.
CAPTURE_PARAMS_PLUGIN = """
import json
import os
from pathlib import Path


def pytest_collection_modifyitems(items):
    captured = {}
    for item in items:
        callspec = getattr(item, "callspec", None)
        if callspec is None or "storyboard_check" not in callspec.params:
            continue
        captured[callspec.id] = callspec.params["storyboard_check"]
    Path(os.environ["CAPTURE_STORYBOARD_PARAMS"]).write_text(json.dumps(captured), encoding="utf-8")
"""

# Injected via ``-p``. Makes the pinned-version resolution that the bundle
# derivation runs through blow up, which is what a drifted or incomplete
# checkout does to a contributor.
RAISING_PINNED_VERSION_PLUGIN = """
import os


def pytest_configure(config):
    from scripts.audit import storyboard_spec

    exc_type = {
        "StoryboardAuditError": storyboard_spec.StoryboardAuditError,
        "FileNotFoundError": FileNotFoundError,
    }[os.environ["STUB_PINNED_VERSION_RAISES"]]
    message = os.environ["STUB_PINNED_VERSION_MESSAGE"]

    def _raise(*args, **kwargs):
        raise exc_type(message)

    storyboard_spec.pinned_version = _raise
"""


def synthetic_summaries(entries: list[ledger.LedgerCheckId], reason: str) -> dict[str, dict[str, Any]]:
    """One synthetic runner summary per protocol, failing exactly ``entries``."""
    summaries: dict[str, dict[str, Any]] = {}
    for protocol in ("mcp", "a2a"):
        failures = [
            {
                "track": e.track,
                "storyboard_id": e.storyboard_id,
                "step_id": e.step_id,
                "reason": reason,
                "reason_kind": "synthetic",
            }
            for e in entries
            if e.protocol == protocol
        ]
        summaries[protocol] = {
            "agent_url": f"stub://{protocol}",
            "overall_status": "fail",
            # Non-zero graded total, so `_no_graded_checks` does not inject its
            # own synthetic reachability check and change the id set.
            "passed": 1,
            "failed": len(failures),
            "skipped": 0,
            "failures": failures,
            "skip_causes": [],
        }
    return summaries


def stub_runner(tmp_path: Path, summaries: dict[str, dict[str, Any]]) -> tuple[str, dict[str, str]]:
    """Materialize the runner stub; returns its ``-p`` name and the env it needs."""
    (tmp_path / "_stub_storyboard_runner.py").write_text(STUB_RUNNER_PLUGIN, encoding="utf-8")
    summaries_path = tmp_path / "summaries.json"
    summaries_path.write_text(json.dumps(summaries), encoding="utf-8")
    return "_stub_storyboard_runner", {"STUB_STORYBOARD_SUMMARIES": str(summaries_path)}


def stub_raising_pinned_version(tmp_path: Path, exc_name: str, message: str) -> tuple[str, dict[str, str]]:
    """Materialize the pinned-version failure stub; returns its ``-p`` name and env."""
    (tmp_path / "_stub_pinned_version.py").write_text(RAISING_PINNED_VERSION_PLUGIN, encoding="utf-8")
    return "_stub_pinned_version", {
        "STUB_PINNED_VERSION_RAISES": exc_name,
        "STUB_PINNED_VERSION_MESSAGE": message,
    }


def capture_params(tmp_path: Path) -> tuple[str, dict[str, str], Path]:
    """Materialize the parametrization capture plugin; returns its ``-p`` name, env, and sink."""
    (tmp_path / "_capture_storyboard_params.py").write_text(CAPTURE_PARAMS_PLUGIN, encoding="utf-8")
    sink = tmp_path / "captured_params.json"
    return "_capture_storyboard_params", {"CAPTURE_STORYBOARD_PARAMS": str(sink)}, sink


def run_conformance_session(
    tmp_path: Path,
    *,
    env: dict[str, str],
    plugins: tuple[str, ...] = (),
    args: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Run ``pytest`` on the conformance module for real, in a subprocess.

    ``env`` is applied over the ambient environment. A value of ``""`` is NOT a
    way to unset — an empty string is exactly the falsy-but-set state this
    module's subject historically confused with "missing" — so callers that need
    a variable ABSENT pass it through :func:`without`.
    """
    session_env = _apply(env)
    session_env["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path), str(REPO_ROOT), session_env.get("PYTHONPATH", "")],
    )
    argv = [sys.executable, "-m", "pytest", CONFORMANCE_MODULE, "-q", "-o", "addopts=", "-p", "no:randomly"]
    for plugin in plugins:
        argv += ["-p", plugin]
    argv += list(args)
    return subprocess.run(argv, cwd=REPO_ROOT, env=session_env, capture_output=True, text=True, timeout=600)


_UNSET = "\x00unset\x00"


def without(*names: str) -> dict[str, str]:
    """Env overrides that REMOVE ``names`` from the nested session's environment.

    :func:`run_conformance_session` merges over ``os.environ``, so an ambient
    value would otherwise leak in and silently decide the case under test.
    """
    return dict.fromkeys(names, _UNSET)


def _apply(overrides: dict[str, str]) -> dict[str, str]:
    """The ambient environment with ``overrides`` applied, honouring :func:`without`."""
    resolved = dict(os.environ)
    for name, value in overrides.items():
        if value == _UNSET:
            resolved.pop(name, None)
        else:
            resolved[name] = value
    return resolved


def outcome_counts(proc: subprocess.CompletedProcess[str]) -> dict[str, int]:
    """Parse pytest's ``-q`` summary line into ``{outcome: count}``."""
    output = proc.stdout + proc.stderr
    matches = re.findall(r"(\d+) (passed|failed|xfailed|xpassed|skipped|error|errors)", output)
    assert matches, f"could not read a pytest summary line from:\n{output[-4000:]}"
    return {outcome.rstrip("s") if outcome == "errors" else outcome: int(count) for count, outcome in matches}
