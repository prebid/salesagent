"""Shared helpers for mutating the ci-test tenant's adapter config in e2e tests.

The adapter_config row is SHARED tenant state across the whole e2e session.
Any test that switches to manual approval (``manual=True``) MUST restore
``manual=False`` in a finally block: leaving manual approval on leaks into
every later e2e test (pytest-randomly ordering), turning their creates into
spec-3.1.1 submitted envelopes with no media_buy_id.
"""


def set_mock_approval(live_server: dict, *, manual: bool) -> None:
    """Set ci-test's mock adapter approval mode (SHARED tenant state).

    Thin domain-specific alias for :func:`tests.e2e.utils.set_live_adapter_behavior`
    — the sole e2e adapter-config mutator (which itself delegates to the one
    factory-level home, ``tests.factories.core.set_adapter_test_behavior``).
    Fails loud on a missing tenant or DB error; never print-and-continue.
    """
    from tests.e2e.utils import set_live_adapter_behavior

    set_live_adapter_behavior(live_server, manual_approval_required=manual)
