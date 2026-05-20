"""Guard: No raw dict[str, threading.Thread] paired with threading.Lock.

All background-thread registries must use src.core.thread_registry.ThreadRegistry,
not a hand-rolled dict + Lock + reaper. Hand-rolled copies drift: a bug fixed in
one copy is missed in the others (CLAUDE.md DRY invariant, real bugs in #1264).

A module/class is a violation when it BOTH:
  - annotates a name as ``dict[str, threading.Thread]`` (or ``Dict[str, Thread]``)
  - and constructs a ``threading.Lock()`` in the same scope

beads: salesagent-x2h.3 (structural guard — ThreadRegistry consolidation)
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ThreadRegistry's own module is the ONLY allowed dict+Lock pairing.
REGISTRY_FILE = "src/core/thread_registry.py"

# Pre-existing violations: (file_path). Allowlist shrinks as they migrate.
# x2h.3 migrates all 5 known sites in the same change, so this is empty.
ALLOWLIST: set[str] = set()

EXPECTED_VIOLATION_COUNT = len(ALLOWLIST)


def _is_thread_dict_annotation(node: ast.AST) -> bool:
    """True if the annotation is dict[str, threading.Thread] / Dict[str, Thread]."""
    if not isinstance(node, ast.Subscript):
        return False
    base = node.value
    base_name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
    if base_name not in ("dict", "Dict"):
        return False
    sl = node.slice
    if not isinstance(sl, ast.Tuple) or len(sl.elts) != 2:
        return False
    value_type = sl.elts[1]
    # threading.Thread (Attribute) or Thread (Name)
    if isinstance(value_type, ast.Attribute):
        return value_type.attr == "Thread"
    if isinstance(value_type, ast.Name):
        return value_type.id == "Thread"
    return False


def _is_lock_construction(node: ast.AST) -> bool:
    """True if the node is a threading.Lock() / Lock() call."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in ("Lock", "RLock")
    if isinstance(func, ast.Name):
        return func.id in ("Lock", "RLock")
    return False


def _find_raw_thread_registries() -> list[str]:
    """Find files that pair a thread-dict annotation with a Lock construction."""
    violations: list[str] = []
    src_dir = ROOT / "src"

    for py_file in src_dir.rglob("*.py"):
        rel_path = str(py_file.relative_to(ROOT))
        if rel_path == REGISTRY_FILE:
            continue

        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        has_thread_dict = False
        has_lock = False
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and _is_thread_dict_annotation(node.annotation):
                has_thread_dict = True
            if _is_lock_construction(node):
                has_lock = True

        if has_thread_dict and has_lock:
            violations.append(rel_path)

    return violations


def test_no_raw_thread_dict_with_lock():
    """All thread registries must use ThreadRegistry, not raw dict + Lock."""
    violations = sorted(_find_raw_thread_registries())
    unexpected = [v for v in violations if v not in ALLOWLIST]
    assert not unexpected, (
        "Raw dict[str, threading.Thread] + threading.Lock found outside "
        f"{REGISTRY_FILE}. Use src.core.thread_registry.ThreadRegistry:\n" + "\n".join(f"  - {v}" for v in unexpected)
    )


def test_allowlist_has_no_stale_entries():
    """Allowlisted files that no longer violate must be removed from the allowlist."""
    violations = set(_find_raw_thread_registries())
    stale = ALLOWLIST - violations
    assert not stale, f"Stale allowlist entries (no longer violate — remove them): {sorted(stale)}"
