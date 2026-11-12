"""``storyboard_signing --env`` runs on the bare host ``python3``.

``scripts/dev/storyboard-signing-env.sh`` calls it with ``python3`` when ``uv`` is not on
``PATH``, which is the in-network CI runner's case, so a module-level import of a
third-party package fails the in-network job before a single test starts.
"""

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

_RUN_WITH_ONLY_STDLIB = """
import runpy, sys

class _RefuseNonStdlib:
    def find_spec(self, name, path=None, target=None):
        top = name.split(".")[0]
        if top not in sys.stdlib_module_names and top != "scripts":
            raise ModuleNotFoundError(name)

sys.meta_path.insert(0, _RefuseNonStdlib())
sys.argv = ["storyboard_signing", "--env"]
runpy.run_module("scripts.setup.storyboard_signing", run_name="__main__")
"""


def test_env_mode_imports_nothing_outside_the_standard_library():
    result = subprocess.run(
        [sys.executable, "-c", _RUN_WITH_ONLY_STDLIB], cwd=_ROOT, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert "ADCP_SIGNING_REVOKED_KEYIDS=" in result.stdout
