#!/usr/bin/env python3
"""Run Alembic upgrade/downgrade/upgrade for CI migration roundtrip."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from alembic import command
from tests.unit._migration_helpers import get_migration_heads, resolve_roundtrip_downgrade_target


def _assert_revision(cfg: Config, expected: str, label: str) -> None:
    engine = create_engine(cfg.get_main_option("sqlalchemy.url"))
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        current = context.get_current_revision()
    if current != expected:
        msg = f"{label}: expected revision {expected}, got {current}"
        raise RuntimeError(msg)


def main() -> int:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    head_revision = next(iter(get_migration_heads()))

    print("Running alembic upgrade head...")
    command.upgrade(cfg, "head")
    _assert_revision(cfg, head_revision, "after initial upgrade")

    target = resolve_roundtrip_downgrade_target(head_revision)
    print(f"Running alembic downgrade {target}...")
    command.downgrade(cfg, target)
    _assert_revision(cfg, target, "after downgrade")

    print("Running alembic upgrade head...")
    command.upgrade(cfg, "head")
    _assert_revision(cfg, head_revision, "after final upgrade")

    print("Migration roundtrip completed successfully.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Migration roundtrip failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
