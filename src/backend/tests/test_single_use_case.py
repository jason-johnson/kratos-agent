"""Tests for the single Agent Reliability Engineer runtime policy."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.constants import PRIMARY_USE_CASE
from app.models import AgentRequest, Conversation, ConversationCreate, CopilotStudioRequest
from app.services.blob_skill_service import BlobSkillService
from app.services.copilot_agent import DEFAULT_USE_CASE
from app.services.skill_registry import SkillRegistry


def test_request_models_default_to_are():
    assert ConversationCreate().useCase == PRIMARY_USE_CASE
    assert AgentRequest(conversationId="conversation", message="hello").useCase == PRIMARY_USE_CASE
    assert CopilotStudioRequest(message="hello").useCase == PRIMARY_USE_CASE
    assert DEFAULT_USE_CASE == PRIMARY_USE_CASE
    assert SkillRegistry().use_case == PRIMARY_USE_CASE


@pytest.mark.parametrize(
    "model, payload",
    [
        (ConversationCreate, {"useCase": "generic"}),
        (AgentRequest, {"conversationId": "conversation", "message": "hello", "useCase": "generic"}),
        (CopilotStudioRequest, {"message": "hello", "useCase": "generic"}),
    ],
)
def test_request_models_reject_legacy_personas(model, payload):
    with pytest.raises(ValidationError, match="Only the 'agent-reliability-engineer' use-case is supported"):
        model(**payload)


def test_persisted_legacy_conversation_remains_readable():
    conversation = Conversation.model_validate(
        {
            "id": "conversation",
            "userId": "user",
            "title": "Legacy conversation",
            "useCase": "generic",
            "status": "active",
            "createdAt": "2026-10-15T10:00:00Z",
            "updatedAt": "2026-10-15T10:00:00Z",
        }
    )

    assert conversation.useCase == "generic"


class _EmptyContainer:
    async def list_blobs(self, **_kwargs):
        if False:
            yield None


@pytest.mark.asyncio
async def test_blob_seed_can_scope_to_are_only(tmp_path):
    for name in (PRIMARY_USE_CASE, "generic"):
        use_case = tmp_path / name
        use_case.mkdir()
        (use_case / "SYSTEM_PROMPT.md").write_text(f"---\nname: {name}\n---\n")

    service = BlobSkillService(Settings(), local_base_dir=str(tmp_path))
    service._container_client = _EmptyContainer()
    service.upload_file = AsyncMock()

    seeded = await service.seed_from_local({PRIMARY_USE_CASE})

    assert seeded == [PRIMARY_USE_CASE]
    uploaded_paths = [call.args[0] for call in service.upload_file.await_args_list]
    assert uploaded_paths
    assert all(path.startswith(f"use-cases/{PRIMARY_USE_CASE}/") for path in uploaded_paths)
