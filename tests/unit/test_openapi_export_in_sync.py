"""Structural guard: the committed OpenAPI spec must match what the
live spectree wiring produces.

If an endpoint shape changes (new endpoint, new field on a request
schema, new error code) and the committed
``docs/api/tenant-management-openapi.{json,yaml}`` doesn't get
regenerated, this test fails CI with a clear message: run
``make openapi``.

Why this matters:

* SDK consumers (Scope3) generate typed clients from the static
  spec — drift means stale client code.
* PR diffs that touch endpoint shape become invisible without the
  static file refresh.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "export_openapi.py"
JSON_PATH = REPO_ROOT / "docs" / "api" / "tenant-management-openapi.json"
YAML_PATH = REPO_ROOT / "docs" / "api" / "tenant-management-openapi.yaml"
ROOT_JSON_PATH = REPO_ROOT / "openapi.json"
ROOT_YAML_PATH = REPO_ROOT / "openapi.yaml"
ADAPTER_OUT_DIR = REPO_ROOT / "docs" / "api" / "adapters"
ADAPTER_TYPES = ("broadstreet", "freewheel", "google_ad_manager", "mock", "springserve")


def _live_spec() -> dict:
    """Build the OpenAPI dict from the live blueprint wiring,
    matching ``scripts/export_openapi.py:build_spec``."""
    sys.path.insert(0, str(REPO_ROOT))
    from flask import Flask

    from src.admin.tenant_management_api import spec, tenant_management_api

    app = Flask("openapi-test")
    app.register_blueprint(tenant_management_api, url_prefix="/api/v1/tenant-management")
    with app.app_context():
        return dict(spec.spec)


def _live_adapter_specs() -> dict[str, dict]:
    """Build adapter-specific OpenAPI docs from the live contract builder."""
    sys.path.insert(0, str(REPO_ROOT))
    from src.admin.tenant_management_api import build_adapter_openapi_documents

    return build_adapter_openapi_documents()


def _json_normalized(value: dict) -> dict:
    """Normalize tuples and other JSON-compatible Python values before comparing."""
    return json.loads(json.dumps(value, sort_keys=True))


def test_committed_openapi_json_matches_live_spec():
    """The JSON artifact in docs/api/ must be byte-identical to what
    ``scripts/export_openapi.py`` would write right now."""
    assert JSON_PATH.exists(), f"{JSON_PATH.relative_to(REPO_ROOT)} missing — run `make openapi` to generate"

    committed = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    live = _live_spec()

    if committed != live:
        # Don't dump the full diff into the assertion message — too
        # noisy. Tell the operator what to do instead.
        raise AssertionError(
            "Committed OpenAPI spec is out of sync with the live "
            "blueprint wiring. Run `make openapi` (or "
            "`uv run python scripts/export_openapi.py`) and commit "
            f"the regenerated {JSON_PATH.relative_to(REPO_ROOT)} + "
            f"{YAML_PATH.relative_to(REPO_ROOT)}."
        )


def test_committed_openapi_yaml_matches_json():
    """JSON and YAML artifacts must encode the same OpenAPI dict —
    otherwise a consumer reading one and a CI checking the other
    diverges silently."""
    assert YAML_PATH.exists(), f"{YAML_PATH.relative_to(REPO_ROOT)} missing — run `make openapi`"

    json_doc = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    yaml_doc = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    assert json_doc == yaml_doc, (
        f"{YAML_PATH.relative_to(REPO_ROOT)} and "
        f"{JSON_PATH.relative_to(REPO_ROOT)} are out of sync — run "
        "`make openapi` to regenerate both atomically."
    )


def test_adapter_openapi_document_route_has_responses():
    """The adapter OpenAPI route must not export an empty response map."""
    doc = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    operation = doc["paths"]["/api/v1/tenant-management/adapters/{adapter_type}/openapi.json"]["get"]
    assert operation.get("responses"), "GET /adapters/{adapter_type}/openapi.json has no OpenAPI responses"
    assert "200" in operation["responses"]
    assert "404" in operation["responses"]


def test_root_copies_match_docs_api_copies():
    """The repo-root ``openapi.{json,yaml}`` files exist for discoverability
    (Stripe/Twilio convention — SDK generators and humans look at the root)
    and must be byte-identical to the canonical artifacts in ``docs/api/``."""
    for root_path, canonical_path in (
        (ROOT_JSON_PATH, JSON_PATH),
        (ROOT_YAML_PATH, YAML_PATH),
    ):
        assert root_path.exists(), (
            f"{root_path.relative_to(REPO_ROOT)} missing — run `make openapi`. "
            "Repo-root copies exist for SDK generators and Swagger UI loaders "
            "that expect openapi.{json,yaml} at the repository root."
        )
        assert root_path.read_text(encoding="utf-8") == canonical_path.read_text(encoding="utf-8"), (
            f"{root_path.relative_to(REPO_ROOT)} drifted from "
            f"{canonical_path.relative_to(REPO_ROOT)} — run `make openapi` "
            "to regenerate both atomically."
        )


def test_committed_adapter_openapi_json_matches_live_specs():
    """Every published adapter contract artifact must match the live builder."""
    live_specs = _live_adapter_specs()
    assert set(live_specs) == set(ADAPTER_TYPES)

    for adapter_type, live_spec in live_specs.items():
        path = ADAPTER_OUT_DIR / f"{adapter_type}-openapi.json"
        assert path.exists(), f"{path.relative_to(REPO_ROOT)} missing — run `make openapi`"
        assert json.loads(path.read_text(encoding="utf-8")) == _json_normalized(live_spec), (
            f"{path.relative_to(REPO_ROOT)} is out of sync with the live adapter contract builder. "
            "Run `make openapi` (or `uv run python scripts/export_openapi.py`) and commit the regenerated docs."
        )


def test_committed_adapter_openapi_yaml_matches_json():
    """Adapter JSON and YAML artifacts must encode the same OpenAPI dict."""
    for adapter_type in ADAPTER_TYPES:
        json_path = ADAPTER_OUT_DIR / f"{adapter_type}-openapi.json"
        yaml_path = ADAPTER_OUT_DIR / f"{adapter_type}-openapi.yaml"
        assert yaml_path.exists(), f"{yaml_path.relative_to(REPO_ROOT)} missing — run `make openapi`"
        assert json.loads(json_path.read_text(encoding="utf-8")) == yaml.safe_load(
            yaml_path.read_text(encoding="utf-8")
        ), f"{yaml_path.relative_to(REPO_ROOT)} is out of sync with {json_path.relative_to(REPO_ROOT)}"


def test_export_script_is_idempotent():
    """Running ``export_openapi.py`` twice produces identical output.
    Catches non-determinism (random ids, timestamps in metadata) that
    would break the drift-detection test."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"export_openapi.py failed: {result.stderr}"

    first = JSON_PATH.read_text(encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    second = JSON_PATH.read_text(encoding="utf-8")

    assert first == second, (
        "export_openapi.py is non-deterministic — two consecutive "
        "runs produced different output. The drift-detection test "
        "would flap. Sort dict keys, freeze timestamps, etc."
    )
