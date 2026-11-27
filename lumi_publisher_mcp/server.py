"""Stdlib-only JSON-RPC 2.0 stdio MCP server wrapping Lumi Publisher's /v1 REST API.

Run:
    LUMI_API_KEY=lumi_sk_... LUMI_BASE_URL=https://app.lumi.owenisas.com \\
        uv run python -m lumi_publisher.mcp_server

Never prints or logs LUMI_API_KEY to stdout or stderr.
"""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, TextIO

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
VIDEO_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/mp4", ".webm": "video/webm"}


def multipart_stream(boundary: str, fields: dict[str, str], file_field: str, path: Path, content_type: str):
    """Stream multipart/form-data without loading large videos into RAM."""
    parts_head = []
    for k, v in fields.items():
        parts_head.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    safe_name = path.name.replace("\"", "_").replace("\r", "_").replace("\n", "_")
    parts_head.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{safe_name}\"\r\n"
        f"Content-Type: {content_type}\r\n\r\n".encode()
    )
    head = b"".join(parts_head)
    tail = f"\r\n--{boundary}--\r\n".encode()
    total_len = len(head) + path.stat().st_size + len(tail)

    def gen():
        yield head
        with path.open("rb") as fh:
            while chunk := fh.read(1024 * 1024):
                yield chunk
        yield tail

    return total_len, gen()

DEFAULT_BASE_URL = "https://app.lumi.owenisas.com"
SERVER_INFO = {"name": "lumi-publisher", "version": "0.2.0"}
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = "2024-11-05"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "lumi_list_accounts",
        "description": "List connected social accounts in the API key's Lumi workspace (public metadata only).",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "lumi_upload_media",
        "description": "Upload a local image or video file to Lumi's workspace media library (SHA-256 deduplicated within the workspace).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path to a local image (jpg, png, webp, gif) or video (mp4, mov, m4v, webm).",
                },
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lumi_create_post",
        "description": "Propose a new post in waiting state (approval-first: nothing publishes until explicitly approved).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "object", "additionalProperties": {"type": "string"}},
                    ],
                    "description": "Caption string, or per-platform caption map with optional 'default' key.",
                },
                "account_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "Workspace account IDs to publish to (from lumi_list_accounts).",
                },
                "media_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional workspace media IDs (from lumi_upload_media).",
                },
                "scheduled_at": {
                    "type": "string",
                    "description": "Optional ISO-8601 proposed publish time (e.g. 2026-12-01T15:00:00Z).",
                },
                "ai": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether the media/content is AI-generated.",
                },
                "note": {
                    "type": "string",
                    "description": "Optional internal note for approvers.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional campaign or client label.",
                },
                "options": {
                    "type": "object",
                    "description": "Optional validated platform options (Threads/X/Pinterest hints).",
                },
            },
            "required": ["content", "account_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lumi_list_posts",
        "description": "List posts in the workspace, optionally filtered by state or label.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["waiting", "approved", "done", "kept"],
                    "description": "Optional state filter.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional label filter.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "lumi_schedule_calendar",
        "description": "Get scheduled and proposed posts from the workspace calendar, optionally bounded by from/to timestamps.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from": {
                    "type": "string",
                    "description": "Optional start time (ISO-8601 or Unix seconds).",
                },
                "to": {
                    "type": "string",
                    "description": "Optional end time (ISO-8601 or Unix seconds).",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "lumi_approve_post",
        "description": "Explicitly approve a waiting post at its exact version for publishing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "when": {
                    "type": "string",
                    "description": "'now', 'proposed', or an ISO-8601 timestamp at least 2 minutes in the future.",
                },
                "version": {
                    "type": "integer",
                    "description": "Exact post version being approved.",
                },
                "tiktok": {"type": "object", "description": "Optional per-account TikTok posting choices."},
                "youtube": {"type": "object", "description": "Optional per-account YouTube posting choices."},
                "pinterest": {"type": "object", "description": "Optional per-account Pinterest board choices."},
            },
            "required": ["post_id", "when", "version"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lumi_get_post",
        "description": "Get full status and per-account delivery results for a single post.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
            },
            "required": ["post_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lumi_retry_post",
        "description": "Retry failed or uncertain deliveries on a post (all failed targets or one account_id).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "account_id": {
                    "type": "string",
                    "description": "Optional single account ID to retry; omit to retry all failed/unknown targets.",
                },
            },
            "required": ["post_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lumi_cancel_post",
        "description": "Cancel / set aside a waiting post (moves it to kept state).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
            },
            "required": ["post_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lumi_analytics_overview",
        "description": "Workspace telemetry overview: connected account follower/view counts, deltas, 24h publish health, and YouTube daily quota remaining.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "Optional platform filter (e.g. youtube, pinterest, bluesky, mastodon, telegram)."},
                "from": {"type": "string", "description": "Optional window start (ISO-8601 or Unix seconds)."},
                "to": {"type": "string", "description": "Optional window end (ISO-8601 or Unix seconds)."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "lumi_post_metrics",
        "description": "Get post milestone metric snapshots (+1h/+24h/+72h/+7d) across its deliveries; pass refresh=true to pull live review-safe stats now.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "refresh": {"type": "boolean", "description": "If true, fetch fresh metrics from connected platforms before returning."},
            },
            "required": ["post_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lumi_account_metrics",
        "description": "Get account metric time series (followers, posts, total views, reconnect health); pass refresh=true to pull a fresh snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "from": {"type": "string", "description": "Optional start timestamp (ISO-8601 or Unix seconds)."},
                "to": {"type": "string", "description": "Optional end timestamp (ISO-8601 or Unix seconds)."},
                "refresh": {"type": "boolean", "description": "If true, fetch fresh metrics from the platform before returning."},
            },
            "required": ["account_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lumi_list_webhooks",
        "description": "List HMAC-SHA256 signed webhook endpoints in the workspace (requires webhooks:manage scope).",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "lumi_create_webhook",
        "description": "Register a public HTTPS HMAC-SHA256 webhook endpoint (requires webhooks:manage scope). Returns the signing secret once.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Public HTTPS endpoint URL."},
                "events": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Event names (post.approved, post.published, post.failed, post.kept, account.needs_reconnect, analytics.milestone).",
                },
                "description": {"type": "string"},
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lumi_delete_webhook",
        "description": "Delete an active webhook endpoint by id (requires webhooks:manage scope).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "webhook_id": {"type": "string"},
            },
            "required": ["webhook_id"],
            "additionalProperties": False,
        },
    },
]



TOOL_ANNOTATIONS: dict[str, dict[str, Any]] = {
    "lumi_list_accounts": {"title": "List Connected Accounts", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "lumi_upload_media": {"title": "Upload Media File", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    "lumi_create_post": {"title": "Stage Draft Post", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    "lumi_list_posts": {"title": "List Posts", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "lumi_schedule_calendar": {"title": "Read Publishing Calendar", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "lumi_approve_post": {"title": "Approve Post (Scoped)", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    "lumi_get_post": {"title": "Get Post Details", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "lumi_retry_post": {"title": "Retry Failed Delivery", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    "lumi_cancel_post": {"title": "Cancel Scheduled Post", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
    "lumi_analytics_overview": {"title": "Workspace Analytics Overview", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "lumi_post_metrics": {"title": "Post Delivery Metrics", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "lumi_account_metrics": {"title": "Channel Account Metrics", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "lumi_list_webhooks": {"title": "List Webhooks", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "lumi_create_webhook": {"title": "Create Signed Webhook", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    "lumi_delete_webhook": {"title": "Delete Webhook", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
}

for _t in TOOLS:
    _ann = TOOL_ANNOTATIONS.get(_t["name"])
    if _ann:
        _t["annotations"] = _ann


def server_card(base_url: str = DEFAULT_BASE_URL) -> dict[str, Any]:
    """Return Smithery / Glama /.well-known/mcp/server-card.json discovery metadata."""
    return {
        "serverInfo": SERVER_INFO,
        "description": "Approval-first social & video publishing for creators and AI agents. Drafts never publish without human approval.",
        "transport": {
            "type": "streamable-http",
            "endpoint": base_url.rstrip("/") + "/mcp",
        },
        "authentication": {
            "type": "bearer",
            "header": "Authorization",
            "scheme": "Bearer",
            "tokenPrefix": "lumi_sk_",
            "registrationUrl": base_url.rstrip("/") + "/v1/agents/register",
            "docsUrl": "https://lumi.owenisas.com/skill.md",
        },
        "capabilities": {"tools": {}},
        "tools": TOOLS,
    }

class LumiAPIError(Exception):
    """Error raised when the Lumi /v1 API call fails."""


TransportFn = Callable[
    [str, str, dict[str, str], bytes | None],
    tuple[int, bytes],
]


def _default_transport(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
        host = urllib.parse.urlsplit(url).hostname or "Lumi server"
        raise LumiAPIError(f"Could not reach {host}: {getattr(err, 'reason', err)}") from None


class LumiClient:
    """Minimal /v1 HTTP client backed by stdlib urllib (or an injected transport for tests)."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        transport: TransportFn | None = None,
    ) -> None:
        raw_base = base_url if base_url is not None else os.environ.get("LUMI_BASE_URL", DEFAULT_BASE_URL)
        self.base_url = (raw_base or DEFAULT_BASE_URL).strip().rstrip("/")
        parsed = urllib.parse.urlsplit(self.base_url)
        local_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}
        if parsed.scheme != "https" and (parsed.hostname or "").lower() not in local_hosts and os.environ.get("LUMI_ALLOW_INSECURE_HTTP") != "1":
            raise LumiAPIError("LUMI_BASE_URL must use https:// (except localhost/127.0.0.1).")
        self._api_key = (api_key if api_key is not None else os.environ.get("LUMI_API_KEY", "")).strip()
        self._custom_transport = transport is not None
        self._transport: TransportFn = transport or _default_transport

    def sanitize(self, text: str) -> str:
        if self._api_key:
            return text.replace(self._api_key, "[redacted]")
        return text

    def _require_key(self) -> str:
        if not self._api_key:
            raise LumiAPIError("LUMI_API_KEY is not set.")
        return self._api_key

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = self._require_key()
        url = self.base_url + path
        if params:
            clean = {k: str(v) for k, v in params.items() if v is not None and v != ""}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": f"lumi-publisher-mcp/{SERVER_INFO['version']}",
        }
        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=UTF-8"
            headers["Content-Length"] = str(len(body))
        elif method in ("POST", "PUT", "PATCH", "DELETE"):
            headers["Content-Length"] = "0"
        status, raw = self._transport(method, url, headers, body)
        return self._parse_response(status, raw)

    def upload_media(self, file_path: str) -> dict[str, Any]:
        key = self._require_key()
        path = Path(file_path).expanduser()
        if not path.is_file():
            raise LumiAPIError(f"File not found: {file_path}")
        if path.suffix.lower() not in (IMAGE_TYPES | VIDEO_TYPES):
            raise LumiAPIError("Only pictures (jpg, png, webp, gif) and videos (mp4, mov, m4v, webm) can be uploaded.")
        if path.stat().st_size > 1024 ** 3:
            raise LumiAPIError("File exceeds the 1 GB upload limit.")
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        stream, ctype, clen = multipart_stream({}, [("file", path.name, mime, path)])
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": f"lumi-publisher-mcp/{SERVER_INFO['version']}",
            "Content-Type": ctype,
            "Content-Length": str(clen),
        }
        body = b"".join(stream) if self._custom_transport else stream
        status, raw = self._transport("POST", self.base_url + "/v1/media/upload", headers, body)
        return self._parse_response(status, raw)

    def _parse_response(self, status: int, raw: bytes) -> dict[str, Any]:
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw and raw.strip() else {}
        except ValueError:
            parsed = {"raw": raw[:300].decode("utf-8", errors="replace")}
        if status >= 400:
            detail = parsed.get("detail") if isinstance(parsed, dict) else parsed
            raise LumiAPIError(self.sanitize(f"Lumi API returned HTTP {status}: {detail or parsed}"))
        return parsed if isinstance(parsed, dict) else {"data": parsed}


class MCPServer:
    """JSON-RPC 2.0 MCP server dispatching the 9 Lumi tools."""

    def __init__(self, client: LumiClient | None = None) -> None:
        self._client = client or LumiClient()
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "lumi_list_accounts": self._list_accounts,
            "lumi_upload_media": self._upload_media,
            "lumi_create_post": self._create_post,
            "lumi_list_posts": self._list_posts,
            "lumi_schedule_calendar": self._schedule_calendar,
            "lumi_approve_post": self._approve_post,
            "lumi_get_post": self._get_post,
            "lumi_retry_post": self._retry_post,
            "lumi_cancel_post": self._cancel_post,
            "lumi_analytics_overview": self._analytics_overview,
            "lumi_post_metrics": self._post_metrics,
            "lumi_account_metrics": self._account_metrics,
            "lumi_list_webhooks": self._list_webhooks,
            "lumi_create_webhook": self._create_webhook,
            "lumi_delete_webhook": self._delete_webhook,
        }

    def _list_accounts(self, args: dict[str, Any]) -> dict[str, Any]:
        return self._client.request_json("GET", "/v1/accounts")

    def _upload_media(self, args: dict[str, Any]) -> dict[str, Any]:
        file_path = args.get("file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            raise ValueError("file_path is required.")
        return self._client.upload_media(file_path)

    def _create_post(self, args: dict[str, Any]) -> dict[str, Any]:
        if "content" not in args or "account_ids" not in args:
            raise ValueError("content and account_ids are required.")
        payload: dict[str, Any] = {
            "content": args["content"],
            "account_ids": args["account_ids"],
        }
        for key in ("media_ids", "scheduled_at", "ai", "note", "label", "options"):
            if key in args and args[key] is not None:
                payload[key] = args[key]
        return self._client.request_json("POST", "/v1/posts", json_body=payload)

    def _list_posts(self, args: dict[str, Any]) -> dict[str, Any]:
        params = {}
        for key in ("state", "label", "limit"):
            if key in args and args[key] is not None:
                params[key] = args[key]
        return self._client.request_json("GET", "/v1/posts", params=params)

    def _schedule_calendar(self, args: dict[str, Any]) -> dict[str, Any]:
        params = {}
        for key in ("from", "to"):
            if key in args and args[key] is not None:
                params[key] = args[key]
        return self._client.request_json("GET", "/v1/schedule", params=params)

    def _approve_post(self, args: dict[str, Any]) -> dict[str, Any]:
        post_id = args.get("post_id")
        if not isinstance(post_id, str) or not post_id.strip():
            raise ValueError("post_id is required.")
        if "when" not in args or "version" not in args:
            raise ValueError("when and version are required.")
        payload: dict[str, Any] = {"when": args["when"], "version": args["version"]}
        for key in ("tiktok", "youtube", "pinterest"):
            if key in args and args[key] is not None:
                payload[key] = args[key]
        safe_id = urllib.parse.quote(post_id.strip(), safe="")
        return self._client.request_json("POST", f"/v1/posts/{safe_id}/approve", json_body=payload)

    def _get_post(self, args: dict[str, Any]) -> dict[str, Any]:
        post_id = args.get("post_id")
        if not isinstance(post_id, str) or not post_id.strip():
            raise ValueError("post_id is required.")
        safe_id = urllib.parse.quote(post_id.strip(), safe="")
        return self._client.request_json("GET", f"/v1/posts/{safe_id}")

    def _retry_post(self, args: dict[str, Any]) -> dict[str, Any]:
        post_id = args.get("post_id")
        if not isinstance(post_id, str) or not post_id.strip():
            raise ValueError("post_id is required.")
        safe_id = urllib.parse.quote(post_id.strip(), safe="")
        payload = {"account_id": args["account_id"]} if args.get("account_id") else None
        return self._client.request_json("POST", f"/v1/posts/{safe_id}/retry", json_body=payload)

    def _cancel_post(self, args: dict[str, Any]) -> dict[str, Any]:
        post_id = args.get("post_id")
        if not isinstance(post_id, str) or not post_id.strip():
            raise ValueError("post_id is required.")
        safe_id = urllib.parse.quote(post_id.strip(), safe="")
        return self._client.request_json("DELETE", f"/v1/posts/{safe_id}")

    def _analytics_overview(self, args: dict[str, Any]) -> dict[str, Any]:
        params = {}
        if args.get("platform"):
            params["platform"] = str(args["platform"])
        if args.get("from"):
            params["from"] = str(args["from"])
        if args.get("to"):
            params["to"] = str(args["to"])
        return self._client.request_json("GET", "/v1/analytics/overview", params=params or None)

    def _post_metrics(self, args: dict[str, Any]) -> dict[str, Any]:
        post_id = args.get("post_id")
        if not isinstance(post_id, str) or not post_id.strip():
            raise ValueError("post_id is required.")
        safe_id = urllib.parse.quote(post_id.strip(), safe="")
        params = {"refresh": "true"} if args.get("refresh") else None
        return self._client.request_json("GET", f"/v1/analytics/posts/{safe_id}", params=params)

    def _account_metrics(self, args: dict[str, Any]) -> dict[str, Any]:
        account_id = args.get("account_id")
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("account_id is required.")
        safe_id = urllib.parse.quote(account_id.strip(), safe="")
        params = {}
        if args.get("from"):
            params["from"] = str(args["from"])
        if args.get("to"):
            params["to"] = str(args["to"])
        if args.get("refresh"):
            params["refresh"] = "true"
        return self._client.request_json("GET", f"/v1/accounts/{safe_id}/metrics", params=params or None)

    def _list_webhooks(self, args: dict[str, Any]) -> dict[str, Any]:
        return self._client.request_json("GET", "/v1/webhooks")

    def _create_webhook(self, args: dict[str, Any]) -> dict[str, Any]:
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url is required.")
        payload: dict[str, Any] = {"url": url.strip()}
        if args.get("events") is not None:
            payload["events"] = list(args["events"])
        if args.get("description"):
            payload["description"] = str(args["description"])
        return self._client.request_json("POST", "/v1/webhooks", json_body=payload)

    def _delete_webhook(self, args: dict[str, Any]) -> dict[str, Any]:
        wh_id = args.get("webhook_id")
        if not isinstance(wh_id, str) or not wh_id.strip():
            raise ValueError("webhook_id is required.")
        safe_id = urllib.parse.quote(wh_id.strip(), safe="")
        return self._client.request_json("DELETE", f"/v1/webhooks/{safe_id}")

    @staticmethod
    def _ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _err(req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def handle_message(self, msg: Any) -> dict[str, Any] | None:
        if not isinstance(msg, dict):
            return self._err(None, -32600, "Invalid Request")
        req_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params")
        if req_id is None:
            return None  # JSON-RPC notification: never respond
        if not isinstance(method, str):
            return self._err(req_id, -32600, "Invalid Request: missing method")
        if method == "initialize":
            requested = params.get("protocolVersion") if isinstance(params, dict) else None
            version = requested if requested in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
            return self._ok(req_id, {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })
        if method == "ping":
            return self._ok(req_id, {})
        if method == "tools/list":
            return self._ok(req_id, {"tools": TOOLS})
        if method == "tools/call":
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                return self._err(req_id, -32602, "Invalid params: tool name required")
            name = params["name"]
            handler = self._handlers.get(name)
            if handler is None:
                return self._err(req_id, -32602, f"Unknown tool: {name}")
            arguments = params.get("arguments")
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                return self._err(req_id, -32602, "Invalid params: arguments must be an object")
            try:
                result = handler(arguments)
                return self._ok(req_id, {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                    "isError": False,
                })
            except (LumiAPIError, ValueError, OSError) as exc:
                return self._ok(req_id, {
                    "content": [{"type": "text", "text": self._client.sanitize(str(exc))}],
                    "isError": True,
                })
        return self._err(req_id, -32601, f"Method not found: {method}")

    def serve_stdio(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        in_stream = stdin or sys.stdin
        out_stream = stdout or sys.stdout
        for raw_line in in_stream:
            line = raw_line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                out_stream.write(json.dumps(self._err(None, -32700, "Parse error")) + "\n")
                out_stream.flush()
                continue
            reply = self.handle_message(msg)
            if reply is not None:
                out_stream.write(json.dumps(reply, ensure_ascii=False) + "\n")
                out_stream.flush()


def main() -> None:  # pragma: no cover
    MCPServer().serve_stdio()


if __name__ == "__main__":  # pragma: no cover
    main()
