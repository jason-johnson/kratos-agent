"""Coerce hosted-agent invocation request bodies into a normalized dict.

The Foundry hosted-agent receives invocations through several paths, each of
which frames the request body differently:

* ``azd ai agent invoke "msg"`` — posts the raw message as ``text/plain``,
  **not** JSON. Naively calling ``request.json()`` on this raises and the agent
  used to reject it with a 400, breaking the primary CLI test path.
* The Kratos backend proxy — posts a JSON object
  (``{"message": ..., "useCase": ..., "conversationId": ...}``).
* Platform keep-alive — posts ``{"warmup": true}`` (or an empty body) to reset
  the idle timer without invoking the model.

This helper is intentionally dependency-free (stdlib ``json`` only) so it can be
unit-tested without importing the agentserver runtime.
"""

from __future__ import annotations

import json
import re
from typing import Any

_MCP_BLOCK_PATTERN = r"<mcp_access_tokens\b[^>]*>(.*?)(?:</mcp_access_tokens\s*>|$)"
_MCP_ACCESS_TOKEN_BLOCK_RE = re.compile(_MCP_BLOCK_PATTERN, flags=re.IGNORECASE | re.DOTALL)
_MCP_ACCESS_TOKEN_MARKER_RE = re.compile(r"<mcp_access_tokens\b", flags=re.IGNORECASE)


def parse_invoke_payload(raw: bytes) -> dict[str, Any]:
    """Normalize a raw invocation body into a dict.

    Returns one of:
      * the JSON object as-is, when the body is a JSON object;
      * ``{"message": <text>}`` for a JSON string, plain text, or any other
        non-object JSON value (arrays/numbers are stringified back to text);
      * ``{"warmup": True}`` for an empty or whitespace-only body.
    """
    text = raw.decode("utf-8", "replace").strip() if raw else ""
    if not text:
        return {"warmup": True}

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Plain text (e.g. `azd ai agent invoke "hello"`).
        return {"message": text}

    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, str):
        return {"message": parsed}

    # Any other JSON scalar/array: treat the original text as the message.
    return {"message": text}


def strip_mcp_access_token_tags(message: str) -> tuple[str, list[str]]:
    """Remove prompt-embedded OBO token blocks and return keys for diagnostics.

    Delegated tokens are accepted only through the invocation JSON body's
    ``mcpAccessTokens`` field. Any prompt tag is ignored and removed. A dangling
    opening marker consumes the rest of the message so malformed secret content
    fails closed instead of reaching the model or message-content telemetry.
    """
    server_names: list[str] = []

    def consume_block(match: re.Match[str]) -> str:
        try:
            parsed = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            return ""
        if isinstance(parsed, dict):
            server_names.extend(str(key) for key, value in parsed.items() if isinstance(value, str) and value)
        return ""

    sanitized = _MCP_ACCESS_TOKEN_BLOCK_RE.sub(consume_block, message)
    dangling_marker = _MCP_ACCESS_TOKEN_MARKER_RE.search(sanitized)
    if dangling_marker:
        sanitized = sanitized[: dangling_marker.start()]
    return sanitized.strip(), sorted(set(server_names))
