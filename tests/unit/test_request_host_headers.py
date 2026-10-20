"""The host a request is for, read in one place.

``requested_host`` and ``hostname_of`` are pure functions over their inputs, which is why
they are graded here rather than through a transport.
"""

from src.core.http_utils import hostname_of, requested_host


class TestRequestedHost:
    """``requested_host`` is the ``Host``."""

    def test_reads_the_host(self):
        assert requested_host({"Host": "acme.example.com"}) == "acme.example.com"

    def test_reads_a_lowercase_host(self):
        assert requested_host({"host": "acme.example.com"}) == "acme.example.com"

    def test_no_host_is_none(self):
        assert requested_host({"User-Agent": "curl/8"}) is None

    def test_no_headers_at_all_is_none(self):
        assert requested_host({}) is None


class TestHostnameOf:
    """``hostname_of`` is *host* without its port."""

    def test_drops_the_port(self):
        assert hostname_of("storyboard.adcp.test:8443") == "storyboard.adcp.test"

    def test_leaves_a_portless_host_alone(self):
        assert hostname_of("storyboard.adcp.test") == "storyboard.adcp.test"

    def test_empty_host_stays_empty(self):
        assert hostname_of("") == ""

    def test_the_answer_is_lowercase(self):
        """A contract both callers depend on, pinned here because neither can state it.

        ``_same_host`` compares this against a case-folded ``virtual_host`` column, and
        ``Tenant.virtual_host_name`` stores it as the key the adagents.json route looks up
        properties by, whose ``publisher_domain`` pattern (``^[a-z0-9]...``) admits no
        uppercase. A host stored with a capital matched no spelling at all while only one of
        the two sides folded (PR #2191).
        """
        assert hostname_of("Probe-Case.AdCP.test:8443") == "probe-case.adcp.test"
        assert hostname_of("PROBE-CASE.ADCP.TEST") == "probe-case.adcp.test"
