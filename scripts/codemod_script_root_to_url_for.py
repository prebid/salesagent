#!/usr/bin/env python3
"""STUB — Phase-A template codemod (L0-20 Red state).

This is the Red-state stub: it parses CLI args and exits 0 for ``--write``
and ``--dry-run``, but performs ZERO rewrites. The obligation tests at
``tests/migration/test_codemod_idempotency.py`` assert a byte-identical match
against the frozen ``expected/`` fixture — which this stub cannot satisfy.
The stub is idempotent vacuously (running it twice produces zero diff
because it never mutates), so the idempotency assertion passes trivially
while the correctness assertion is the Red-state signal.

The Green commit replaces this file with the real implementation.
"""

from __future__ import annotations

import argparse
import sys


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase-A template codemod (stub).")
    parser.add_argument("--templates-dir", required=True, help="Root of the template tree to scan.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview; do not write.")
    mode.add_argument("--write", action="store_true", help="Apply rewrites in-place.")
    mode.add_argument("--check", action="store_true", help="Exit nonzero if any rewrite is pending.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    # Stub: no-op for dry-run and write. `--check` also exits 0 because the
    # stub considers every tree already-migrated — the Red-state test asserts
    # that --check on INPUT fixtures exits NONZERO, so this trivially fails.
    _ = args
    return 0


if __name__ == "__main__":
    sys.exit(main())
