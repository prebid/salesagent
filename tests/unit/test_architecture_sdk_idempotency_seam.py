"""Structural guard: the SDK idempotency canonicalizer has a single import seam.

``adcp.server.idempotency`` (the RFC 8785 canonicalizer engine) must be imported
ONLY by ``src/core/idempotency_canonical.py`` — the single production seam in
front of it. Routing all access through that one module is what lets us swap the
hashing engine in one place and keeps the ``RecursionError`` -> typed-error
boundary ours (see the module docstring there). A direct
``import adcp.server.idempotency`` anywhere else silently defeats the seam's
entire reason for existing, and no other guard would catch it.

This enforces, as a mechanism, the architectural intent that was previously
protected only by a docstring.
"""

import ast
from pathlib import Path

SRC = Path(__file__).parent.parent.parent / "src"
SEAM_MODULE = "adcp.server.idempotency"
# The single production module permitted to import the engine (path relative to src/).
ALLOWED = {"core/idempotency_canonical.py"}


def _attr_chain(node: ast.AST) -> str | None:
    """Dotted path of an attribute/name chain (``adcp.server.idempotency`` ← Attribute), else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _reaches_seam(name: str | None) -> bool:
    return name is not None and (name == SEAM_MODULE or name.startswith(SEAM_MODULE + "."))


def _seam_import_line(file_path: Path) -> int | None:
    """Return the line where ``file_path`` REACHES ``adcp.server.idempotency``, else None.

    Models every form that reaches the engine — static imports AND the dynamic /
    attribute routes a static-import scan would miss (all positive/negative-tested below):
      - ``from adcp.server.idempotency import X`` / ``.sub import Y``           (ImportFrom)
      - ``from adcp.server import idempotency``                                (ImportFrom, parent+name)
      - ``import adcp.server.idempotency`` / ``... as alias``                  (Import)
      - ``importlib.import_module("adcp.server.idempotency")`` / ``__import__(...)``  (Call, literal arg)
      - ``adcp.server.idempotency.<attr>`` attribute-chain access              (Attribute)
    Residual (out of scope, accepted, documented per the matcher-completeness rule):
    a module path assembled at runtime from non-literal fragments (e.g.
    ``"adcp.server." + "idempotency"``) — not statically detectable.
    """
    tree = ast.parse(file_path.read_text(), filename=str(file_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if _reaches_seam(node.module):
                return node.lineno
            if node.module == "adcp.server" and any(a.name == "idempotency" for a in node.names):
                return node.lineno
        elif isinstance(node, ast.Import):
            if any(_reaches_seam(alias.name) for alias in node.names):
                return node.lineno
        elif isinstance(node, ast.Attribute):
            if _reaches_seam(_attr_chain(node)):
                return node.lineno
        elif isinstance(node, ast.Call):
            func = node.func
            is_dynamic_import = (isinstance(func, ast.Attribute) and func.attr == "import_module") or (
                isinstance(func, ast.Name) and func.id == "__import__"
            )
            if is_dynamic_import and node.args and isinstance(node.args[0], ast.Constant):
                if isinstance(node.args[0].value, str) and _reaches_seam(node.args[0].value):
                    return node.lineno
    return None


def test_sdk_idempotency_canonicalizer_has_single_import_seam():
    """No src/ module outside the allowlist imports the SDK canonicalizer directly."""
    violations = []
    for py in SRC.rglob("*.py"):
        rel = py.relative_to(SRC).as_posix()
        if rel in ALLOWED:
            continue
        line = _seam_import_line(py)
        if line is not None:
            violations.append(f"{rel}:{line}")

    assert not violations, (
        f"'{SEAM_MODULE}' may be imported only by {sorted(ALLOWED)} (the single production "
        f"seam). Import the wrappers from src.core.idempotency_canonical instead, so the engine "
        f"stays swappable in one place and the RecursionError->typed-error boundary stays ours.\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_seam_module_actually_imports_the_engine():
    """Positive control: the seam itself DOES import the engine, so the guard is live.

    If this fails, the seam moved or the engine import changed shape — the
    negative test above could then be vacuously green, so its allowlist is stale.
    """
    seam = SRC / "core" / "idempotency_canonical.py"
    assert _seam_import_line(seam) is not None, (
        "idempotency_canonical.py must import the SDK canonicalizer; if it no longer does, "
        "update this guard (the seam moved)."
    )


def test_matcher_models_every_reaching_form(tmp_path):
    """Positive/negative self-test across every seam-reaching form the matcher models."""
    positives = [
        # static imports
        "from adcp.server.idempotency import canonical_json_sha256",
        "from adcp.server.idempotency.sub import thing",
        "from adcp.server import idempotency",
        "import adcp.server.idempotency",
        "import adcp.server.idempotency as canon",
        # dynamic / attribute routes a static-import scan would miss
        "import adcp.server\nx = adcp.server.idempotency.canonical_json_sha256({})",
        "import importlib\nm = importlib.import_module('adcp.server.idempotency')",
        "m = __import__('adcp.server.idempotency', fromlist=['canonical_json_sha256'])",
    ]
    negatives = [
        "from adcp.server import helpers",
        "from adcp.types import Product",
        "import adcp",
        "from src.core.idempotency_canonical import canonical_payload_hash",
        # near-misses: same package root / sibling submodule, NOT the seam
        "import adcp\nx = adcp.server.helpers.adcp_error",
        "import importlib\nm = importlib.import_module('adcp.server.helpers')",
    ]
    for i, src in enumerate(positives):
        f = tmp_path / f"pos_{i}.py"
        f.write_text(src + "\n")
        assert _seam_import_line(f) is not None, f"matcher missed: {src!r}"
    for i, src in enumerate(negatives):
        f = tmp_path / f"neg_{i}.py"
        f.write_text(src + "\n")
        assert _seam_import_line(f) is None, f"matcher false-positive: {src!r}"
