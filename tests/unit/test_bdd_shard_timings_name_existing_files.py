"""Every entry in ``scripts/ci/bdd_shard_timings.json`` names a BDD module that exists.

``scripts/ci/shard_split.py`` sums every recorded duration to estimate a module it has no
timing for, so an entry left behind by a deleted module skews every shard estimate.
"""

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_every_recorded_timing_names_an_existing_module():
    timings = json.loads((_ROOT / "scripts/ci/bdd_shard_timings.json").read_text())

    assert [path for path in timings if not (_ROOT / path).is_file()] == []
