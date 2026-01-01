#!/usr/bin/env python3
"""Check for broken relative links in markdown documentation files.

This script validates that all relative links in markdown files point to existing files.
It checks both markdown links [text](path) and anchor links [text](path#anchor).

Usage:
    python .pre-commit-hooks/check_docs_links.py [--fix]

Exit codes:
    0: All links are valid
    1: Broken links found
"""

import re
import sys
from pathlib import Path

# Regex to match markdown links: [text](path) or [text](path#anchor)
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

# Skip external links
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "#")


def extract_links_from_file(filepath: Path) -> list[tuple[int, str, str]]:
    """Extract all links from a markdown file.

    Returns list of (line_number, link_text, link_path).
    """
    links = []
    try:
        content = filepath.read_text(encoding="utf-8")
        for line_num, line in enumerate(content.splitlines(), start=1):
            for match in MARKDOWN_LINK_PATTERN.finditer(line):
                link_text = match.group(1)
                link_path = match.group(2)
                links.append((line_num, link_text, link_path))
    except Exception as e:
        print(f"Error reading {filepath}: {e}", file=sys.stderr)
    return links


def resolve_link(base_file: Path, link_path: str) -> Path | None:
    """Resolve a relative link path from a base file location.

    Returns the resolved Path or None if it's an external/anchor-only link.
    """
    # Skip external links and anchor-only links
    if any(link_path.startswith(prefix) for prefix in EXTERNAL_PREFIXES):
        return None

    # Remove anchor from path
    path_without_anchor = link_path.split("#")[0]
    if not path_without_anchor:
        return None  # Anchor-only link

    # Resolve relative to the directory containing the file
    base_dir = base_file.parent
    resolved = (base_dir / path_without_anchor).resolve()

    return resolved


def check_link_exists(resolved_path: Path) -> bool:
    """Check if a resolved path exists (file or directory)."""
    # For directory links, check if directory exists or if README.md exists
    if resolved_path.is_dir():
        return True
    if resolved_path.exists():
        return True
    # If path ends with /, check directory
    if str(resolved_path).endswith("/"):
        return resolved_path.is_dir()
    return False


def check_anchor_exists(filepath: Path, anchor: str) -> bool:
    """Check if an anchor (heading) exists in a markdown file.

    Anchors are generated from headings by:
    - Converting to lowercase
    - Replacing spaces with hyphens
    - Removing special characters
    """
    if not anchor or not filepath.exists():
        return True  # No anchor to check or file doesn't exist (will be caught elsewhere)

    try:
        content = filepath.read_text(encoding="utf-8")
        # Find all headings
        heading_pattern = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
        for match in heading_pattern.finditer(content):
            heading_text = match.group(1).strip()
            # Convert heading to anchor format
            generated_anchor = heading_text.lower()
            generated_anchor = re.sub(r"[^\w\s-]", "", generated_anchor)
            generated_anchor = re.sub(r"\s+", "-", generated_anchor)
            generated_anchor = generated_anchor.strip("-")

            if generated_anchor == anchor:
                return True
        return False
    except Exception:
        return True  # Don't fail on read errors


def main() -> int:
    """Main entry point."""
    docs_dir = Path("docs")
    broken_links: list[tuple[Path, int, str, str, str]] = []

    # Collect markdown files to check
    md_files: list[Path] = []

    # Check all root-level markdown files
    md_files.extend(Path(".").glob("*.md"))

    # Find all markdown files in docs/
    if docs_dir.exists():
        md_files.extend(docs_dir.rglob("*.md"))

    if not md_files:
        print("No markdown files found, skipping link check")
        return 0

    for md_file in md_files:
        links = extract_links_from_file(md_file)

        for line_num, link_text, link_path in links:
            resolved = resolve_link(md_file, link_path)
            if resolved is None:
                continue  # External or anchor-only link

            if not check_link_exists(resolved):
                broken_links.append((md_file, line_num, link_text, link_path, "file not found"))
                continue

            # Check anchor if present
            if "#" in link_path:
                anchor = link_path.split("#")[1]
                path_without_anchor = link_path.split("#")[0]

                # Resolve the file for anchor checking
                if path_without_anchor:
                    anchor_file = resolve_link(md_file, path_without_anchor)
                    if anchor_file and anchor_file.is_file():
                        if not check_anchor_exists(anchor_file, anchor):
                            broken_links.append(
                                (md_file, line_num, link_text, link_path, f"anchor #{anchor} not found")
                            )

    if broken_links:
        print("Broken links found in documentation:")
        print()
        for filepath, line_num, link_text, link_path, reason in broken_links:
            print(f"  {filepath}:{line_num}")
            print(f"    [{link_text}]({link_path})")
            print(f"    Reason: {reason}")
            print()
        print(f"Total: {len(broken_links)} broken link(s)")
        return 1

    print("All documentation links are valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
