"""Production-configured schema probes shared by UC-005 tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any


def _validate_model_in_production(module: str, model_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a model through a fresh process using production config."""
    code = (
        "import importlib, json, sys; "
        "model = getattr(importlib.import_module(sys.argv[1]), sys.argv[2]); "
        "request = model.model_validate(json.loads(sys.argv[3])); "
        "print(json.dumps(request.model_dump(mode='json', exclude_none=True, exclude_defaults=True), sort_keys=True))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, module, model_name, json.dumps(payload)],
        capture_output=True,
        text=True,
        env={**os.environ, "ENVIRONMENT": "production"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def validate_list_creative_formats_in_production(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the canonical request model with its actual production policy."""
    return _validate_model_in_production("src.core.schemas", "ListCreativeFormatsRequest", payload)


def validate_list_creative_formats_rest_body_in_production(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the REST boundary model with its actual production policy."""
    return _validate_model_in_production("src.routes.api_v1", "ListCreativeFormatsBody", payload)
