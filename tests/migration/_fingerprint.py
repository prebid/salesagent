"""L0-21 golden-fingerprint capture helper for L1+ Flask→FastAPI ports.

Each Flask blueprint that lands at L1+ gets a Red/Green pair: the Red
test captures a fingerprint of the Flask response (using this helper)
and saves it as a baseline; the Green implementation of the FastAPI
router must produce a fingerprint that :func:`assert_matches` accepts.

This module is COMPLEMENTARY to L0-02's bespoke fingerprint fixtures
under ``tests/integration/fixtures/rest_wire/*.json`` and
``tests/migration/fixtures/{openapi-byte-hash.sha256,
mcp-tool-inventory.json, a2a-agent-card.json}``. L0-02 captured 5
specific AdCP wire shapes with a custom one-off schema; L0-21 ships a
general-purpose helper with configurable strictness that L1+ router
ports will use as their comparison primitive. Where the AdCP wire
shapes overlap, L0-21 baselines MAY reference L0-02's fixtures rather
than duplicate bytes (see ``baselines/README.md`` for the map).

Design decisions:

* Fingerprints are byte-stable **canonical JSON** (sorted keys, no
  whitespace) hashed with SHA-256. Non-JSON bodies are hashed on their
  raw bytes with the same algorithm.
* ``content_type`` is stripped to the media type only — charset and
  boundary parameters drift between Flask and FastAPI defaults and
  would break equality on a pure pass-through port.
* ``headers_of_interest`` retains only ``cache-control`` and any
  ``x-*`` header. ``Date``, ``Server``, and ``Content-Length`` drift
  per process or response framing and would make byte equality
  impossible to maintain.
* ``body_schema`` reports the top-level JSON key set (for objects),
  array length (for arrays), or a sentinel (for HTML/text bodies).
  This is what the ``"schema"`` strictness level compares against.

The helper intentionally does NOT attempt to reconstruct semantic
diffing (e.g. "this field is a date, skip it"). Consumers pick the
right strictness for their endpoint: ``"byte"`` for deterministic
bodies, ``"schema"`` for objects with dynamic fields, ``"status_only"``
for endpoints where only the status code matters (pings, redirects).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_BASELINES_DIR: Path = Path(__file__).parent / "fingerprints" / "baselines"

_VALID_STRICTNESS = ("byte", "schema", "status_only")


@dataclass
class ResponseFingerprint:
    """A stable, comparable fingerprint of an HTTP response.

    Fields:
        status_code: HTTP status code.
        content_type: Media type only (charset / boundary stripped).
        body_sha256: Lowercase hex SHA-256 over canonical JSON bytes
            (for JSON) or raw body bytes (for non-JSON).
        headers_of_interest: ``cache-control`` and any ``x-*`` headers.
            Date/Server/Content-Length intentionally excluded.
        body_schema: Top-level structure descriptor (see module docstring).
    """

    status_code: int
    content_type: str
    body_sha256: str
    headers_of_interest: dict[str, str] = field(default_factory=dict)
    body_schema: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def _canonical_json_bytes(value: Any) -> bytes:
    """Serialize ``value`` to bytes with sorted keys and no whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _compute_body_schema(body: Any, content_type: str) -> dict[str, Any]:
    """Return a structure descriptor for the parsed body."""
    if isinstance(body, dict):
        return {"__type__": "object", "keys": sorted(body.keys())}
    if isinstance(body, list):
        return {"__type__": "array", "length": len(body)}
    if content_type.startswith("text/html"):
        return {"__type__": "html"}
    if body is None:
        return {"__type__": "none"}
    return {"__type__": "scalar", "python_type": type(body).__name__}


def _extract_headers_of_interest(headers: Any) -> dict[str, str]:
    """Pick the stable subset of response headers.

    Keeps ``cache-control`` and any ``x-*`` header (lowercased). Drops
    Date/Server/Content-Length and anything else that drifts per
    request.
    """
    keep: dict[str, str] = {}
    for raw_key, value in headers.items():
        key = raw_key.lower()
        if key == "cache-control" or key.startswith("x-"):
            keep[key] = value
    return dict(sorted(keep.items()))


def capture_fingerprint(client: Any, method: str, path: str, **kwargs: Any) -> ResponseFingerprint:
    """Hit an endpoint via a TestClient and compute a fingerprint.

    Args:
        client: A Starlette/FastAPI ``TestClient`` (or any object with
            a ``.request(method, path, **kwargs)`` method returning a
            response with ``.status_code``, ``.headers``, and ``.content``).
        method: HTTP method (``"GET"``, ``"POST"``, ...).
        path: URL path relative to the client's base URL.
        **kwargs: Forwarded to the underlying ``.request()`` call
            (``json=``, ``params=``, ``headers=``, etc).

    Returns:
        A :class:`ResponseFingerprint` suitable for comparison or
        persistence via :func:`save_fingerprint`.
    """
    response = client.request(method, path, **kwargs)
    content_type = response.headers.get("content-type", "").split(";")[0].strip()

    parsed_body: Any
    body_bytes: bytes
    try:
        parsed_body = response.json()
        # For JSON bodies, hash the canonicalized form so cosmetic
        # whitespace / key-order drift does not break equality.
        body_bytes = _canonical_json_bytes(parsed_body)
    except (json.JSONDecodeError, ValueError):
        parsed_body = None
        body_bytes = response.content or b""

    return ResponseFingerprint(
        status_code=response.status_code,
        content_type=content_type,
        body_sha256=hashlib.sha256(body_bytes).hexdigest(),
        headers_of_interest=_extract_headers_of_interest(response.headers),
        body_schema=_compute_body_schema(parsed_body, content_type),
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_fingerprint(name: str, fp: ResponseFingerprint) -> None:
    """Serialize ``fp`` to ``baselines/<name>.json``.

    Uses sorted keys and 2-space indentation for git-review friendly
    diffs. Callers are responsible for committing the resulting file
    alongside the consumer test.
    """
    path = _BASELINES_DIR / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(fp)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_fingerprint(name: str) -> ResponseFingerprint:
    """Read a previously saved baseline back into a fingerprint instance."""
    path = _BASELINES_DIR / f"{name}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ResponseFingerprint(**payload)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _assert_shared_prefix(actual: ResponseFingerprint, expected: ResponseFingerprint) -> None:
    """Every strictness level requires status_code + content_type match."""
    assert (
        actual.status_code == expected.status_code
    ), f"status_code drift: {actual.status_code} != {expected.status_code}"
    assert (
        actual.content_type == expected.content_type
    ), f"content_type drift: {actual.content_type!r} != {expected.content_type!r}"


def assert_matches(
    actual: ResponseFingerprint,
    expected: ResponseFingerprint,
    strictness: str = "byte",
) -> None:
    """Compare ``actual`` against ``expected`` at the requested strictness.

    Args:
        actual: Fingerprint captured from the port under test.
        expected: Baseline fingerprint loaded from ``baselines/``.
        strictness: One of ``"byte"`` (body_sha256 must match),
            ``"schema"`` (body_schema keys/shape must match, body
            content may drift), or ``"status_only"`` (only status_code
            + content_type must match).

    Raises:
        AssertionError: On any drift at the requested strictness level.
        ValueError: If ``strictness`` is not one of the three accepted values.
    """
    if strictness not in _VALID_STRICTNESS:
        raise ValueError(f"strictness must be one of {_VALID_STRICTNESS!r}; got {strictness!r}")
    _assert_shared_prefix(actual, expected)
    if strictness == "status_only":
        return
    # Both "byte" and "schema" require the body schema to match.
    assert (
        actual.body_schema == expected.body_schema
    ), f"body_schema drift: {actual.body_schema!r} != {expected.body_schema!r}"
    if strictness == "byte":
        assert (
            actual.body_sha256 == expected.body_sha256
        ), f"body_sha256 drift: {actual.body_sha256} != {expected.body_sha256}"
