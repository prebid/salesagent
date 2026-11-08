"""The corrected corpus keeps one sandbox account across the error-probe lifecycle.

AdCP v3.1.1 sandbox.mdx requires the selector on every natural-key request.
Upstream adcp#8216 pins all six invalid_transitions account references. These
structural tests grade the generated artifact and its real construction hook;
no server, application mock or downloaded compliance bundle is needed.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tests.storyboard import corrected_storyboards

_RELATIVE = Path("media-buy/scenarios/invalid_transitions.yaml")
_STEPS = (
    "update_unknown_media_buy",
    "get_products_brief",
    "create_buy",
    "update_unknown_package",
    "first_cancel",
    "second_cancel",
)


@pytest.fixture
def authored_tree(tmp_path: Path) -> Path:
    """A small authored corpus with the real probe subjects and error oracles."""
    account = {"brand": {"domain": "acmeoutdoor.example"}, "operator": "pinnacle-agency.example"}
    requests = (
        {"media_buy_id": "does-not-exist-invalid-transitions-v1", "paused": True},
        {"buying_mode": "brief", "filters": {"is_fixed_price": True}},
        {"packages": [{"product_id": "$context.product_id", "budget": 5000}]},
        {
            "media_buy_id": "$context.media_buy_id",
            "packages": [{"package_id": "does-not-exist-package-invalid-transitions-v1", "budget": 5001}],
        },
        {"media_buy_id": "$context.media_buy_id", "canceled": True},
        {"media_buy_id": "$context.media_buy_id", "canceled": True},
    )
    error_codes = {0: "MEDIA_BUY_NOT_FOUND", 3: "PACKAGE_NOT_FOUND", 5: "NOT_CANCELLABLE"}
    steps = []
    for index, (step_id, request) in enumerate(zip(_STEPS, requests, strict=True)):
        step = {
            "id": step_id,
            "task": "get_products" if index == 1 else "create_media_buy" if index == 2 else "update_media_buy",
            "stateful": index >= 2,
            "sample_request": {
                **request,
                "account": deepcopy(account),
                "context": {"correlation_id": f"invalid_transitions--{step_id}"},
            },
            "validations": [{"check": "response_schema"}],
        }
        if index in error_codes:
            step.update(expect_error=True, negative_path="payload_well_formed")
            step["validations"] = [{"check": "error_code", "value": error_codes[index]}]
        steps.append(step)
    steps[2]["context_outputs"] = [{"path": "media_buy_id", "key": "media_buy_id"}]
    document = {"id": "media_buy_seller/invalid_transitions", "phases": [{"id": "probes", "steps": steps}]}
    tree = tmp_path / "adcp-pinned" / "compliance"
    for spelling in ("protocols", "domains"):
        path = tree / spelling / _RELATIVE
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump(document, sort_keys=False))
    (tree / "universal").mkdir()
    (tree / "universal/webhook-emission.yaml").write_text("phases:\n- id: capability_discovery\n  steps: []\n")
    (tree / "universal/live-control.yaml").write_text("sample_request:\n  account:\n    sandbox: false\n")
    (tree / "test-vectors/request-signing").mkdir(parents=True)
    return tree


def test_graded_tree_preserves_authored_oracles_and_selects_sandbox_for_every_step(authored_tree: Path) -> None:
    before = {path.relative_to(authored_tree): path.read_bytes() for path in authored_tree.rglob("*.yaml")}

    graded = corrected_storyboards.graded_compliance_tree(authored_tree)

    for spelling in ("protocols", "domains"):
        relative = spelling / _RELATIVE
        original = yaml.safe_load(before[relative])
        corrected = yaml.safe_load((graded / relative).read_text())
        steps = corrected["phases"][0]["steps"]
        assert {step["id"]: step["sample_request"]["account"].pop("sandbox", None) for step in steps} == dict.fromkeys(
            _STEPS, True
        )
        assert corrected == original, "Only account.sandbox may change; probe subjects and oracles must survive"
    assert (graded / "universal/live-control.yaml").read_bytes() == before[Path("universal/live-control.yaml")]
    assert {path.relative_to(authored_tree): path.read_bytes() for path in authored_tree.rglob("*.yaml")} == before


def test_reapplying_the_correction_is_byte_stable(authored_tree: Path) -> None:
    corrected_storyboards.correct_invalid_transitions_accounts(authored_tree)
    before = {path: path.read_bytes() for path in authored_tree.rglob("*.yaml")}

    corrected_storyboards.correct_invalid_transitions_accounts(authored_tree)

    assert {path: path.read_bytes() for path in authored_tree.rglob("*.yaml")} == before


@pytest.mark.parametrize("drift", ["missing_step", "extra_step", "wrong_account", "explicit_live", "wrong_storyboard"])
def test_changed_fixture_contract_is_refused_before_either_mirror_is_written(authored_tree: Path, drift: str) -> None:
    path = authored_tree / "domains" / _RELATIVE
    document = yaml.safe_load(path.read_text())
    steps = document["phases"][0]["steps"]
    if drift == "missing_step":
        steps.pop()
    elif drift == "extra_step":
        steps.append({**deepcopy(steps[-1]), "id": "new_probe"})
    elif drift == "wrong_account":
        steps[-1]["sample_request"]["account"]["operator"] = "another-buyer.example"
    elif drift == "explicit_live":
        steps[-1]["sample_request"]["account"]["sandbox"] = False
    else:
        document["id"] = "another_storyboard"
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    before = {path: path.read_bytes() for path in authored_tree.rglob("*.yaml")}

    with pytest.raises(ValueError, match="invalid_transitions"):
        corrected_storyboards.correct_invalid_transitions_accounts(authored_tree)

    assert {path: path.read_bytes() for path in authored_tree.rglob("*.yaml")} == before


def test_missing_protocol_storyboard_is_not_silently_ignored(authored_tree: Path) -> None:
    (authored_tree / "protocols" / _RELATIVE).unlink()

    with pytest.raises(FileNotFoundError, match="invalid_transitions"):
        corrected_storyboards.correct_invalid_transitions_accounts(authored_tree)
