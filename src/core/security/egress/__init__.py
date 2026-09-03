"""The egress policy package: one address predicate, shared by every verdict
that decides whether this application dials a URL, and one retry state
machine shared by every attempt loop.

``policy.py`` (Epic A lane 1), ``attempts.py`` (lane 2), ``response.py`` (the
closed ``OutboundResult`` shape, lane 5) and ``destination.py`` (the typed
notion of WHERE a URL comes from, lane 6) exist today — see
``GH #1802
"""
