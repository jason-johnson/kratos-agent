"""OBO MCP server — exposes a Microsoft Graph ``/me`` tool over MCP (HTTP).

The kratos agent connects to this server as a *remote* MCP server and injects
the signed-in user's Entra access token into the ``Authorization`` request
header (see kratos hosted-agent ``main.py``). This server validates that token,
performs a secret-less On-Behalf-Of exchange, and returns the user's Graph
profile — so the tool runs **as the signed-in user**, not as the agent.

Transport: streamable HTTP at ``/mcp`` (FastMCP default). The Copilot SDK
connects with ``{"type": "http", "url": "https://<fqdn>/mcp"}``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from obo import TokenValidationError, assert_auth_config_safe, classify_graph_error, validate_user_token
from obo import get_calendar_event as fetch_calendar_event
from obo import get_mail_thread as fetch_mail_thread
from obo import get_message as fetch_message
from obo import get_my_profile as fetch_my_profile
from obo import get_user as fetch_user
from obo import get_user_presence as fetch_user_presence
from obo import list_calendar_events as fetch_calendar_events
from obo import list_chat_messages as fetch_chat_messages
from obo import list_chats as fetch_chats
from obo import search_mail as fetch_mail
from starlette.requests import Request

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("obo-mcp-server")

PORT = int(os.environ.get("PORT", "8000"))

mcp = FastMCP(name="graph-obo", host="0.0.0.0", port=PORT)  # noqa: S104


def _bearer_from_context(ctx: Context) -> str:
    """Extract the Bearer token from the inbound HTTP request headers.

    The token is NEVER passed as a tool argument (which would make it visible to
    the model); it travels only in the transport ``Authorization`` header.
    """
    request: Request | None = getattr(ctx.request_context, "request", None)
    if request is None:
        raise TokenValidationError("no HTTP request context (is this running over HTTP transport?)")
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise TokenValidationError("missing or malformed Authorization header")
    return auth[7:].strip()


def _graph_read(ctx: Context, operation: str, reader: Callable[[str], dict[str, Any]]) -> dict:
    try:
        token = _bearer_from_context(ctx)
        validate_user_token(token)
        result = reader(token)
        logger.info("%s: delegated Graph read succeeded", operation)
        return result
    except TokenValidationError as exc:
        logger.warning("%s: token rejected: %s", operation, exc)
        return {"error": "unauthorized", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("%s: OBO/Graph call failed", operation)
        code, detail = classify_graph_error(exc)
        return {"error": code, "detail": detail}


@mcp.tool()
def get_my_profile(ctx: Context) -> dict:
    """Return the signed-in user's Microsoft 365 profile (name, email, job title).

    Use this whenever the user asks about themselves — "who am I", "what's my
    email", "what's my job title", "show my profile". The profile is fetched
    live from Microsoft Graph on behalf of the signed-in user.
    """
    try:
        token = _bearer_from_context(ctx)
        validate_user_token(token)  # rejects wrong audience / scope / tenant / expiry
        profile = fetch_my_profile(token)
        # Never log the profile payload — it is personal data and logs are a
        # common leak vector (CodeQL clear-text-logging). Log success only.
        logger.info("get_my_profile: profile fetched for the signed-in user")
        # The 48x48 photo is a base64 data URI — noise for the model, which might
        # echo the blob. Hand the model everything EXCEPT the raw image bytes.
        model_view = {k: v for k, v in profile.items() if k != "photoDataUri"}
        if "photoDataUri" in profile:
            model_view["hasProfilePhoto"] = True
        return model_view
    except TokenValidationError as exc:
        # Do not leak the token; surface a clean, actionable error.
        logger.warning("get_my_profile: token rejected: %s", exc)
        return {"error": "unauthorized", "detail": str(exc)}
    except Exception:  # noqa: BLE001 — surface OBO/Graph failures without leaking internals
        # Log full detail server-side; return an opaque message so library
        # internals (correlation ids, tenant info, token fragments) never reach
        # the model or end user.
        logger.exception("get_my_profile: OBO/Graph call failed")
        return {"error": "obo_failed", "detail": "Graph on-behalf-of request failed"}


@mcp.tool()
def m365_get_user(ctx: Context, selector: str) -> dict:
    """Read one responder's basic directory profile by exact ID or user principal name."""
    return _graph_read(ctx, "m365_get_user", lambda token: fetch_user(token, selector))


@mcp.tool()
def m365_get_user_presence(ctx: Context, selector: str) -> dict:
    """Read one responder's current availability and activity."""
    return _graph_read(ctx, "m365_get_user_presence", lambda token: fetch_user_presence(token, selector))


@mcp.tool()
def m365_list_events(ctx: Context, start_at: str, end_at: str, limit: int = 20) -> dict:
    """Read the signed-in user's calendar in a bounded timezone-aware window of at most seven days."""
    return _graph_read(
        ctx,
        "m365_list_events",
        lambda token: fetch_calendar_events(token, start_at, end_at, limit),
    )


@mcp.tool()
def m365_search_messages(ctx: Context, query: str, limit: int = 20) -> dict:
    """Search the signed-in user's mailbox for incident-relevant messages; returns compact previews only."""
    return _graph_read(ctx, "m365_search_messages", lambda token: fetch_mail(token, query, limit))


@mcp.tool()
def m365_get_message(ctx: Context, message_id: str) -> dict:
    """Read one exact mailbox message selected from m365_search_messages; body content is bounded."""
    return _graph_read(ctx, "m365_get_message", lambda token: fetch_message(token, message_id))


@mcp.tool()
def m365_get_thread(ctx: Context, conversation_id: str, limit: int = 20) -> dict:
    """Read a bounded mailbox thread by exact conversation ID."""
    return _graph_read(
        ctx,
        "m365_get_thread",
        lambda token: fetch_mail_thread(token, conversation_id, limit),
    )


@mcp.tool()
def m365_get_event(ctx: Context, event_id: str) -> dict:
    """Read one exact calendar event selected from m365_list_events."""
    return _graph_read(ctx, "m365_get_event", lambda token: fetch_calendar_event(token, event_id))


@mcp.tool()
def m365_list_chats(ctx: Context, limit: int = 20) -> dict:
    """List the signed-in user's recent Teams chats with bounded results."""
    return _graph_read(ctx, "m365_list_chats", lambda token: fetch_chats(token, limit))


@mcp.tool()
def m365_search_chat_messages(ctx: Context, chat_id: str, limit: int = 20) -> dict:
    """Read bounded recent messages from one exact Teams chat returned by m365_list_chats."""
    return _graph_read(
        ctx,
        "m365_search_chat_messages",
        lambda token: fetch_chat_messages(token, chat_id, limit),
    )


if __name__ == "__main__":
    assert_auth_config_safe()  # refuse to start with AUTH_DISABLED outside dev
    logger.info("Starting OBO MCP server on :%d (streamable-http at /mcp)", PORT)
    mcp.run(transport="streamable-http")
