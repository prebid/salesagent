"""Guard: Every workflow job declares timeout-minutes.

Default GitHub-hosted timeout is 6 hours per job — too long for our cost model
and an attack-vector multiplier (a hung job ties up runner concurrency). Each
job must declare an explicit ceiling.
"""

import yaml
from tests.unit._architecture_helpers import iter_workflow_files, repo_root

# Jobs where timeout truly doesn't matter (rare). Tuple: (workflow, job-id).
_ALLOWLIST: set[tuple[str, str]] = set()


def test_jobs_have_timeout_minutes():
    violations: list[str] = []
    for wf in iter_workflow_files(repo_root()):
        cfg = yaml.safe_load(wf.read_text())
        if not isinstance(cfg, dict):
            continue
        for job_id, job in (cfg.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            if (wf.name, job_id) in _ALLOWLIST:
                continue
            # Reusable workflow callers (uses:) inherit timeout from the callee.
            if "uses" in job:
                continue
            if "timeout-minutes" not in job:
                violations.append(f"{wf.name}:{job_id}: missing timeout-minutes")
    assert not violations, "\n".join(violations)
