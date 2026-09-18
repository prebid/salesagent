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
