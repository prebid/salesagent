"""Shared helpers for CI workflow guard tests."""

from __future__ import annotations

from pathlib import Path

import yaml

CI_WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def load_ci_workflow() -> dict:
    """Load and parse the canonical CI workflow file."""
    return yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))


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
                rendered = name
                for key, value in row.items():
                    rendered = rendered.replace("${{ matrix." + key + " }}", str(value))
                names.add(f"CI / {rendered}")
            continue

        expanded = False
        for key, values in matrix.items():
            if not isinstance(values, list):
                continue
            token = "${{ matrix." + key + " }}"
            if token in name:
                for value in values:
                    names.add(f"CI / {name.replace(token, str(value))}")
                expanded = True
                break
        if not expanded:
            names.add(f"CI / {name}")
    return names
