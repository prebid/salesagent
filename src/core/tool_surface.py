"""Configurable advertised tool surface.

An AdCP agent is graded on the tools it advertises: the conformance runner
marks a storyboard scenario ``not_applicable`` when a tool listed in its
``required_tools`` is not advertised, and ``not_applicable`` steps do not count
as failures. This module lets a deployment advertise a subset of the tools it
implements — the handlers stay registered and callable internally; only the
advertised MCP tool list and the A2A AgentCard skills are narrowed.

Configured via the ``ADCP_UNADVERTISED_TOOLS`` environment variable
(comma-separated tool names). Empty/unset by default → every tool is
advertised, so default deployments are unaffected.
"""

from __future__ import annotations

import os

_ENV_VAR = "ADCP_UNADVERTISED_TOOLS"


def unadvertised_tools() -> frozenset[str]:
    """Tool names withheld from the advertised surface for this deployment.

    Empty by default; set ``ADCP_UNADVERTISED_TOOLS`` to a comma-separated list
    of tool names to advertise a narrower surface.
    """
    raw = os.environ.get(_ENV_VAR, "")
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


def is_advertised(tool_name: str, withheld: frozenset[str] | None = None) -> bool:
    """Whether ``tool_name`` should be advertised, given the withheld set.

    Passing ``withheld`` explicitly avoids re-reading the environment in tight
    loops and keeps the decision pure/testable.
    """
    if withheld is None:
        withheld = unadvertised_tools()
    return tool_name not in withheld


def advertised_skills(skills: list) -> list:
    """Filter A2A AgentSkill objects to the advertised surface (matched by ``.id``)."""
    withheld = unadvertised_tools()
    return [skill for skill in skills if getattr(skill, "id", None) not in withheld]
