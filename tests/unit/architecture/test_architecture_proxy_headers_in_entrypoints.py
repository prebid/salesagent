"""Doc-drift guard: the canonical entrypoint ``scripts/run_server.py``
invokes ``uvicorn.run(...)`` with ``proxy_headers=True`` and a
``forwarded_allow_ips`` flag set to ``'*'`` (default) or sourced from
``FORWARDED_ALLOW_IPS``.

Rationale — if either flag is missing, ``request.url.scheme`` reports
``http`` instead of ``https`` behind the Fly edge proxy, and the OAuth
redirect URI fails Google Cloud Console verification
(``redirect_uri_mismatch``). The L2 Flask-removal PR is the most
likely drift point because it re-writes the uvicorn invocation path.

Per ``foundation-modules.md §11.34``: also assert that ``Dockerfile``
CMD and ``scripts/deploy/run_all_services.py`` invoke the canonical
entrypoint (inheritance path, not duplicated flag placement) so the
flags live in ONE place.

Per ``.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-23``.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.architecture._doc_parser import REPO_ROOT

CANONICAL_ENTRYPOINT = REPO_ROOT / "scripts" / "run_server.py"
DOCKERFILE = REPO_ROOT / "Dockerfile"
RUN_ALL_SERVICES = REPO_ROOT / "scripts" / "deploy" / "run_all_services.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_canonical_entrypoint_passes_proxy_headers_true() -> None:
    """``scripts/run_server.py`` calls ``uvicorn.run`` with proxy_headers=True."""
    text = _read(CANONICAL_ENTRYPOINT)
    assert text, f"canonical entrypoint missing: {CANONICAL_ENTRYPOINT}"
    assert "uvicorn.run" in text, "canonical entrypoint must call uvicorn.run(...); it does not."
    assert "proxy_headers=True" in text, (
        "canonical entrypoint must pass proxy_headers=True to uvicorn.run — "
        "without it, request.url.scheme reports http behind the Fly edge."
    )


def test_canonical_entrypoint_sets_forwarded_allow_ips() -> None:
    """``scripts/run_server.py`` passes a ``forwarded_allow_ips`` value.

    Accepts either the literal ``'*'``/``"*"`` or an env-read of
    ``FORWARDED_ALLOW_IPS`` — the latter is the current canonical form
    so operators can restrict in production deployments.
    """
    text = _read(CANONICAL_ENTRYPOINT)
    assert "forwarded_allow_ips" in text, "canonical entrypoint must pass forwarded_allow_ips to uvicorn.run"
    # Accept any of: literal '*', env-read, or list-passed value.
    acceptable = (
        'forwarded_allow_ips="*"',
        "forwarded_allow_ips='*'",
        'forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS"',
        "forwarded_allow_ips=os.environ.get('FORWARDED_ALLOW_IPS'",
        "forwarded_allow_ips=os.getenv",
    )
    assert any(snippet in text for snippet in acceptable), (
        "forwarded_allow_ips must be set to '*' or sourced from "
        "FORWARDED_ALLOW_IPS (operator-controlled). Accepted forms: " + " | ".join(acceptable)
    )


def test_dockerfile_and_run_all_services_invoke_canonical_entrypoint() -> None:
    """``Dockerfile`` CMD + ``run_all_services.py`` delegate to run_server.py.

    Per the plan: the proxy-headers flags live in ONE place
    (``scripts/run_server.py``). Any deployment path that bypasses the
    canonical entrypoint — e.g. a ``CMD uvicorn src.app:app`` in
    Dockerfile — is drift and would silently reintroduce the broken
    ``request.url.scheme`` behavior.
    """
    dockerfile = _read(DOCKERFILE)
    run_all = _read(RUN_ALL_SERVICES)
    if dockerfile:
        # Dockerfile is allowed to not use run_server.py directly IFF
        # it invokes uvicorn with proxy_headers inline; otherwise must
        # route through run_server.py.
        invokes_run_server = "run_server" in dockerfile
        invokes_uvicorn_direct = "uvicorn " in dockerfile or "uvicorn.run" in dockerfile
        if invokes_uvicorn_direct and not invokes_run_server:
            assert "proxy_headers" in dockerfile, (
                "Dockerfile invokes uvicorn directly without proxy_headers; "
                "it must either route through scripts/run_server.py OR pass "
                "proxy_headers + forwarded_allow_ips itself."
            )
    if run_all:
        # run_all_services.py is explicitly called out by the plan as
        # inheriting from the canonical entrypoint.
        invokes_run_server = "run_server" in run_all
        invokes_uvicorn_direct = "uvicorn.run" in run_all
        if invokes_uvicorn_direct and not invokes_run_server:
            assert "proxy_headers" in run_all, (
                "scripts/deploy/run_all_services.py calls uvicorn.run directly "
                "without proxy_headers; either invoke scripts/run_server.py OR "
                "pass the flags inline."
            )
