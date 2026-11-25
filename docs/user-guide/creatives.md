# Creative management

Creatives are the ad assets (images, videos, HTML) that run in campaigns. The Prebid Sales
Agent reviews each uploaded creative according to the tenant's approval mode.

## Creative workflow

A creative moves through these stages:

1. **Submission**: A buyer's AI agent uploads the creative, for example with
   `sync_creatives`.
2. **Validation**: The sales agent checks the creative against the creative schema and the
   format it names.
3. **Review**: The approval mode approves the creative or sets it to pending review.
4. **Decision**: A user, or the AI review, approves or rejects a pending creative.
5. **Delivery**: The sales agent sends approved creatives to the ad server.

## Approval modes

The approval mode decides what happens when a buyer uploads a creative. To set the mode,
follow these steps:

1. Open **Settings > Policies & Workflows**.
2. Under **Creative Review**, select a **Creative Approval Mode**.
3. Click **Save Business Rules**.

The following table lists the three modes.

| Mode | Status after upload | Who decides |
|------|---------------------|-------------|
| Auto-approve all creatives | Approved | Nobody. The sales agent approves every creative. |
| Require human approval | Pending review | A user, on the creative review page. |
| AI-powered review | Pending review | The AI review, or a user when the AI review defers. |

A tenant that has selected no mode uses **Require human approval**.

The mode applies to every creative, whatever its format. The **Approval Workflow** checkbox
in the same section concerns media buys and does not change creative review.

## AI-powered review

In **AI-powered review** mode, the sales agent runs an AI review after each upload. The
review needs review criteria, which you enter in the **AI Review Criteria** field under
**Creative Review**.

The AI review runs on the AI provider and API key that you set under
**Settings > Integrations > AI Services**. When the tenant sets no AI provider, the review
uses the deployment's AI provider and key, for example `GEMINI_API_KEY`.

The AI review ends in one of three results:

- **Approved**: the AI recommends approval with high confidence.
- **Rejected**: the AI recommends rejection with high confidence.
- **Pending review**: every other case. The creative waits for a user. When the AI
  recommends approval or rejection without high confidence, the creative also shows that
  recommendation.

A creative also stays in pending review in these cases:

- The tenant has no usable AI provider or no review criteria.
- The creative's category or tags name a sensitive category: political, healthcare, or
  financial.
- The AI review fails or takes longer than 60 seconds.

## Review creatives

To open the creative review page, click **Uploaded Creatives** on the tenant dashboard.

The page shows counts for **Pending Review**, **Approved**, **Rejected**, and
**Total Creatives**, and tabs that filter the list by status. The **Pending Review** count
and the **Pending** tab match a status that the sales agent does not assign, so they show no
creatives. In the **All** tab, a creative that waits for review has a **Pending Review**
badge.

Each creative shows its name, format, advertiser, and the media buys that use it. After the
AI review that runs on upload, a creative also shows that review's result, reason, and
confidence.

The following table lists the actions on a creative.

| Action | Shown for | Result |
|--------|-----------|--------|
| **Approve** | Pending creatives | Approves the creative after you confirm. |
| **Reject** | Pending creatives | Rejects the creative. You must enter a reason. |
| **AI Review** | Pending creatives, when AI review is set up | Runs the AI review and reports its result. The creative stays pending until you decide. |
| **Approve Anyway** | Rejected creatives | Approves the creative. |
| **Revoke Approval** | Approved creatives | Rejects the creative. You must enter a reason. |
| **View Preview** | All creatives | Shows the creative. |

## Delivery to the ad server

Approving a creative with **Approve** or **Approve Anyway** starts delivery in two ways:

- A media buy that waits for creatives is created in the ad server when you approve its
  last unapproved creative.
- A media buy that is live in the ad server receives the approved creative. When the push
  fails, the sales agent logs the failure, and the page reports the approval as successful.

An approval by the AI review starts neither.

## Notifications

The sales agent reports activity through Slack and webhooks. It sends no email.

- **Slack**: Set **Task Notifications Webhook URL** under
  **Settings > Integrations > Slack Integration**. The channel gets a notice when a media
  buy waits for approval, is created, or fails, and when the Google Ad Manager or
  Broadstreet adapter needs a person to act on an order. For creatives, it gets a notice
  when a user approves or rejects a creative, and when an AI review after an upload
  finishes. In **Require human approval** mode, it also gets a notice for each creative
  that waits for review.
- **Buyer webhook**: A buyer that sends `push_notification_config` with `sync_creatives`
  gets one webhook call per creative that waited for review. The call lists that creative.
  It comes when a user approves the creative, or when the AI review approves or rejects it.
  A rejection by a user sends no call. Known issue:
  [#2241](https://github.com/prebid/salesagent/issues/2241).

## Creative formats

A creative names its format with a canonical `format_kind` from the Ad Context Protocol
(AdCP) format vocabulary, or with a named format that a creative agent defines. The sales
agent checks the creative's assets against that format.

To manage creative agents, open **Settings > Integrations**, then click
**Manage Creative Agents**. The tenant uses the AdCP standard creative agent until you
turn it off on that page. Click **Add Creative Agent** to add an agent for other formats.

A product lists the formats it accepts. See [Product management](products.md).
