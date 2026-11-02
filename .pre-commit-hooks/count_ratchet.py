"""Shared count-baseline ratchet driver for pre-commit / quality-ci hooks.

Extracted so type-ignore, duplication, ruff-complexity (and future mypy)
ratchets share one create / ``--update-baseline`` / compare / auto-lower
control flow (ADR-009 / #1613 review). Each hook keeps only its count
method; baseline codecs, CLI prelude, and tooling-failure guards live here.

A ratcheting baseline may only SHRINK, and enforcing that takes two compares,
not one:

1. ``current`` vs the committed baseline — every hook always had this;
2. the committed baseline vs an UPSTREAM ceiling — because the commit under
   test can also edit the baseline file, and (1) alone reads a raised baseline
   as the new truth.

(2) used to be an opt-in that four hooks each re-implemented as a ~40-line
near-copy and two — ``check_code_duplication`` and
``check_mypy_untyped_defs_count`` — never implemented at all.
``.mypy-untyped-defs-baseline`` was duly committed at 237 against a merge-base
value of 227 and rode through green. Two further paths in this module leaked
the same way regardless of the hook: ``baseline is None`` created the file at
today's count (so ``rm .type-ignore-baseline`` accepted any count), and
``--update-baseline`` rewrote it unconditionally.

So the ceiling probe is the DRIVER's, it is not optional, and it runs on every
path that can write — create, ``--update-baseline``, compare and auto-lower.
Growth is unrepresentable through the tooling rather than merely visible to a
reviewer; ``tests/unit/test_architecture_ratchet_hooks_use_driver.py`` keeps it
that way for hooks written later.

That probe is ONE-SIDED, and the other side leaked for just as long. It refuses
values ABOVE the ceiling; a count that is spuriously BELOW it is exactly what a
crashed tool produces, and ``min(baseline, current)`` waves it through. Since
this module auto-lowers on an ORDINARY run — no ``--update-baseline``, no
failure, no line a reviewer could act on — a pylint or mypy process that dies
partway through ``src/`` commits a ceiling nobody chose, and every honest run
afterwards fails with a message whose only documented remedy is forbidden by
policy (salesagent-b341x.20).

Refusing to write a suspicious number cannot fix that, because nothing about a
short count LOOKS suspicious: it is a smaller integer. So the fix is one step
earlier, at ``run_counting_tool``, which makes a short count unrepresentable
instead of detectable — a counter that shells out must say how the tool's
COMPLETION is recognised, and a tool that did not complete yields a refusal
rather than a number.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import NoReturn, TextIO

#: Refs consulted for the upstream ceiling, in order. ``origin/main`` is what CI
#: compares against; the merge base is what a branch actually departed from, and
#: is the only reference that answers for a baseline main does not carry yet.
#: CI fetches main with ``--depth=1``, so the merge base is often unresolvable
#: there — the list is filtered to refs that resolve, never assumed.
MAIN_REF = "origin/main"


def iter_python_sources(root: Path) -> Iterator[tuple[Path, str]]:
    """Yield ``(path, text)`` for every ``.py`` under *root*, in path order.

    A file that VANISHES between the listing and the read is skipped, the rule
    ``tests/unit/_architecture_helpers.py::iter_module_trees`` already follows. Some unit
    tests write a real probe file into the scanned tree for one case (ast-grep grades a
    rule's ``files:`` glob only by a real path), and under xdist another worker deletes
    its probe between the two passes. Measured: ``FileNotFoundError:
    src/core/tools/_synthetic_identity_probe_<hex>.py`` failing the fixme-citation ratchet
    on an unrelated full run. A file that no longer exists holds nothing to count; any
    other read failure raises.
    """
    for path in sorted(root.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        yield path, text


def read_json_baseline(baseline_file: Path, keys: Sequence[str]) -> dict[str, int] | None:
    """Load a JSON object baseline; missing keys default to 0."""
    if not baseline_file.exists():
        return None
    try:
        data = json.loads(baseline_file.read_text())
        if not isinstance(data, dict):
            raise ValueError(f"baseline must be a JSON object, got {type(data).__name__}")
        return {key: int(data.get(key, 0)) for key in keys}
    except (ValueError, OSError, TypeError) as e:
        print(f"Warning: Could not read baseline from {baseline_file}: {e}", file=sys.stderr)
        return None


def write_json_baseline(baseline_file: Path, counts: Mapping[str, int], keys: Sequence[str]) -> None:
    """Write a JSON baseline with a stable key order."""
    payload = {key: int(counts[key]) for key in keys}
    baseline_file.write_text(json.dumps(payload, indent=2) + "\n")


def read_int_baseline(baseline_file: Path) -> int | None:
    """Load a single-integer baseline file."""
    if not baseline_file.exists():
        return None
    try:
        return int(baseline_file.read_text().strip())
    except (ValueError, OSError) as e:
        print(f"Warning: Could not read baseline from {baseline_file}: {e}", file=sys.stderr)
        return None


def write_int_baseline(baseline_file: Path, count: int) -> None:
    """Write a single-integer baseline file."""
    baseline_file.write_text(f"{count}\n")


def int_baseline_io(
    key: str,
) -> tuple[Callable[[Path], dict[str, int] | None], Callable[[Path, Mapping[str, int]], None]]:
    """Reader/writer pair for a single-integer baseline exposed as a one-key dict."""

    def read_baseline(baseline_file: Path) -> dict[str, int] | None:
        value = read_int_baseline(baseline_file)
        if value is None:
            return None
        return {key: value}

    def write_baseline(baseline_file: Path, counts: Mapping[str, int]) -> None:
        write_int_baseline(baseline_file, int(counts[key]))

    return read_baseline, write_baseline


def json_baseline_io(
    keys: Sequence[str],
) -> tuple[Callable[[Path], dict[str, int] | None], Callable[[Path, Mapping[str, int]], None]]:
    """Reader/writer pair for a multi-key JSON baseline."""

    def read_baseline(baseline_file: Path) -> dict[str, int] | None:
        return read_json_baseline(baseline_file, keys)

    def write_baseline(baseline_file: Path, counts: Mapping[str, int]) -> None:
        write_json_baseline(baseline_file, counts, keys)

    return read_baseline, write_baseline


def parse_ratchet_args(description: str) -> argparse.Namespace:
    """Shared argparse for count-ratchet hooks (``--update-baseline`` only)."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Force update baseline to current count(s) (↑ must be justified in review)",
    )
    return parser.parse_args()


def resolve_ratchet_paths(
    *,
    baseline_name: str,
    src_dirname: str = "src",
) -> tuple[Path, Path, Path]:
    """Return ``(repo_root, src_path, baseline_file)``; exit 1 if ``src/`` is missing."""
    repo_root = Path(__file__).resolve().parent.parent
    src_path = repo_root / src_dirname
    if not src_path.exists():
        print(f"Error: {src_dirname}/ directory not found", file=sys.stderr)
        raise SystemExit(1)
    return repo_root, src_path, repo_root / baseline_name


def refuse_unmeasured(label: str, reason: str, detail: str = "", *, version: str = "") -> NoReturn:
    """Refuse to yield a number, and say why. Never returns.

    A counter's only two outcomes are a measurement and a refusal. There is no
    third one where the tool half-ran and the caller gets to decide, because the
    caller's number goes straight into ``run_count_ratchet``, which writes it.

    ``version`` names the tool that failed. A refusal is the message most likely to
    be read after a dependency bump, so it is the one that most needs to say which
    build of the tool produced it.
    """
    instrument = f"{label} [{version}]" if version else label
    print(f"NOT_MEASURED: {instrument} did not run to completion — {reason}", file=sys.stderr)
    if detail:
        print(detail, file=sys.stderr)
    print("", file=sys.stderr)
    print("A partial count is not a low count. Refusing rather than returning a", file=sys.stderr)
    print("number: a short tally reads as an improvement, and run_count_ratchet", file=sys.stderr)
    print("writes an improvement to the baseline on an ORDINARY run, after which", file=sys.stderr)
    print("every honest run fails against a ceiling nobody chose.", file=sys.stderr)
    raise SystemExit(2)


def version_probe(cmd: Sequence[str]) -> list[str]:
    """The ``--version`` invocation for the tool *cmd* runs.

    DERIVED from the counting command rather than declared beside it. A
    ``version_cmd=`` parameter would be a second place to name the tool, and a
    provenance line that names a DIFFERENT tool than the one that produced the
    number is worse than no line at all. Every counter here shells out as
    ``[sys.executable, "-m", <module>, ...]``, so the probe is that same prefix; the
    fallback covers a bare executable.
    """
    if len(cmd) >= 3 and cmd[1] == "-m":
        return [cmd[0], "-m", cmd[2], "--version"]
    return [cmd[0], "--version"]


def _tool_version(cmd: Sequence[str], *, cwd: Path) -> str:
    """The counting tool's own version line, or a NAMED unknown.

    ADR-009's caveat has lived in ``check_mypy_untyped_defs_count``'s docstring and
    nowhere a run can see: "counts drift with mypy / plugin versions". A mypy or
    SQLAlchemy/Pydantic plugin bump moves the count with no source change, and the
    same holds for pylint/astroid and R0801 — so the number lands in the baseline
    with no record of what produced it, and the next honest run fails against a
    ceiling that a dependency bump set.

    Deliberately NOT written into the baseline FILE. A version there would make a
    bump a visible diff, which is the appeal — but it would equally make every
    contributor on a different patch release produce one, and the driver's
    upstream-ceiling probe reads baselines across git refs, so a version key in them
    is a new way for the ratchet to disagree with itself about what a baseline says.
    Provenance belongs in the run's output, which is where a reader goes when a count
    moves and the source did not.

    A failed probe yields ``version UNKNOWN (<why>)`` rather than refusing. This is
    provenance, not a measurement: no number depends on it, so a refusal would block
    a commit over a cosmetic failure. It is NAMED rather than omitted, because a line
    that silently dropped the version is the same defect one size down.
    """
    try:
        probe = subprocess.run(version_probe(cmd), capture_output=True, text=True, cwd=cwd, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"version UNKNOWN ({type(exc).__name__})"
    if probe.returncode != 0:
        return f"version UNKNOWN (exit {probe.returncode})"
    lines = (probe.stdout or probe.stderr or "").strip().splitlines()
    return lines[0].strip() if lines else "version UNKNOWN (no output)"


def run_counting_tool(
    cmd: Sequence[str],
    *,
    cwd: Path,
    label: str,
    accepts_returncode: Callable[[int], bool],
    completion_marker: Callable[[str], str | None],
    truncate: int = 800,
) -> subprocess.CompletedProcess[str]:
    """Run a counting tool, and return only if it demonstrably FINISHED.

    Both proofs are REQUIRED keywords with no default, which is the whole point:
    a counter cannot be written that shells out without saying how completion is
    recognised. The two are independent, and a real short count trips one or the
    other:

    ``accepts_returncode``
        The exit statuses that mean "ran to the end". Not "did not obviously
        explode" — pylint's status is a bitmask and a fatal on ONE module sets a
        bit while the process still exits and still prints the hits it found.

    ``completion_marker``
        ``stdout -> evidence, or None``. The line a tool prints only after
        finishing (pylint's score, mypy's ``Found N errors ... (checked N source
        files)``). An exit code cannot distinguish a process killed at 60% from
        one that finished, and a signal death is not even in the tool's own
        vocabulary; the trailing marker can, because it is never reached.

    The evidence is echoed, so a run states what it measured instead of only
    what it counted. This replaces a ``has_findings`` predicate whose contract
    was "rc 1 is fine as long as SOMETHING was found", under which a crash that
    had already emitted findings was indistinguishable from a clean run.

    The echoed line also carries WHICH TOOL measured it — see :func:`_tool_version`.
    The denominator without the instrument is half a provenance record: these counts
    drift with tool and plugin versions, so a number that moved with no source change
    is otherwise unattributable.
    """
    version = _tool_version(cmd, cwd=cwd)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    output = result.stdout or ""
    detail = (result.stderr or output or "")[:truncate]
    if not accepts_returncode(result.returncode):
        refuse_unmeasured(label, f"exit status {result.returncode}", detail, version=version)
    evidence = completion_marker(output)
    if evidence is None:
        refuse_unmeasured(
            label, "its output carries no completion marker, so it stopped early", detail, version=version
        )
    print(f"  {label} [{version}]: completed — {evidence}")
    return result


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)


def _upstream_refs(repo_root: Path) -> list[str]:
    """Resolvable upstream references, most authoritative first.

    The MERGE BASE leads, because it is what this branch actually inherited,
    and a ratchet grades a branch against what it inherited. ``origin/main``
    alone cannot do that job once main moves on: main paying complexity down
    from 182 to 177 after a branch departs is main's progress, not the branch's
    regression, yet a naive origin/main compare reports the branch's untouched
    182 as a raise. Failures a branch cannot act on are how ``--update-baseline``
    becomes a habit — the very habit these ratchets exist to prevent.

    ``origin/main`` still follows, and carries real weight in two places: it
    answers for baselines and KEYS the merge base does not have, and in CI it is
    usually the only ref that resolves at all (the quality-gate job fetches main
    with ``--depth=1``, which leaves no common history to compute a merge base
    from). So the branch is graded on what it inherited, and the merged result is
    graded against main — which is the ref that is binding at merge time.
    """
    refs = []
    merge_base = _git(repo_root, "merge-base", "HEAD", MAIN_REF)
    if merge_base.returncode == 0 and merge_base.stdout.strip():
        refs.append(merge_base.stdout.strip())
    if _git(repo_root, "rev-parse", "--verify", "--quiet", f"{MAIN_REF}^{{commit}}").returncode == 0:
        refs.append(MAIN_REF)
    return refs


def _read_upstream_baseline(repo_root: Path, ref: str, baseline_name: str, parse: Callable[[str], object]) -> dict:
    """Decode ``<ref>:<baseline_name>``; ``{}`` when the ref lacks the file."""
    result = _git(repo_root, "show", f"{ref}:{baseline_name}")
    if result.returncode != 0:
        return {}
    try:
        decoded = parse(result.stdout)
    except (ValueError, TypeError) as e:
        print(f"ERROR: {ref}:{baseline_name} is not a valid baseline: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    if not isinstance(decoded, Mapping):
        print(f"ERROR: {ref}:{baseline_name} did not decode to a mapping", file=sys.stderr)
        raise SystemExit(1)
    return {str(key): int(value) for key, value in decoded.items()}


def _extract_upstream_tree(repo_root: Path, ref: str, paths: Sequence[str], dest: Path) -> Path:
    """Materialize ``ref``'s copy of ``paths`` under ``dest`` and return it."""
    archive = dest / "tree.tar"
    with archive.open("wb") as handle:
        # stdout is a real fd, so the child's bytes land in the file whatever
        # `text` says; text=True only decodes the captured stderr.
        result = subprocess.run(
            ["git", "archive", ref, *paths], cwd=repo_root, stdout=handle, stderr=subprocess.PIPE, text=True
        )
    if result.returncode != 0:
        raise RuntimeError(f"git archive {ref} failed: {(result.stderr or '')[:300]}")
    with tarfile.open(archive) as tar:
        tar.extractall(dest, filter="data")
    archive.unlink()
    return dest


def resolve_upstream_ceiling(
    *,
    repo_root: Path,
    baseline_name: str,
    keys: Sequence[str],
    parse: Callable[[str], object],
    count_upstream: Callable[[Path], Mapping[str, int]] | None = None,
    upstream_paths: Sequence[str] = ("src", "tests", "scripts", "pyproject.toml"),
) -> dict[str, int]:
    """Highest value each key is allowed to hold, per upstream evidence.

    Evidence is consulted cheapest-first and per KEY, so a newly ratcheted key
    is not judged against a ceiling of 0 (its true count is pre-existing debt —
    that is why ``F841`` could join the ruff baseline at 39) while the keys
    upstream does track keep their real ceiling:

    1. the baseline file at the merge base — what this branch inherited;
    2. the baseline file committed on ``origin/main``, for keys (1) omits (and
       as the only available reference in CI's shallow checkout);
    3. ``count_upstream`` re-run against upstream SOURCE, for keys neither
       baseline carries. This is the branch that catches a ratchet whose
       baseline file is itself new — the shape that let
       ``.fixme-citation-baseline`` land seeded at ``tests_fixme_beads=4`` when
       the merge base's true count was 0.

    A key with no upstream evidence at all is OMITTED rather than defaulted:
    unmeasurable is not the same as unbounded, and the caller reports it.
    """
    refs = _upstream_refs(repo_root)
    ceiling: dict[str, int] = {}
    for ref in refs:
        upstream = _read_upstream_baseline(repo_root, ref, baseline_name, parse)
        for key in keys:
            if key not in ceiling and key in upstream:
                ceiling[key] = upstream[key]
        if all(key in ceiling for key in keys):
            return ceiling

    if count_upstream is None or not refs:
        return ceiling

    source_ref = refs[0]
    with tempfile.TemporaryDirectory(prefix="ratchet-upstream-") as tmp:
        try:
            tree = _extract_upstream_tree(repo_root, source_ref, upstream_paths, Path(tmp))
            counted = count_upstream(tree)
        except (RuntimeError, OSError, tarfile.TarError) as e:
            print(f"Warning: could not count {source_ref}'s source for {baseline_name}: {e}", file=sys.stderr)
            return ceiling
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    for key in keys:
        if key not in ceiling and key in counted:
            ceiling[key] = int(counted[key])
    return ceiling


def check_against_ceiling(
    *,
    keys: Sequence[str],
    probe: Mapping[str, int],
    ceiling: Mapping[str, int],
    baseline_name: str,
    format_key: Callable[[str], str],
    err: TextIO,
) -> int:
    """Fail when any probed value exceeds its upstream ceiling."""
    raised = [(key, probe[key], ceiling[key]) for key in keys if key in ceiling and probe[key] > ceiling[key]]
    if not raised:
        return 0
    print(f"{baseline_name} raised above upstream!", file=err)
    for key, value, limit in raised:
        print(f"  {format_key(key)}: upstream={limit} local={value} (+{value - limit})", file=err)
    print("", file=err)
    print("A ratcheting baseline may only SHRINK. Fix the new violations", file=err)
    print("instead of raising the baseline — and note that neither deleting", file=err)
    print("the baseline file nor --update-baseline gets around this probe.", file=err)
    return 1


def format_counts(counts: Mapping[str, int], keys: Sequence[str]) -> str:
    return ", ".join(f"{key}={counts[key]}" for key in keys)


def run_count_ratchet(
    *,
    keys: Sequence[str],
    current: Mapping[str, int],
    baseline_file: Path,
    update_baseline: bool,
    read_baseline: Callable[[Path], dict[str, int] | None],
    write_baseline: Callable[[Path, Mapping[str, int]], None],
    increase_header: str,
    increase_hints: Sequence[str],
    repo_root: Path | None = None,
    parse_upstream: Callable[[str], object] | None = None,
    count_upstream: Callable[[Path], Mapping[str, int]] | None = None,
    format_key: Callable[[str], str] | None = None,
    unit: str = "",
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Create / update / compare / auto-lower a multi-key count baseline.

    Returns a process exit code (0 ok, 1 regression).
    ``out`` / ``err`` default to ``None`` and resolve at call time so tests can
    inject ``io.StringIO`` (defaults bound at def-time shadow ``sys.stdout``).

    Before ANY write, the committed baseline is probed against the upstream
    ceiling (see ``resolve_upstream_ceiling``). The probed value differs per
    path, and each choice is what makes that path un-launderable:

    - create (no baseline file) and ``--update-baseline`` probe ``current``,
      because ``current`` is what is about to be written;
    - the normal compare probes ``min(baseline, current)``, so an auto-lower
      cannot mask a committed raise and a failed probe never writes.
    """
    out_stream = sys.stdout if out is None else out
    err_stream = sys.stderr if err is None else err
    label = format_key or (lambda key: key)
    unit_suffix = f" {unit}" if unit else ""

    baseline = read_baseline(baseline_file)
    writes_current = baseline is None or update_baseline
    probe = dict(current) if writes_current else {key: min(baseline.get(key, 0), current[key]) for key in keys}
    ceiling = resolve_upstream_ceiling(
        repo_root=repo_root if repo_root is not None else baseline_file.resolve().parent,
        baseline_name=baseline_file.name,
        keys=keys,
        parse=parse_upstream if parse_upstream is not None else json.loads,
        count_upstream=count_upstream,
    )
    if (
        check_against_ceiling(
            keys=keys,
            probe=probe,
            ceiling=ceiling,
            baseline_name=baseline_file.name,
            format_key=label,
            err=err_stream,
        )
        != 0
    ):
        return 1

    if baseline is None:
        print(
            f"No baseline found. Creating {baseline_file.name}: {format_counts(current, keys)}",
            file=out_stream,
        )
        write_baseline(baseline_file, current)
        return 0

    if update_baseline:
        print(
            f"Updating baseline: {format_counts(baseline, keys)} -> {format_counts(current, keys)}",
            file=out_stream,
        )
        write_baseline(baseline_file, current)
        return 0

    failed = False
    for key in keys:
        base = baseline.get(key, 0)
        cur = current[key]
        display = label(key)
        if cur > base:
            print(
                f"  {display}: {cur}{unit_suffix} (+{cur - base} NEW vs baseline {base})",
                file=err_stream,
            )
            failed = True
        elif cur < base:
            print(
                f"  {display}: {cur}{unit_suffix} (-{base - cur} fixed vs baseline {base})",
                file=out_stream,
            )
        else:
            print(f"  {display}: {cur}{unit_suffix} (unchanged)", file=out_stream)

    if failed:
        print("", file=err_stream)
        print(increase_header, file=err_stream)
        for hint in increase_hints:
            print(hint, file=err_stream)
        return 1

    normalized_baseline = {key: baseline.get(key, 0) for key in keys}
    if dict(current) != normalized_baseline:
        print(f"Automatically updating {baseline_file.name}...", file=out_stream)
        write_baseline(baseline_file, current)

    return 0
