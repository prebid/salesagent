"""Port helpers shared by the test suites.

Only the EPHEMERAL grab lives here. The other port code in the tree looks similar
and is deliberately not folded in, because it answers a different question:

* ``tests/e2e/conftest.py`` allocates from a randomized start within a fixed
  range, retrying, so concurrent stacks do not converge on the same lowest free
  port. That is a contention strategy, not an ephemeral grab.
* ``tests/unit/test_e2e_port_allocation.py::_bind_all_interfaces`` binds a
  SPECIFIC port to prove the allocator notices it is taken. Opposite direction.

Collapsing those into this would be the mistake DRY is usually invoked to
prevent — two things that resemble each other today being fused despite meaning
different things (salesagent-og9k.10, which called for extracting this one
"opportunistically").
"""

from __future__ import annotations

import socket


def free_port() -> int:
    """An ephemeral port the OS reports free, released before it is returned.

    Binds the ALL-INTERFACES address rather than loopback: a port already taken
    by another stack on ``0.0.0.0`` must count as taken, and a ``127.0.0.1``-only
    probe misses those.

    Inherently racy — the port can be claimed between this returning and the
    caller binding it. That is acceptable for a test fixture and is why the e2e
    stack, which cannot tolerate the race across concurrent runs, uses its own
    retrying allocator instead.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("", 0))
        probe.listen(1)
        return int(probe.getsockname()[1])
