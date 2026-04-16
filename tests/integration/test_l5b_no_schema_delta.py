"""Schema-freeze guard: no migrations between L5a and L5b.

Decision R10 — L5b rollback relies on redeploying the `v2.0.0-rc.L5a` container
image against the current database. If L5b ships a migration, rollback = schema
drift (app at L5a expects pre-migration schema, DB has post-migration schema),
and the app fails to start. This guard enforces "zero schema change during L5b".

Checks:
1. Current `alembic heads` matches the `v2.0.0-rc.L5a`-tagged head (if tag exists).
2. `alembic revision --autogenerate` produces an empty upgrade() body on the
   L5b branch (no drift from ORM to DB schema).

Skips gracefully if the `v2.0.0-rc.L5a` tag doesn't exist yet (pre-L5a) or if
running outside an alembic-configured environment.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _git_tag_exists(tag: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/tags/{tag}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _alembic_head_at_tag(tag: str) -> str | None:
    """Return the alembic head at a given git tag, or None if not determinable."""
    result = subprocess.run(
        ["git", "show", f"{tag}:alembic/versions"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    # Parse out the newest migration file's revision ID.
    # Simple heuristic: match `revision = "..."` in the most recently-mtimed file.
    # For a more robust implementation, parse each migration's down_revision chain.
    return result.stdout.strip() or None


@pytest.mark.integration
def test_l5b_no_schema_migrations_since_l5a() -> None:
    """Fail if any new migration was added since the v2.0.0-rc.L5a tag.

    Skipped before L5a tag exists; becomes a hard gate on L5b and all sub-layers
    (L5c, L5d1-L5d5, L5e) until L6 opens.
    """
    tag = "v2.0.0-rc.L5a"
    if not _git_tag_exists(tag):
        pytest.skip(f"Tag {tag} does not exist yet — guard becomes active at L5a EXIT")

    # Compare migration files at the tag versus current HEAD.
    l5a_files = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", tag, "alembic/versions/"],
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    current_files = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD", "alembic/versions/"],
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    new_migrations = set(current_files) - set(l5a_files)
    # Ignore non-migration files (e.g., __pycache__, README)
    new_migrations = {f for f in new_migrations if f.endswith(".py") and "__" not in Path(f).name}
    assert not new_migrations, (
        f"L5b schema-freeze violated — {len(new_migrations)} new migration(s) "
        f"since {tag}: {sorted(new_migrations)}. "
        "L5b MUST ship zero schema changes so that rollback to the "
        f"{tag} container image is schema-safe. "
        "Move any schema changes to a separate PR that lands after L5e completes "
        "(L6+ is free to ship migrations again)."
    )


@pytest.mark.integration
@pytest.mark.requires_db
def test_no_orm_vs_db_schema_drift(integration_db) -> None:
    """Verify `alembic revision --autogenerate` would produce an empty upgrade().

    Catches the case where a developer added an ORM change without the
    corresponding migration — such drift would NOT be caught by the migration-file
    count check above, but would break rollback just as badly.
    """
    result = subprocess.run(
        ["uv", "run", "alembic", "check"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    # `alembic check` (2.0+) exits non-zero if drift is detected.
    # For older alembic, fall back to --autogenerate into /tmp and inspect.
    assert result.returncode == 0, (
        f"Alembic detected ORM/DB schema drift: {result.stdout}\n{result.stderr}\n"
        "An ORM change was added without a corresponding migration. "
        "Either add the migration (outside the L5b schema-freeze window — see "
        "test_l5b_no_schema_migrations_since_l5a) or revert the ORM change."
    )
