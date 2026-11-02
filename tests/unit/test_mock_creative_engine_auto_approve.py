"""The mock creative engine's auto-approve list must actually match a creative's format.

``MockCreativeEngine.__init__`` builds ``auto_approve_format_ids`` from the tenant's
``auto_approve_formats`` config, which holds format-id STRINGS, and then tested
membership with ``creative.format_id in self.auto_approve_format_ids`` -- where the
left side is a ``FormatId`` model. A model is never equal to a string, so the test
was false for every creative ever passed in: nothing was auto-approvable, the
configuration had no effect at all, and the result was indistinguishable from a
tenant that had configured nothing.

The same file already reads the id correctly four lines further down
(``creative.format_id.id if creative.format_id else ""``, used for the video
adaptation suggestions), which is the shape the membership test needed too.

Part of the #2093 family: format identity decided by comparing whole objects of
differing types rather than by the value that actually identifies the format.
"""

import pytest

from src.adapters.mock_creative_engine import MockCreativeEngine
from src.core.schemas import Creative

AGENT_URL = "https://creative.adcontextprotocol.org"


def _creative(creative_id: str = "c1", format_id: str = "display_300x250") -> Creative:
    return Creative(
        creative_id=creative_id,
        name=f"Creative {creative_id}",
        format_id={"agent_url": AGENT_URL, "id": format_id},
    )


class TestAutoApproveList:
    def test_a_configured_format_is_auto_approved(self):
        """The whole point of the setting: a listed format skips human review."""
        engine = MockCreativeEngine({"auto_approve_formats": ["display_300x250"], "human_review_required": False})

        [result] = engine.process_creatives([_creative()])

        assert result.status == "approved"
        assert result.estimated_approval_time is None

    def test_a_configured_format_bypasses_human_review_when_review_is_required(self):
        """The documented bypass branch — auto-approve outranks human_review_required."""
        engine = MockCreativeEngine({"auto_approve_formats": ["display_300x250"], "human_review_required": True})

        [result] = engine.process_creatives([_creative()])

        assert result.status == "approved"
        assert "bypasses human review" in result.detail

    def test_an_unlisted_format_still_requires_review(self):
        """The fix must not make everything approvable — the negative half of the rule."""
        engine = MockCreativeEngine({"auto_approve_formats": ["video_16x9"], "human_review_required": True})

        [result] = engine.process_creatives([_creative()])

        assert result.status == "pending_review"
        assert result.estimated_approval_time is not None

    def test_an_empty_auto_approve_list_approves_nothing(self):
        engine = MockCreativeEngine({"auto_approve_formats": [], "human_review_required": False})

        [result] = engine.process_creatives([_creative()])

        assert result.status == "pending_review"

    @pytest.mark.parametrize(
        ("configured", "creative_format", "expected"),
        [
            (["display_300x250", "video_16x9"], "video_16x9", "approved"),
            (["display_300x250", "video_16x9"], "display_728x90", "pending_review"),
        ],
    )
    def test_membership_is_decided_per_format(self, configured, creative_format, expected):
        """Several configured formats, and only the matching one is approved."""
        engine = MockCreativeEngine({"auto_approve_formats": configured, "human_review_required": False})

        [result] = engine.process_creatives([_creative(format_id=creative_format)])

        assert result.status == expected
