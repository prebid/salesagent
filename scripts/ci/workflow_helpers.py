"""Operational helpers for parsing the canonical CI workflow file."""

from __future__ import annotations

from pathlib import Path

import yaml

CI_WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def load_ci_workflow() -> dict:
    """Load and parse the canonical CI workflow file."""
    if not CI_WORKFLOW_PATH.is_file():
        msg = f"CI workflow file not found: {CI_WORKFLOW_PATH}"
        raise FileNotFoundError(msg)

    workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
    if not isinstance(workflow, dict):
        msg = f"CI workflow YAML must parse to a mapping, got {type(workflow).__name__}"
        raise ValueError(msg)
    if "jobs" not in workflow or not isinstance(workflow["jobs"], dict):
        msg = "CI workflow is missing a top-level 'jobs' mapping."
        raise ValueError(msg)
    return workflow


def _matrix_job_total(matrix: dict) -> int:
    """Mirror GitHub Actions ``strategy.job-total`` for a job matrix."""
    include = matrix.get("include")
    if include:
        return len(include)
    lists = [values for values in matrix.values() if isinstance(values, list)]
    if not lists:
        return 1
    total = 1
    for values in lists:
        total *= len(values)
    return total


def _render_job_name(name: str, matrix: dict, substitutions: dict[str, str]) -> str:
    rendered = name
    for key, value in substitutions.items():
        rendered = rendered.replace("${{ matrix." + key + " }}", value)
    rendered = rendered.replace("${{ strategy.job-total }}", str(_matrix_job_total(matrix)))
    return rendered


def rendered_ci_check_names(workflow: dict | None = None) -> set[str]:
    """Return rendered ``CI / …`` check names, expanding strategy.matrix jobs."""
    parsed = workflow or load_ci_workflow()
    jobs = parsed["jobs"]
    names: set[str] = set()
    for job in jobs.values():
        name = job.get("name", "")
        strategy = job.get("strategy") or {}
        matrix = strategy.get("matrix") or {}
        if not matrix:
            names.add(f"CI / {name}")
            continue

        include = matrix.get("include")
        if include:
            for row in include:
                subs = {key: str(value) for key, value in row.items()}
                names.add(f"CI / {_render_job_name(name, matrix, subs)}")
            continue

        expanded = False
        for key, values in matrix.items():
            if not isinstance(values, list):
                continue
            token = "${{ matrix." + key + " }}"
            if token in name:
                for value in values:
                    names.add(f"CI / {_render_job_name(name, matrix, {key: str(value)})}")
                expanded = True
                break
        if not expanded:
            names.add(f"CI / {name}")
    return names
