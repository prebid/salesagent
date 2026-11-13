"""Guard: a scenario the run never measured resolves not-wired.

The rest of the env-route agreement grades a real run's collection, in
``tests/collection/test_architecture_env_route_agreement.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.audit.scenario_liveness_join import build_index
from tests.bdd.conftest import ENV_ROUTES
from tests.unit._liveness_contract_pins import FIX_HINT

pytestmark = pytest.mark.arch_guard


def test_scenario_never_measured_this_run_resolves_not_wired(tmp_path: Path) -> None:
    """4c: a marker-keyed resolver has no input for an unmeasured scenario.

    ``build_index`` calls the resolver for ids whose record is ``None``. Unstated,
    the implementer keeps the tag-only lookup as a fallback and resurrects the
    divergent path as a branch. Record-is-None resolves NOT-WIRED — consistent,
    because ``graded_by_live_scenario`` already ANDs ``measured_this_run``.
    """
    empty = tmp_path / "liveness.json"
    empty.write_text(json.dumps({"scenarios": []}), encoding="utf-8")
    index = build_index({"T-UC-005-boundary-agent-asset"}, artifact_path=empty, env_routes=ENV_ROUTES)
    record = index["T-UC-005-boundary-agent-asset"]

    assert record.measured_this_run is False
    assert record.registry_wired is False, (
        "an unmeasured scenario has no marker set to route on, so it cannot be reported registry-wired — "
        f"a tag-only fallback here is the divergent path returning as a branch.\n{FIX_HINT}"
    )
