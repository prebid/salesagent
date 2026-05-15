"""Shared helper for "per-file ratchet" structural guards.

Both ``test_architecture_no_error_construction_in_impl`` (Pattern A: forbidden
Error(code=...) construction) and ``test_architecture_no_value_error_in_impl``
(forbidden ``raise ValueError``) use the same enforcement shape: scan
directories, count violation sites per file, compare against a per-file cap
dict, fail on overage or on unknown files.

This module extracts the shared scanner/asserter so the two guards don't
duplicate the loop (CLAUDE.md DRY invariant).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def assert_per_file_caps(
    *,
    cap_dict: dict[str, int],
    count_sites: Callable[[Path], list[int]],
    scan_dirs: list[Path],
    site_label: str,
    typed_raise_hint: str,
    rel: Callable[[Path], str] | None = None,
) -> None:
    """Fail if any scanned file exceeds its cap or appears unaccounted-for.

    Args:
        cap_dict: ``{relative_file_path: max_allowed_site_count}``. New files
            with sites that aren't in the dict fail immediately.
        count_sites: callable returning the line numbers of violation sites in
            a given file. Empty list means clean.
        scan_dirs: directories to walk (recursively, ``*.py``).
        site_label: human-readable name for the violation type, used in the
            error message (e.g., ``"Pattern A"`` or ``"raise ValueError"``).
        typed_raise_hint: short remediation hint shown when violations fire.
        rel: optional callable converting an absolute path to a repo-root-
            relative key (matching the cap_dict keys). Defaults to ``str(path)``
            for backward compatibility with CWD-anchored callers.
    """
    if rel is None:
        rel = str
    violations: list[str] = []
    unknown_files_with_sites: list[str] = []

    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for py in sorted(scan_dir.rglob("*.py")):
            key = rel(py)
            sites = count_sites(py)
            count = len(sites)
            if count == 0:
                continue
            cap = cap_dict.get(key)
            if cap is None:
                unknown_files_with_sites.append(f"  {key}: {count} {site_label} site(s) — new file, {typed_raise_hint}")
            elif count > cap:
                site_lines = ", ".join(str(s) for s in sites)
                violations.append(f"  {key}: {count} sites at lines [{site_lines}] exceeds cap of {cap}")

    msg_parts = []
    if violations:
        msg_parts.append(
            f"Files exceed {site_label} allowlist cap (caps only SHRINK — {typed_raise_hint}):\n"
            + "\n".join(violations)
        )
    if unknown_files_with_sites:
        msg_parts.append(
            f"New files contain {site_label} sites ({typed_raise_hint}):\n" + "\n".join(unknown_files_with_sites)
        )
    assert not msg_parts, "\n\n".join(msg_parts)


def assert_capped_files_still_exist(
    cap_dict: dict[str, int],
    cap_dict_name: str,
    repo_root: Path | None = None,
) -> None:
    """Fail if cap_dict has entries pointing to files that no longer exist."""
    base = repo_root or Path.cwd()
    stale = [f for f in cap_dict if not (base / f).exists()]
    assert not stale, f"{cap_dict_name} entries point to non-existent files (remove them):\n" + "\n".join(
        f"  {f}" for f in stale
    )


def assert_caps_only_shrink(
    cap_dict: dict[str, int],
    count_sites: Callable[[Path], list[int]],
    repo_root: Path | None = None,
) -> None:
    """Fail if any cap exceeds the actual count — caps must track reality.

    A cap that lags reality weakens the ratchet (new violations can sneak in
    while the cap is artificially high). When a cleanup commit lowers actual
    counts, the cap must drop in the same commit.
    """
    base = repo_root or Path.cwd()
    lagging: list[str] = []
    for rel, cap in cap_dict.items():
        path = base / rel
        if not path.exists():
            continue
        actual = len(count_sites(path))
        if actual < cap:
            lagging.append(f"  {rel}: actual={actual}, cap={cap} — lower cap to {actual}")
    assert not lagging, "Caps must track actual count (only shrink); lower the following caps:\n" + "\n".join(lagging)
