"""Run the admin UI's shipped JavaScript in Node: the helpers in static/js/escape_html.js and the code built on them.

Shared by the tests of the JS helpers and of their Python twin ``safe_url``, so both grade
the code the browser loads. GitHub's runners carry Node; a machine without it fails rather
than skipping.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from tests.unit._architecture_helpers import repo_root

HELPER = repo_root() / "static" / "js" / "escape_html.js"


def run_node(script: str) -> str:
    """What *script* writes to stdout when Node runs it."""
    node = shutil.which("node")
    assert node, "Node.js is required to run the admin UI's JavaScript"
    return subprocess.run([node, "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout


def run_escape_html_helper(function: str, values: list) -> list:
    """``[function(v) for v in values]``, computed by the JS helper of that name."""
    script = (
        f"const h = require({json.dumps(str(HELPER))});"
        f"process.stdout.write(JSON.stringify({json.dumps(values)}.map(v => h.{function}(v))));"
    )
    return json.loads(run_node(script))
