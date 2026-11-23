"""The admin app sends a Content-Security-Policy on every response.

The pages rely on inline scripts and handlers, so the policy cannot restrict script
sources yet; it takes away plugins, a ``<base>`` that re-points relative URLs, framing by
another site, form posts to another origin, and fetches to hosts the pages do not use.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.admin.app import (
    ADMIN_CONNECT_SOURCES,
    ADMIN_CONTENT_SECURITY_POLICY,
    UPLOADED_FILE_CONTENT_SECURITY_POLICY,
    create_app,
)
from tests.unit._architecture_helpers import repo_root


@pytest.fixture(scope="module")
def client():
    return create_app({"TESTING": True, "SECRET_KEY": "test-secret"}).test_client()


def test_every_admin_response_carries_the_policy_and_nosniff(client):
    response = client.get("/static/js/escape_html.js")
    assert response.headers["Content-Security-Policy"] == ADMIN_CONTENT_SECURITY_POLICY
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert set(ADMIN_CONTENT_SECURITY_POLICY.split("; ")) == {
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'self'",
        "form-action 'self'",
        "connect-src 'self' https://cloud.approximated.app",
    }


#: A literal absolute URL handed to something that opens a connection from the page.
_CONNECTION_TARGET = re.compile(
    r"""(?:\bfetch\(|\bnew\s+(?:EventSource|WebSocket)\(|\.open\(\s*['"][A-Z]+['"]\s*,|"""
    r"""\b(?:api_)?url\s*:)\s*[`'"]((?:https?|wss?)://[^/`'"$]+)"""
)


def connection_origins(src: str) -> set[str]:
    return set(_CONNECTION_TARGET.findall(src))


def test_every_origin_the_pages_connect_to_is_in_connect_src():
    """A page that fetches a host connect-src does not list breaks; list it in ADMIN_CONNECT_SOURCES."""
    root = repo_root()
    found = {
        (origin, path.relative_to(root).as_posix())
        for pattern in ("templates/**/*.html", "static/js/**/*.js")
        for path in root.glob(pattern)
        for origin in connection_origins(path.read_text(encoding="utf-8"))
    }
    assert {origin for origin, _ in found} <= set(ADMIN_CONNECT_SOURCES), sorted(found)
    assert set(ADMIN_CONNECT_SOURCES) <= {origin for origin, _ in found}, "drop a connect source nothing uses"


def test_an_uploaded_favicon_is_served_in_a_sandbox(client):
    response = client.get("/static/favicons/default/favicon.jpg")
    assert response.status_code == 200
    assert (
        response.headers["Content-Security-Policy"]
        == UPLOADED_FILE_CONTENT_SECURITY_POLICY
        == ("sandbox; default-src 'none'")
    )
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.fixture
def svg_on_disk():
    """An SVG favicon left on disk by an upload from before SVG was refused; removed afterwards."""
    import shutil
    import uuid

    from src.admin.blueprints.tenants import _get_favicon_upload_dir

    tenant_dir = Path(_get_favicon_upload_dir()) / f"test-{uuid.uuid4().hex}"
    tenant_dir.mkdir()
    (tenant_dir / "favicon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>')
    (tenant_dir / "favicon.png").write_bytes(bytes([0x89]) + b"PNG")
    yield tenant_dir.name
    shutil.rmtree(tenant_dir)


#: The same file, spelled so a check on the raw URL would miss it: the server decodes %2F
#: before routing, and send_from_directory resolves ./ and ../.
_SPELLINGS = [
    "/static/favicons/{t}/{f}",
    "/static/FAVICONS/{t}/{f}",
    "/static/.%2Ffavicons/{t}/{f}",
    "/static/%2E%2Ffavicons/{t}/{f}",
    "/static/a/..%2Ffavicons/{t}/{f}",
    "/static/./favicons/{t}/{f}",
]


@pytest.mark.parametrize("spelling", _SPELLINGS)
def test_an_svg_favicon_is_never_served_however_the_path_is_spelled(client, svg_on_disk, spelling):
    assert client.get(spelling.format(t=svg_on_disk, f="favicon.svg")).status_code == 404
    assert client.get(spelling.format(t=svg_on_disk, f="FAVICON.SVG")).status_code == 404


@pytest.mark.parametrize("spelling", _SPELLINGS)
def test_a_raster_favicon_is_sandboxed_however_the_path_is_spelled(client, svg_on_disk, spelling):
    response = client.get(spelling.format(t=svg_on_disk, f="favicon.png"))
    if response.status_code == 404:
        return  # a spelling this file system does not resolve serves nothing at all
    assert response.status_code == 200
    assert response.headers["Content-Security-Policy"] == UPLOADED_FILE_CONTENT_SECURITY_POLICY


def test_every_response_carries_a_referrer_policy(client):
    assert client.get("/static/js/escape_html.js").headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
