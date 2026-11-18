"""Unit tests for update_media_buy affected_packages response."""

from src.core.schemas import AffectedPackage, UpdateMediaBuySuccess

#: ``revision`` carries no model default -- it is the column the repository owns -- so
#: every buyer-facing envelope states where its value came from. These cases assert on
#: ``affected_packages``, never on the revision, and a test does not speak for the
#: repository.
_REVISION = 1


# REMOVED: test_affected_packages_includes_creative_assignment_details,
# test_affected_packages_can_be_empty and test_affected_packages_shows_replaced_creatives.
# Each constructed an AffectedPackage with changes_applied={...} and then read the same
# dict back OFF THE MODEL -- no production between the set and the read, so the assertion
# could not fail. tests/CLAUDE.md names the shape: a Then that checks the Given.
#
# The obligation they claimed -- that a package update is REPORTED in affected_packages --
# is graded falsifiably by tests/unit/test_update_media_buy_behavioral.py
# (test_database_persisted_after_adapter_success and
# test_multi_package_update_processes_all_packages), which run the real _impl and read
# affected_packages off its return. That is where it stays: measured in run
# innet_150926_1232, all seven BDD scenarios asserting
# ``the response should contain affected_packages including "pkg_001"`` are XFAILED on
# every transport, in-process and e2e, so no scenario grades this outcome either.
#
# What survives below is the case that CAN fail: changes_applied and buyer_package_ref are
# OUR fields on our AffectedPackage subclass, declared Field(exclude=True), and their
# absence from the dump is our behavior rather than the library parent's.


def test_response_serialization_includes_affected_packages():
    """Test that UpdateMediaBuySuccess serializes affected_packages correctly."""
    response = UpdateMediaBuySuccess.sync_success(
        media_buy_id="test_buy_serialization",
        revision=_REVISION,
        affected_packages=[
            AffectedPackage(
                package_id="pkg_1",  # Required by AdCP
                paused=False,  # Required by AdCP 2.12.0
                buyer_package_ref="pkg_1_buyer_ref",  # Internal field
                changes_applied={  # Internal field
                    "creative_ids": {
                        "added": ["creative_a"],
                        "removed": [],
                        "current": ["creative_a"],
                    }
                },
            )
        ],
    )

    # There is ONE serialization. A second "internal" dump used to be asserted here via
    # ``model_dump_internal()``, deleted in 07ed51f24 (CLAUDE.md pattern 4 — one
    # serializer per model): a field that must stay off the wire is
    # ``Field(exclude=True)`` at its declaration, so it is absent on every path. The
    # block's own note already said so ("even with model_dump_internal(), the nested
    # AffectedPackage excludes internal fields... exclude=True takes precedence"), and
    # its only assertion duplicated the first line below.
    response_dict = response.model_dump()
    assert "affected_packages" in response_dict
    assert len(response_dict["affected_packages"]) == 1
    # Internal fields should be EXCLUDED
    assert "buyer_package_ref" not in response_dict["affected_packages"][0], "Internal field should be excluded"
    assert "changes_applied" not in response_dict["affected_packages"][0], "Internal field should be excluded"
    # AdCP-required fields should be PRESENT
    assert response_dict["affected_packages"][0]["package_id"] == "pkg_1"
