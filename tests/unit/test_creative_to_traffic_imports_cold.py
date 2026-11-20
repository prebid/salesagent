"""The creative reader imports in a fresh interpreter, and the carrier it shares with the adapters is a leaf.

The reader (``src/core/creative_to_traffic.py``) reaches ``src.adapters`` through
``src.core.helpers``, and the adapters import the carrier. With the carrier in the reader's own
module, importing the reader first raised a circular ``ImportError``; it worked only where
something had imported ``src.adapters`` before it. The carrier lives in
``src/core/traffic_carrier.py``, which imports no ``src`` module, so neither side can close the
cycle again.
"""

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

_IMPORT_COLD = """
import sys

import src.core.traffic_carrier

leaked = sorted(m for m in sys.modules if m.startswith("src.") and m not in {"src.core", "src.core.traffic_carrier"})
assert not leaked, f"the carrier module imported {leaked}"

import src.core.creative_to_traffic
"""


def test_the_reader_imports_cold_and_the_carrier_imports_no_src_module():
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_COLD], cwd=_ROOT, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
