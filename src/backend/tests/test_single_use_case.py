"""Tests for the single Agent Reliability Engineer runtime policy."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.constants import PRIMARY_USE_CASE
from app.main import app
from app.models import AgentRequest, Conversation, ConversationCreate, CopilotStudioRequest
from app.routers.settings import _environment_class, _evidence_target
from app.services.blob_skill_service import BlobSkillService
from app.services.copilot_agent import DEFAULT_USE_CASE
from app.services.skill_registry import SkillRegistry


def test_request_models_default_to_are():
    assert ConversationCreate().model_dump() == {"title": "New Conversation"}
    assert AgentRequest(conversationId="conversation", message="hello").model_dump(exclude_defaults=True) == {
        "conversationId": "conversation",
        "message": "hello",
    }
    assert CopilotStudioRequest(message="hello").model_dump() == {
        "message": "hello",
        "conversationId": "",
    }
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
def test_request_models_reject_obsolete_use_case_field(model, payload):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
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

    assert "useCase" not in conversation.model_dump()


def test_openapi_exposes_only_single_agent_routes():
    paths = app.openapi()["paths"]

    assert "/api/evals/scenarios" in paths
    assert "/api/admin/skills" in paths
    assert "/api/context" in paths
    assert "/api/settings" not in paths
    assert "/api/use-cases" not in paths
    assert "/api/admin/apm" not in paths
    assert all("use-case" not in path and "use_case" not in path for path in paths)


@pytest.mark.parametrize(
    ("environment", "local_mode", "expected"),
    [
        ("production", False, "production"),
        ("prod-east", False, "production"),
        ("staging", False, "non-production"),
        ("custom", False, "unknown"),
        ("production", True, "local"),
    ],
)
def test_operational_environment_classification(environment, local_mode, expected):
    assert _environment_class(environment, local_mode) == expected


def test_operational_evidence_target_redacts_resource_identifiers():
    target, configured = _evidence_target(
        "/subscriptions/private-subscription/resourceGroups/private-rg/providers/"
        "microsoft.insights/components/incident-observability",
        "",
    )

    assert configured is True
    assert target == "Application Insights: incident-observability"
    assert "private-subscription" not in target
    assert "private-rg" not in target


def test_operational_workspace_target_does_not_expose_workspace_id():
    target, configured = _evidence_target("", "private-workspace-id")

    assert configured is True
    assert target == "Configured Log Analytics workspace"
    assert "private-workspace-id" not in target


def test_operational_context_endpoint_uses_local_mode_property():
    response = TestClient(app, raise_server_exceptions=False).get("/api/context")

    assert response.status_code == 200
    assert response.json()["environmentClass"] == "local"


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
