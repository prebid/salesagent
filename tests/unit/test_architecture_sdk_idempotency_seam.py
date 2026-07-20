"""Structural guard: no src/ module imports the SDK canonicalizer engine.

``adcp.server.idempotency`` (the RFC 8785 canonicalizer engine) previously had
a single dormant import seam, ``src/core/idempotency_canonical.py``. The
create-replay descope (#1546) deleted that seam along with the rest of the
dormant replay machinery, so while idempotency replay is descoped the engine
must not be imported ANYWHERE in src/. When the probe-first rebuild re-lands
replay (#1683) and keeps the SDK canonicalizer, it restores the seam module and
re-adds it to ``ALLOWED`` — one entry, never more: routing all access through
one module is what keeps the hashing engine swappable in one place and the
``RecursionError`` -> typed-error boundary ours.

This enforces, as a mechanism, the architectural intent that was previously
protected only by a docstring.
"""

import ast
from pathlib import Path

SRC = Path(__file__).parent.parent.parent / "src"
SEAM_MODULE = "adcp.server.idempotency"
# Source modules permitted to import the engine (relative to src/). Empty while
# replay is descoped (#1546); the rebuild (#1683) may restore the single seam.
ALLOWED: set[str] = set()


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
        f"'{SEAM_MODULE}' must not be imported while replay is descoped (allowlist: "
        f"{sorted(ALLOWED)}). The rebuild (#1683) restores the single canonical seam module; "
        f"until then any import silently revives half the descoped machinery.\n"
        + "\n".join(f"  - {v}" for v in violations)
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
