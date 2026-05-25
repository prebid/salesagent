"""Export the Tenant Management API OpenAPI spec to a static artifact.

Why a static artifact when spectree already serves
``/api/v1/tenant-management/docs/openapi.json`` at runtime:

* **SDK generation.** Scope3 (and any other consumer) generates a typed
  client from the spec. Pulling from runtime requires a live server;
  a checked-in artifact lets them generate from any clone.
* **API-drift visibility.** PR diffs that touch endpoint shape show
  the spec change inline. Without the static file, schema regressions
  are invisible to reviewers.
* **Stable reference.** Tag a snapshot per release; consumers pin to
  it.

Usage::

    uv run python scripts/export_openapi.py
    # writes docs/api/tenant-management-openapi.{json,yaml}
    # and docs/api/adapters/{adapter_type}-openapi.{json,yaml}

The structural test
``tests/unit/test_openapi_export_in_sync.py`` regenerates the spec at
test time and fails if the committed file drifts. CI catches stale
specs without manual diff-checking.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# ``scripts/`` is not a package; ``uv run python scripts/foo.py`` does not
# put the repo root on sys.path the way ``uv run python -m scripts.foo``
# would. Insert it explicitly so ``import src.admin...`` resolves.
sys.path.insert(0, str(REPO_ROOT))

import yaml  # type: ignore[import-untyped, unused-ignore] # noqa: E402
from flask import Flask  # noqa: E402

OUT_DIR = REPO_ROOT / "docs" / "api"
ADAPTER_OUT_DIR = OUT_DIR / "adapters"
JSON_PATH = OUT_DIR / "tenant-management-openapi.json"
YAML_PATH = OUT_DIR / "tenant-management-openapi.yaml"

# Repo-root copies follow the Stripe/Twilio convention so SDK generators,
# Swagger UI loaders, and humans browsing the repo find the spec where
# they expect. Both copies are written atomically here; the drift guard
# in tests/unit/test_openapi_export_in_sync.py keeps them in sync.
ROOT_JSON_PATH = REPO_ROOT / "openapi.json"
ROOT_YAML_PATH = REPO_ROOT / "openapi.yaml"


def build_spec() -> dict:
    """Build the OpenAPI dict from the live blueprint registration.

    Imports the Tenant Management API blueprint, attaches it to a
    throwaway Flask app, and pulls ``spec.spec`` after registration.
    The dict is exactly what spectree serves at
    ``/api/v1/tenant-management/docs/openapi.json`` — same source of truth.
    """
    # Importing the module triggers ``spec.register(tenant_management_api)``
    # at module load (line 978). We still need a Flask app to anchor
    # the blueprint so spectree can compute final paths.
    from src.admin.tenant_management_api import spec, tenant_management_api

    app = Flask("openapi-export")
    app.register_blueprint(tenant_management_api, url_prefix="/api/v1/tenant-management")

    # ``spec.spec`` is a property that calls ``flask.current_app`` to
    # resolve the registered routes — needs an active app context.
    # Push one for the duration of the read; the spec dict itself is
    # plain Python, no Flask state escapes.
    with app.app_context():
        return dict(spec.spec)


def build_adapter_specs() -> dict[str, dict]:
    """Build every published adapter-specific OpenAPI contract."""
    from src.admin.tenant_management_api import build_adapter_openapi_documents

    return build_adapter_openapi_documents()


def _write_openapi_pair(spec_dict: dict, json_path: Path, yaml_path: Path) -> list[Path]:
    json_text = json.dumps(spec_dict, indent=2, sort_keys=True) + "\n"
    yaml_spec = json.loads(json_text)
    yaml_text = yaml.safe_dump(yaml_spec, sort_keys=True, default_flow_style=False)

    json_path.write_text(json_text, encoding="utf-8")
    yaml_path.write_text(yaml_text, encoding="utf-8")
    return [json_path, yaml_path]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ADAPTER_OUT_DIR.mkdir(parents=True, exist_ok=True)
    spec_dict = build_spec()

    written = []
    written.extend(_write_openapi_pair(spec_dict, JSON_PATH, YAML_PATH))
    written.extend(_write_openapi_pair(spec_dict, ROOT_JSON_PATH, ROOT_YAML_PATH))

    for adapter_type, adapter_spec in build_adapter_specs().items():
        written.extend(
            _write_openapi_pair(
                adapter_spec,
                ADAPTER_OUT_DIR / f"{adapter_type}-openapi.json",
                ADAPTER_OUT_DIR / f"{adapter_type}-openapi.yaml",
            )
        )

    for path in written:
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
