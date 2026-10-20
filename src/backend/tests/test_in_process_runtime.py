"""Regression tests for the FastAPI-owned Copilot runtime."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.constants import PRIMARY_USE_CASE
from app.models import (
    AgentCancelRequest,
    AgentRequest,
    ContentEvent,
    ToolCallEvent,
    UsageEvent,
    UserInputResponseRequest,
)
from app.routers.agent import cancel_agent, chat, user_input_response
from app.services.eval_service import EvalService


class _FakeAgent:
    def __init__(self) -> None:
        self.use_cases: list[tuple[str, str]] = []
        self.tokens: list[tuple[str, dict[str, str]]] = []
        self.run_kwargs: dict = {}

    def set_conversation_use_case(self, conversation_id: str, use_case: str) -> None:
        self.use_cases.append((conversation_id, use_case))

    def set_conversation_mcp_tokens(self, conversation_id: str, tokens: dict[str, str]) -> None:
        self.tokens.append((conversation_id, tokens))

    async def run(self, **kwargs):
        self.run_kwargs = kwargs
        yield ToolCallEvent(skillName="app_insights_investigation", status="started")
        yield ToolCallEvent(skillName="app_insights_investigation", status="completed")
        yield ContentEvent(content="Evidence found.")
        yield UsageEvent(promptTokens=10, completionTokens=4, totalTokens=14)

    def resolve_user_input(self, request_id: str, answer: str) -> bool:
        return request_id == "active" and answer == "approve"

    def cancel(self, conversation_id: str) -> bool:
        return conversation_id == "running"

    def get_run_stats(self, conversation_id: str) -> dict[str, int]:
        assert conversation_id == "conv-1"
        return {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "reasoning_tokens": 0,
            "total_tokens": 14,
            "time_to_first_token_ms": 12,
            "model_latency_ms": 20,
        }


def _request(agent: _FakeAgent, cosmos: AsyncMock | None = None) -> MagicMock:
    request = MagicMock()
    request.headers = {}
    request.state = SimpleNamespace()
    request.app.state = SimpleNamespace(
        copilot_agent=agent,
        cosmos_service=cosmos or AsyncMock(),
        registries={},
    )
    return request


@pytest.mark.asyncio
async def test_chat_streams_in_process_events_and_persists_messages():
    agent = _FakeAgent()
    cosmos = AsyncMock()
    request = _request(agent, cosmos)
    body = AgentRequest(
        conversationId="conv-1",
        message="Investigate checkout-api",
        mcpAccessTokens={"graph-obo": "secret-token"},
    )

    with patch("app.routers.agent.generate_follow_ups", new=AsyncMock(return_value=[])):
        response = await chat(body, request)
        chunks = [chunk async for chunk in response.body_iterator]

    assert any(chunk["event"] == "content" and "Evidence found." in chunk["data"] for chunk in chunks)
    assert any(chunk["event"] == "done" for chunk in chunks)
    assert agent.use_cases == [("conv-1", PRIMARY_USE_CASE)]
    assert agent.tokens == [("conv-1", {"graph-obo": "secret-token"})]
    assert agent.run_kwargs["conversation_id"] == "conv-1"
    assert cosmos.upsert_message.await_count == 2
    assistant = cosmos.upsert_message.await_args_list[1].args[0]
    assert assistant.content == "Evidence found."
    assert assistant.metadata["runStats"]["totalTokens"] == 14
    assert assistant.metadata["runStats"]["totalToolCalls"] == 1


@pytest.mark.asyncio
async def test_user_input_and_cancel_route_to_in_process_agent():
    agent = _FakeAgent()
    request = _request(agent)

    accepted = await user_input_response(
        UserInputResponseRequest(conversationId="conv-1", requestId="active", answer="approve"),
        request,
    )
    cancelled = await cancel_agent(AgentCancelRequest(conversationId="running"), request)

    assert json.loads(accepted.body) == {"accepted": True}
    assert json.loads(cancelled.body) == {"cancelled": True}


@pytest.mark.asyncio
async def test_eval_service_aggregates_in_process_agent_events():
    agent = _FakeAgent()
    service = EvalService(
        settings=MagicMock(),
        storage=MagicMock(),
        registries={},
        copilot_agent=agent,
    )

    text, tool_calls, error = await service._invoke_agent(
        message="Investigate",
        conversation_id="eval-conv",
        use_case="agent-reliability-engineer",
        run_id="eval-run",
    )

    assert text == "Evidence found."
    assert len(tool_calls) == 2
    assert error is None
    assert agent.run_kwargs["eval_run_id"] == "eval-run"
