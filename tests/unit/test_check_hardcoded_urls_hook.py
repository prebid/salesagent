"""Unit tests for ``.pre-commit-hooks/check_hardcoded_urls.py``.

Canonical spec: ``L0-implementation-plan-v2.md`` §L0-28.

Under v2.0 the ``scriptRoot`` pattern is DEPRECATED (see
``docs/deployment/static-js-urls.md``). The hook previously REQUIRED
``scriptRoot`` prefix as the "correct" pattern. Post-v2.0 it must
REJECT new ``scriptRoot`` references and REQUIRE the ``url_for`` +
``data-*`` attribute pattern instead.

Obligations:
  1. Hook rejects a JS file that declares ``scriptRoot`` (the v1
     pattern is deprecated in v2.0 per static-js-urls.md).
  2. Hook rejects a JS file that reads ``request.script_root`` via
     any string form.
  3. Hook rejects hardcoded ``/api/...``, ``/auth/...``, ``/tenant/...``
     paths in JS.
  4. Hook accepts JS that reads URLs from ``dataset.*`` / ``data-*``
     attributes — the v2.0 canonical pattern.
  5. Hook accepts JS unrelated to URLs (e.g., pure logic files).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).resolve().parents[2] / ".pre-commit-hooks" / "check_hardcoded_urls.py"


@pytest.fixture(scope="module")
def hook_module():
    """Load the hook as a module so we can call its ``main(filenames)`` entry point."""
    spec = importlib.util.spec_from_file_location("check_hardcoded_urls_hook", HOOK_PATH)
    assert spec is not None, f"Could not load hook at {HOOK_PATH}"
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["check_hardcoded_urls_hook"] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_hook(hook_module, contents: str, tmp_path: Path, name: str = "probe.js") -> int:
    f = tmp_path / name
    f.write_text(contents, encoding="utf-8")
    return hook_module.main([str(f)])


def test_rejects_scriptroot_declaration(hook_module, tmp_path: Path) -> None:
    """``const scriptRoot = ...`` is the deprecated v1 pattern — rejected."""
    source = "const scriptRoot = '{{ request.script_root }}' || '';\n"
    assert _run_hook(hook_module, source, tmp_path) == 1


def test_rejects_script_root_template_ref(hook_module, tmp_path: Path) -> None:
    """Direct reference to ``request.script_root`` in JS is rejected."""
    source = "const root = '{{ request.script_root }}';\n"
    assert _run_hook(hook_module, source, tmp_path) == 1


def test_rejects_hardcoded_api_path(hook_module, tmp_path: Path) -> None:
    """``fetch('/api/...')`` without a data-* source is rejected."""
    source = "fetch('/api/inventory').then(handle);\n"
    assert _run_hook(hook_module, source, tmp_path) == 1


def test_accepts_dataset_url_source(hook_module, tmp_path: Path) -> None:
    """Reading URLs from ``data-*`` attributes is the v2.0 canonical pattern — accepted."""
    source = "const apiUrl = document.body.dataset.inventoryApiUrl;\nfetch(apiUrl);\n"
    assert _run_hook(hook_module, source, tmp_path) == 0


def test_accepts_pure_logic_file(hook_module, tmp_path: Path) -> None:
    """JS file with no URL references is accepted unchanged."""
    source = "function add(a, b) { return a + b; }\n"
    assert _run_hook(hook_module, source, tmp_path) == 0
