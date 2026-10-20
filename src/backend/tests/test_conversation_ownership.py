"""Cross-user isolation tests for conversation-scoped APIs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models import (
    AgentCancelRequest,
    AgentRequest,
    ConversationCreate,
    ConversationUpdate,
    CopilotStudioRequest,
    UserInputResponseRequest,
)
from app.routers.agent import cancel_agent, chat, user_input_response
from app.routers.conversations import (
    create_conversation,
    delete_conversation,
    get_messages,
    list_conversations,
    update_conversation,
)
from app.routers.copilot_studio import copilot_studio_chat


def _request(cosmos: AsyncMock, user_id: str = "user-a") -> MagicMock:
    request = MagicMock()
    request.state = SimpleNamespace(authenticated_principal={"userId": user_id})
    request.headers = {}
    request.app.state = SimpleNamespace(
        cosmos_service=cosmos,
        copilot_agent=MagicMock(),
        blob_skill_service=None,
        registries={},
    )
    return request


@pytest.mark.asyncio
async def test_conversation_create_and_list_use_authenticated_partition():
    cosmos = AsyncMock()
    cosmos.list_conversations.return_value = []
    request = _request(cosmos)

    conversation = await create_conversation(ConversationCreate(title="Incident"), request)
    await list_conversations(request)

    assert conversation.userId == "user-a"
    cosmos.list_conversations.assert_awaited_once_with("user-a")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["messages", "update", "delete"])
async def test_cross_user_conversation_operations_return_not_found(operation: str):
    cosmos = AsyncMock()
    cosmos.get_conversation.return_value = None
    request = _request(cosmos, "user-b")

    with pytest.raises(HTTPException) as exc:
        if operation == "messages":
            await get_messages("user-a-conversation", request)
        elif operation == "update":
            await update_conversation("user-a-conversation", ConversationUpdate(title="Hijacked"), request)
        else:
            await delete_conversation("user-a-conversation", request)

    assert exc.value.status_code == 404
    cosmos.get_conversation.assert_awaited_once_with("user-a-conversation", "user-b")
    cosmos.list_messages.assert_not_awaited()
    cosmos.delete_conversation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["chat", "approval", "cancel"])
async def test_cross_user_agent_operations_return_not_found(operation: str):
    cosmos = AsyncMock()
    cosmos.get_conversation.return_value = None
    request = _request(cosmos, "user-b")

    with pytest.raises(HTTPException) as exc:
        if operation == "chat":
            await chat(AgentRequest(conversationId="user-a-conversation", message="Investigate"), request)
        elif operation == "approval":
            await user_input_response(
                UserInputResponseRequest(
                    conversationId="user-a-conversation",
                    requestId="approval",
                    answer="approve",
                ),
                request,
            )
        else:
            await cancel_agent(AgentCancelRequest(conversationId="user-a-conversation"), request)

    assert exc.value.status_code == 404
    request.app.state.copilot_agent.resolve_user_input.assert_not_called()
    request.app.state.copilot_agent.cancel.assert_not_called()


@pytest.mark.asyncio
async def test_copilot_studio_cannot_reuse_another_users_conversation():
    cosmos = AsyncMock()
    cosmos.get_conversation.return_value = None
    request = _request(cosmos, "studio-user-b")

    with pytest.raises(HTTPException) as exc:
        await copilot_studio_chat(
            CopilotStudioRequest(message="Investigate", conversationId="studio-user-a-conversation"),
            request,
        )

    assert exc.value.status_code == 404
    cosmos.get_conversation.assert_awaited_once_with("studio-user-a-conversation", "studio-user-b")
