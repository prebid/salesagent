"""Shared helpers for CI workflow guard tests."""

from __future__ import annotations

from pathlib import Path

import yaml

CI_WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def load_ci_workflow() -> dict:
    """Load and parse the canonical CI workflow file."""
    return yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))


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
    jobs = (workflow or load_ci_workflow())["jobs"]
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
