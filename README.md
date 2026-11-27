# Lumi Publisher MCP Server

Approval-first video and social publishing for creators, teams, and AI agents.

Agents upload media, stage drafts, inspect the calendar, manage webhooks, and read review-safe analytics. Nothing ever publishes until a human approves that exact post version (`v1`, `v2`, ...) inside [Lumi Publisher](https://app.lumi.owenisas.com).

- **Hosted Streamable HTTP MCP:** `https://app.lumi.owenisas.com/mcp`
- **Server Card:** `https://app.lumi.owenisas.com/.well-known/mcp/server-card.json`
- **One-Prompt Agent Skill:** `https://lumi.owenisas.com/skill.md`
- **OpenAPI v1.1.0:** `https://lumi.owenisas.com/openapi.json`

## Supported platforms

- **Live now:** YouTube (public beta), Bluesky, Telegram, Mastodon, Discord
- **In platform review / tester preview:** TikTok, Pinterest, Instagram, Threads, Facebook, Bilibili

## Connect via Remote Streamable HTTP (recommended)

```json
{
  "mcpServers": {
    "lumi-publisher": {
      "type": "http",
      "url": "https://app.lumi.owenisas.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_LUMI_API_KEY"
      }
    }
  }
}
```

## Run locally over stdio (zero dependencies, Python 3.12+)

```json
{
  "mcpServers": {
    "lumi-publisher": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/owenisas/lumi-publisher-mcp", "lumi-publisher-mcp"],
      "env": {
        "LUMI_BASE_URL": "https://app.lumi.owenisas.com",
        "LUMI_API_KEY": "YOUR_LUMI_API_KEY"
      }
    }
  }
}
```

## Get an API key

- **Web app:** Sign in at [app.lumi.owenisas.com](https://app.lumi.owenisas.com/#developer) and create a workspace key (`lumi_sk_...`).
- **Agent self-signup:** `POST https://app.lumi.owenisas.com/v1/agents/register` returns a draft-only API key plus a human `claim_url` (14-day free trial upon claim).

## 15 MCP Tools

| Tool | Purpose |
| --- | --- |
| `lumi_list_accounts` | List connected channels (public metadata only) |
| `lumi_upload_media` | Stream an image or video into workspace media storage |
| `lumi_create_post` | Stage a draft post into the human approval queue |
| `lumi_list_posts` | Filter drafts, scheduled, published, or kept posts |
| `lumi_get_post` | Inspect a post, version, and per-channel delivery receipts |
| `lumi_schedule_calendar` | Read upcoming scheduled items in a date window |
| `lumi_approve_post` | Approve a version-locked post (scoped keys only) |
| `lumi_retry_post` | Retry a failed or unknown delivery |
| `lumi_cancel_post` | Cancel a pending approval or scheduled post |
| `lumi_analytics_overview` | Workspace review-safe delivery and channel stats |
| `lumi_post_metrics` | Per-post milestone metrics |
| `lumi_account_metrics` | Per-account channel metrics |
| `lumi_list_webhooks` | List registered HMAC-SHA256 webhook endpoints |
| `lumi_create_webhook` | Register a signed webhook endpoint |
| `lumi_delete_webhook` | Remove a webhook endpoint |

## Security

- OAuth tokens stay encrypted server-side and are never returned over REST or MCP.
- Editing a draft after approval automatically bumps its version and clears approval.
- Default and agent-registered keys are draft-only.

## License

MIT
