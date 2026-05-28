#!/usr/bin/env bash
set -euo pipefail

# Print the rendered CI check names ("CI / <job name>") from ci.yml.
uv run python - <<'PY'
from pathlib import Path
import yaml

workflow_path = Path(".github/workflows/ci.yml")
workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
workflow_name = workflow["name"]

for job in workflow["jobs"].values():
    print(f"{workflow_name} / {job['name']}")
PY
