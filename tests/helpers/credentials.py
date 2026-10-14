"""The one producer of the credential headers a test presents to this seller.

WHY HERE AND NOT IN tests/harness/. Building a request credential is what every suite
does -- unit, integration, admin, e2e, BDD -- and the harness env is one consumer among
many, not the owner. A second reason keeps it out of that package:
``test_guards_no_adhoc_testclient_bypass`` reads ANY ``tests.harness`` import as "this
test has the harness available", so a test that imports one pure function from there is
required to route its REST calls through the harness too.

Enforced by ``.ast-grep/rules/test-credential-header-single-producer.yml``, which
``make quality-ci`` runs over ``tests/``; graded by
``tests/unit/test_ast_grep_credential_header_ban.py``.
"""

from __future__ import annotations


def credential_headers(
    *,
    token: str | None = None,
    tenant: str | None = None,
    host: str | None = None,
    host_resolves_nothing: bool = False,
) -> dict[str, str]:
    """THE producer: the headers a test presents to this seller, from plain values.

    Every dispatcher, fixture, builder and per-test literal in ``tests/`` builds its
    credential headers here, so a change in what production reads off the wire is one
    edit. Enforced by ``.ast-grep/rules/test-credential-header-single-producer.yml``,
    which ``make quality-ci`` runs; graded by
    ``tests/unit/test_ast_grep_credential_header_ban.py``.

    Production reads the credential from ``Authorization: Bearer`` on every transport,
    which is why one function serves them all. The ``x-adcp-auth`` alias this used to
    send is not read: pinned 3.1.1 ``L2/authentication.mdx:71`` says the credential MUST
    ride ``Authorization`` and sellers MUST NOT require non-canonical aliases. A caller
    sending the alias presents nothing the resolver can see, which surfaces as
    AUTH_MISSING rather than as a header error.

    Each header is OMITTED when its value is absent, never sent empty: ``token=None``
    dispatches unauthenticated, so the resolver returns the real AUTH_MISSING rejection
    instead of one for a malformed credential.

    ``host_resolves_nothing=True`` is the ONE case that sends BOTH, and it says what it
    requires: the ``Host`` names a seller this deployment serves for nobody, so the tenant
    header is the only name that can resolve. That is what frees the Host to carry something
    else, which is the only shape that tells a stored read from an echo of the request --
    every other request sends the stored host AS the Host, so an echo and a read produce the
    same string and both pass. Without the flag the Host wins alone, deliberately: a Host
    that resolved nothing must not be silently rescued by a header the scenario did not mean
    to lean on.

    ``tenant`` is the ``x-adcp-tenant`` value: the tenant_id on every leg, taken verbatim
    as the literal id by ``_detect_tenant`` (``src/core/resolved_identity.py``). No
    subdomain is involved — a request names its tenant by ``Host`` or by this header, and
    by nothing else. ``BaseTestEnv.credential`` (``tests/harness/_base.py``) is the
    harness's call of this function; a test outside the harness calls it directly.
    """
    headers: dict[str, str] = {}
    if token is not None:
        # ast-grep-ignore: test-credential-header-single-producer - this IS the one producer
        headers["Authorization"] = f"Bearer {token}"
    if host:
        # THE DEFAULT WAY a request names its seller, because it is how a DEPLOYMENT does
        # it: production nginx derives ``x-adcp-tenant`` from the host
        # (``nginx-multi-tenant.conf``: ``map $host $tenant``) and no caller sends it. A
        # suite where every caller asserted the header instead graded a path no deployment
        # runs, and left the virtual_host branches unexecuted — which is how a tenant host
        # reached ``publisher_properties[].publisher_domain`` carrying a port, failing every
        # product of that tenant with nothing to catch it.
        #
        # Sent INSTEAD of the header, not alongside it: ``_detect_tenant`` tries the Host
        # first and the header second, so sending both would let the header silently rescue
        # a Host that resolved nothing, and the suite would go on believing it had graded
        # host resolution.
        headers["Host"] = host
    if tenant and (host_resolves_nothing or not host):
        headers["x-adcp-tenant"] = tenant
    return headers
