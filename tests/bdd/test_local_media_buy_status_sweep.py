"""BDD binding for the locally-added media buy status sweep.

Grades that one failing status write leaves sibling flips durable, that a dropped
connection persists nothing, and that the sweep summary's severity follows what
happened. The tick runs in this process. get_media_buys crosses the transport.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-media-buy-status-sweep.feature")
