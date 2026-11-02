"""The capture must survive xdist, and only a real xdist run can say whether it does.

This test exists because the unit tests did not catch the defect. The recorder's first
full-scale run produced an artifact with all 8182 nodeid rows present and ZERO dispatches
on any of them — a file that parses, looks complete, and says no test sent a request. Two
process-level properties caused it, and neither is visible in a single process:

* ``pytest_runtest_logstart``/``logfinish`` fire on the xdist CONTROLLER as well, because
  xdist forwards every worker's report through them. The controller built a complete set
  of EMPTY rows and wrote them over the rows the workers had shipped.
* The controller/worker decision was first made in ``pytest_configure``, where it is
  WRONG: xdist registers its ``dsession`` plugin from its own ``pytest_configure``, and
  pluggy calls hookimpls in reverse registration order, so a conftest-loaded plugin runs
  first and sees no ``dsession`` yet. The fix moved the decision to ``pytest_sessionstart``
  — and the SECOND all-empty artifact is what proved the first fix insufficient.

So the check runs the real thing: real BDD modules, the real conftest-loaded plugin, real
workers. A subprocess, deliberately — the property under test is what happens across
processes, and an in-process assertion cannot reach it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

#: Three small modules that between them dispatch on some nodeids and not others, so the
#: run exercises both the recorded and the deliberately-empty row.
_MODULES = [
    "tests/bdd/test_codes001_open_vocabulary.py",
    "tests/bdd/test_security001_wire_error_safety.py",
    "tests/bdd/test_uc002_manual_overrides.py",
]


@pytest.mark.requires_db
def test_dispatches_survive_the_worker_to_controller_trip(tmp_path: Path, integration_db: object) -> None:
    del integration_db
    artifact = tmp_path / "payloads.json"
    env = {
        **os.environ,
        "BDD_PAYLOAD_ARTIFACT": str(artifact),
        "BDD_E2E_ENABLED": "false",
    }
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "pytest", *_MODULES, "-q", "--no-cov", "-p", "no:randomly", "-n", "2", "--dist", "load"],
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).resolve().parents[2],
        timeout=600,
    )
    assert artifact.exists(), f"no artifact written under xdist.\n{result.stdout[-3000:]}"

    data = json.loads(artifact.read_text())
    nodes = data["nodes"]
    assert data["run"]["workers"] == 2, f"the run was not distributed: {data['run']}"
    assert len(nodes) == data["run"]["collected"], (
        f"the artifact records {len(nodes)} nodeids for {data['run']['collected']} collected — "
        "a missing row is a nodeid the comparator will read as NOT_MEASURED"
    )

    with_dispatches = {k: v for k, v in nodes.items() if v}
    assert with_dispatches, (
        "every nodeid row is EMPTY under xdist. The workers recorded and shipped, and "
        "something on the controller wrote over them — which is the exact artifact this "
        f"gate's first two full runs produced.\n{result.stdout[-3000:]}"
    )
    assert any(len(v) == 0 for v in nodes.values()), (
        "no empty rows at all: a test that ran and dispatched nothing must still get a row, "
        "or 'did not run' and 'dispatched nothing' become the same artifact"
    )
