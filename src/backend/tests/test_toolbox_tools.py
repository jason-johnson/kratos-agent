from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import toolbox_tools


def test_normalizes_text_and_citations_from_all_supported_locations():
    result = {
        "content": [
            {
                "type": "resource",
                "annotations": {"sources": [{"url": "https://one.example/article"}]},
                "_meta": {"citation": {"url": "https://two.example/report"}},
                "resource": {
                    "text": "Evidence [source](https://one.example/article)",
                    "_meta": {"annotations": [{"url": "https://three.example/news"}]},
                },
            }
        ]
    }

    normalized = toolbox_tools._normalize_web_search_result(result, "test query")

    assert normalized == {
        "query": "test query",
        "text": "Evidence [source](https://one.example/article)",
        "citations": [
            {"url": "https://one.example/article"},
            {"url": "https://two.example/report"},
            {"url": "https://three.example/news"},
        ],
        "grounded": True,
        "billing": {"unit": "transaction", "quantity": 1},
    }


def test_normalizer_fails_closed_when_toolbox_omits_source_urls():
    result = {
        "content": [
            {
                "type": "resource",
                "_meta": {"action": {"query": "test query"}},
                "resource": {"text": "A synthesized answer without source links."},
            }
        ]
    }

    normalized = toolbox_tools._normalize_web_search_result(result, "test query")

    assert normalized["citations"] == []
    assert normalized["grounded"] is False
    assert "Do not invent or cite URLs" in normalized["grounding_warning"]


@pytest.mark.asyncio
async def test_bridge_sends_documented_search_query(monkeypatch):
    credential = AsyncMock()
    credential.get_token.return_value = SimpleNamespace(token="-".join(("test", "token")))
    monkeypatch.setattr(toolbox_tools, "DefaultAzureCredential", lambda: credential)

    initialize_response = SimpleNamespace(
        headers={"mcp-session-id": "session-1", "content-type": "application/json"},
        json=lambda: {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}},
        raise_for_status=lambda: None,
    )
    notification_response = SimpleNamespace()
    tool_response = SimpleNamespace(
        headers={"content-type": "application/json"},
        json=lambda: {"jsonrpc": "2.0", "id": 2, "result": {"content": []}},
        raise_for_status=lambda: None,
    )
    client = AsyncMock(headers={})
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.post.side_effect = [initialize_response, notification_response, tool_response]
    monkeypatch.setattr(toolbox_tools.httpx, "AsyncClient", lambda **_kwargs: client)

    result = await toolbox_tools._call_toolbox_web_search("https://toolbox.example/mcp", "current news")

    tool_call = client.post.await_args_list[2].kwargs["json"]
    assert tool_call["method"] == "tools/call"
    assert tool_call["params"] == {
        "name": "web_search",
        "arguments": {"search_query": "current news"},
    }
    assert client.headers["Mcp-Session-Id"] == "session-1"
    assert result["grounded"] is False
    assert result["billing"] == {"unit": "transaction", "quantity": 1}
    credential.get_token.assert_awaited_once_with("https://ai.azure.com/.default")
    credential.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_code_interpreter_bridge_sends_code_only(monkeypatch):
    credential = AsyncMock()
    credential.get_token.return_value = SimpleNamespace(token="-".join(("test", "token")))
    monkeypatch.setattr(toolbox_tools, "DefaultAzureCredential", lambda: credential)

    initialize_response = SimpleNamespace(
        headers={"mcp-session-id": "session-1", "content-type": "application/json"},
        json=lambda: {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}},
        raise_for_status=lambda: None,
    )
    notification_response = SimpleNamespace()
    tool_response = SimpleNamespace(
        headers={"content-type": "application/json"},
        json=lambda: {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": "result=404"}], "isError": False},
        },
        raise_for_status=lambda: None,
    )
    client = AsyncMock(headers={})
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.post.side_effect = [initialize_response, notification_response, tool_response]
    monkeypatch.setattr(toolbox_tools.httpx, "AsyncClient", lambda **_kwargs: client)

    result = await toolbox_tools._call_toolbox_code_interpreter(
        "https://toolbox.example/mcp", "print(sum(value * value for value in [3, 7, 11, 15]))"
    )

    tool_call = client.post.await_args_list[2].kwargs["json"]
    assert tool_call["params"] == {
        "name": "code_interpreter",
        "arguments": {"code": "print(sum(value * value for value in [3, 7, 11, 15]))"},
    }
    assert result == {"text": "result=404", "billing": {"unit": "session", "quantity": 1}}


def test_code_interpreter_normalizer_fails_closed_on_tool_error():
    normalized = toolbox_tools._normalize_code_interpreter_result(
        {"content": [{"type": "text", "text": "Execution failed"}], "isError": True}
    )

    assert normalized == {
        "error": "Execution failed",
        "billing": {"unit": "session", "quantity": 1},
    }


def test_parse_mcp_sse_response():
    response = toolbox_tools.httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text='event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"content":[]}}\n\n',
    )

    assert toolbox_tools._parse_mcp_response(response)["result"] == {"content": []}
