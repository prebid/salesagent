#!/usr/bin/env python3
"""Generate .agent-index/ — pre-calculated symbol index for AI agents.

Uses stubgen to produce type-resolved .pyi stubs (inheritance-flattened,
implementation-stripped) organized by function. Live queries (BDD steps,
pattern search) use ast-grep CLI instead of pre-computed manifests.

Usage:
  uv run python scripts/gen-agent-index.py          # full regeneration
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = ROOT / ".agent-index"

# ── Stubgen targets ────────────────────────────────────────────────
# Each entry: (output_path_in_index, [source_files_or_dirs])
# When multiple sources map to one output, stubs are concatenated.

STUBGEN_TARGETS: dict[str, list[str]] = {
    # "What can I call?" — domain logic
    "api/impl.pyi": [
        "src/core/tools/capabilities.py",
        "src/core/tools/products.py",
        "src/core/tools/properties.py",
        "src/core/tools/media_buy_create.py",
        "src/core/tools/media_buy_update.py",
        "src/core/tools/media_buy_list.py",
        "src/core/tools/media_buy_delivery.py",
        "src/core/tools/creative_formats.py",
        "src/core/tools/performance.py",
        "src/core/tools/signals.py",
        "src/core/tools/creatives/_sync.py",
        "src/core/tools/creatives/listing.py",
    ],
    # "What data shapes exist?" — schemas
    "schemas/core.pyi": ["src/core/schemas/_base.py"],
    "schemas/creative.pyi": ["src/core/schemas/creative.py"],
    "schemas/delivery.pyi": ["src/core/schemas/delivery.py"],
    "schemas/product.pyi": ["src/core/schemas/product.py"],
    # "What errors?" — exception hierarchy
    "errors.pyi": ["src/core/exceptions.py"],
    # "What test infra?" — harness
    "harness/base.pyi": ["tests/harness/_base.py"],
    "harness/transport.pyi": [
        "tests/harness/transport.py",
        "tests/harness/dispatchers.py",
    ],
    "harness/envs.pyi": [
        "tests/harness/creative_formats.py",
        "tests/harness/creative_list.py",
        "tests/harness/creative_sync.py",
        "tests/harness/delivery_poll.py",
        "tests/harness/delivery_webhook.py",
        "tests/harness/delivery_circuit_breaker.py",
        "tests/harness/product.py",
    ],
    # "How to build test data?" — factories
    "factories.pyi": [
        "tests/factories/__init__.py",
        "tests/factories/core.py",
        "tests/factories/principal.py",
        "tests/factories/product.py",
        "tests/factories/media_buy.py",
        "tests/factories/creative.py",
        "tests/factories/webhook.py",
        "tests/factories/metrics.py",
        "tests/factories/format.py",
    ],
    # "How does data persist?" — database
    "persistence/models.pyi": ["src/core/database/models.py"],
    "persistence/repositories.pyi": [
        "src/core/database/repositories/media_buy.py",
        "src/core/database/repositories/product.py",
        "src/core/database/repositories/delivery.py",
        "src/core/database/repositories/creative.py",
        "src/core/database/repositories/uow.py",
    ],
    # "How does auth work?"
    "auth.pyi": [
        "src/core/resolved_identity.py",
        "src/core/auth.py",
    ],
    # "What adapter interface?"
    "adapters.pyi": [
        "src/adapters/base.py",
        "src/adapters/__init__.py",
    ],
}

# ── Stubgen generation ─────────────────────────────────────────────


def _run_stubgen(source_files: list[str], tmp_dir: Path) -> dict[str, str]:
    """Run stubgen on source files, return {source_path: stub_content}."""
    existing = [f for f in source_files if (ROOT / f).exists()]
    if not existing:
        return {}

    # Use stubgen CLI directly (available via uv run)
    stubgen_bin = shutil.which("stubgen")
    if not stubgen_bin:
        # Fallback: try to find it in the venv
        venv_bin = ROOT / ".venv" / "bin" / "stubgen"
        if venv_bin.exists():
            stubgen_bin = str(venv_bin)
        else:
            print("  stubgen not found — skipping .pyi generation", file=sys.stderr)
            return {}

    cmd = [stubgen_bin, "-o", str(tmp_dir)] + [str(ROOT / f) for f in existing]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if result.returncode != 0:
        # stubgen may warn but still produce output — only fail on hard errors
        if not any(tmp_dir.rglob("*.pyi")):
            print(f"  stubgen failed: {result.stderr.strip()}", file=sys.stderr)
            return {}

    stubs: dict[str, str] = {}
    for src in existing:
        # stubgen mirrors the source path structure
        pyi_path = tmp_dir / src.replace(".py", ".pyi")
        if pyi_path.exists():
            stubs[src] = pyi_path.read_text()
    return stubs


def generate_stubs() -> int:
    """Generate all .pyi files in .agent-index/."""
    count = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        # Collect all unique source files
        all_sources: set[str] = set()
        for sources in STUBGEN_TARGETS.values():
            all_sources.update(sources)

        print(f"Running stubgen on {len(all_sources)} source files...")
        stubs = _run_stubgen(sorted(all_sources), tmp_dir)
        print(f"  Generated {len(stubs)} stubs")

        # Assemble into target files
        for output_path, sources in STUBGEN_TARGETS.items():
            parts: list[str] = []
            for src in sources:
                if src in stubs:
                    # Add a source comment header
                    parts.append(f"# --- {src} ---")
                    parts.append(stubs[src].rstrip())
                    parts.append("")

            if parts:
                out = INDEX_DIR / output_path
                out.parent.mkdir(parents=True, exist_ok=True)
                header = "# Auto-generated by gen-agent-index.py — do not edit\n"
                header += f"# Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}\n"
                header += f"# Sources: {', '.join(s for s in sources if s in stubs)}\n\n"
                out.write_text(header + "\n".join(parts) + "\n")
                count += 1
                full_content = header + "\n".join(parts) + "\n"
                print(f"  {output_path} ({full_content.count(chr(10))} lines)")

    return count


# ── INDEX.md generation ────────────────────────────────────────────

# Maps each output file to a one-line description of what it answers.
FILE_DESCRIPTIONS: dict[str, str] = {
    "api/impl.pyi": "All _impl function signatures (business logic entry points)",
    "schemas/core.pyi": "Request/response Pydantic models (AdCP-compliant)",
    "schemas/creative.pyi": "Creative-domain schemas",
    "schemas/delivery.pyi": "Delivery-domain schemas",
    "schemas/product.pyi": "Product-domain schemas",
    "errors.pyi": "AdCPError hierarchy — exception classes, status codes, error codes",
    "harness/base.pyi": "BaseTestEnv + IntegrationEnv interface (test harness base)",
    "harness/transport.pyi": "Transport enum, TransportResult, dispatcher classes",
    "harness/envs.pyi": "Domain-specific test env classes with methods",
    "factories.pyi": "factory_boy factories (ORM + Pydantic) and helpers",
    "persistence/models.pyi": "SQLAlchemy ORM model classes",
    "persistence/repositories.pyi": "Repository classes (data access layer)",
    "auth.pyi": "ResolvedIdentity, auth helpers",
    "adapters.pyi": "Adapter base class and registry",
}


def generate_index() -> None:
    """Generate INDEX.md — the table of contents for .agent-index/."""
    lines = [
        "# .agent-index",
        "",
        "Auto-generated symbol index. Read the file that answers your question.",
        "",
        "| File | What it answers |",
        "|------|----------------|",
    ]

    # List files that actually exist, in the order defined above
    for path, desc in FILE_DESCRIPTIONS.items():
        if (INDEX_DIR / path).exists():
            lines.append(f"| `{path}` | {desc} |")

    # Catch any generated files not in the description map
    for p in sorted(INDEX_DIR.rglob("*")):
        if p.is_file() and p.name != "INDEX.md":
            rel = str(p.relative_to(INDEX_DIR))
            if rel not in FILE_DESCRIPTIONS:
                lines.append(f"| `{rel}` | *(undocumented)* |")

    lines.append("")
    lines.append(f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    (INDEX_DIR / "INDEX.md").write_text("\n".join(lines) + "\n")
    print("  INDEX.md (table of contents)")


# ── Main ───────────────────────────────────────────────────────────


def main() -> None:
    # Clean previous index
    if INDEX_DIR.exists():
        shutil.rmtree(INDEX_DIR)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Generating .agent-index/ at {INDEX_DIR}")
    stub_count = generate_stubs()
    generate_index()
    print(f"Done: {stub_count} stub files")


if __name__ == "__main__":
    main()
