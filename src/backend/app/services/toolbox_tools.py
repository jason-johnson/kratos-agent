"""Application-owned adapters for Microsoft Foundry Toolbox tools."""

import logging
import re
import time
from collections.abc import Mapping
from urllib.parse import urlparse

import httpx
from azure.identity.aio import DefaultAzureCredential
from copilot.tools import define_tool
from opentelemetry import trace
from pydantic import BaseModel, Field

from app.services.skill_tools import _set_kratos_attrs

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

_URL_RE = re.compile(r"https?://[^\s\])}>\"']+")


class ToolboxWebSearchParams(BaseModel):
    search_query: str = Field(description="The search query to look up on the public web")


class ToolboxCodeInterpreterParams(BaseModel):
    code: str = Field(description="Python code to execute in the managed sandbox")


def _collect_urls(value: object) -> list[str]:
    urls: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key.lower() in {"url", "uri"} and isinstance(child, str):
                urls.append(child)
            urls.extend(_collect_urls(child))
    elif isinstance(value, list):
        for child in value:
            urls.extend(_collect_urls(child))
    return urls


def _normalize_web_search_result(result: Mapping, search_query: str) -> dict:
    """Reduce the MCP result to evidence text and authoritative public URLs."""
    text_parts: list[str] = []
    citation_candidates: list[str] = []

    for item in result.get("content", []):
        resource = item.get("resource") or {}
        text = resource.get("text")
        if isinstance(text, str) and text:
            text_parts.append(text)
            citation_candidates.extend(_URL_RE.findall(text))
        citation_candidates.extend(_collect_urls(item.get("annotations") or {}))
        citation_candidates.extend(_collect_urls(item.get("_meta") or {}))
        citation_candidates.extend(_collect_urls(resource.get("_meta") or {}))

    citations = []
    seen = set()
    for candidate in citation_candidates:
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or candidate in seen:
            continue
        seen.add(candidate)
        citations.append({"url": candidate})

    response = {
        "query": search_query,
        "text": "\n\n".join(text_parts),
        "citations": citations,
        "grounded": bool(citations),
        "billing": {"unit": "transaction", "quantity": 1},
    }
    if not citations:
        response["grounding_warning"] = (
            "Toolbox returned no source URLs. Do not invent or cite URLs; state that sourced links were unavailable."
        )
    return response


def _parse_mcp_response(response: httpx.Response) -> dict:
    """Parse either JSON or SSE returned by an MCP Streamable HTTP endpoint."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        return response.json()
    for line in reversed(response.text.splitlines()):
        if line.startswith("data:"):
            return httpx.Response(200, content=line[5:].strip()).json()
    raise ValueError("Toolbox returned an empty MCP event stream")


async def _call_toolbox_tool(endpoint: str, tool_name: str, arguments: dict) -> dict:
    credential = DefaultAzureCredential()
    try:
        token = await credential.get_token("https://ai.azure.com/.default")
        headers = {
            "Authorization": f"Bearer {token.token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(headers=headers, timeout=120.0) as client:
            initialize_response = await client.post(
                endpoint,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "kratos-agent", "version": "0.1.0"},
                    },
                },
            )
            initialize_response.raise_for_status()
            initialize = _parse_mcp_response(initialize_response)
            if error := initialize.get("error"):
                raise ValueError(f"Toolbox MCP initialize failed: {error}")

            session_id = initialize_response.headers.get("mcp-session-id")
            if session_id:
                client.headers["Mcp-Session-Id"] = session_id
            await client.post(
                endpoint,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
            tool_response = await client.post(
                endpoint,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": arguments,
                    },
                },
            )
            tool_response.raise_for_status()
            tool_payload = _parse_mcp_response(tool_response)
            if error := tool_payload.get("error"):
                raise ValueError(f"Toolbox {tool_name} failed: {error}")
            return tool_payload.get("result") or {}
    finally:
        await credential.close()


async def _call_toolbox_web_search(endpoint: str, search_query: str) -> dict:
    if not search_query.strip():
        return {"error": "Missing required search_query for Toolbox web search"}

    started = time.monotonic()
    try:
        result = await _call_toolbox_tool(endpoint, "web_search", {"search_query": search_query})
    except Exception as exc:
        logger.exception("Toolbox web search failed")
        return {"error": f"Toolbox web search failed: {exc}"}

    normalized = _normalize_web_search_result(result, search_query)
    logger.info(
        "Toolbox web search returned citations=%d grounded=%s elapsed_ms=%.0f",
        len(normalized["citations"]),
        normalized["grounded"],
        (time.monotonic() - started) * 1000,
    )
    return normalized


def _normalize_code_interpreter_result(result: Mapping) -> dict:
    text = "\n\n".join(
        item["text"]
        for item in result.get("content", [])
        if item.get("type") == "text" and isinstance(item.get("text"), str)
    )
    response: dict[str, object] = {"billing": {"unit": "session", "quantity": 1}}
    if result.get("isError") or not text:
        response["error"] = text or "Toolbox Code Interpreter returned no output"
    else:
        response["text"] = text
    return response


async def _call_toolbox_code_interpreter(endpoint: str, code: str) -> dict:
    if not code.strip():
        return {"error": "Missing required code for Toolbox Code Interpreter"}

    try:
        result = await _call_toolbox_tool(endpoint, "code_interpreter", {"code": code})
    except Exception as exc:
        logger.exception("Toolbox Code Interpreter failed")
        return {"error": f"Toolbox Code Interpreter failed: {exc}"}
    return _normalize_code_interpreter_result(result)


def create_toolbox_web_search_tool(endpoint: str):
    """Create the Copilot custom tool bound to one trusted Toolbox endpoint."""

    @define_tool(
        name="kratos-tools-web_search",
        description="Search the public web using Microsoft Foundry Toolbox and return grounded source URLs.",
    )
    async def toolbox_web_search(params: ToolboxWebSearchParams) -> dict:
        with tracer.start_as_current_span("skill.toolbox_web_search") as span:
            _set_kratos_attrs(span)
            result = await _call_toolbox_web_search(endpoint, params.search_query)
            if "error" not in result:
                span.set_attribute("kratos.tool.billing.unit", "transaction")
                span.set_attribute("kratos.tool.billing.quantity", 1)
            return result

    return toolbox_web_search


def create_toolbox_code_interpreter_tool(endpoint: str):
    """Create the code-only Toolbox Code Interpreter pilot tool."""

    @define_tool(
        name="kratos-tools-code_interpreter",
        description="Execute Python computations in Microsoft Foundry Code Interpreter without file inputs.",
    )
    async def toolbox_code_interpreter(params: ToolboxCodeInterpreterParams) -> dict:
        with tracer.start_as_current_span("skill.toolbox_code_interpreter") as span:
            _set_kratos_attrs(span)
            return await _call_toolbox_code_interpreter(endpoint, params.code)

    return toolbox_code_interpreter
