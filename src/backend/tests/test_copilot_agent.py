"""Tests for the Copilot SDK agent service."""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.models import ContentEvent, ErrorEvent, ThoughtEvent, ToolCallEvent
from app.services.copilot_agent import CopilotAgent, _limit_tool_event_output
from app.services.skill_tools import ALL_TOOLS


@pytest.fixture
def settings():
    return Settings(
        foundry_endpoint="https://test.services.ai.azure.com",
        foundry_model_deployment="gpt-52",
    )


@pytest.fixture
def copilot_agent(settings):
    return CopilotAgent(settings)


def test_copilot_agent_init(copilot_agent, settings):
    """Test CopilotAgent initializes with correct settings."""
    assert copilot_agent.settings is settings
    assert copilot_agent._client is None
    assert copilot_agent._sessions == {}


def test_foundry_toolbox_is_disabled_by_default(copilot_agent):
    """Keep existing local tools as the default rollback path."""
    assert copilot_agent._toolbox_endpoint() == ""


def test_foundry_toolbox_uses_injected_endpoint(settings, monkeypatch):
    monkeypatch.setenv(
        "TOOLBOX_KRATOS_TOOLS_MCP_ENDPOINT",
        "https://example.test/versioned/toolbox/mcp?api-version=v1",
    )
    agent = CopilotAgent(
        settings.model_copy(
            update={
                "toolbox_enabled": True,
                "foundry_project_endpoint": "https://example.test/api/projects/project",
            }
        )
    )

    assert agent._toolbox_endpoint() == "https://example.test/versioned/toolbox/mcp?api-version=v1"

    generic_tools = agent._apply_foundry_toolbox_tools("generic", ALL_TOOLS)
    assert "web_search" not in {tool.name for tool in generic_tools}
    assert "kratos-tools-web_search" in {tool.name for tool in generic_tools}
    assert agent._apply_foundry_toolbox_tools("finance-close", ALL_TOOLS) is ALL_TOOLS


def test_foundry_toolbox_adds_generic_web_search_routing_guidance(settings, monkeypatch):
    monkeypatch.setenv(
        "TOOLBOX_KRATOS_TOOLS_MCP_ENDPOINT",
        "https://example.test/versioned/toolbox/mcp?api-version=v1",
    )
    agent = CopilotAgent(settings.model_copy(update={"toolbox_enabled": True}))

    prompt = agent._apply_toolbox_prompt("generic", "Base prompt")

    assert prompt.startswith("Base prompt")
    assert "kratos-tools-web_search" in prompt
    assert "Cite only URLs in its citations list" in prompt
    assert "Retry it at most once" in prompt
    assert "Do not call web_fetch" in prompt
    assert "Do not narrate the search" in prompt
    assert "follow that format exactly" in prompt
    assert agent._apply_toolbox_prompt("finance-close", "Base prompt") == "Base prompt"


def test_microsoft_learn_toolbox_pilot_replaces_direct_generic_mcp(settings, monkeypatch):
    monkeypatch.setenv("TOOLBOX_KRATOS_TOOLS_MCP_ENDPOINT", "https://example.test/toolbox/mcp")
    agent = CopilotAgent(
        settings.model_copy(
            update={
                "toolbox_enabled": True,
                "toolbox_learn_enabled": True,
                "toolbox_learn_version": "6",
                "foundry_project_endpoint": "https://example.test/api/projects/project",
            }
        )
    )
    direct_servers = {"microsoft-learn": {"url": "https://learn.microsoft.com/api/mcp"}}

    generic_names = {tool.name for tool in agent._apply_foundry_toolbox_tools("generic", ALL_TOOLS)}

    assert {
        "kratos-tools-microsoft_docs_search",
        "kratos-tools-microsoft_code_sample_search",
        "kratos-tools-microsoft_docs_fetch",
    } <= generic_names
    assert agent._apply_foundry_toolbox_mcp("generic", direct_servers) == {}
    assert agent._apply_foundry_toolbox_mcp("finance-close", direct_servers) is direct_servers
    prompt = agent._apply_toolbox_prompt("generic", "Base prompt")
    assert "kratos-tools-microsoft_docs_search instead of Web Search" in prompt
    assert "kratos-tools-microsoft_docs_fetch" in prompt
    assert "kratos-tools-microsoft_code_sample_search" in prompt


def test_microsoft_learn_toolbox_pilot_rejects_unpinned_version(settings):
    agent = CopilotAgent(
        settings.model_copy(
            update={
                "toolbox_enabled": True,
                "toolbox_learn_enabled": True,
                "toolbox_learn_version": "latest",
                "foundry_project_endpoint": "https://example.test/api/projects/project",
            }
        )
    )
    direct_servers = {"microsoft-learn": {"url": "https://learn.microsoft.com/api/mcp"}}

    assert agent._toolbox_learn_endpoint() == ""
    assert agent._apply_foundry_toolbox_mcp("generic", direct_servers) is direct_servers
    generic_names = {tool.name for tool in agent._apply_foundry_toolbox_tools("generic", ALL_TOOLS)}
    assert not any(name.startswith("kratos-tools-microsoft_") for name in generic_names)


def test_microsoft_learn_toolbox_pilot_is_disabled_by_default(copilot_agent):
    direct_servers = {"microsoft-learn": {"url": "https://learn.microsoft.com/api/mcp"}}

    assert copilot_agent._apply_foundry_toolbox_mcp("generic", direct_servers) is direct_servers
    generic_names = {tool.name for tool in copilot_agent._apply_foundry_toolbox_tools("generic", ALL_TOOLS)}
    assert not any(name.startswith("kratos-tools-microsoft_") for name in generic_names)


def test_hosted_agent_manifest_declares_microsoft_learn_pilot_controls():
    manifest = (Path(__file__).parents[2] / "hosted-agent" / "agent.yaml").read_text(encoding="utf-8")

    assert "TOOLBOX_LEARN_ENABLED" in manifest
    assert "${TOOLBOX_LEARN_ENABLED=false}" in manifest
    assert "TOOLBOX_LEARN_VERSION" in manifest
    assert "${TOOLBOX_LEARN_VERSION=0}" in manifest


def test_foundry_toolbox_prompt_guidance_is_disabled_with_toolbox(settings):
    agent = CopilotAgent(settings)

    assert agent._apply_toolbox_prompt("generic", "Base prompt") == "Base prompt"


def test_tool_event_output_limit_preserves_structured_citations_and_billing():
    value = {
        "query": "latest AI trends",
        "text": "Evidence sentence. " * 1_000,
        "citations": [{"url": f"https://example.com/source-{index}"} for index in range(5)],
        "billing": {"unit": "transaction", "quantity": 1},
    }

    output = _limit_tool_event_output(value, limit=1_000)
    parsed = json.loads(output)

    assert len(output) <= 1_000
    assert parsed["text"].endswith("...[text truncated for tool-event storage]")
    assert parsed["citations"] == value["citations"]
    assert parsed["billing"] == value["billing"]


def test_code_interpreter_pilot_uses_immutable_version_for_generic_only(settings):
    agent = CopilotAgent(
        settings.model_copy(
            update={
                "toolbox_enabled": True,
                "toolbox_code_interpreter_enabled": True,
                "toolbox_code_interpreter_version": "5",
                "foundry_project_endpoint": "https://example.test/api/projects/project",
            }
        )
    )

    assert agent._toolbox_code_interpreter_endpoint() == (
        "https://example.test/api/projects/project/toolboxes/kratos-tools/versions/5/mcp?api-version=v1"
    )
    generic_tools = agent._apply_foundry_toolbox_tools("generic", ALL_TOOLS)
    generic_names = {tool.name for tool in generic_tools}
    assert "code_interpreter" not in generic_names
    assert "kratos-tools-code_interpreter" in generic_names
    assert agent._apply_foundry_toolbox_tools("finance-close", ALL_TOOLS) is ALL_TOOLS

    prompt = agent._apply_toolbox_prompt("generic", "Base prompt")
    assert "kratos-tools-code_interpreter" in prompt
    assert "synthetic generated output files" in prompt
    assert "Do not use file inputs" in prompt
    assert "include each exact local path" in prompt
    assert "user-uploaded data" in prompt


def test_code_interpreter_pilot_rejects_unpinned_version(settings):
    agent = CopilotAgent(
        settings.model_copy(
            update={
                "toolbox_enabled": True,
                "toolbox_code_interpreter_enabled": True,
                "toolbox_code_interpreter_version": "latest",
                "foundry_project_endpoint": "https://example.test/api/projects/project",
            }
        )
    )

    assert agent._toolbox_code_interpreter_endpoint() == ""
    generic_names = {tool.name for tool in agent._apply_foundry_toolbox_tools("generic", ALL_TOOLS)}
    assert "code_interpreter" in generic_names
    assert "kratos-tools-code_interpreter" not in generic_names


@pytest.mark.asyncio
async def test_foundry_toolbox_auth_is_renewable_and_endpoint_scoped(settings):
    """Mint Foundry tokens only for the configured Toolbox endpoint."""
    agent = CopilotAgent(
        settings.model_copy(
            update={
                "toolbox_enabled": True,
                "foundry_project_endpoint": "https://example.test/api/projects/project",
            }
        )
    )
    agent._credential = AsyncMock()
    test_access_token = "-".join(("test", "token"))
    agent._credential.get_token.return_value = SimpleNamespace(
        token=test_access_token,
        expires_on=time.time() + 3600,
    )
    endpoint = agent._toolbox_endpoint()

    result = await agent._handle_mcp_auth_request(
        {"serverName": "kratos-tools", "serverUrl": endpoint, "reason": "initial"},
        {},
    )
    assert result["kind"] == "token"
    assert result["accessToken"] == test_access_token
    assert result["tokenType"] == "Bearer"
    assert 3500 <= result["expiresIn"] <= 3600
    agent._credential.get_token.assert_awaited_once_with("https://ai.azure.com/.default")

    rejected = await agent._handle_mcp_auth_request(
        {"serverName": "kratos-tools", "serverUrl": "https://untrusted.test/mcp"},
        {},
    )
    assert rejected == {"kind": "cancelled"}
    agent._credential.get_token.assert_awaited_once()


@pytest.mark.asyncio
async def test_copilot_agent_start_stop(settings):
    """Test CopilotClient start and stop lifecycle."""
    # Force cloud mode so the Azure credential is actually created and closed;
    # in local mode start()/stop() intentionally skip the credential entirely.
    cloud_agent = CopilotAgent(settings.model_copy(update={"local_mode": False}))
    mock_client = AsyncMock()
    mock_credential = AsyncMock()
    with (
        patch("app.services.copilot_agent.CopilotClient", return_value=mock_client),
        patch("app.services.copilot_agent.ManagedIdentityCredential", return_value=mock_credential),
        patch("app.services.copilot_agent._HAS_CLI_CREDENTIAL", False),
        patch("app.services.copilot_agent.get_bearer_token_provider", return_value=lambda: "token"),
    ):
        await cloud_agent.start()

        assert cloud_agent._client is mock_client
        mock_client.start.assert_awaited_once()

        await cloud_agent.stop()
        mock_client.stop.assert_awaited_once()
        mock_credential.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_copilot_agent_run_streams_content(copilot_agent):
    """Test that run() yields ContentEvent from SDK assistant.message.delta events."""
    mock_session = AsyncMock()
    mock_client = AsyncMock()
    mock_client.create_session = AsyncMock(return_value=mock_session)

    # Simulate SDK events via the on_event callback
    def fake_on(callback):
        # Simulate content delta event
        delta_event = MagicMock()
        delta_event.type.value = "assistant.message_delta"
        delta_event.data.delta_content = "Hello, world!"
        callback(delta_event)

        # Simulate session idle (end of stream)
        idle_event = MagicMock()
        idle_event.type.value = "session.idle"
        callback(idle_event)

    mock_session.on = fake_on
    mock_session.send = AsyncMock()

    with (
        patch("app.services.copilot_agent.CopilotClient", return_value=mock_client),
        patch("app.services.copilot_agent.ManagedIdentityCredential", return_value=AsyncMock()),
        patch("app.services.copilot_agent._HAS_CLI_CREDENTIAL", False),
        patch("app.services.copilot_agent.get_bearer_token_provider", return_value=lambda: "token"),
    ):
        await copilot_agent.start()

        events = []
        async for event in copilot_agent.run(
            message="Hello",
            conversation_id="test-conv-1",
        ):
            events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], ContentEvent)
    assert events[0].content == "Hello, world!"


@pytest.mark.asyncio
async def test_copilot_agent_run_streams_tool_events(copilot_agent):
    """Test that run() yields ThoughtEvent and ToolCallEvent for tool executions."""
    mock_session = AsyncMock()
    mock_client = AsyncMock()
    mock_client.create_session = AsyncMock(return_value=mock_session)

    def fake_on(callback):
        # Tool start event
        start_event = MagicMock()
        start_event.type.value = "tool.execution_start"
        start_event.data.tool_name = "web_search"
        start_event.data.arguments = {"query": "test"}
        start_event.data.tool_call_id = "call-1"
        callback(start_event)

        # Tool end event
        end_event = MagicMock()
        end_event.type.value = "tool.execution_complete"
        end_event.data.tool_name = "web_search"
        end_event.data.output = '{"results": []}'
        end_event.data.duration_ms = 150
        end_event.data.success = True
        callback(end_event)

        # Content
        delta_event = MagicMock()
        delta_event.type.value = "assistant.message_delta"
        delta_event.data.delta_content = "Based on the search..."
        callback(delta_event)

        # Done
        idle_event = MagicMock()
        idle_event.type.value = "session.idle"
        callback(idle_event)

    mock_session.on = fake_on
    mock_session.send = AsyncMock()

    with (
        patch("app.services.copilot_agent.CopilotClient", return_value=mock_client),
        patch("app.services.copilot_agent.ManagedIdentityCredential", return_value=AsyncMock()),
        patch("app.services.copilot_agent._HAS_CLI_CREDENTIAL", False),
        patch("app.services.copilot_agent.get_bearer_token_provider", return_value=lambda: "token"),
    ):
        await copilot_agent.start()

        events = []
        async for event in copilot_agent.run(
            message="Search the web",
            conversation_id="test-conv-2",
        ):
            events.append(event)

    # ThoughtEvent, ToolCallEvent(started), ToolCallEvent(completed), ContentEvent
    assert len(events) == 4
    assert isinstance(events[0], ThoughtEvent)
    assert "Web Search" in events[0].content
    assert isinstance(events[1], ToolCallEvent)
    assert events[1].status == "started"
    assert json.loads(events[1].input) == {"query": "test"}
    assert isinstance(events[2], ToolCallEvent)
    assert events[2].status == "completed"
    assert events[2].durationMs == 150
    assert isinstance(events[3], ContentEvent)


@pytest.mark.asyncio
async def test_copilot_agent_retains_tool_output_for_evaluation(copilot_agent):
    mock_session = AsyncMock()
    mock_client = AsyncMock()
    mock_client.create_session = AsyncMock(return_value=mock_session)
    tool_output = '{"billing":{"unit":"transaction","quantity":1},"text":"' + "x" * 15_000 + '"}'

    def fake_on(callback):
        start_event = MagicMock()
        start_event.type.value = "tool.execution_start"
        start_event.data.tool_name = "web_search"
        callback(start_event)

        end_event = MagicMock()
        end_event.type.value = "tool.execution_complete"
        end_event.data.tool_name = "web_search"
        end_event.data.output = tool_output
        end_event.data.success = True
        callback(end_event)

        idle_event = MagicMock()
        idle_event.type.value = "session.idle"
        callback(idle_event)

    mock_session.on = fake_on
    mock_session.send = AsyncMock()

    with (
        patch("app.services.copilot_agent.CopilotClient", return_value=mock_client),
        patch("app.services.copilot_agent.ManagedIdentityCredential", return_value=AsyncMock()),
        patch("app.services.copilot_agent._HAS_CLI_CREDENTIAL", False),
        patch("app.services.copilot_agent.get_bearer_token_provider", return_value=lambda: "token"),
    ):
        await copilot_agent.start()
        events = [
            event
            async for event in copilot_agent.run(
                message="Search the web",
                conversation_id="test-conv-tool-output",
            )
        ]

    completed = next(event for event in events if isinstance(event, ToolCallEvent) and event.status == "completed")
    assert len(completed.output) == 12_000
    assert completed.output.startswith('{\n  "billing":')
    assert completed.billing == {"unit": "transaction", "quantity": 1}


@pytest.mark.asyncio
async def test_copilot_agent_run_handles_error(copilot_agent):
    """Test that run() yields ErrorEvent on SDK error."""
    mock_session = AsyncMock()
    mock_client = AsyncMock()
    mock_client.create_session = AsyncMock(return_value=mock_session)

    def fake_on(callback):
        error_event = MagicMock()
        error_event.type.value = "session.error"
        error_event.data.message = "Model unavailable"
        callback(error_event)

    mock_session.on = fake_on
    mock_session.send = AsyncMock()

    with (
        patch("app.services.copilot_agent.CopilotClient", return_value=mock_client),
        patch("app.services.copilot_agent.ManagedIdentityCredential", return_value=AsyncMock()),
        patch("app.services.copilot_agent._HAS_CLI_CREDENTIAL", False),
        patch("app.services.copilot_agent.get_bearer_token_provider", return_value=lambda: "token"),
    ):
        await copilot_agent.start()

        events = []
        async for event in copilot_agent.run(
            message="Hello",
            conversation_id="test-conv-3",
        ):
            events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert events[0].code == "SDK_ERROR"
    assert "Model unavailable" in events[0].message


@pytest.mark.asyncio
async def test_copilot_agent_session_reuse(copilot_agent):
    """Test that the same conversation reuses the same SDK session."""
    mock_session = AsyncMock()
    mock_client = AsyncMock()
    mock_client.create_session = AsyncMock(return_value=mock_session)

    # The agent registers the event handler ONCE per session, so model a
    # persistent SDK stream: capture the callback on registration and deliver a
    # `session.idle` event on every send() (including the reuse turn).
    captured: dict = {}

    def fake_on(callback):
        captured["callback"] = callback

    async def fake_send(*args, **kwargs):
        idle_event = MagicMock()
        idle_event.type.value = "session.idle"
        captured["callback"](idle_event)

    mock_session.on = fake_on
    mock_session.send = fake_send

    with (
        patch("app.services.copilot_agent.CopilotClient", return_value=mock_client),
        patch("app.services.copilot_agent.ManagedIdentityCredential", return_value=AsyncMock()),
        patch("app.services.copilot_agent._HAS_CLI_CREDENTIAL", False),
        patch("app.services.copilot_agent.get_bearer_token_provider", return_value=lambda: "token"),
    ):
        await copilot_agent.start()

        # First call creates a session
        async for _ in copilot_agent.run(message="Hello", conversation_id="conv-reuse"):
            pass
        assert mock_client.create_session.await_count == 1

        # Second call reuses the session
        async for _ in copilot_agent.run(message="Follow up", conversation_id="conv-reuse"):
            pass
        assert mock_client.create_session.await_count == 1  # still 1


@pytest.mark.asyncio
async def test_copilot_agent_exception_drops_session(copilot_agent):
    """Test that a failed session is dropped so the next call gets a fresh one."""
    mock_client = AsyncMock()
    mock_client.create_session = AsyncMock(side_effect=Exception("connection failed"))

    with (
        patch("app.services.copilot_agent.CopilotClient", return_value=mock_client),
        patch("app.services.copilot_agent.ManagedIdentityCredential", return_value=AsyncMock()),
        patch("app.services.copilot_agent._HAS_CLI_CREDENTIAL", False),
        patch("app.services.copilot_agent.get_bearer_token_provider", return_value=lambda: "token"),
    ):
        await copilot_agent.start()

        events = []
        async for event in copilot_agent.run(message="Hello", conversation_id="conv-fail"):
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], ErrorEvent)
        assert events[0].code == "AGENT_ERROR"
        # Session should be dropped
        assert "conv-fail" not in copilot_agent._sessions
