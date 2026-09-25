import json
from pathlib import Path
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


@pytest.mark.asyncio
async def test_downloads_and_deletes_generated_file(monkeypatch):
    requests = []

    async def handler(request):
        requests.append((request.method, str(request.url)))
        if request.method == "GET":
            return toolbox_tools.httpx.Response(200, content=b"month,value\nJan,10\n")
        return toolbox_tools.httpx.Response(200)

    monkeypatch.setattr(toolbox_tools, "uuid4", lambda: SimpleNamespace(hex="test-id"))
    result = {
        "content": [
            {
                "_meta": {
                    "container_file_citations": json.dumps(
                        [
                            {
                                "container_id": "container-1",
                                "file_id": "file-1",
                                "filename": "summary.csv",
                            }
                        ]
                    )
                }
            }
        ]
    }

    async with toolbox_tools.httpx.AsyncClient(transport=toolbox_tools.httpx.MockTransport(handler)) as client:
        files = await toolbox_tools._download_generated_files(
            client, "https://project.example/toolboxes/kratos/versions/5/mcp?api-version=v1", result
        )

    local_path = Path(files[0]["path"])
    try:
        assert files == [{"filename": "summary.csv", "path": "/tmp/toolbox-test-id-summary.csv", "size": 19}]
        assert local_path.read_bytes() == b"month,value\nJan,10\n"
        assert [method for method, _url in requests] == ["GET", "DELETE"]
        assert all("container-1" in url and "file-1" in url for _method, url in requests)
    finally:
        local_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_rejects_unsafe_generated_filename_before_download():
    result = {
        "content": [
            {
                "_meta": {
                    "container_file_citations": [
                        {"container_id": "container-1", "file_id": "file-1", "filename": "../secret.txt"}
                    ]
                }
            }
        ]
    }
    client = AsyncMock()

    with pytest.raises(ValueError, match="unsafe generated-file citation"):
        await toolbox_tools._download_generated_files(
            client, "https://project.example/toolboxes/kratos/versions/5/mcp?api-version=v1", result
        )

    client.stream.assert_not_called()


@pytest.mark.asyncio
async def test_deletes_remote_file_when_download_exceeds_limit(monkeypatch):
    methods = []

    async def handler(request):
        methods.append(request.method)
        if request.method == "GET":
            return toolbox_tools.httpx.Response(200, content=b"too large")
        return toolbox_tools.httpx.Response(200)

    monkeypatch.setattr(toolbox_tools, "_MAX_GENERATED_FILE_BYTES", 4)
    monkeypatch.setattr(toolbox_tools, "uuid4", lambda: SimpleNamespace(hex="oversized"))
    result = {
        "content": [
            {
                "_meta": {
                    "container_file_citations": [
                        {"container_id": "container-1", "file_id": "file-1", "filename": "summary.csv"}
                    ]
                }
            }
        ]
    }

    async with toolbox_tools.httpx.AsyncClient(transport=toolbox_tools.httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="exceeds the 10 MiB limit"):
            await toolbox_tools._download_generated_files(
                client, "https://project.example/toolboxes/kratos/versions/5/mcp?api-version=v1", result
            )

    assert methods == ["GET", "DELETE"]
    assert not Path("/tmp/toolbox-oversized-summary.csv").exists()


def test_code_interpreter_normalizer_exposes_only_local_generated_file_metadata():
    normalized = toolbox_tools._normalize_code_interpreter_result(
        {
            "content": [{"type": "text", "text": "Created summary.csv"}],
            "_kratos_generated_files": [
                {"filename": "summary.csv", "path": "/tmp/toolbox-id-summary.csv", "size": 19}
            ],
        }
    )

    assert normalized == {
        "text": "Created summary.csv",
        "generated_files": [{"filename": "summary.csv", "path": "/tmp/toolbox-id-summary.csv", "size": 19}],
        "billing": {"unit": "session", "quantity": 1},
    }


def test_parse_mcp_sse_response():
    response = toolbox_tools.httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text='event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"content":[]}}\n\n',
    )

    assert toolbox_tools._parse_mcp_response(response)["result"] == {"content": []}
