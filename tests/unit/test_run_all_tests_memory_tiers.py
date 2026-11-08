"""The memory tier run_all_tests.sh selects, graded against its own declared range.

``run_all_tests.sh`` picks worker counts from a tier keyed on the Docker VM's
MemTotal, and its comment states the range the CI-box tier was measured green
at. Those two drifted 22 GB apart: the comment said 86-196GB and the predicate
admitted anything from 64GB, so a box in the gap received a configuration nobody
measured. hetzner2-vm sat there at 74GB and its unit leg truncated 20 of 30
retained in-network runs, while the same box at 86GB truncated 0 of 45
(salesagent-8qela.157, salesagent-8qela.158).

Two tests, because the two failure modes are different. The first reads the
floor and the declared range out of the script and refuses a disagreement, so
the comment is a check rather than a claim. The second RUNS the script at the
boundaries and grades what it resolves, because a text agreement that no
arithmetic honours would still ship the gap.
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest

from tests.unit._run_all_tests_helpers import REPO_ROOT, RUNNER

#: `_CI_BOX_FLOOR_GB=86`
_FLOOR_RE = re.compile(r"^_CI_BOX_FLOOR_GB=(\d+)\s*$", re.MULTILINE)

#: `... (16/16/8 at 86-196GB).` in the tier comment.
_DECLARED_RANGE_RE = re.compile(r"\((\d+)/(\d+)/(\d+) at (\d+)-(\d+)GB\)")


def _runner_source() -> str:
    return RUNNER.read_text()


def test_ci_box_floor_matches_the_range_the_comment_declares() -> None:
    """The predicate's floor is the bottom of the range the comment measured."""
    source = _runner_source()

    floor_match = _FLOOR_RE.search(source)
    assert floor_match, (
        "run_all_tests.sh no longer assigns _CI_BOX_FLOOR_GB on its own line. This test reads "
        "the floor from that literal; if the tier moved to another spelling, point this test at "
        "it rather than deleting the check -- the 64-vs-86 gap is what it exists for."
    )

    range_match = _DECLARED_RANGE_RE.search(source)
    assert range_match, (
        "run_all_tests.sh's tier comment no longer states its measured range as "
        "'(unit/integration/e2e at LOW-HIGHGB)'. That sentence is the only record of what the "
        "CI-box tier was actually measured green at, so a reword has to keep the shape or this "
        "check grades nothing."
    )

    floor = int(floor_match.group(1))
    declared_low = int(range_match.group(4))
    declared_high = int(range_match.group(5))

    assert floor == declared_low, (
        f"the CI-box tier admits a box from {floor}GB but the comment says it was measured green "
        f"at {declared_low}-{declared_high}GB. A box between {floor}GB and {declared_low - 1}GB "
        "then runs a configuration nobody measured, which is salesagent-8qela.158. Move both "
        "numbers in one edit, or measure the lower bound and say so."
    )
    assert declared_low < declared_high, f"the declared range {declared_low}-{declared_high}GB is not a range."


def _resolve_at(tmp_path, *, mem_gb: int, cores: int) -> dict[str, int]:
    """Run the real script's resolution with `docker` and `nproc` stubbed.

    The Parallelism line is emitted before the RUN_ALL_TESTS_RESOLVE_ONLY exit,
    so this costs no Docker stack and no suite.
    """
    stub_bin = tmp_path / f"stub_{mem_gb}_{cores}"
    stub_bin.mkdir(parents=True)

    docker = stub_bin / "docker"
    docker.write_text(
        '#!/usr/bin/env bash\ncase "$*" in\n  *info*MemTotal*) echo "$DOCKER_STUB_MEMTOTAL_BYTES" ;;\nesac\nexit 0\n'
    )
    docker.chmod(0o755)

    nproc = stub_bin / "nproc"
    nproc.write_text('#!/usr/bin/env bash\necho "$STUB_CORES"\n')
    nproc.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{stub_bin}:{os.environ.get('PATH', '')}",
        "DOCKER_STUB_MEMTOTAL_BYTES": str(mem_gb * 1024**3),
        "STUB_CORES": str(cores),
        "RUN_ALL_TESTS_RESOLVE_ONLY": "1",
    }
    # The script reads these as overrides, so a value inherited from the caller's
    # shell would be reported instead of the tier's own choice.
    for inherited in (
        "UNIT_XDIST_N",
        "INTEGRATION_XDIST_N",
        "E2E_WORKERS",
        "BDD_XDIST_N",
        "PYTEST_XDIST_AUTO_NUM_WORKERS",
    ):
        env.pop(inherited, None)

    proc = subprocess.run(
        ["bash", str(RUNNER)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    line = next(
        (ln for ln in proc.stderr.splitlines() if ln.startswith("Parallelism:")),
        None,
    )
    assert line, f"run_all_tests.sh printed no Parallelism line at {mem_gb}GB/{cores}c. stderr:\n{proc.stderr[-2000:]}"
    found = dict(re.findall(r"(unit|integration|e2e_workers)=(\d+)", line))
    assert set(found) == {"unit", "integration", "e2e_workers"}, (
        f"could not read all three worker counts out of: {line!r}"
    )
    return {k: int(v) for k, v in found.items()}


@pytest.mark.parametrize(
    "mem_gb, cores, expected",
    [
        # At and above the declared floor: the CI-box tier, unchanged.
        (196, 40, {"unit": 16, "integration": 8, "e2e_workers": 8}),
        (86, 16, {"unit": 16, "integration": 8, "e2e_workers": 8}),
        # The gap salesagent-8qela.158 closed. Each of these got 16/8/8 before
        # the floor was raised, and 74GB is the box that truncated 20 of 30.
        (85, 16, {"unit": 8, "integration": 4, "e2e_workers": 2}),
        (74, 16, {"unit": 8, "integration": 4, "e2e_workers": 2}),
        (64, 16, {"unit": 8, "integration": 4, "e2e_workers": 2}),
        # Below the gap, untouched by the change.
        (63, 16, {"unit": 8, "integration": 4, "e2e_workers": 2}),
    ],
    ids=["196GB", "86GB-floor", "85GB-just-under", "74GB-hetzner2", "64GB-old-floor", "63GB"],
)
def test_the_tier_resolved_at_a_memory_size(tmp_path, mem_gb, cores, expected) -> None:
    """A box's memory selects the tier, and no box in the old gap gets CI-box counts."""
    assert _resolve_at(tmp_path, mem_gb=mem_gb, cores=cores) == expected


def test_no_box_below_the_declared_floor_receives_ci_box_parallelism(tmp_path) -> None:
    """The property behind the parametrization, stated once over the boundary.

    The cases above pin specific sizes. This one states the rule: one GB under
    the declared floor must not resolve to the CI-box tier. It reads the floor
    from the script, so raising the floor moves this test with it instead of
    leaving it pinned to 86.
    """
    floor_match = _FLOOR_RE.search(_runner_source())
    assert floor_match, "see test_ci_box_floor_matches_the_range_the_comment_declares"
    floor = int(floor_match.group(1))

    at_floor = _resolve_at(tmp_path, mem_gb=floor, cores=16)
    just_under = _resolve_at(tmp_path, mem_gb=floor - 1, cores=16)

    assert at_floor["e2e_workers"] == 8, (
        f"{floor}GB is the declared floor and must resolve to the CI-box tier, got {at_floor}"
    )
    assert just_under != at_floor, (
        f"{floor - 1}GB resolved identically to {floor}GB ({at_floor}), so the floor is not a "
        "boundary and a box under the measured range still gets CI-box parallelism."
    )
