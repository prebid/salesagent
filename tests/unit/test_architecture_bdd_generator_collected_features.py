"""Guard: the generator must not overwrite a feature file that a driver collects.

``scripts/compile_bdd.py`` regenerates ``tests/bdd/features/BR-UC-*.feature`` from
``adcp-req``. Its ``TARGET-WINS`` bucket takes the upstream scenario WHOLESALE --
no LLM merge, no manifest entry, nothing for anyone to review -- on the premise
that the file has no step-def bindings to protect. When that premise is wrong the
bucket is a silent overwrite of downstream work, in both directions:

  * a step sentence deleted downstream comes BACK, bound to a step definition that
    was deleted with it. ``tests/bdd/conftest.py`` converts the resulting
    ``StepDefinitionNotFoundError`` into dormancy, so the scenario goes quiet
    rather than red -- the failure mode salesagent-b341x.8 exists to stop;
  * a step sentence added downstream is DROPPED. Already true in the tree: every
    one of the 72 scenarios in ``BR-UC-018-list-creatives.feature`` carries
    ``Then the response is compliant with the list_creatives spec``, which upstream
    does not have, and all 72 classified TARGET-WINS.

The premise used to be a hardcoded ``WIRED_UCS`` frozenset of 8 UCs, read off a
2026-06-01 pytest baseline and kept current by a manual step in adcp-req's own
runbook (``docs/plan/phase5-handoff.md`` step 2). Nobody ran it. ``test_uc010_*``
and ``test_uc018_*`` were added afterwards, and the generator went on believing
those files had no bindings: 141 scenarios on the wholesale path.

So the premise is now DERIVED -- ``collected_feature_files()`` reads the
``scenarios("features/...")`` calls that decide collection, which is the same fact
pytest-bdd itself uses. This guard holds it there.

GH: salesagent-b341x.8 (BDD scenarios that grade nothing, epic salesagent-b9hi1).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import iter_call_expressions

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from compile_bdd import (  # noqa: E402  (path is set immediately above)
    TRACEABILITY_PATH,
    CollectedFeaturesUnavailable,
    _load_traceability,
    collected_feature_files,
    collected_ucs,
    merge_feature,
)

#: A file a driver collects (``tests/bdd/test_uc018_list_creatives.py``) and one no
#: driver names. Both exist upstream, so both reach the classifier the same way; the
#: ONLY thing that differs is whether pytest-bdd would ever run them. UC-018 is also
#: one of the two UCs the hardcoded list had drifted past, so the first test below is
#: the regression for that drift and not merely a demonstration of the rule.
_COLLECTED = "BR-UC-018-list-creatives.feature"
_UNCOLLECTED = "BR-UC-013-manage-property-lists.feature"

#: The sentence the downstream copy has and upstream does not -- a real one, in the
#: shape of the compliance check added to 1118 of 1168 scenarios.
_DOWNSTREAM_ONLY = "Then the response is compliant with the list_creatives spec"

_UPSTREAM = """\
Feature: BR-UC-999 fixture

  Background:
    Given a Seller Agent is operational and accepting requests

  # @contextgit id=T-UC-999-demo type=test upstream=[BR-UC-999]
  Scenario: A scenario that exists on both sides
    Given the authenticated principal has 5 creatives
    When the Buyer Agent sends a list_creatives request with no parameters
    Then the response contains a creatives array with 4 items
"""

_DOWNSTREAM = f"""\
# Generated from adcp-req @ deadbeef on 2026-06-03T11:30:04Z (merge mode)
# DO NOT EDIT -- re-run: python scripts/compile_bdd.py --merge

Feature: BR-UC-999 fixture

  Background:
    Given a Seller Agent is operational and accepting requests

  @T-UC-999-demo @main-flow
  Scenario: A scenario that exists on both sides
    Given the authenticated principal has 5 creatives
    When the Buyer Agent sends a list_creatives request with no parameters
    {_DOWNSTREAM_ONLY}
    And the response contains a creatives array with 4 items
"""


def _classify(tmp_path: Path, filename: str) -> tuple[str, dict[str, int]]:
    """Merge the fixture pair under ``filename`` and return (output text, buckets).

    ``lockfile_root=None`` keeps the guard off the ``../adcp-req`` checkout: the
    classification under test is decided before any lockfile lookup, and a guard
    that needs a sibling clone does not run for whoever lacks one.
    """
    upstream = tmp_path / "upstream" / filename
    downstream = tmp_path / "downstream" / filename
    for path, text in ((upstream, _UPSTREAM), (downstream, _DOWNSTREAM)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _uc, output_text, _entries, _ids, _maps, buckets = merge_feature(
        upstream,
        downstream,
        _load_traceability(TRACEABILITY_PATH),
        "deadbeef",
    )
    return output_text, buckets


def test_a_collected_feature_file_is_never_overwritten_wholesale(tmp_path: Path) -> None:
    """The scenario routes to review, and the downstream-only sentence survives."""
    output_text, buckets = _classify(tmp_path, _COLLECTED)

    assert buckets.get("TARGET-WINS", 0) == 0, (
        f"{_COLLECTED} is collected by tests/bdd/test_uc018_list_creatives.py, so its "
        f"scenarios have step-def bindings, yet the merge classified "
        f"{buckets.get('TARGET-WINS')} of them TARGET-WINS -- taken from upstream "
        f"wholesale, with no manifest entry anyone can review. buckets={buckets}"
    )
    assert buckets.get("NEEDS-SEMANTIC-MERGE", 0) == 1, f"expected review routing, got {buckets}"
    assert _DOWNSTREAM_ONLY in output_text, (
        "the downstream-only sentence was dropped by the merge; a regeneration would "
        "silently revert every edit made to a file the suite actually runs"
    )


def test_an_uncollected_feature_file_still_takes_upstream_wholesale(tmp_path: Path) -> None:
    """Control. Without it the guard above passes for any classifier that never
    emits TARGET-WINS at all, which would say nothing about the collected/uncollected
    distinction it exists to grade."""
    output_text, buckets = _classify(tmp_path, _UNCOLLECTED)

    assert buckets.get("TARGET-WINS", 0) == 1, (
        f"no driver names {_UNCOLLECTED}, so pytest-bdd never collects it and there is "
        f"no binding to preserve -- upstream should win mechanically. buckets={buckets}"
    )
    assert _DOWNSTREAM_ONLY not in output_text, "the control did not exercise the wholesale path"


def test_the_derivation_names_every_feature_file_a_driver_collects() -> None:
    """Cross-check the regex against an AST walk of the same call sites.

    Two independent readings of the drivers, so a regex that silently stops matching
    (a reformatted call, a renamed helper) is a failure rather than a shrinking set
    -- and a shrinking set is what puts files back on the wholesale path.
    """
    from_ast: set[str] = set()
    for module in sorted((_REPO_ROOT / "tests" / "bdd").glob("test_*.py")):
        tree = ast.parse(module.read_text())
        for call in (*iter_call_expressions(tree, "scenario"), *iter_call_expressions(tree, "scenarios")):
            for arg in call.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.endswith(".feature"):
                    from_ast.add(Path(arg.value).name)

    assert from_ast, "the AST cross-check found no scenarios() call, so it grades nothing"
    assert _COLLECTED in from_ast, f"{_COLLECTED} is the fixture for the guard above and must be collected"
    assert from_ast == set(collected_feature_files()), (
        "the regex derivation and an AST walk of the same drivers disagree; the "
        "difference is exactly the set of files that would be overwritten wholesale"
    )
    # The gate reads UCs, so pin the two the hardcoded WIRED_UCS omitted. This is
    # the drift regression itself: both had drivers and neither was on the list.
    assert {"UC-010", "UC-018"} <= set(collected_ucs()), (
        "UC-010 and UC-018 are collected by tests/bdd/test_uc010_*.py and "
        "test_uc018_*.py; a gate that omits them puts 141 scenarios back on the "
        "wholesale-overwrite path"
    )


def test_an_empty_derivation_refuses_instead_of_returning_nothing(tmp_path: Path) -> None:
    """An empty set is indistinguishable from "nothing is collected", which routes
    EVERY file to TARGET-WINS. Refuse rather than absorb a moved test directory."""
    with pytest.raises(CollectedFeaturesUnavailable, match="refusing to merge"):
        collected_feature_files(tmp_path)
    with pytest.raises(CollectedFeaturesUnavailable, match="refusing to merge"):
        collected_ucs(tmp_path)
