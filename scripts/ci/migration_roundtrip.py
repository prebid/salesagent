#!/usr/bin/env python3
"""Run Alembic upgrade/downgrade/upgrade for CI migration roundtrip."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from alembic.config import Config

from alembic import command
from tests.unit._migration_helpers import resolve_roundtrip_downgrade_target


def main() -> int:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))

    print("Running alembic upgrade head...")
    command.upgrade(cfg, "head")
    command.current(cfg)

    target = resolve_roundtrip_downgrade_target()
    print(f"Running alembic downgrade {target}...")
    command.downgrade(cfg, target)
    command.current(cfg)

    print("Running alembic upgrade head...")
    command.upgrade(cfg, "head")
    command.current(cfg)

    print("Migration roundtrip completed successfully.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Migration roundtrip failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
