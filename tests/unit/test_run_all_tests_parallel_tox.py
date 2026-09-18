"""The default in-network run must invoke ``tox -p``, not serial ``tox``.

The runner was serial from 2026-06-18, on an OOM rationale that a live
all-7-suites ``tox -p`` run disproved: the ``PYTEST_XDIST_AUTO_NUM_WORKERS`` /
``BDD_XDIST_N`` caps genuinely reach the in-network tests container, and memory
peaked at ~40.5% of the box -- well under the 70% fail threshold -- with zero
OOM-kills. Serial made the run's critical path the SUM of every suite rather
than the longest one.

Runs the REAL run_all_tests.sh end to end (real arg parsing, real env/suite
resolution, real command construction) rather than grepping its source, so it
asserts on genuine behavior instead of text shape. The principal thing replaced
is the ``docker`` binary on PATH -- a stub that records every invocation and
exits 0 -- because standing up the full Postgres/app/proxy compose stack is not
needed to observe *which command run_all_tests.sh hands to tox*, and doing so
would make this slow, non-hermetic, and dependent on a real Docker daemon.
Docker orchestration is the external boundary being stubbed, not the subject.

The helper scripts run_all_tests.sh calls on the host are copied in REAL
wherever their only reach outside the process is through that stubbed
``docker``; only the one that would touch the host's Python toolchain and
generate real X.509 material is replaced. See ``_REAL_HOST_SCRIPTS`` /
``_HOST_SCRIPT_STUBS``.

Also covers ``scripts/check_truncated_reports.py``, the predicate BOTH runners
apply to decide whether a green-looking run actually reported everything it
collected. Nothing graded it before: it was inline shell in one runner and
absent from the other.
"""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from scripts import check_truncated_reports
from scripts.check_truncated_reports import truncation_report
from tests.unit._run_all_tests_helpers import REPO_ROOT, RUNNER

_DOCKER_STUB = """#!/usr/bin/env bash
# Records every invocation of this fake `docker` (argv, space-joined) to
# $DOCKER_STUB_LOG, then reports success unconditionally so run_all_tests.sh's
# control flow proceeds exactly as it would against a real, healthy stack.
#
# "As it would against a healthy stack" includes WRITING THE REPORTS. tox runs
# inside the container, so its `.tox/<suite>.json` never reaches the host when
# `docker` is stubbed -- and the runner's missing-report branch correctly fails a
# run that produced none ("a suite that produced none was not measured"). A stub
# that swallows the tox call without leaving reports behind is simulating a
# stack where every suite died, not a healthy one. Emit a minimal report per
# suite on the tox invocation so the simulation is faithful; the shape is what
# scripts/check_truncated_reports.py reads (collected/total/deselected).
#
# The suites are whatever the runner just asked tox for (`... tests tox -p -e
# a,b,c`), so they are read off the invocation rather than restated here: a
# hard-coded list silently stops covering a suite the moment one is added, and
# the runner then fails the run for a report the stub never wrote -- which is
# how the `quality` env, added to ALL_SUITES upstream, turned this simulation
# into a red run that said nothing about the runner.
printf '%s\\n' "$*" >> "$DOCKER_STUB_LOG"
case "$*" in
  *tox*)
    mkdir -p .tox
    _envs="" _seen_tox=0 _take_next=0
    for _arg in "$@"; do
      if [ "$_take_next" = 1 ]; then _envs="$_arg"; break; fi
      # Only tox's own -e: `docker compose run` takes -e KEY=VAL too.
      if [ "$_seen_tox" = 1 ] && [ "$_arg" = "-e" ]; then _take_next=1; fi
      [ "$_arg" = "tox" ] && _seen_tox=1
    done
    for _s in ${_envs//,/ }; do
      printf '%s' '{"summary": {"collected": 1, "total": 1, "passed": 1, "deselected": 0}, "exitcode": 0}' \\
        > ".tox/${_s}.json"
      # A BDD suite now emits TWO artifacts: the pytest report and the dispatched-request
      # payload capture (tests/bdd/payload_capture.py), and the runner fails a run whose
      # BDD suite produced no payload artifact for the same reason it fails one that
      # produced no report -- a suite that produced none was not measured. Emitting only
      # the report here would simulate a stack whose BDD suites all died halfway, which
      # is the failure this stub's comment above already warns against.
      case "$_s" in bdd*) printf '%s' '{"run": {"collected": 1}, "nodes": {}}' > ".tox/${_s}_payloads.json" ;; esac
      # The storyboard suite publishes the runner's per-protocol summary to
      # test-results/ (tests/storyboard/test_storyboard_conformance.py, _publish_summary),
      # and the runner keeps that score inside the run directory -- a storyboard env that
      # produced no summary reads as not measured, so a healthy simulation writes both.
      case "$_s" in storyboard)
        mkdir -p test-results
        for _p in mcp a2a; do printf '%s' '{"passed": 1, "failed": 0, "failures": []}' > "test-results/storyboard_summary_${_p}.json"; done ;;
      esac
    done
    ;;
esac
exit 0
"""

#: Helper scripts run_all_tests.sh executes ON THE HOST that are copied in for
#: REAL, repo-relative to REPO_ROOT. Each one's only reach outside its own
#: process is through `docker`, which is already stubbed, so running the genuine
#: article is both hermetic and more faithful than a hand-written double:
#:
#:   creative-agent-stack.sh   `build` is docker image inspect/build/tag only.
#:   dev/alloc-e2e-subnet.sh   pure `ipaddress` arithmetic over the subnets
#:                             `docker network inspect` reports; against the
#:                             stub it sees none taken and prints a real
#:                             `E2E_NETWORK_SUBNET=<cidr>` for the runner's
#:                             `eval "export $(...)"` to consume. A double that
#:                             printed nothing would leave that variable unbound
#:                             under `set -u`.
#:   check_truncated_reports.py  pure-stdlib JSON arithmetic over $RESULTS_DIR
#:                             (main, PR #2091): the runner shells out to it
#:                             after collecting reports so a truncated suite is
#:                             not mistakable for a green one. It reaches
#:                             nothing outside the workdir, and its absence is
#:                             not inert -- whichever report-extraction shape
#:                             run_all_tests.sh settles on, a populated
#:                             $RESULTS_DIR makes the runner invoke it, and a
#:                             missing file would surface only as a python3
#:                             "No such file or directory" the rot guard below
#:                             has to name.
#:   report_suite_failures.py  the runner's second post-collection check (a suite
#:                             can report "failed 0" everywhere and still exit
#:                             non-zero), same pure-stdlib reach.
#:   _suite_reports.py         not invoked directly -- it is the report walk the
#:                             two checks above both import. Listing only the
#:                             entry points was how this sandbox went stale
#:                             once: it copies files rather than the tree, so a
#:                             missing shared dependency hands the runner a
#:                             scripts/ dir that cannot execute the checks it
#:                             invokes.
_REAL_HOST_SCRIPTS = (
    "scripts/creative-agent-stack.sh",
    "scripts/dev/alloc-e2e-subnet.sh",
    "scripts/check_truncated_reports.py",
    "scripts/report_suite_failures.py",
    "scripts/_suite_reports.py",
)

#: Helper scripts run_all_tests.sh executes on the host that must NOT run for
#: real, mapped to the stub that replaces each.
#:
#: ensure-test-tls.sh searches the HOST for a Python that has `cryptography`
#: (trying `uv run python`, which resolves and builds project environments) and
#: then generates real X.509 material on disk. That is host and toolchain reach,
#: and none of it bears on which command the runner hands to tox. Exiting 0 is
#: the faithful simulation: the runner reads it as "TLS material is present" and
#: skips its in-container fallback.
_HOST_SCRIPT_STUBS = {
    "scripts/dev/ensure-test-tls.sh": """#!/usr/bin/env bash
# Stands in for scripts/dev/ensure-test-tls.sh: reports that the stack's TLS
# material exists, without touching the host's Python toolchain or writing
# certificates. See _HOST_SCRIPT_STUBS.
exit 0
""",
    # storyboard-signing-env.sh derives the storyboard agent's signed-requests settings by
    # running `uv run python -m scripts.setup.storyboard_signing`, which resolves and builds
    # project environments -- the same host-toolchain reach ensure-test-tls.sh is stubbed
    # for, and none of it bears on which command the runner hands to tox.
    #
    # NO `exit 0` HERE, unlike the stub above: the runner SOURCES this one, so an `exit`
    # would end run_all_tests.sh itself at that line and the test would be asserting on a
    # run that never reached tox. Falling off the end returns 0, which is the faithful
    # simulation -- the runner reads it as "the settings were derived" and proceeds with
    # them unset, which is exactly what a stack brought up without them looks like.
    "scripts/dev/storyboard-signing-env.sh": """#!/usr/bin/env bash
# Stands in for scripts/dev/storyboard-signing-env.sh: exports nothing and returns 0,
# without reaching the host's Python toolchain. See _HOST_SCRIPT_STUBS.
:
""",
}


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _provision_runner_dependencies(workdir: Path) -> None:
    """Populates `workdir` with run_all_tests.sh and the helpers it shells out to.

    Copying is deliberate over stubbing wherever the real script's only external
    boundary is the stubbed `docker` -- see _REAL_HOST_SCRIPTS.
    """
    shutil.copy2(RUNNER, workdir / "run_all_tests.sh")

    for relative in _REAL_HOST_SCRIPTS:
        source = REPO_ROOT / relative
        assert source.is_file(), (
            f"{relative} is copied into this test's stub workdir but no longer exists in the repo; "
            "run_all_tests.sh's host dependencies have moved -- update _REAL_HOST_SCRIPTS."
        )
        destination = workdir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    for relative, body in _HOST_SCRIPT_STUBS.items():
        destination = workdir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body)
        _make_executable(destination)


def _run_with_stubbed_docker(tmp_path: Path) -> tuple[subprocess.CompletedProcess, Path]:
    """Runs the real run_all_tests.sh default invocation with `docker` stubbed.

    Returns the completed process and the path to the log of every command the
    script attempted to hand to `docker`.
    """
    workdir = tmp_path / "workdir"
    workdir.mkdir(parents=True)
    _provision_runner_dependencies(workdir)

    stub_bin = tmp_path / "stub_bin"
    stub_bin.mkdir()
    docker_stub = stub_bin / "docker"
    docker_stub.write_text(_DOCKER_STUB)
    _make_executable(docker_stub)

    docker_log = tmp_path / "docker_calls.log"
    docker_log.touch()

    env = {
        **os.environ,
        "PATH": f"{stub_bin}:{os.environ.get('PATH', '')}",
        "DOCKER_STUB_LOG": str(docker_log),
        # The dedicated CI security-audit check already owns this scan; skip it
        # here so a missing/real uvx on the test box can't affect this test.
        "RUN_ALL_SKIP_AUDIT": "1",
    }

    proc = subprocess.run(
        ["bash", "run_all_tests.sh"],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    # The rot guard. run_all_tests.sh gaining another host helper is the failure
    # this harness has already suffered once (a cross-branch merge added two,
    # and the run died at line 219 before reaching the assertion below). Naming
    # the missing path here turns that into a one-line diagnosis, and catches
    # the SILENT variant too -- a new dependency invoked as `helper.sh || true`
    # leaves the exit code 0 while the runner takes a branch it would never take
    # in production.
    missing = [line for line in proc.stderr.splitlines() if "No such file or directory" in line]
    assert not missing, (
        "run_all_tests.sh reached for a host file this stub workdir does not provide:\n"
        + "\n".join(missing)
        + "\n\nAdd it to _REAL_HOST_SCRIPTS (copy it, if its only external boundary is the "
        "stubbed `docker`) or to _HOST_SCRIPT_STUBS (double it, if running it for real would "
        "touch the network, the host toolchain, or a live Docker daemon)."
    )

    return proc, docker_log


def _tox_invocation_tokens(docker_log: Path) -> list[str]:
    """Extracts the argv tokens run_all_tests.sh hands to `tox` inside the
    tests container, from the recorded `docker compose ... run ... tox ...`
    call (the only stubbed `docker` invocation that names `tox`).
    """
    tox_lines = [line for line in docker_log.read_text().splitlines() if " tox " in f" {line} "]
    assert len(tox_lines) == 1, f"expected exactly one docker invocation naming tox, got: {tox_lines!r}"
    tokens = tox_lines[0].split()
    tox_index = tokens.index("tox")
    return tokens[tox_index + 1 :]


@pytest.mark.slow
def test_default_run_invokes_tox_with_parallel_flag(tmp_path):
    """The default (no-flag) invocation must run `tox -p`.

    Genuine multi-suite parallelism, matching what the live all-suites run
    proved safe: 7 suites concurrently, ~40.5% peak box memory, no OOM.
    """
    proc, docker_log = _run_with_stubbed_docker(tmp_path)

    assert proc.returncode == 0, (
        f"run_all_tests.sh (stubbed docker) exited {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )

    tox_args = _tox_invocation_tokens(docker_log)

    assert "-p" in tox_args, (
        "run_all_tests.sh's default tox invocation must include -p (parallel "
        f"multi-suite execution) but did not: tox {' '.join(tox_args)}"
    )


# ---------------------------------------------------------------------------
# The truncation predicate both runners share
# ---------------------------------------------------------------------------


def _write_report(directory: Path, name: str, **summary) -> None:
    (directory / name).write_text(json.dumps({"summary": summary}), encoding="utf-8")


def test_a_suite_that_reported_everything_it_collected_is_not_flagged(tmp_path):
    _write_report(tmp_path, "unit.json", collected=5846, total=5846, deselected=0, failed=0)
    assert truncation_report(str(tmp_path)) == []


def test_a_deselected_suite_is_not_mistaken_for_a_truncated_one(tmp_path):
    """`collected` counts what collection FOUND, before -m/-k filtering.

    The plain `bdd` env legitimately reports collected 9895 / deselected 323 /
    total 9572. Without subtracting deselection this predicate reddens every
    marker-filtered suite -- a guard that cries wolf gets deleted.
    """
    _write_report(tmp_path, "bdd.json", collected=9895, total=9572, deselected=323, failed=0)
    assert truncation_report(str(tmp_path)) == []


def test_a_truncated_suite_is_flagged_even_though_it_claims_zero_failures(tmp_path):
    """The signature of the bug: items missing, `failed` reading 0.

    A dead xdist worker ends the session after relaying only the tests already
    collected back, so exit code and `failed` both say the run was fine.
    """
    _write_report(tmp_path, "unit.json", collected=5846, total=5430, deselected=0, failed=0)

    problems = truncation_report(str(tmp_path))

    assert len(problems) == 1, problems
    assert "416 item(s) never reported" in problems[0], problems[0]


def test_an_unreadable_report_is_a_finding_not_a_pass(tmp_path):
    """A truncated run can also corrupt its own JSON; silence would be wrong."""
    (tmp_path / "unit.json").write_text("{not json", encoding="utf-8")

    problems = truncation_report(str(tmp_path))

    assert len(problems) == 1 and "unreadable" in problems[0], problems


def test_the_predicate_exits_non_zero_so_a_shell_caller_can_branch_on_it(tmp_path):
    """Both runners consume this through `if ! python3 ...`, not by parsing."""
    _write_report(tmp_path, "unit.json", collected=100, total=40, deselected=0, failed=0)
    assert check_truncated_reports.main(["check_truncated_reports.py", str(tmp_path)]) == 1

    _write_report(tmp_path, "unit.json", collected=100, total=100, deselected=0, failed=0)
    assert check_truncated_reports.main(["check_truncated_reports.py", str(tmp_path)]) == 0
