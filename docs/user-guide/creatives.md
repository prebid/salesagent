# Creative Management

Creatives are the ad assets (images, videos, HTML) that run in campaigns. The Prebid Sales Agent provides a creative approval workflow.

## Creative Workflow

1. **Submission**: Advertiser submits creative via MCP API
2. **Validation**: System checks format, dimensions, file size
3. **Review**: Based on configuration, auto-approved or sent to queue
4. **Approval/Rejection**: Admin reviews and decides
5. **Deployment**: Approved creatives pushed to ad server

## Auto-Approval

Configure auto-approval for standard formats to reduce manual review:

### Per-Tenant Settings

In Admin UI > Settings > Creatives:

- **Auto-approve formats**: List of formats that skip review
- **Max file size**: Auto-reject oversized files
- **Required review**: Force manual review for all creatives

### Example Configuration

```json
{
  "creative_engine": {
    "auto_approve_formats": ["display_300x250", "display_728x90"],
    "max_file_size_mb": 2,
    "human_review_required": false
  }
}
```

## Review Queue

Creatives requiring review appear in Admin UI > Creatives:

- **Pending**: Awaiting review
- **Approved**: Ready for delivery
- **Rejected**: With rejection reason

### Review Actions

- **Approve**: Creative can be used in campaigns
- **Reject**: Provide reason, advertiser notified
- **Request Changes**: Ask for modifications

## Creative Formats

### Display

- Standard IAB sizes (300x250, 728x90, 160x600, etc.)
- Image formats: JPG, PNG, GIF
- HTML5 creatives (zip bundles)

### Video

- VAST tags (URL reference)
- MP4 uploads
- Duration limits configurable

### Native

- JSON-based native ad format
- Headline, description, image, CTA

## AI-Powered Review

With `GEMINI_API_KEY` configured, the system can:

- Analyze creative content
- Flag potentially problematic content
- Generate creative summaries
- Suggest improvements

This assists human reviewers but doesn't replace approval decisions.

## Creative Groups

Organize creatives across campaigns:

- Group by advertiser
- Group by campaign theme
- Share creatives across media buys
- Track performance by group
