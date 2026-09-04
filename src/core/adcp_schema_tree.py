"""Read the AdCP JSON-Schema tree the installed ``adcp`` SDK ships for the pinned spec.

The SDK bundles the spec's own schemas under ``adcp/_schemas/<major.minor>/``
alongside its generated models. Reading THAT tree — not a hand-maintained list,
not a separately vendored copy — is how production code grounds a protocol
vocabulary in the pinned spec: the pin in ``pyproject.toml`` moves the models and
the schemas together, so a value set derived here cannot drift from the spec the
SDK claims to implement (see docs/adcp-spec-version.md).

Where the SDK's generated models and the pinned schemas disagree, the schema is
the contract and the model is derived from it (CLAUDE.md § Spec-Grounding Gate),
which is why a vocabulary read must be able to reach the schema directly.

This module is the read side only: no validation, no ``$ref`` resolution across
files (a caller that follows a ``$ref`` passes the resolved relative path back
in). ``tests/helpers/pinned_schema.py`` builds the full test-side validator on
top of :func:`schema_root` so there is exactly ONE place that knows the tree's
location.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any


class AdCPSchemaTreeError(Exception):
    """The pinned schema tree, or a schema within it, could not be read.

    A hard failure, never a silent fallback: a vocabulary derived from a schema
    that failed to load would be silently empty, and "no known asset types" is
    indistinguishable from "every asset type is unknown".
    """


def schema_root() -> Path:
    """The installed adcp SDK's schema tree for the pinned spec version.

    The SDK stores schemas under ``adcp/_schemas/<major.minor>/`` (e.g. the
    3.1.1 spec lives in ``_schemas/3.1/``; its ``index.json`` carries the full
    ``adcp_version``). Resolved at call time, so the version the installed SDK
    reports is always the version read.
    """
    import adcp

    spec_version = adcp.get_adcp_spec_version()
    major_minor = ".".join(spec_version.split(".")[:2])
    root = Path(adcp.__file__).parent / "_schemas" / major_minor
    if not root.is_dir():
        raise AdCPSchemaTreeError(
            f"Installed adcp SDK (spec {spec_version}) has no schema tree at {root} — "
            "the SDK layout changed; update schema_root()."
        )
    return root


def _resolve(ref: str) -> Path:
    """Resolve a root-relative schema ref (``core/assets/asset-union.json``)."""
    root = schema_root()
    path = (root / ref.split("#", 1)[0]).resolve()
    if not path.is_relative_to(root.resolve()):
        raise AdCPSchemaTreeError(f"Schema ref {ref!r} escapes the pinned SDK schema tree: {path}")
    if not path.is_file():
        raise AdCPSchemaTreeError(f"Pinned schema not found: {ref} -> {path}")
    return path


@cache
def load_schema(ref: str) -> dict[str, Any]:
    """Load one schema by its path relative to the version root.

    ``$ref``s in the returned dict are left exactly as the spec wrote them
    (relative to the referring file's own directory) — see
    :func:`sibling_ref` for turning one back into a ref this function accepts.
    """
    path = _resolve(ref)
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AdCPSchemaTreeError(f"Pinned schema {ref} at {path} could not be read: {exc}") from exc
    if not isinstance(loaded, dict):
        raise AdCPSchemaTreeError(f"Pinned schema {ref} at {path} is not a JSON object")
    return loaded


def sibling_ref(ref: str, target: str) -> str:
    """Turn a ``$ref`` found inside *ref* into a root-relative ref.

    Relative ``$ref``s resolve against the referring file's directory, so
    ``sibling_ref("core/assets/asset-union.json", "image-asset.json")`` is
    ``"core/assets/image-asset.json"``.
    """
    root = schema_root()
    resolved = (root / ref).parent / target.split("#", 1)[0]
    return str(resolved.resolve().relative_to(root.resolve()))
