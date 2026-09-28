"""Application-owned adapters for Microsoft Foundry Toolbox tools."""

import contextlib
import logging
import os
import re
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote, urlparse
from uuid import uuid4

import httpx
from azure.identity.aio import DefaultAzureCredential
from copilot.tools import define_tool
from opentelemetry import trace
from pydantic import BaseModel, Field

from app.services.skill_tools import _set_kratos_attrs

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

_URL_RE = re.compile(r"https?://[^\s\])}>\"']+")
_SAFE_GENERATED_FILENAME_RE = re.compile(r"^[\w\-. ]+$")
_GENERATED_FILE_EXTENSIONS = frozenset(
    {".csv", ".docx", ".gif", ".html", ".jpeg", ".jpg", ".json", ".md", ".pdf", ".png", ".pptx", ".txt", ".xlsx", ".zip"}
)
_MAX_GENERATED_FILES = 5
_MAX_GENERATED_FILE_BYTES = 10 * 1024 * 1024


class ToolboxWebSearchParams(BaseModel):
    search_query: str = Field(description="The search query to look up on the public web")


class ToolboxCodeInterpreterParams(BaseModel):
    code: str = Field(description="Python code to execute in the managed sandbox")


class ToolboxMicrosoftDocsSearchParams(BaseModel):
    query: str = Field(description="Microsoft product or Azure documentation topic to search for")


class ToolboxMicrosoftCodeSampleSearchParams(BaseModel):
    query: str = Field(description="Microsoft SDK, API, or implementation topic to find code samples for")
    language: str | None = Field(default=None, description="Optional programming language filter")


class ToolboxMicrosoftDocsFetchParams(BaseModel):
    url: str = Field(description="Microsoft Learn documentation page URL returned by documentation search")


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


def _file_citations(result: Mapping) -> list[Mapping]:
    citations: list[Mapping] = []
    for item in result.get("content", []):
        raw = (item.get("_meta") or {}).get("container_file_citations")
        if isinstance(raw, str):
            try:
                raw = httpx.Response(200, content=raw).json()
            except ValueError as exc:
                raise ValueError("Toolbox returned invalid generated-file metadata") from exc
        if isinstance(raw, list):
            citations.extend(citation for citation in raw if isinstance(citation, Mapping))
    if len(citations) > _MAX_GENERATED_FILES:
        raise ValueError(f"Toolbox returned more than {_MAX_GENERATED_FILES} generated files")
    return citations


async def _download_generated_files(client: httpx.AsyncClient, endpoint: str, result: Mapping) -> list[dict]:
    citations = _file_citations(result)
    if not citations:
        return []

    project_endpoint, separator, _ = endpoint.partition("/toolboxes/")
    if not separator:
        raise ValueError("Toolbox endpoint is not project-scoped")

    generated_files = []
    for citation in citations:
        filename = citation.get("filename")
        container_id = citation.get("container_id")
        file_id = citation.get("file_id")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not _SAFE_GENERATED_FILENAME_RE.fullmatch(filename)
            or Path(filename).suffix.lower() not in _GENERATED_FILE_EXTENSIONS
            or not isinstance(container_id, str)
            or not container_id
            or not isinstance(file_id, str)
            or not file_id
        ):
            raise ValueError("Toolbox returned an unsafe generated-file citation")

        container_segment = quote(container_id, safe="")
        file_segment = quote(file_id, safe="")
        file_url = f"{project_endpoint}/openai/v1/containers/{container_segment}/files/{file_segment}"
        local_path = Path("/tmp") / f"toolbox-{uuid4().hex}-{filename}"
        try:
            try:
                data = bytearray()
                async with client.stream("GET", f"{file_url}/content") as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > _MAX_GENERATED_FILE_BYTES:
                            raise ValueError("Toolbox generated file exceeds the 10 MiB limit")
                local_path.write_bytes(data)
            finally:
                delete_response = await client.delete(file_url)
                if delete_response.status_code not in {200, 204, 404}:
                    delete_response.raise_for_status()
            generated_files.append({"filename": filename, "path": str(local_path), "size": len(data)})
        except Exception:
            with contextlib.suppress(OSError):
                os.remove(local_path)
            raise
    return generated_files


async def _call_toolbox_tool(
    endpoint: str, tool_name: str, arguments: dict, *, download_generated_files: bool = False
) -> dict:
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
            result = tool_payload.get("result") or {}
            if download_generated_files:
                result["_kratos_generated_files"] = await _download_generated_files(client, endpoint, result)
            return result
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


def _normalize_microsoft_learn_result(result: Mapping, source_url: str = "") -> dict:
    text_parts: list[str] = []
    citation_candidates = [source_url] if source_url else []
    for item in result.get("content", []):
        text = item.get("text")
        if not isinstance(text, str):
            text = (item.get("resource") or {}).get("text")
        if isinstance(text, str) and text:
            text_parts.append(text)
            citation_candidates.extend(_URL_RE.findall(text))

    text = "\n\n".join(text_parts)
    if result.get("isError") or not text:
        return {"error": text or "Microsoft Learn returned no documentation"}

    citations = []
    seen = set()
    for candidate in citation_candidates:
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or candidate in seen:
            continue
        seen.add(candidate)
        citations.append({"url": candidate})
    return {"text": text, "citations": citations, "grounded": bool(citations)}


async def _call_toolbox_microsoft_learn(endpoint: str, tool_name: str, arguments: dict) -> dict:
    try:
        result = await _call_toolbox_tool(endpoint, f"learn_mcp___{tool_name}", arguments)
    except Exception:
        logger.exception("Toolbox Microsoft Learn %s failed", tool_name)
        return {"error": "Toolbox Microsoft Learn request failed"}
    return _normalize_microsoft_learn_result(result, str(arguments.get("url", "")))


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
        if generated_files := result.get("_kratos_generated_files"):
            response["generated_files"] = generated_files
    return response


async def _call_toolbox_code_interpreter(endpoint: str, code: str) -> dict:
    if not code.strip():
        return {"error": "Missing required code for Toolbox Code Interpreter"}

    try:
        result = await _call_toolbox_tool(
            endpoint, "code_interpreter", {"code": code}, download_generated_files=True
        )
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


def create_toolbox_microsoft_docs_search_tool(endpoint: str):
    """Create the Microsoft Learn documentation-search pilot tool."""

    @define_tool(
        name="kratos-tools-microsoft_docs_search",
        description="Search current first-party Microsoft and Azure documentation through Microsoft Learn.",
    )
    async def toolbox_microsoft_docs_search(params: ToolboxMicrosoftDocsSearchParams) -> dict:
        with tracer.start_as_current_span("skill.toolbox_microsoft_docs_search") as span:
            _set_kratos_attrs(span)
            return await _call_toolbox_microsoft_learn(endpoint, "microsoft_docs_search", {"query": params.query})

    return toolbox_microsoft_docs_search


def create_toolbox_microsoft_code_sample_search_tool(endpoint: str):
    """Create the Microsoft Learn code-sample-search pilot tool."""

    @define_tool(
        name="kratos-tools-microsoft_code_sample_search",
        description="Search current code samples in first-party Microsoft documentation.",
    )
    async def toolbox_microsoft_code_sample_search(params: ToolboxMicrosoftCodeSampleSearchParams) -> dict:
        arguments = {"query": params.query}
        if params.language:
            arguments["language"] = params.language
        with tracer.start_as_current_span("skill.toolbox_microsoft_code_sample_search") as span:
            _set_kratos_attrs(span)
            return await _call_toolbox_microsoft_learn(endpoint, "microsoft_code_sample_search", arguments)

    return toolbox_microsoft_code_sample_search


def create_toolbox_microsoft_docs_fetch_tool(endpoint: str):
    """Create the Microsoft Learn documentation-fetch pilot tool."""

    @define_tool(
        name="kratos-tools-microsoft_docs_fetch",
        description="Fetch a Microsoft Learn documentation page returned by documentation search.",
    )
    async def toolbox_microsoft_docs_fetch(params: ToolboxMicrosoftDocsFetchParams) -> dict:
        parsed = urlparse(params.url)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".microsoft.com"):
            return {"error": "Only HTTPS pages on microsoft.com can be fetched"}
        with tracer.start_as_current_span("skill.toolbox_microsoft_docs_fetch") as span:
            _set_kratos_attrs(span)
            return await _call_toolbox_microsoft_learn(endpoint, "microsoft_docs_fetch", {"url": params.url})

    return toolbox_microsoft_docs_fetch


def create_toolbox_code_interpreter_tool(endpoint: str):
    """Create the Toolbox Code Interpreter pilot tool."""

    @define_tool(
        name="kratos-tools-code_interpreter",
        description="Execute synthetic Python computations and generate synthetic output files in Microsoft Foundry Code Interpreter.",
    )
    async def toolbox_code_interpreter(params: ToolboxCodeInterpreterParams) -> dict:
        with tracer.start_as_current_span("skill.toolbox_code_interpreter") as span:
            _set_kratos_attrs(span)
            return await _call_toolbox_code_interpreter(endpoint, params.code)

    return toolbox_code_interpreter
