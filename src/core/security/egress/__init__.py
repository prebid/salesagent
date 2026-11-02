"""The egress policy package: one address predicate, shared by every verdict
that decides whether this application dials a URL, and one retry state
machine shared by every attempt loop.

``policy.py`` holds the address predicate, ``attempts.py`` the retry state
machine, ``response.py`` the closed ``OutboundResult`` shape and
``destination.py`` the typed notion of where a URL comes from. The module map
and what each one owns is ``docs/design/egress-sdk-boundary.md``; the rule that
nothing outside this package validates a URL is
``docs/security/outbound-egress.md``.
"""
