# Hand-authored feature — not compiled from adcp-req.
#
# LOCALLY-ADDED (survives BR-*.feature regeneration).
#
# What it grades: a sync keeps the creative agent's preview for the seller's own use and writes
# none of it into the creative, on the create branch and on the update branch. A creative's
# own url and size are what the seller later hands its ad server, so a preview page or a
# preview's size stored there would be trafficked as if it were the creative. Sync used to
# copy the first render's page and size onto a creative that sent none (#2390).
#
# AdCP 3.2.7 docs/creative/task-reference/preview_creative.mdx: "Every render's `preview_url`
# returns an untrusted HTML page for cross-origin iframe embedding". The creative is on the
# static format whose only required asset is text, so it carries no url or size of its own and
# a copied page or size would be the only one on the row. The preview's 728x90 is a size the
# format does not declare.
#
# Ungraded by the storyboard runner: sync-creatives-response.json's per-creative entry carries
# no url or size, so the stored row is the observable.
#
# @source repo=adcp ref=v3.2.7 path=docs/creative/task-reference/preview_creative.mdx
Feature: UC-006 sync_creatives — a creative agent's preview stays out of the creative (local)

  @T-UC-006-local-preview-kept-out-of-creative
  Scenario Outline: a sync keeps the agent's preview page and size out of the creative - <branch>
    Given the Buyer is authenticated
    And a <creative_state> creative on a text-only format served by a creative agent
    And the creative agent previews every creative as its page "https://preview.example.com/render/desktop.html", sized 728x90
    When the Buyer Agent syncs the creative
    Then every creative result has action "<expected_action>"
    And the creative agent is called to build or preview the creative
    And the stored creative keeps the preview and carries neither its page nor its size

    Examples:
      | branch | creative_state | expected_action |
      | create | new            | created         |
      | update | existing       | updated         |
