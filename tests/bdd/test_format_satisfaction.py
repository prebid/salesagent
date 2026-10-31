"""BDD scenario binding for canonical format satisfaction at create_media_buy.

Scenarios store a real dual-arm ``products`` row and send a real package selector, so what
they grade is whether this seller holds a buyer to the canonical declaration it published --
the question four separate gates asked of the DEPRECATED arm only, and so never asked at
all of a product declaring ``format_options[]``.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-FORMAT-SATISFACTION.feature")
