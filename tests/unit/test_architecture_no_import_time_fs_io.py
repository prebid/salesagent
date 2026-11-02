"""Guard: production modules must not touch the filesystem at import time.

Creating a directory or opening a file while a module is being imported makes
that module's importability depend on the working directory and the uid of
whoever imports it -- neither of which the module chooses, and both of which
change between a developer's shell, a container, and CI.

This is not hypothetical. ``src/core/audit_logger.py`` opened two
``logging.FileHandler``s on a CWD-relative ``logs/`` path at import time. Under
Docker two containers share ``/app`` as a bind mount while running as different
uids; whichever one created ``logs/audit.log`` first owned it at umask-derived
mode 644, and every other uid then died at pytest COLLECTION with
``PermissionError: [Errno 13] Permission denied: '/app/logs/audit.log'`` --
taking entire suites down before a single test ran, and looking like flakiness
because which container won the race varied.

Test modules are in scope for the same reason, and the failure there is if
anything more insidious: pytest imports every test module during COLLECTION, so
a module-level write runs before any test does -- even for ``--collect-only``, or
a run filtered down to one unrelated test. ``tests/integration/test_mock_adapter.py``
did exactly that, dropping a JSON file into the CWD on every integration run
while containing no tests at all.

Scope is therefore every directory whose modules get imported by anything --
``src/``, ``scripts/``, ``tests/``, ``alembic/`` (the container's migration step
imports all 180), ``.pre-commit-hooks/`` and ``examples/`` -- with NO allowlist:
the tree has zero instances today and must keep it that way. Deferring the work
to first use is always available: open on demand, not on import.

DETECTOR NOTE: the match is by attribute NAME, so a module-level ``str.replace``
reads the same as ``Path.replace`` (a rename) and is reported. One such site
existed when ``tests/`` came into scope -- ``test_architecture_bdd_xfail_reason_tokens``
built a mutated xfail reason at module level -- and it was moved into a function,
which is the right shape regardless: nothing about that string needs to be
computed during collection. Narrowing the detector to let the RECEIVER decide is
still worth doing; dropping ``tests/`` from scope is not, because that hides the
historical defect this guard was written for.

The detector treats a class body and a function's default arguments as
import-time, because they are. A class-level ``handler = logging.FileHandler(...)``
is one refactor away from the defect above; skipping ``ClassDef`` made exactly
that construct invisible to this guard.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    find_import_time_fs_io_violations,
    format_failure,
    iter_module_trees,
    repo_root,
)

_SCANNED_DIRS = ("src", "scripts", "tests", "alembic", ".pre-commit-hooks", "examples")

_KNOWN_BAD_SNIPPETS = {
    "module_level_mkdir": "from pathlib import Path\nLOG_DIR = Path('logs')\nLOG_DIR.mkdir(exist_ok=True)",
    "module_level_file_handler": "import logging\nh = logging.FileHandler('logs/audit.log')",
    "module_level_makedirs": "import os\nos.makedirs('cache')",
    "module_level_open": "f = open('state.json', 'w')",
    # Control flow still executes on import -- these must not be a way around it.
    "inside_module_level_try": "import os\ntry:\n    os.makedirs('cache')\nexcept OSError:\n    pass",
    "inside_module_level_if": "import os\nif os.sep == '/':\n    os.makedirs('cache')",
    "inside_module_level_with": ("import contextlib, os\nwith contextlib.suppress(OSError):\n    os.makedirs('cache')"),
    "inside_module_level_match": "import os\nmatch os.sep:\n    case '/':\n        os.makedirs('cache')",
    # A class body runs at import. This is the construct the defect above is one
    # refactor away from, and it was invisible while the walk skipped ClassDef.
    "class_body_file_handler": "import logging\nclass Writer:\n    handler = logging.FileHandler('logs/audit.log')",
    "nested_class_body": "import os\nclass Outer:\n    class Inner:\n        cache = os.makedirs('cache')",
    # `def` evaluates its defaults and calls its decorators at import; only the
    # BODY is deferred.
    "default_argument": "import os\ndef setup(path=os.makedirs('cache')):\n    pass",
    "decorator_call_on_function": "import os\n@os.mkdir('cache')\ndef setup():\n    pass",
    "decorator_call_on_class": "import os\n@os.mkdir('cache')\nclass Writer:\n    pass",
    # `if __name__ != "__main__":` is the INVERSE of a script guard and does run.
    "inverted_main_guard": "import os\nif __name__ != '__main__':\n    os.makedirs('cache')",
}

# Deferred execution: present in the source, but NOT run by an import.
_KNOWN_GOOD_SNIPPETS = {
    "inside_function": "import os\ndef setup():\n    os.makedirs('cache')",
    "inside_async_function": "import os\nasync def setup():\n    os.makedirs('cache')",
    "inside_method": "import os\nclass Writer:\n    def setup(self):\n        os.makedirs('cache')",
    "inside_main_guard": "import os\nif __name__ == '__main__':\n    os.makedirs('cache')",
    "no_fs_io_at_all": "from pathlib import Path\nLOG_DIR = Path('logs')\nNAME = LOG_DIR / 'audit.log'",
    "inside_main_guard_reversed": "import os\nif '__main__' == __name__:\n    os.makedirs('cache')",
    # Built where written, executed somewhere else -- reporting these as
    # import-time I/O names a call that has not happened.
    "inside_lambda": "import os\nsetup = lambda: os.makedirs('cache')",
    "inside_generator_expression": "import os\nresults = (os.makedirs(p) for p in [])",
}


@pytest.mark.arch_guard
def test_no_import_time_filesystem_io_anywhere_in_the_tree() -> None:
    repo = repo_root()
    _SCANNED_ROOTS = [repo / d for d in _SCANNED_DIRS]
    violations: list[str] = []
    for tree, rel_path in iter_module_trees(_SCANNED_ROOTS):
        for lineno in find_import_time_fs_io_violations(tree):
            violations.append(f"{rel_path}:{lineno} — filesystem I/O runs at import time")

    assert not violations, format_failure(
        summary=(
            "Modules must not touch the filesystem while being imported — defer it to "
            "first use, so importing cannot fail on a path the importer never chose"
        ),
        violations=violations,
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_detector_catches_known_bad_snippets() -> None:
    assert_detector_catches_ast_snippets(find_import_time_fs_io_violations, snippets=_KNOWN_BAD_SNIPPETS)


@pytest.mark.arch_guard
def test_detector_ignores_deferred_filesystem_io() -> None:
    """A guard that flags every mkdir anywhere would be useless and get disabled."""
    false_positives = [
        label for label, source in _KNOWN_GOOD_SNIPPETS.items() if find_import_time_fs_io_violations(ast.parse(source))
    ]
    assert not false_positives, "Detector flagged deferred (non-import-time) filesystem I/O:\n" + "\n".join(
        f"  {label}" for label in false_positives
    )
