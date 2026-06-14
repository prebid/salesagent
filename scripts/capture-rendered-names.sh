#!/usr/bin/env bash
set -euo pipefail

# Print rendered CI check names ("CI / <job name>") from ci.yml, including matrix expansion.
uv run python - <<'PY'
from scripts.ci.workflow_helpers import rendered_ci_check_names

for name in sorted(rendered_ci_check_names()):
    print(name)
PY
