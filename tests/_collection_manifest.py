"""What a real pytest session collected, published so nobody has to re-collect it.

Four tests used to answer "what does ``tests/bdd`` collect?" by spawning their
own ``pytest --collect-only`` subprocess. Each paid ~58 s of import before
collecting anything, so narrowing their target files could not help -- only
removing the subprocess could. Measured on one full run, those four cost 934.3 s
across 46 tests, 790.1 s of it in a single module whose ``lru_cache`` is
per-PROCESS, so every xdist worker that landed any of its 29 tests paid its own
import plus its own nested collection.

The suite already collects everything, once, for real. This publishes that, and
the four questions become in-process set operations. Same shape as
``tests/_worker_profile.py``: off unless an environment variable names a
directory, one file per process, and a reader that folds the parts.

## A record is identified by its SCOPE, not by its suite

Every suite in a run writes into the ONE directory the variable names -- unit,
integration, admin, e2e, ui and the collection env itself, not just the BDD
ones. A reader that cannot say which collection it wants is a reader that
silently answers a different question, so each record stamps:

``target``
    The paths pytest was invoked with, relative to rootdir and sorted. This is
    what makes "the full-tree ``tests/bdd`` record" nameable, and what keeps
    ``tests/unit`` rows out of a BDD observer's input. Without it the only
    available repair is a ``startswith("tests/bdd")`` filter at each call site,
    which is the same defect four times instead of once.

``filter``
    The ``-k``/``-m`` narrowing in force, empty when there is none. This is what
    makes each row's ``selected`` flag interpretable. ``[testenv:bdd_e2e]`` runs
    ``pytest tests/bdd/ -k e2e_rest``, so in ITS record every mcp/rest row is
    deselected by the filter rather than by the conftest exemption that a caller
    might be trying to grade. ``load(filter="")`` demands an unfiltered record
    rather than handing back rows whose deselection means something else.

``e2e_enabled``
    Whether ``BDD_E2E_ENABLED`` was on, which decides whether e2e_rest rows exist
    at all. ``docker-compose.e2e.yml`` defaults it true in-network while a host
    run has it unset, so a caller that needs those rows asks for the stamp and
    gets a loud failure on the wrong path instead of a confidently empty answer.

## Two hooks, because one cannot do it

``retain()`` runs at ``pytest_itemcollected``, which fires during genitems and is
the only point that sees the PRE-deselection population -- the denominator a
caller needs to ask what survived.

``serialize()`` runs at ``pytest_collection_finish``, which
``Session.perform_collect`` calls AFTER every ``pytest_collection_modifyitems``.
That ordering is load-bearing: ``tests/conftest.py`` applies the entity markers
there and ``tests/bdd/conftest.py`` applies the transport xfail markers, so a
marker set captured at ``itemcollected`` would predate marker application. The
routing contract is pinned on ``iter_markers()`` precisely BECAUSE it is the
superset including those auto-applied markers, so recording early would make the
test that grades that superset pass while comparing two equally-truncated sets.

Retaining item references between the two costs nothing: the items are alive for
the whole session either way.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from tests.helpers.marker_names import derive_marker_names

_OUT_DIR = os.environ.get("PYTEST_COLLECTION_MANIFEST")

enabled = bool(_OUT_DIR)

# Items retained at collection, serialized at collection_finish. Holding the
# nodes rather than their marker names is the whole point -- see the module
# docstring on hook ordering.
_retained: list[Any] = []
_record: dict[str, Any] | None = None


def _relative_target(config: Any) -> list[str]:
    """The invocation paths, relative to rootdir, normalized and sorted.

    ``pytest tests/bdd/`` and ``pytest tests/bdd`` are the same collection and
    must produce the same target, or a caller asking for the full tree by name
    would miss it over a trailing slash.
    """
    rootdir = Path(str(config.rootdir))
    targets = []
    for arg in config.args:
        # A nodeid argument (``path::test``) narrows selection, not the target
        # directory; keep only the path half so the scope stays comparable.
        path_part = str(arg).split("::", 1)[0]
        path = Path(path_part)
        try:
            path = path.resolve().relative_to(rootdir.resolve())
        except ValueError:
            # Outside rootdir (or not a path at all) -- record it verbatim
            # rather than guessing, so an unexpected invocation is visible.
            targets.append(str(arg))
            continue
        targets.append(path.as_posix().rstrip("/") or ".")
    return sorted(set(targets))


def _selection_filter(config: Any) -> str:
    """The ``-k``/``-m`` narrowing in force, as one comparable string."""
    parts = []
    for attr in ("keyword", "markexpr"):
        value = getattr(config.option, attr, None) or ""
        if value:
            parts.append(f"{attr}={value}")
    return " ".join(parts)


def retain(item: Any) -> None:
    """Remember a collected item. Called for every item, before any deselection."""
    if enabled:
        _retained.append(item)


def serialize(session: Any) -> None:
    """Build the record, after every ``modifyitems`` hook has run."""
    if not enabled:
        return

    global _record
    config = session.config
    selected = {item.nodeid for item in session.items}
    _record = {
        "suite": os.environ.get("TOX_ENV_NAME", ""),
        "target": _relative_target(config),
        "filter": _selection_filter(config),
        "e2e_enabled": os.environ.get("BDD_E2E_ENABLED", "").lower() == "true",
        "worker": os.environ.get("PYTEST_XDIST_WORKER", "main"),
        "rows": [
            {
                "nodeid": item.nodeid,
                "markers": sorted({marker.name for marker in item.iter_markers()}),
                "derived": sorted(derive_marker_names(item)),
                "selected": item.nodeid in selected,
            }
            for item in _retained
        ],
    }


def on_session_finish() -> None:
    """Write this process's record."""
    if not enabled or _record is None:
        return
    out = Path(str(_OUT_DIR))
    out.mkdir(parents=True, exist_ok=True)

    # Named by a fingerprint of the SCOPE, not by the tox env. `_worker_profile`
    # can use `TOX_ENV_NAME or "adhoc"` because only tox writes its directory;
    # this one is also written by CI's bdd-tests-shard job, which runs
    # `.github/actions/_pytest` with paths rather than tox, leaving TOX_ENV_NAME
    # unset. Both shards would then write `adhoc-gw0.json` -- harmless while
    # they are separate runners, and a silent overwrite the moment the two
    # artifacts are downloaded into one directory, which is exactly what the CI
    # wiring step does. The pid keeps two same-scope processes apart; no reader
    # parses the filename, since every field it encodes is in the record too.
    scope = json.dumps([_record["target"], _record["filter"]], sort_keys=True)
    fingerprint = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12]
    name = f"{fingerprint}-{_record['worker']}-{os.getpid()}.json"
    (out / name).write_text(json.dumps(_record), encoding="utf-8")


def manifest_dir() -> Path:
    """Where the records live, for a consumer running in a later suite.

    Raises rather than defaulting. A consumer that silently falls back to an
    empty directory asks its question of an empty set and passes, which is the
    failure this whole artifact exists to remove.
    """
    directory = os.environ.get("PYTEST_COLLECTION_MANIFEST")
    if not directory:
        raise ManifestNotFound(
            "PYTEST_COLLECTION_MANIFEST is unset, so no collection record can be "
            "read. These tests grade what a real run collected; run them through "
            "`tox -e collection` after a BDD session, or via run_all_tests.sh, "
            "which exports it."
        )
    return Path(directory)


# The full `tests/bdd` tree, as `target` records it. Both `[testenv:bdd]` and
# `[testenv:bdd_e2e]` are invoked with exactly this path, so a caller asking for
# the whole suite gets a record on either run path.
BDD_TREE = ["tests/bdd"]


class ManifestNotFound(RuntimeError):
    """No record in the directory matches the requested scope.

    Raised rather than returning nothing, because a caller that receives an
    empty collection asks its question of an empty set and passes. Every
    consumer of this artifact exists to catch something; a silent empty answer
    turns all of them green at once.
    """


def load(
    directory: str | Path,
    *,
    target: list[str] | None = None,
    filter: str | None = None,  # noqa: A002 - matches the record's own field name
    e2e_enabled: bool | None = None,
    selected_only: bool = False,
) -> list[dict[str, Any]]:
    """Every row from the records matching this scope, merged and deduplicated.

    ``target=None`` means any target, which is what a caller wants when the
    record it needs is a shard whose path list moves as files are added.
    ``filter=""`` demands an UNFILTERED record and is not satisfied by a
    filtered one.
    """
    directory = Path(directory)
    records = []
    for path in sorted(directory.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if target is not None and record.get("target") != sorted(target):
            continue
        if filter is not None and record.get("filter", "") != filter:
            continue
        if e2e_enabled is not None and record.get("e2e_enabled") is not e2e_enabled:
            continue
        records.append(record)

    if not records:
        raise ManifestNotFound(
            f"no collection record in {directory} matches "
            f"target={target!r} filter={filter!r} e2e_enabled={e2e_enabled!r}. "
            "This artifact is written by a real pytest session with "
            "PYTEST_COLLECTION_MANIFEST set; a suite that never ran wrote none."
        )

    merged: dict[str, dict[str, Any]] = {}
    for record in records:
        for row in record["rows"]:
            previous = merged.get(row["nodeid"])
            if previous is not None and previous != row:
                # The WHOLE row, not just `selected`: a duplicate nodeid whose
                # markers or derived set disagree is the same kind of unanswered
                # question, and comparing one field would let the other two be
                # decided by whichever record happened to sort last.
                #
                # No such conflict exists today -- shards are disjoint, the
                # plain bdd env and the shards are mutually exclusive in
                # run_all_tests.sh, xdist workers apply identical hooks, and the
                # controller never collects. Asserting keeps it that way rather
                # than silently picking a winner.
                differing = sorted(k for k in row if previous.get(k) != row.get(k))
                raise ManifestNotFound(
                    f"{row['nodeid']} appears in two records disagreeing on {differing}; "
                    "the merge has no rule for that because no run produces it."
                )
            merged[row["nodeid"]] = row

    rows = list(merged.values())
    if selected_only:
        rows = [row for row in rows if row["selected"]]
    return rows
