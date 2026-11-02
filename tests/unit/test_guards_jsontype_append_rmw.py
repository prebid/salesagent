"""Guard: no read-modify-write appends to concurrent JSONType collections.

Disease: code appends to ``Context.conversation_history`` or
``WorkflowStep.comments`` by loading the list and writing it back — in-place
``.append()`` (which SQLAlchemy never even flushes without Mutable
instrumentation), copy-append-reassign, or ``flag_modified`` after an in-place
append. Concurrent appenders read the same snapshot and the later commit
erases the earlier append. The correct mechanism is the
single-statement atomic append: ``append_step_comment`` /
``jsonb_list_append`` (src/core/database/jsonb_append.py).

Scope is the two concurrent-append collections by NAME. Other JSONType
columns are whole-value configs with replace semantics — flagging every
``flag_modified`` would drag those in.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: JSONType collections with concurrent-append semantics.
_APPEND_COLLECTIONS = {"conversation_history", "comments"}

# (file, form) pairs permitted to violate — shrink-only.
ALLOWLIST: set[tuple[str, str]] = set()


def _scan_file(path: Path) -> list[tuple[str, str, int]]:
    """(rel_path, form, lineno) per violation in one file."""
    tree = ast.parse(path.read_text())
    rel = str(path.relative_to(REPO_ROOT))
    hits: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        # obj.<col>.append(...) — skip `self.<col>` (in-memory model methods;
        # documented would-be-missed residual).
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr in _APPEND_COLLECTIONS
        ):
            receiver = node.func.value.value
            if not (isinstance(receiver, ast.Name) and receiver.id == "self"):
                hits.append((rel, f".{node.func.value.attr}.append", node.lineno))
        # flag_modified(x, "comments") — the in-place-append-then-flag shape.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "flag_modified"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in _APPEND_COLLECTIONS
        ):
            hits.append((rel, f"flag_modified:{node.args[1].value}", node.lineno))
    return hits


def _scan_src() -> list[tuple[str, str, int]]:
    hits: list[tuple[str, str, int]] = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        hits.extend(_scan_file(path))
    return hits


class TestJsontypeAppendRmwGuard:
    def test_no_rmw_appends_to_concurrent_collections(self):
        violations = [v for v in _scan_src() if (v[0], v[1]) not in ALLOWLIST]
        assert not violations, (
            "Read-modify-write append to a concurrent JSONType collection — use the "
            "atomic append (append_step_comment / jsonb_list_append) instead:\n"
            + "\n".join(f"  {f}:{line} {form}" for f, form, line in violations)
        )

    def test_allowlist_entries_still_violate(self):
        """Stale-entry check: every allowlisted pair still has a live violation."""
        actual = {(v[0], v[1]) for v in _scan_src()}
        stale = ALLOWLIST - actual
        assert not stale, f"Allowlist entries no longer violating — remove them: {sorted(stale)}"


class TestGuardMetaTests:
    def _scan_source(self, source: str) -> list[tuple[str, str, int]]:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=REPO_ROOT, delete=False) as fh:
            fh.write(source)
            tmp = Path(fh.name)
        try:
            return _scan_file(tmp)
        finally:
            tmp.unlink()

    def test_positive_in_place_append(self):
        src = "context.conversation_history.append({'role': 'user'})\n"
        assert [h[1] for h in self._scan_source(src)] == [".conversation_history.append"]

    def test_positive_comments_append(self):
        src = "step.comments.append({'user': 'x'})\n"
        assert [h[1] for h in self._scan_source(src)] == [".comments.append"]

    def test_positive_flag_modified(self):
        src = "attributes.flag_modified(step, 'comments')\n"
        assert [h[1] for h in self._scan_source(src)] == ["flag_modified:comments"]

    def test_negative_other_column_flag_modified(self):
        """Whole-value config columns keep replace semantics — out of scope."""
        src = "attributes.flag_modified(pkg, 'package_config')\n"
        assert self._scan_source(src) == []

    def test_negative_unrelated_append(self):
        src = "results.append({'id': 1})\n"
        assert self._scan_source(src) == []

    def test_would_be_missed_self_receiver_documented(self):
        """Known limitation: ``self.conversation_history.append`` is skipped so
        in-memory Pydantic models (ToolContext.add_to_history) do not need an
        allowlist. An ORM model method appending to its own collection would
        slip through — reviewers own that residual."""
        src = "self.conversation_history.append({'role': 'user'})\n"
        assert self._scan_source(src) == []

    def test_would_be_missed_copy_reassign_documented(self):
        """Known limitation: copy-append-reassign (``new = list(x.comments);
        new.append(e); x.comments = new``) appends via a plain local name, which
        this scan cannot tie to the column. The repro race test is the
        behavioral backstop for that shape."""
        src = "new = list(step.comments)\nnew.append({'user': 'x'})\nstep.comments = new\n"
        assert self._scan_source(src) == []
