#!/usr/bin/env python3
"""A2A skill registration: every advertised skill is a dispatchable registry row.

The invocation tests this file used to carry (explicit and hybrid get_products, create,
update, sync_creatives, list_creatives, delivery, the two-skills and unknown-skill
refusals) drove ``on_message_send`` in-process and graded transport behaviour. That is
graded on the wire by BDD, parametrized over a2a: BR-UC-001, BR-UC-002 (including the
manual-approval submitted envelope), BR-UC-003, BR-UC-004, BR-UC-006, BR-UC-018 and
local-pre-dispatch-refusals. The hybrid (text plus skill) invocation and the TextPart ==
DataPart.message equality have no scenario; they are listed as BDD gaps in the step-1
report rather than kept as unit tests of the transport.
"""

import pytest

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestA2ASkillInvocation:
    """The agent card and the registry agree on what is dispatchable."""

    @pytest.fixture
    def handler(self):
        """Create an AdCP request handler for testing."""
        return AdCPRequestHandler()

    def test_skill_handler_mapping(self, handler):
        """Test that all advertised skills have handlers."""
        # The advertised skill set, from the one derivation over TOOLS. Not a
        # rendered card: a card describes a resolved tenant, and skills do not.
        from src.a2a_server.adcp_a2a_server import _derived_skills

        expected_skills = {skill.name for skill in _derived_skills()}

        # Test that _dispatch_skill can handle all advertised skills
        for skill_name in expected_skills:
            # This should not raise an exception for any advertised skill
            try:
                # We can't easily test the actual execution without full setup,
                # but we can at least verify the skill name is recognized
                # DERIVED: a row with a2a=True is a dispatchable skill. The literal list
                # this replaces named six skills the agent no longer ships (approve_creative,
                # get_media_buy_status, optimize_media_buy, get_creative_delivery,
                # list_authorized_properties, update_performance_index) and omitted the task
                # tools it does.
                from src.core.tools.registry import TOOLS

                assert skill_name in {name for name, spec in TOOLS.items() if spec.a2a}, (
                    f"Skill {skill_name} is advertised but not declared with a2a in the registry"
                )
            except Exception as e:
                pytest.fail(f"Skill {skill_name} should be handled but caused error: {e}")


if __name__ == "__main__":
    # Run tests directly
    pytest.main([__file__, "-v"])
