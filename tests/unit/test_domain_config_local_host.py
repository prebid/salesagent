"""``is_local_host`` — the one local-dev-host predicate.

Re-homed out of ``test_ssrf_url_validator.py``: #1802 migrated address policy
onto the outbound egress seam and deleted
``src.core.security.url_validator``, emptying that file of its subject. This
predicate is a different question (can this host serve https, i.e. is it a
local dev host) with a different home (``src.core.domain_config``), so it gets
its own module rather than riding along in a file about something else.
"""

import pytest

from src.core.domain_config import is_local_host


class TestIsLocalHost:
    """is_local_host distinguishes real local dev hosts from public hosts.

    The single predicate behind two call sites — the A2A agent card's scheme
    choice (``src/app.py``) and the TMP seller-agent URL resolver
    (``src/services/tmp_provider_sync.py``). They forked on ``*.localhost``
    before it existed, so a per-tenant dev host was public to one and local to
    the other (#1197 review). Substring-style near misses are covered
    explicitly: ``my-localhost-mirror.example.com`` and ``localhost.evil.com``
    must both be public.
    """

    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "localhost:8001",
            "tenant.localhost",
            "tenant.sales-agent.localhost:8001",
            "LOCALHOST",
            "127.0.0.1",
            "127.0.0.1:8000",
        ],
    )
    def test_local_hosts_return_true(self, host):
        assert is_local_host(host) is True

    @pytest.mark.parametrize(
        "host",
        [
            "tenant.salesagent.example.com",
            "my-localhost-mirror.example.com",
            "example.com",
            "localhost.evil.com",
            "127.0.0.1.evil.com",
        ],
    )
    def test_public_hosts_return_false(self, host):
        assert is_local_host(host) is False
